"""Upload the best clips to YouTube Shorts (PRIVATE) and TikTok (DRAFT in the creator's inbox).

Safety by design (the user reviews before anything is public):
- YouTube: videos.insert with privacyStatus=private. The user makes it public from YouTube Studio.
- TikTok: Content Posting API "upload to inbox" (scope video.upload). The video lands as a draft in the
  TikTok app inbox; the user edits the caption and posts it from the app. TikTok allows at most 5
  pending inbox uploads per 24 h.
- Credentials and tokens live only in workspace/credentials/ (git-ignored), never in the repo or logs.
- An SQLite ledger keyed by the video's SHA-256 prevents uploading the same file twice.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import threading
import time
import webbrowser
from contextlib import closing
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from clipforge.logging_utils import log_event
from clipforge.publishing.publication import resolve_clip, save_json, upload_youtube, verify_channel_id

PLATFORMS_AUTO = ('youtube', 'tiktok')
DEFAULT_SETTINGS = {
    'youtube': {'client_secret': '', 'channel_id': ''},
    'tiktok': {'client_key': '', 'client_secret': '', 'redirect_port': 3455},
    'auto': {'min_score': 70, 'daily_limit': 5, 'hashtags': '#shorts #gaming #twitch'},
}
YOUTUBE_SCOPES = [
    'https://www.googleapis.com/auth/youtube.upload',
    'https://www.googleapis.com/auth/youtube.readonly',
]
TIKTOK_AUTH_URL = 'https://www.tiktok.com/v2/auth/authorize/'
TIKTOK_TOKEN_URL = 'https://open.tiktokapis.com/v2/oauth/token/'
TIKTOK_INBOX_INIT_URL = 'https://open.tiktokapis.com/v2/post/publish/inbox/video/init/'
TIKTOK_SCOPES = 'user.info.basic,video.upload'
TIKTOK_SINGLE_CHUNK_MAX = 64 * 1024 * 1024
TIKTOK_CHUNK_SIZE = 10 * 1024 * 1024
MAX_TITLE = 95


class PublishStore:
    """Local settings, OAuth tokens and the upload ledger under workspace/credentials/."""

    def __init__(self, workspace_root: Path):
        self.dir = Path(workspace_root).resolve() / 'credentials'
        self.dir.mkdir(parents=True, exist_ok=True)
        self.settings_path = self.dir / 'publish.json'
        self.youtube_token = self.dir / 'youtube_token.json'
        self.tiktok_token = self.dir / 'tiktok_token.json'
        self.ledger = self.dir / 'uploads.sqlite3'
        self._lock = threading.RLock()

    # ---- settings -------------------------------------------------------------------------------
    def settings(self) -> dict:
        data = json.loads(json.dumps(DEFAULT_SETTINGS))
        if self.settings_path.is_file():
            try:
                stored = json.loads(self.settings_path.read_text(encoding='utf-8'))
                for section, values in stored.items():
                    if section in data and isinstance(values, dict):
                        data[section].update(values)
            except (OSError, json.JSONDecodeError):
                pass
        return data

    def update_settings(self, patch: dict) -> dict:
        with self._lock:
            data = self.settings()
            yt = patch.get('youtube') or {}
            if 'client_secret' in yt:
                path = str(yt['client_secret'] or '').strip().strip('"')
                if path and not Path(path).is_file():
                    raise ValueError(f'No se encontró el archivo client_secret: {path}')
                data['youtube']['client_secret'] = path
            if 'channel_id' in yt:
                channel = str(yt['channel_id'] or '').strip()
                data['youtube']['channel_id'] = verify_channel_id(channel) if channel else ''
            tt = patch.get('tiktok') or {}
            for key in ('client_key', 'client_secret'):
                value = str(tt.get(key) or '').strip()
                if value and not value.startswith('•'):  # masked value coming back from the UI
                    data['tiktok'][key] = value
            if tt.get('redirect_port'):
                data['tiktok']['redirect_port'] = max(1024, min(65535, int(tt['redirect_port'])))
            auto = patch.get('auto') or {}
            if 'min_score' in auto:
                data['auto']['min_score'] = max(0, min(100, int(auto['min_score'])))
            if 'daily_limit' in auto:
                data['auto']['daily_limit'] = max(1, min(20, int(auto['daily_limit'])))
            if 'hashtags' in auto:
                data['auto']['hashtags'] = ' '.join(str(auto['hashtags']).split())[:200]
            save_json(self.settings_path, data)
            return data

    def public_status(self) -> dict:
        """Settings safe to show in the UI (secrets masked) + connection state."""
        data = self.settings()
        secret = data['tiktok'].get('client_secret') or ''
        data['tiktok']['client_secret'] = ('•' * 8 + secret[-4:]) if secret else ''
        data['tiktok']['redirect_uri'] = tiktok_redirect_uri(data['tiktok']['redirect_port'])
        data['connected'] = {
            'youtube': self.youtube_token.is_file(),
            'tiktok': self.tiktok_token.is_file(),
        }
        data['today'] = {platform: self.uploads_last_24h(platform) for platform in PLATFORMS_AUTO}
        return data

    # ---- ledger ---------------------------------------------------------------------------------
    def _db(self):
        db = sqlite3.connect(self.ledger)
        db.execute('''CREATE TABLE IF NOT EXISTS auto_uploads (
            platform TEXT NOT NULL, sha256 TEXT NOT NULL, remote_id TEXT, created_at TEXT NOT NULL,
            PRIMARY KEY (platform, sha256))''')
        return db

    def already_uploaded(self, platform: str, digest: str) -> str | None:
        with closing(self._db()) as db:
            row = db.execute('SELECT remote_id FROM auto_uploads WHERE platform=? AND sha256=?',
                             (platform, digest)).fetchone()
        return row[0] if row else None

    def record_upload(self, platform: str, digest: str, remote_id: str) -> None:
        with closing(self._db()) as db, db:
            db.execute('INSERT OR REPLACE INTO auto_uploads VALUES (?, ?, ?, ?)',
                       (platform, digest, remote_id, datetime.now(timezone.utc).isoformat()))

    def uploads_last_24h(self, platform: str) -> int:
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        with closing(self._db()) as db:
            return db.execute('SELECT COUNT(*) FROM auto_uploads WHERE platform=? AND created_at>=?',
                              (platform, since)).fetchone()[0]


# ---- YouTube ------------------------------------------------------------------------------------
def youtube_connect_interactive(store: PublishStore) -> dict:
    """Open the Google consent screen once and keep a refreshable token locally."""
    settings = store.settings()['youtube']
    if not settings['client_secret']:
        raise ValueError('Primero indica la ruta de tu client_secret.json de Google Cloud.')
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    flow = InstalledAppFlow.from_client_secrets_file(settings['client_secret'], YOUTUBE_SCOPES)
    credentials = flow.run_local_server(port=0, authorization_prompt_message=None, open_browser=True)
    api = build('youtube', 'v3', credentials=credentials, cache_discovery=False)
    try:
        items = api.channels().list(part='id,snippet', mine=True).execute().get('items', [])
    finally:
        api.close()
    if not items:
        raise ValueError('La cuenta autorizada no tiene canal de YouTube.')
    expected = settings.get('channel_id')
    channel = next((c for c in items if not expected or c['id'] == expected), None)
    if channel is None:
        raise ValueError('El canal autorizado no coincide con el ID de canal configurado.')
    store.youtube_token.write_text(credentials.to_json(), encoding='utf-8')
    if not expected:
        store.update_settings({'youtube': {'channel_id': channel['id']}})
    return {'channel_id': channel['id'], 'channel_title': channel['snippet']['title']}


def _youtube_api(store: PublishStore):
    from google.auth.transport.requests import Request as GoogleRequest
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    if not store.youtube_token.is_file():
        raise ValueError('YouTube no está conectado. Pulsa "Conectar YouTube" en Publicar.')
    credentials = Credentials.from_authorized_user_file(str(store.youtube_token), YOUTUBE_SCOPES)
    if not credentials.valid:
        credentials.refresh(GoogleRequest())
        store.youtube_token.write_text(credentials.to_json(), encoding='utf-8')
    return build('youtube', 'v3', credentials=credentials, cache_discovery=False)


def publish_youtube_private(store: PublishStore, video: Path, digest: str, title: str, description: str) -> dict:
    channel = store.settings()['youtube'].get('channel_id')
    if not channel:
        raise ValueError('Falta el ID del canal de YouTube.')
    api = _youtube_api(store)
    try:
        video_id, _created = upload_youtube(
            api, store.ledger, channel, video, digest,
            {'title': title[:MAX_TITLE], 'caption': description[:4900]}, None, False,
        )
    finally:
        api.close()
    return {'id': video_id, 'url': f'https://studio.youtube.com/video/{video_id}/edit', 'privacy': 'private'}


# ---- TikTok -------------------------------------------------------------------------------------
def tiktok_redirect_uri(port: int) -> str:
    return f'http://localhost:{int(port)}/callback/'


def pkce_pair() -> tuple[str, str]:
    """TikTok desktop PKCE: verifier of unreserved chars; challenge = HEX-encoded SHA-256 (TikTok-specific)."""
    verifier = secrets.token_urlsafe(64)[:86]
    challenge = hashlib.sha256(verifier.encode('ascii')).hexdigest()
    return verifier, challenge


def _post_form(url: str, fields: dict, *, opener=urlopen) -> dict:
    body = urlencode(fields).encode('utf-8')
    req = Request(url, data=body, method='POST', headers={'Content-Type': 'application/x-www-form-urlencoded'})
    try:
        with opener(req, timeout=30) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except HTTPError as error:
        raise RuntimeError(f'TikTok OAuth HTTP {error.code}: {error.read().decode("utf-8", "replace")[:200]}') from error


def _save_tiktok_token(store: PublishStore, payload: dict) -> dict:
    if not payload.get('access_token'):
        raise RuntimeError(f'TikTok no devolvió token: {payload.get("error_description") or payload.get("error") or payload}')
    now = time.time()
    token = {
        'access_token': payload['access_token'],
        'refresh_token': payload.get('refresh_token', ''),
        'open_id': payload.get('open_id', ''),
        'scope': payload.get('scope', ''),
        'expires_at': now + int(payload.get('expires_in', 0)),
        'refresh_expires_at': now + int(payload.get('refresh_expires_in', 0)),
    }
    store.tiktok_token.write_text(json.dumps(token), encoding='utf-8')
    return token


def tiktok_connect_interactive(
    store: PublishStore,
    *,
    open_url: Callable[[str], object] = webbrowser.open,
    timeout: float = 300.0,
    opener=urlopen,
) -> dict:
    """Login Kit for Desktop: local callback server + PKCE, then exchange the code for tokens."""
    settings = store.settings()['tiktok']
    if not settings['client_key'] or not settings['client_secret']:
        raise ValueError('Indica el Client key y el Client secret de tu app de TikTok for Developers.')
    port = int(settings['redirect_port'])
    redirect_uri = tiktok_redirect_uri(port)
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(24)
    result: dict = {}
    done = threading.Event()

    class Callback(BaseHTTPRequestHandler):
        def log_message(self, *args):  # noqa: D401 - silence
            return

        def do_GET(self):  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path.rstrip('/') != '/callback':
                self.send_response(404)
                self.end_headers()
                return
            params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            result.update(params)
            ok = params.get('state') == state and 'code' in params
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            msg = 'TikTok conectado. Ya puedes cerrar esta pestaña y volver a Gevik Clip.' if ok else \
                'No se pudo conectar TikTok. Vuelve a Gevik Clip e inténtalo de nuevo.'
            self.wfile.write(f'<html><body style="font-family:sans-serif"><h2>{msg}</h2></body></html>'.encode('utf-8'))
            done.set()

    server = HTTPServer(('127.0.0.1', port), Callback)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = TIKTOK_AUTH_URL + '?' + urlencode({
            'client_key': settings['client_key'],
            'scope': TIKTOK_SCOPES,
            'redirect_uri': redirect_uri,
            'state': state,
            'response_type': 'code',
            'code_challenge': challenge,
            'code_challenge_method': 'S256',
        })
        open_url(url)
        if not done.wait(timeout):
            raise TimeoutError('No se completó la autorización de TikTok a tiempo (5 min).')
    finally:
        server.shutdown()
        server.server_close()
    if result.get('state') != state:
        raise RuntimeError('Respuesta de TikTok inválida (state no coincide).')
    if 'code' not in result:
        raise RuntimeError(f'TikTok rechazó la autorización: {result.get("error_description") or result.get("error")}')
    payload = _post_form(TIKTOK_TOKEN_URL, {
        'client_key': settings['client_key'],
        'client_secret': settings['client_secret'],
        'code': result['code'],
        'grant_type': 'authorization_code',
        'redirect_uri': redirect_uri,
        'code_verifier': verifier,
    }, opener=opener)
    token = _save_tiktok_token(store, payload)
    return {'open_id': token['open_id'], 'scope': token['scope']}


def tiktok_access_token(store: PublishStore, *, opener=urlopen) -> str:
    if not store.tiktok_token.is_file():
        raise ValueError('TikTok no está conectado. Pulsa "Conectar TikTok" en Publicar.')
    token = json.loads(store.tiktok_token.read_text(encoding='utf-8'))
    if token.get('expires_at', 0) - time.time() > 300:
        return token['access_token']
    if not token.get('refresh_token') or token.get('refresh_expires_at', 0) <= time.time():
        raise ValueError('La sesión de TikTok expiró. Vuelve a pulsar "Conectar TikTok".')
    settings = store.settings()['tiktok']
    payload = _post_form(TIKTOK_TOKEN_URL, {
        'client_key': settings['client_key'],
        'client_secret': settings['client_secret'],
        'grant_type': 'refresh_token',
        'refresh_token': token['refresh_token'],
    }, opener=opener)
    return _save_tiktok_token(store, payload)['access_token']


def tiktok_chunk_plan(size: int) -> tuple[int, int]:
    """(chunk_size, total_chunk_count) per TikTok media transfer rules (last chunk absorbs the remainder)."""
    if size <= 0:
        raise ValueError('El video está vacío.')
    if size <= TIKTOK_SINGLE_CHUNK_MAX:
        return size, 1
    return TIKTOK_CHUNK_SIZE, size // TIKTOK_CHUNK_SIZE


def tiktok_upload_draft(store: PublishStore, video: Path, *, opener=urlopen) -> dict:
    """Send the MP4 to the creator's TikTok inbox as a draft (they finish and post it in the app)."""
    access = tiktok_access_token(store, opener=opener)
    size = video.stat().st_size
    chunk_size, chunks = tiktok_chunk_plan(size)
    init = Request(TIKTOK_INBOX_INIT_URL, method='POST', data=json.dumps({
        'source_info': {'source': 'FILE_UPLOAD', 'video_size': size, 'chunk_size': chunk_size,
                        'total_chunk_count': chunks},
    }).encode('utf-8'), headers={'Authorization': f'Bearer {access}', 'Content-Type': 'application/json; charset=UTF-8'})
    try:
        with opener(init, timeout=30) as resp:
            payload = json.loads(resp.read().decode('utf-8'))
    except HTTPError as error:
        raise RuntimeError(f'TikTok init HTTP {error.code}: {error.read().decode("utf-8", "replace")[:240]}') from error
    err = payload.get('error') or {}
    if err.get('code') not in (None, '', 'ok'):
        raise RuntimeError(f'TikTok: {err.get("code")} — {err.get("message", "")}')
    data = payload.get('data') or {}
    upload_url, publish_id = data.get('upload_url'), data.get('publish_id')
    if not upload_url or not publish_id:
        raise RuntimeError('TikTok no devolvió upload_url/publish_id.')
    with video.open('rb') as handle:
        for index in range(chunks):
            first = index * chunk_size
            last = size - 1 if index == chunks - 1 else first + chunk_size - 1
            handle.seek(first)
            body = handle.read(last - first + 1)
            put = Request(upload_url, method='PUT', data=body, headers={
                'Content-Type': 'video/mp4',
                'Content-Length': str(len(body)),
                'Content-Range': f'bytes {first}-{last}/{size}',
            })
            try:
                with opener(put, timeout=300) as resp:
                    resp.read()
            except HTTPError as error:
                raise RuntimeError(f'TikTok upload HTTP {error.code} en el bloque {index + 1}/{chunks}') from error
    return {'id': publish_id, 'url': 'https://www.tiktok.com/', 'privacy': 'draft'}


