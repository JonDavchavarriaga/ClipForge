import csv
import hashlib
import json
import re
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


PLATFORMS = {
    'youtube': 'https://studio.youtube.com/',
    'tiktok': 'https://www.tiktok.com/tiktokstudio/upload',
    'instagram': 'https://business.facebook.com/',
    'facebook': 'https://business.facebook.com/',
}


def save_json(path, data):
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, encoding='utf-8', delete=False) as file:
        temporary = Path(file.name)
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def utc_date(value, *, future=False):
    if not value:
        return None
    try:
        date = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError('Usa una fecha ISO 8601, por ejemplo 2027-01-15T18:00:00-05:00.') from error
    if date.tzinfo is None or (future and date <= datetime.now(timezone.utc)):
        raise ValueError('La fecha requiere zona horaria explícita y debe estar en el futuro al programar.')
    return date.astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def resolve_clip(run_dir, clip):
    path = (run_dir / clip['video']).resolve()
    if not path.is_relative_to(run_dir) or not path.is_file() or path.suffix.lower() != '.mp4':
        raise ValueError(f'No se encontró el MP4 de {clip["id"]}.')
    with path.open('rb') as video:
        digest = hashlib.file_digest(video, 'sha256').hexdigest()
    return path, digest


def approved_clip(run_dir, clip):
    path, digest = resolve_clip(run_dir, clip)
    if digest != clip['approval']['sha256']:
        raise ValueError(f'El video {clip["id"]} cambió después de la aprobación. Apruébalo de nuevo.')
    return path, digest


def safe_cell(value):
    if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')):
        return "'" + value
    return value


def export_queue(run_dir, clips):
    headers = ['clip', 'plataforma', 'video', 'titulo', 'texto', 'fecha_hora_utc', 'abrir', 'flujo']
    rows = []
    for clip in clips:
        video, _ = approved_clip(run_dir, clip)
        approval = clip['approval']
        for platform in approval['platforms']:
            rows.append([clip['id'], platform, video.relative_to(run_dir).as_posix(), approval['title'],
                         approval['caption'], approval['publish_at'], PLATFORMS[platform],
                         'YouTube Studio o comando youtube' if platform == 'youtube' else 'Programador oficial'])
    path = run_dir / 'publicacion.csv'
    with tempfile.NamedTemporaryFile(mode='w', dir=run_dir, encoding='utf-8-sig', newline='', delete=False) as file:
        temporary = Path(file.name)
        try:
            writer = csv.writer(file)
            writer.writerow(headers)
            writer.writerows([safe_cell(value) for value in row] for row in rows)
        except Exception:
            file.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path, len(rows)


def youtube_connect(client_secret, expected_channel):
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as error:
        raise ValueError('Instala requirements.txt para usar YouTube Data API.') from error
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret), [
        'https://www.googleapis.com/auth/youtube.upload', 'https://www.googleapis.com/auth/youtube.readonly',
    ])
    credentials = flow.run_local_server(port=0, authorization_prompt_message=None)
    api = build('youtube', 'v3', credentials=credentials, cache_discovery=False)
    result = api.channels().list(part='id', mine=True).execute()
    if expected_channel not in {channel['id'] for channel in result.get('items', [])}:
        api.close()
        raise ValueError('El canal autorizado no coincide con --channel-id.')
    return api


def upload_youtube(api, ledger, channel, path, digest, approval, publish_at, made_for_kids):
    from googleapiclient.http import MediaFileUpload

    status = {'privacyStatus': 'private', 'selfDeclaredMadeForKids': made_for_kids}
    if publish_at:
        status['publishAt'] = utc_date(publish_at, future=True)
    media = MediaFileUpload(str(path), mimetype='video/mp4', chunksize=8 * 1024 * 1024, resumable=True)
    try:
        with closing(sqlite3.connect(ledger)) as database:
            database.execute('''CREATE TABLE IF NOT EXISTS uploads (
                channel TEXT NOT NULL, sha256 TEXT NOT NULL, video_id TEXT,
                PRIMARY KEY (channel, sha256))''')
            with database:
                new = database.execute('INSERT OR IGNORE INTO uploads VALUES (?, ?, NULL)', (channel, digest))
                if not new.rowcount:
                    existing, = database.execute('SELECT video_id FROM uploads WHERE channel=? AND sha256=?',
                                                 (channel, digest)).fetchone()
                    if existing:
                        return existing, False
                    raise ValueError('Existe un intento pendiente para este archivo. Revisa YouTube Studio antes de volver a intentar.')
            try:
                with database:
                    database.execute('BEGIN IMMEDIATE')
                    pending = database.execute('SELECT video_id FROM uploads WHERE channel=? AND sha256=?',
                                               (channel, digest)).fetchone()
                    if pending != (None,):
                        raise ValueError('El intento cambió de estado antes de iniciar.')
                    request = api.videos().insert(
                        part='snippet,status', notifySubscribers=False, media_body=media,
                        body={'snippet': {'title': approval['title'], 'description': approval['caption'],
                                          'categoryId': '22'}, 'status': status},
                    )
                    response = None
                    while response is None:
                        _, response = request.next_chunk(num_retries=3)
                    video_id = response['id']
                    database.execute('UPDATE uploads SET video_id=? WHERE channel=? AND sha256=?',
                                     (video_id, channel, digest))
            except Exception as error:
                raise ValueError(f'Carga sin confirmar ({type(error).__name__}). Verifica el resultado en Studio.') from error
            return video_id, True
    finally:
        media.stream().close()


def reconcile_youtube(ledger, channel, digest, video_id):
    with closing(sqlite3.connect(ledger)) as database, database:
        if video_id:
            changed = database.execute('UPDATE uploads SET video_id=? WHERE channel=? AND sha256=? AND video_id IS NULL',
                                       (video_id, channel, digest))
        else:
            changed = database.execute('DELETE FROM uploads WHERE channel=? AND sha256=? AND video_id IS NULL',
                                       (channel, digest))
        if not changed.rowcount:
            raise ValueError('No hay una carga pendiente para este archivo y canal.')


def verify_channel_id(value):
    if not re.fullmatch(r'UC[A-Za-z0-9_-]{22}', value):
        raise ValueError('Usa el ID del canal de YouTube que empieza por UC.')
    return value