# ---- Orchestration ------------------------------------------------------------------------------
def build_youtube_texts(clip: dict, hashtags: str) -> tuple[str, str]:
    title = (clip.get('display_title') or clip['id']).strip()
    if '#shorts' not in title.lower() and len(title) + 8 <= MAX_TITLE:
        title = f'{title} #shorts'
    reason = ((clip.get('quality') or {}).get('ai_reason') or '').strip()
    description = '\n\n'.join(part for part in (clip.get('display_title', ''), reason, hashtags) if part)
    return title, description


def publish_clip(
    store: PublishStore,
    folder: Path,
    clip_id: str,
    platforms: list[str],
    *,
    respect_daily_limit: bool = True,
    uploaders: dict | None = None,
) -> dict:
    """Upload one rendered clip to the requested platforms; results are stored in manifest.json."""
    folder = Path(folder).resolve(strict=True)
    manifest_path = folder / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    clip = next((c for c in manifest.get('clips', []) if c['id'] == clip_id), None)
    if clip is None:
        raise ValueError(f'Clip no encontrado: {clip_id}')
    video, digest = resolve_clip(folder, clip)
    settings = store.settings()
    uploaders = uploaders or {
        'youtube': lambda: publish_youtube_private(store, video, digest, *build_youtube_texts(clip, settings['auto']['hashtags'])),
        'tiktok': lambda: tiktok_upload_draft(store, video),
    }
    results: dict[str, dict] = {}
    for platform in platforms:
        if platform not in PLATFORMS_AUTO:
            continue
        existing = store.already_uploaded(platform, digest)
        if existing:
            results[platform] = {'status': 'already', 'id': existing}
            continue
        if respect_daily_limit and store.uploads_last_24h(platform) >= int(settings['auto']['daily_limit']):
            results[platform] = {'status': 'skipped', 'error': f'Límite diario alcanzado ({settings["auto"]["daily_limit"]}/24 h)'}
            continue
        try:
            outcome = uploaders[platform]()
            store.record_upload(platform, digest, str(outcome['id']))
            results[platform] = {'status': 'uploaded', **outcome}
        except Exception as error:  # noqa: BLE001 - report per platform
            results[platform] = {'status': 'error', 'error': str(error)[:300]}
    uploads = clip.setdefault('uploads', {})
    stamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
    for platform, result in results.items():
        uploads[platform] = {**result, 'at': stamp}
    save_json(manifest_path, manifest)
    log_event('publishing', 'clip_published', {
        'batch': folder.name, 'clip_id': clip_id,
        'results': {k: {kk: vv for kk, vv in v.items() if kk in ('status', 'id', 'error')} for k, v in results.items()},
    })
    return results
