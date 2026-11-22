"""Localhost-only HTTP backend for Gevik Clip Desktop Studio."""

from contextlib import redirect_stdout
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import threading
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlparse

from clipforge import __version__
from clipforge.cli import (
    LAYOUTS,
    approve,
    continue_batch,
    process,
    queue,
    read_run,
    rerender,
    retest_preview_clip,
    translate_apply,
    translate_pack,
)
from clipforge.config import (
    DEFAULT_OUTPUTS_DIR,
    DEFAULT_SOURCES_DIR,
    ROOT_DIR,
    load_channel_profiles,
)
from clipforge.desktop.jobs import JobManager
from clipforge.logging_utils import (
    configure_logging,
    ensure_safe_stdio,
    log_event,
    read_recent_errors,
    read_recent_logs,
    record_error,
)
from clipforge.pipeline.auto_translate import available_translators, translate_clip_with_agent
from clipforge.pipeline.editor import edit_clip
from clipforge.pipeline.live import LiveRecorder, parse_stream_embed
from clipforge.pipeline.media import NO_WINDOW_FLAGS, extract_frame_jpg, generate_thumbnail, probe
from clipforge.pipeline.subtitles import CAPTION_STYLES, style_catalog
from clipforge.process_manager import REGISTRY
from clipforge.publishing.publication import PLATFORMS, save_json
from clipforge.storage import resolve_workspace

STATIC_DIR = Path(__file__).resolve().parent / 'static'
AGENT_REVIEW_SCRIPT = ROOT_DIR / '.agents' / 'skills' / 'multi-agent-review' / 'scripts' / 'agent_review.py'
VIDEO_EXTS = {'.mp4', '.mkv', '.mov', '.webm', '.flv', '.ts'}
VALID_REVIEW_STATUSES = {'pending', 'approved', 'discarded', 'published'}


def _safe_download_filename(raw: str) -> str:
    cleaned = re.sub(r'[^A-Za-z0-9._ -]+', '', raw or '').strip()
    cleaned = re.sub(r'\s+', '_', cleaned)[:64] or 'clip'
    return f'GevikClip_{cleaned}.mp4'


class ClipForgeAppState:
    def __init__(self, root_dir: Path | None = None, workspace_dir: Path | None = None):
        ensure_safe_stdio()
        self.root_dir = (root_dir or ROOT_DIR).resolve()
        self.workspace = resolve_workspace(workspace_dir or (self.root_dir / 'workspace'))
        configure_logging(self.workspace.logs_dir)

        self.outputs_dir = self.workspace.rendered_clips_dir
        self.legacy_outputs_dir = (self.root_dir / DEFAULT_OUTPUTS_DIR).resolve()
        self.legacy_sources_dir = (self.root_dir / DEFAULT_SOURCES_DIR).resolve()

        self.token = secrets.token_urlsafe(24)
        self.jobs = JobManager(logs_dir=self.workspace.logs_dir)
        self.recorder = LiveRecorder(
            self.workspace.live_recordings_dir,
            clips_root=self.workspace.live_clips_dir,
            logs_dir=self.workspace.logs_dir,
        )
        self.shutdown_event = threading.Event()
        self.auto_process_config: dict = {
            'enabled': False,
            'layout': 'split-cam',
            'cam_region': 'auto',
            'caption_style': 'highlight',
            'language': 'auto',
            'model': 'base',
            'denoise': True,
        }

    def _scan_videos(self, folders: list[Path], limit: int = 40) -> list[dict]:
        items: list[dict] = []
        seen: set[str] = set()
        candidates: list[tuple[float, int, Path, Path]] = []
        for base in folders:
            if not base.is_dir():
                continue
            base_resolved = base.resolve()
            for dirpath, dirnames, filenames in os.walk(base_resolved):
                dirnames[:] = [d for d in dirnames if d != '_buffer']
                for name in filenames:
                    if Path(name).suffix.lower() not in VIDEO_EXTS or name.startswith('.tmp-'):
                        continue
                    path = (Path(dirpath) / name).resolve()
                    try:
                        st = path.stat()
                    except OSError:
                        continue
                    candidates.append((st.st_mtime, st.st_size, path, base_resolved))
        for _, size_bytes, path, base in sorted(candidates, key=lambda item: item[0], reverse=True):
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            meta = {}
            for sidecar in ('clip.json', 'session.json', 'origen.json'):
                meta_file = path.parent / sidecar
                if meta_file.is_file():
                    try:
                        meta = json.loads(meta_file.read_text(encoding='utf-8'))
                    except (OSError, json.JSONDecodeError):
                        meta = {}
                    break
            items.append({
                'name': path.name,
                'path': key,
                'relative': path.relative_to(base).as_posix(),
                'size_mb': round(size_bytes / (1024 * 1024), 2),
                'mode': meta.get('mode', ''),
                'reason': meta.get('reason', ''),
                'platform': meta.get('platform', ''),
                'channel': meta.get('channel', ''),
            })
            if len(items) >= limit:
                break
        return items

    def list_library(self) -> dict:
        return {
            'paths': self.workspace.to_dict(),
            'live_recordings': self._scan_videos([self.workspace.live_recordings_dir], limit=30),
            'live_clips': self._scan_videos([self.workspace.live_clips_dir, self.legacy_sources_dir], limit=50),
            'sources': self._scan_videos([self.workspace.sources_dir], limit=40),
        }

    def list_sources(self) -> list[dict]:
        return self._scan_videos(
            [
                self.workspace.live_recordings_dir,
                self.workspace.live_clips_dir,
                self.workspace.sources_dir,
                self.legacy_sources_dir,
            ],
            limit=60,
        )

    def _batch_roots(self) -> list[Path]:
        roots = [self.outputs_dir]
        if self.legacy_outputs_dir.is_dir() and self.legacy_outputs_dir != self.outputs_dir:
            roots.append(self.legacy_outputs_dir)
        return roots

    def _fallback_display_name(self, folder: Path, manifest: dict) -> str:
        if manifest.get('display_name'):
            return str(manifest['display_name'])
        src = Path(manifest.get('source') or folder.name)
        if src.name == 'full_recording.mp4' and '-' in src.parent.name:
            parts = src.parent.name.split('-')
            if len(parts) >= 2:
                return f'{parts[0].title()} · {parts[1]}'
        return src.stem[:36] or folder.name

    def list_batches(self) -> list[dict]:
        batches: list[dict] = []
        folders: list[tuple[float, Path]] = []
        for root in self._batch_roots():
            if not root.is_dir():
                continue
            for folder in root.glob('lote-*'):
                if (folder / 'manifest.json').is_file():
                    folders.append((folder.stat().st_mtime, folder.resolve()))
        for _, folder in sorted(folders, key=lambda item: item[0], reverse=True):
            try:
                manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
            except (OSError, json.JSONDecodeError):
                continue
            clips = manifest.get('clips', [])
            enriched = []
            for clip in clips:
                clip_dir = folder / clip['id']
                if not (clip_dir / 'video.mp4').is_file():
                    continue
                approval_path = clip_dir / 'approval.json'
                approval = json.loads(approval_path.read_text(encoding='utf-8')) if approval_path.is_file() else None
                status = clip.get('review_status') or ('approved' if approval else 'pending')
                trans_files = sorted(p.name for p in clip_dir.glob('translation-*.json'))
                srt_files = sorted(p.name for p in clip_dir.glob('captions*.srt'))
                enriched.append({
                    **clip,
                    'display_title': clip.get('display_title') or clip['id'],
                    'review_status': status,
                    'subtitle_position': clip.get('subtitle_position', 'bottom'),
                    'subtitle_offset_ms': int(clip.get('subtitle_offset_ms', 0)),
                    'volume_db': float(clip.get('volume_db', 0.0)),
                    'denoise': bool(clip.get('denoise', True)),
                    'approval': approval,
                    'translation_files': trans_files,
                    'srt_files': srt_files,
                    'has_thumb': (clip_dir / 'thumb.jpg').is_file(),
                })
            batches.append({
                'id': folder.name,
                'display_name': self._fallback_display_name(folder, manifest),
                'path': str(folder),
                'source': manifest.get('source', ''),
                'duration': manifest.get('duration', 0),
                'status': manifest.get('status', 'complete'),
                'planned_total': manifest.get('planned_total', len(enriched)),
                'remaining_count': manifest.get('remaining_count', 0),
                'muted_ranges': manifest.get('muted_ranges', []),
                'has_plan': (folder / 'plan.json').is_file(),
                'has_queue': (folder / 'publicacion.csv').is_file(),
                'clips': enriched,
            })
            if len(batches) >= 35:
                break
        return batches

    def resolve_batch_dir(self, batch_id: str) -> Path:
        if not re.fullmatch(r'lote-[A-Za-z0-9._-]+', batch_id):
            raise ValueError('Identificador de lote inválido.')
        for root in self._batch_roots():
            folder = (root / batch_id).resolve()
            if folder.is_relative_to(root) and (folder / 'manifest.json').is_file():
                return folder
        raise FileNotFoundError('Lote no encontrado.')

    def resolve_clip_dir(self, batch_id: str, clip_id: str) -> tuple[Path, Path]:
        if not re.fullmatch(r'clip-\d{2,3}', clip_id):
            raise ValueError('Identificador de clip inválido.')
        folder = self.resolve_batch_dir(batch_id)
        clip_dir = (folder / clip_id).resolve()
        if not clip_dir.is_relative_to(folder) or not clip_dir.is_dir():
            raise FileNotFoundError('Clip no encontrado.')
        return folder, clip_dir

    def submit_process_job(self, data: dict):
        source_raw = str(data.get('source') or '').strip()
        if not source_raw:
            raise ValueError('Elige un video o pega la ruta del archivo local.')
        source_path = Path(source_raw)
        if not source_path.is_file():
            raise ValueError(f'No se encontró el archivo: {source_path}')
        ranges_raw = str(data.get('ranges') or '').strip()
        ranges = [line.strip() for line in ranges_raw.splitlines() if line.strip()] if ranges_raw else []
        raw_count = data.get('count', 0)
        count = 0 if str(raw_count).strip().lower() in {'', 'auto', '0', 'none'} else int(raw_count)
        preview_first = bool(data.get('preview_first', True))

        args = SimpleNamespace(
            source=source_path,
            output=self.outputs_dir,
            count=count,
            min_seconds=int(data.get('min_seconds') or 20),
            max_seconds=int(data.get('max_seconds') or 55),
            range=ranges,
            layout=str(data.get('layout') or 'split-cam'),
            cam_region=str(data.get('cam_region') or 'auto'),
            caption_style=str(data.get('caption_style') or 'highlight'),
            preview_first=preview_first,
            srt=Path(data['srt']) if data.get('srt') else None,
            skip_asr=bool(data.get('skip_asr', False)),
            model=str(data.get('model') or 'base'),
            language=str(data.get('language') or 'auto'),
            denoise=bool(data.get('denoise', True)),
            audio_track=int(data['audio_track']) if data.get('audio_track') not in (None, '') else None,
            rights=str(data.get('rights') or 'local'),
            rights_note=str(data.get('rights_note') or ''),
        )

        def run_process(progress, cancel_event):
            folder = process(args, progress_cb=progress, cancel_event=cancel_event)
            manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
            return {
                'batch_path': str(folder),
                'batch_id': folder.name,
                'display_name': manifest.get('display_name', folder.name),
                'status': manifest.get('status', 'complete'),
                'planned_total': manifest.get('planned_total', len(manifest.get('clips', []))),
                'remaining_count': manifest.get('remaining_count', 0),
            }

        mode_tag = '1er clip prueba' if preview_first else ('Auto clips' if count == 0 else f'{count} clips')
        return self.jobs.submit('process', f'{source_path.name} ({mode_tag})', run_process)


def make_handler(state: ClipForgeAppState):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, format, *args):  # noqa: A002
            return

        def _security_headers(self):
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('X-Frame-Options', 'SAMEORIGIN')

        def _send_json(self, payload: dict, status: int = HTTPStatus.OK):
            body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self._security_headers()
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def _send_bytes(self, content: bytes, content_type: str, status: int = HTTPStatus.OK, *, extra_headers: dict | None = None):
            self.send_response(status)
            self._security_headers()
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Cache-Control', 'no-store')
            for k, v in (extra_headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(content)

        def _read_json_body(self) -> dict:
            length = int(self.headers.get('Content-Length') or 0)
            if length > 2_000_000:
                raise ValueError('Cuerpo demasiado grande.')
            raw = self.rfile.read(length) if length else b'{}'
            try:
                data = json.loads(raw.decode('utf-8'))
            except json.JSONDecodeError as error:
                raise ValueError('JSON inválido en la petición.') from error
            if not isinstance(data, dict):
                raise ValueError('El cuerpo JSON debe ser un objeto.')
            return data

        def _require_token(self) -> bool:
            if self.headers.get('X-ClipForge-Token') != state.token:
                self._send_json({'error': 'Token de sesión inválido o ausente.'}, HTTPStatus.FORBIDDEN)
                return False
            return True

        def _serve_video_file(self, path: Path, *, download_name: str | None = None):
            if not path.is_file():
                self._send_json({'error': 'Archivo de video no encontrado.'}, HTTPStatus.NOT_FOUND)
                return
            file_size = path.stat().st_size
            range_header = None if download_name else self.headers.get('Range')
            start, end = 0, file_size - 1
            status = HTTPStatus.OK
            if range_header and range_header.startswith('bytes='):
                match = re.match(r'bytes=(\d*)-(\d*)', range_header)
                if match:
                    s_raw, e_raw = match.groups()
                    if s_raw:
                        start = int(s_raw)
                    if e_raw:
                        end = min(file_size - 1, int(e_raw))
                    if start >= file_size or start > end:
                        self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                        self.send_header('Content-Range', f'bytes */{file_size}')
                        self.send_header('Content-Length', '0')
                        self.end_headers()
                        return
                    status = HTTPStatus.PARTIAL_CONTENT

            length = end - start + 1
            mime = mimetypes.guess_type(path.name)[0] or 'video/mp4'
            try:
                self.send_response(status)
                self._security_headers()
                self.send_header('Content-Type', mime)
                self.send_header('Accept-Ranges', 'bytes')
                self.send_header('Content-Length', str(length))
                self.send_header('Cache-Control', 'no-store')
                if download_name:
                    self.send_header('Content-Disposition', f'attachment; filename="{download_name}"')
                if status == HTTPStatus.PARTIAL_CONTENT:
                    self.send_header('Content-Range', f'bytes {start}-{end}/{file_size}')
                self.end_headers()
                with path.open('rb') as handle:
                    handle.seek(start)
                    remaining = length
                    while remaining > 0:
                        chunk = handle.read(min(65536, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                return

        def do_GET(self):  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path
            try:
                if path == '/api/ping':
                    self._send_json({'ok': True, 'app': 'Gevik Clip', 'version': __version__})
                    return
                if path in {'/', '/index.html'}:
                    html = (STATIC_DIR / 'index.html').read_text(encoding='utf-8')
                    html = html.replace('__CLIPFORGE_TOKEN__', state.token)
                    self._send_bytes(html.encode('utf-8'), 'text/html; charset=utf-8')
                    return
                if path == '/static/styles.css':
                    self._send_bytes((STATIC_DIR / 'styles.css').read_bytes(), 'text/css; charset=utf-8')
                    return
                if path == '/static/app.js':
                    self._send_bytes((STATIC_DIR / 'app.js').read_bytes(), 'application/javascript; charset=utf-8')
                    return
                if path == '/static/logo.svg':
                    svg = state.root_dir / 'assets' / 'icons' / 'gevik-clip.svg'
                    if not svg.is_file():
                        svg = state.root_dir / 'assets' / 'icons' / 'clipforge.svg'
                    if svg.is_file():
                        self._send_bytes(svg.read_bytes(), 'image/svg+xml')
                        return
                if path == '/api/status':
                    translators = available_translators()
                    self._send_json({
                        'app_name': 'Gevik Clip',
                        'version': __version__,
                        'root_dir': str(state.root_dir),
                        'workspace': state.workspace.to_dict(),
                        'layouts': list(LAYOUTS),
                        'caption_styles': list(CAPTION_STYLES),
                        'style_catalog': style_catalog(),
                        'platforms': list(PLATFORMS.keys()),
                        'agents': {k: v['available'] for k, v in translators.items()},
                        'translators': translators,
                        'channels': load_channel_profiles().get('channels', {}),
                        'live': state.recorder.status(),
                    })
                    return
                if path == '/api/library':
                    self._send_json(state.list_library())
                    return
                if path == '/api/logs':
                    self._send_json({
                        'paths': state.workspace.to_dict(),
                        'errors': read_recent_errors(state.workspace.logs_dir, limit=40),
                        'logs': read_recent_logs(state.workspace.logs_dir, limit=120),
                    })
                    return
                if path == '/api/sources':
                    self._send_json({'sources': state.list_sources()})
                    return
                if path == '/api/batches':
                    self._send_json({'batches': state.list_batches()})
                    return
                if path == '/api/jobs':
                    self._send_json(state.jobs.snapshot())
                    return
                if path.startswith('/api/jobs/'):
                    job_id = path.split('/')[-1]
                    job = state.jobs.get(job_id)
                    if not job:
                        self._send_json({'error': 'Trabajo no encontrado.'}, HTTPStatus.NOT_FOUND)
                        return
                    self._send_json({'job': job})
                    return
                if path == '/api/live/status':
                    self._send_json({'live': state.recorder.status()})
                    return
                if path == '/api/media':
                    qs = parse_qs(parsed.query)
                    batch_id = unquote((qs.get('batch') or [''])[0])
                    clip_id = unquote((qs.get('clip') or [''])[0])
                    _, clip_dir = state.resolve_clip_dir(batch_id, clip_id)
                    self._serve_video_file(clip_dir / 'video.mp4')
                    return
                if path == '/api/download':
                    qs = parse_qs(parsed.query)
                    batch_id = unquote((qs.get('batch') or [''])[0])
                    clip_id = unquote((qs.get('clip') or [''])[0])
                    folder, clip_dir = state.resolve_clip_dir(batch_id, clip_id)
                    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
                    clip = next((c for c in manifest.get('clips', []) if c['id'] == clip_id), None)
                    label = (clip.get('display_title') if clip else '') or f'{batch_id}_{clip_id}'
                    self._serve_video_file(clip_dir / 'video.mp4', download_name=_safe_download_filename(label))
                    return
                if path == '/api/thumb':
                    qs = parse_qs(parsed.query)
                    batch_id = unquote((qs.get('batch') or [''])[0])
                    clip_id = unquote((qs.get('clip') or [''])[0])
                    _, clip_dir = state.resolve_clip_dir(batch_id, clip_id)
                    thumb = clip_dir / 'thumb.jpg'
                    if not thumb.is_file() and (clip_dir / 'video.mp4').is_file():
                        generate_thumbnail(clip_dir / 'video.mp4', thumb)
                    if thumb.is_file():
                        self._send_bytes(thumb.read_bytes(), 'image/jpeg')
                        return
                    self._send_json({'error': 'Miniatura no disponible.'}, HTTPStatus.NOT_FOUND)
                    return
                if path == '/api/frame':
                    qs = parse_qs(parsed.query)
                    batch_id = unquote((qs.get('batch') or [''])[0]).strip()
                    clip_id = unquote((qs.get('clip') or [''])[0]).strip()
                    source_raw = unquote((qs.get('source') or [''])[0]).strip()
                    offset = float((qs.get('t') or ['0'])[0] or 0)
                    if batch_id:
                        folder = state.resolve_batch_dir(batch_id)
                        manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
                        source_path = Path(manifest['source']).resolve(strict=True)
                        clip_obj = next((c for c in manifest.get('clips', []) if c['id'] == clip_id), None) if clip_id else None
                        base_ts = float(clip_obj['start']) if clip_obj else (float(manifest['clips'][0]['start']) if manifest.get('clips') else 0.0)
                        info = probe(source_path)
                        ts = max(0.0, min(max(0.0, info.duration - 0.5), base_ts + offset))
                    elif source_raw:
                        source_path = Path(source_raw).resolve(strict=True)
                        if source_path.suffix.lower() not in VIDEO_EXTS:
                            raise ValueError('Archivo de video no soportado para fotograma.')
                        info = probe(source_path)
                        ts = max(0.0, min(max(0.0, info.duration - 0.5), offset if offset > 0 else min(5.0, info.duration * 0.25)))
                    else:
                        raise ValueError('Indica batch o source para capturar el fotograma.')
                    frame_cache = state.workspace.root / '_frame_preview.jpg'
                    extract_frame_jpg(source_path, ts, frame_cache)
                    self._send_bytes(frame_cache.read_bytes(), 'image/jpeg')
                    return
                if path == '/api/translation':
                    qs = parse_qs(parsed.query)
                    batch_id = unquote((qs.get('batch') or [''])[0])
                    clip_id = unquote((qs.get('clip') or [''])[0])
                    target_lang = (qs.get('target_lang') or ['es'])[0].strip().lower()
                    _, clip_dir = state.resolve_clip_dir(batch_id, clip_id)
                    bundle_path = clip_dir / f'translation-{target_lang}.json'
                    if bundle_path.is_file():
                        bundle = json.loads(bundle_path.read_text(encoding='utf-8'))
                        self._send_json({'exists': True, 'path': str(bundle_path), 'bundle': bundle})
                        return
                    srt_path = clip_dir / 'captions.srt'
                    self._send_json({'exists': False, 'has_srt': srt_path.is_file(), 'bundle': None})
                    return
                self._send_json({'error': 'Ruta no encontrada.'}, HTTPStatus.NOT_FOUND)
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                return
            except FileNotFoundError as exc:
                self._send_json({'error': str(exc)}, HTTPStatus.NOT_FOUND)
            except ValueError as exc:
                record_error(f'api.GET.{path}', exc, logs_dir=state.workspace.logs_dir)
                self._send_json({'error': str(exc)}, HTTPStatus.BAD_REQUEST)
            except Exception as exc:  # pragma: no cover
                record_error(f'api.GET.{path}', exc, logs_dir=state.workspace.logs_dir)
                self._send_json({'error': f'Error interno: {exc}'}, HTTPStatus.INTERNAL_SERVER_ERROR)

        def do_POST(self):  # noqa: N802
            if not self._require_token():
                return
            path = urlparse(self.path).path
            try:
                data = self._read_json_body()
                if path == '/api/jobs/cancel':
                    res = state.jobs.cancel(str(data.get('job_id') or '') or None)
                    self._send_json(res)
                    return
                if path == '/api/live/embed':
                    host_header = (self.headers.get('Host') or '127.0.0.1').split(':')[0]
                    embed = parse_stream_embed(str(data.get('url') or ''), host_parent=host_header)
                    self._send_json({'embed': embed})
                    return
                if path == '/api/live/start':
                    auto_process = bool(data.get('auto_process', False))
                    state.auto_process_config = {
                        'enabled': auto_process,
                        'layout': str(data.get('layout') or 'split-cam'),
                        'cam_region': str(data.get('cam_region') or 'auto'),
                        'caption_style': str(data.get('caption_style') or 'highlight'),
                        'language': str(data.get('language') or 'auto'),
                        'model': str(data.get('model') or 'base'),
                        'denoise': bool(data.get('denoise', True)),
                    }

                    def on_auto_clip(record):
                        if state.auto_process_config.get('enabled'):
                            state.submit_process_job({
                                'source': record.path,
                                'rights': 'local',
                                'layout': state.auto_process_config['layout'],
                                'cam_region': state.auto_process_config['cam_region'],
                                'caption_style': state.auto_process_config['caption_style'],
                                'language': state.auto_process_config['language'],
                                'model': state.auto_process_config['model'],
                                'count': 1,
                                'preview_first': False,
                                'min_seconds': max(10, min(record.window_seconds, 20)),
                                'max_seconds': max(record.window_seconds, 60),
                                'denoise': state.auto_process_config['denoise'],
                            })

                    status = state.recorder.start(
                        str(data.get('url') or ''),
                        rights_kind=str(data.get('rights') or 'local'),
                        rights_note=str(data.get('rights_note') or ''),
                        quality=str(data.get('quality') or 'best'),
                        save_full_recording=bool(data.get('save_full_recording', True)),
                        auto_clip=bool(data.get('auto_clip', True)),
                        auto_clip_seconds=int(data.get('auto_clip_seconds') or 45),
                        auto_sensitivity=str(data.get('auto_sensitivity') or 'medium'),
                        on_auto_clip=on_auto_clip,
                    )
                    self._send_json({'live': status})
                    return
                if path == '/api/live/autoclip':
                    status = state.recorder.set_auto_clip(
                        bool(data.get('auto_clip', False)),
                        seconds=int(data['auto_clip_seconds']) if data.get('auto_clip_seconds') else None,
                        sensitivity=str(data['auto_sensitivity']) if data.get('auto_sensitivity') else None,
                    )
                    self._send_json({'live': status})
                    return
                if path == '/api/live/stop':
                    status = state.recorder.stop()
                    self._send_json({'live': status})
                    return
                if path == '/api/live/clip':
                    window = int(data.get('window_seconds') or 45)
                    record = state.recorder.save_clip(window, mode='manual', reason=f'Clip manual ({window}s)')
                    auto_process = bool(data.get('auto_process', False))
                    job_dict = None
                    if auto_process:
                        job = state.submit_process_job({
                            'source': record.path,
                            'rights': 'local',
                            'layout': data.get('layout') or state.auto_process_config['layout'],
                            'cam_region': data.get('cam_region') or state.auto_process_config['cam_region'],
                            'caption_style': data.get('caption_style') or state.auto_process_config['caption_style'],
                            'language': data.get('language') or state.auto_process_config['language'],
                            'model': data.get('model') or state.auto_process_config['model'],
                            'count': 1,
                            'preview_first': False,
                            'min_seconds': max(10, min(window, 20)),
                            'max_seconds': max(window, 60),
                            'denoise': bool(data.get('denoise', True)),
                        })
                        job_dict = job.to_dict()
                    self._send_json({'clip': vars(record), 'live': state.recorder.status(), 'job': job_dict})
                    return
                if path == '/api/open-folder':
                    key = str(data.get('folder') or 'root')
                    mapping = {
                        'root': state.workspace.root,
                        'logs': state.workspace.logs_dir,
                        'live_recordings': state.workspace.live_recordings_dir,
                        'live_clips': state.workspace.live_clips_dir,
                        'rendered_clips': state.workspace.rendered_clips_dir,
                        'sources': state.workspace.sources_dir,
                    }
                    target = mapping.get(key)
                    if not target:
                        raise ValueError('Carpeta no reconocida.')
                    target.mkdir(parents=True, exist_ok=True)
                    if sys.platform == 'win32':
                        os.startfile(str(target))
                    self._send_json({'opened': str(target)})
                    return
                if path == '/api/clip/reveal':
                    batch_id = str(data.get('batch') or '')
                    clip_id = str(data.get('clip') or '')
                    _, clip_dir = state.resolve_clip_dir(batch_id, clip_id)
                    video = clip_dir / 'video.mp4'
                    if sys.platform == 'win32' and video.is_file():
                        subprocess.Popen(['explorer', '/select,', str(video)])
                    self._send_json({'revealed': str(video)})
                    return
                if path == '/api/clip/status':
                    batch_id = str(data.get('batch') or '')
                    clip_id = str(data.get('clip') or '')
                    new_status = str(data.get('status') or '').strip().lower()
                    new_title = data.get('display_title')
                    if new_status and new_status not in VALID_REVIEW_STATUSES:
                        raise ValueError(f'Estado inválido: {new_status}')
                    folder, _ = state.resolve_clip_dir(batch_id, clip_id)
                    manifest_path = folder / 'manifest.json'
                    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
                    clip = next((c for c in manifest.get('clips', []) if c['id'] == clip_id), None)
                    if not clip:
                        raise FileNotFoundError('Clip no encontrado.')
                    if new_status:
                        clip['review_status'] = new_status
                    if new_title is not None and str(new_title).strip():
                        clip['display_title'] = str(new_title).strip()[:80]
                    save_json(manifest_path, manifest)
                    self._send_json({'clip': clip})
                    return
                if path == '/api/clip/purge-discarded':
                    batch_id = str(data.get('batch') or '')
                    purged = 0
                    targets = [state.resolve_batch_dir(batch_id)] if batch_id else [
                        f for r in state._batch_roots() if r.is_dir() for f in r.glob('lote-*') if (f / 'manifest.json').is_file()
                    ]
                    for folder in targets:
                        manifest_path = folder / 'manifest.json'
                        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
                        kept = []
                        for clip in manifest.get('clips', []):
                            if clip.get('review_status') == 'discarded':
                                shutil.rmtree(folder / clip['id'], ignore_errors=True)
                                purged += 1
                            else:
                                kept.append(clip)
                        if len(kept) != len(manifest.get('clips', [])):
                            manifest['clips'] = kept
                            save_json(manifest_path, manifest)
                    log_event('manager', 'purged_discarded', {'count': purged}, logs_dir=state.workspace.logs_dir)
                    self._send_json({'purged': purged})
                    return
                if path == '/api/clip/auto-translate':
                    batch_id = str(data.get('batch') or '')
                    clip_id = str(data.get('clip') or '')
                    source_lang = str(data.get('source_lang') or 'en').strip().lower()
                    target_lang = str(data.get('target_lang') or 'es').strip().lower()
                    engine = str(data.get('engine') or 'claude').strip().lower()
                    caption_style = str(data.get('caption_style') or 'highlight').strip().lower()
                    context = str(data.get('context') or '').strip()
                    folder, clip_dir = state.resolve_clip_dir(batch_id, clip_id)
                    srt_path = clip_dir / 'captions.srt'
                    if not srt_path.is_file():
                        raise ValueError('Este clip no tiene captions.srt para traducir.')

                    def run_auto_translate(progress, cancel_event):
                        progress(f'Traduciendo con {engine}', 25, f'Traduciendo clip completo ({source_lang.upper()} → {target_lang.upper()})...')
                        _, out_srt, warnings, _ = translate_clip_with_agent(
                            srt_path,
                            source_lang,
                            target_lang,
                            engine=engine,
                            context=context,
                            cancel_event=cancel_event,
                        )
                        progress('Aplicando subtítulos', 65, f'Renderizando {clip_id} en {target_lang.upper()} ({caption_style})...')
                        edit_clip(
                            folder,
                            clip_id,
                            caption_style=caption_style,
                            srt_path=out_srt,
                            cancel_event=cancel_event,
                        )
                        progress('Completado', 100, f'Traducción natural ({engine}) aplicada a {clip_id}')
                        return {
                            'batch_id': batch_id,
                            'clip_id': clip_id,
                            'target_lang': target_lang,
                            'engine': engine,
                            'warnings': warnings,
                        }

                    job = state.jobs.submit(
                        'auto-translate',
                        f'Traducir {clip_id} ({source_lang.upper()}→{target_lang.upper()} · {engine})',
                        run_auto_translate,
                    )
                    self._send_json({'job': job.to_dict()}, HTTPStatus.ACCEPTED)
                    return
                if path == '/api/clip/edit':
                    batch_id = str(data.get('batch') or '')
                    clip_id = str(data.get('clip') or '')
                    folder, _ = state.resolve_clip_dir(batch_id, clip_id)

                    def run_edit(progress, cancel_event):
                        updated = edit_clip(
                            folder,
                            clip_id,
                            start=float(data['start']) if data.get('start') not in (None, '') else None,
                            end=float(data['end']) if data.get('end') not in (None, '') else None,
                            layout=str(data['layout']) if data.get('layout') else None,
                            cam_region_spec=str(data['cam_region']) if data.get('cam_region') else None,
                            caption_style=str(data['caption_style']) if data.get('caption_style') else None,
                            subtitle_position=str(data['subtitle_position']) if data.get('subtitle_position') else None,
                            subtitle_offset_ms=int(data['subtitle_offset_ms']) if data.get('subtitle_offset_ms') not in (None, '') else None,
                            denoise=bool(data['denoise']) if data.get('denoise') is not None else None,
                            volume_db=float(data['volume_db']) if data.get('volume_db') not in (None, '') else None,
                            progress_cb=progress,
                            cancel_event=cancel_event,
                        )
                        return {'batch_id': batch_id, 'clip_id': clip_id, 'clip': updated}

                    job = state.jobs.submit('edit', f'Editar {clip_id}', run_edit)
                    self._send_json({'job': job.to_dict()}, HTTPStatus.ACCEPTED)
                    return
                if path == '/api/process':
                    job = state.submit_process_job(data)
                    self._send_json({'job': job.to_dict()}, HTTPStatus.ACCEPTED)
                    return
                if path == '/api/batch/retest-preview':
                    batch_id = str(data.get('batch') or '')
                    folder = state.resolve_batch_dir(batch_id)
                    layout = str(data.get('layout') or '') or None
                    cam_region = str(data.get('cam_region') or '') or None
                    caption_style = str(data.get('caption_style') or '') or None

                    def run_retest(progress, cancel_event):
                        manifest = retest_preview_clip(
                            folder,
                            layout=layout,
                            cam_region_spec=cam_region,
                            caption_style=caption_style,
                            progress_cb=progress,
                            cancel_event=cancel_event,
                        )
                        return {'batch_id': batch_id, 'status': manifest.get('status')}

                    job = state.jobs.submit('retest-preview', f'Probar cámara {batch_id}/clip-01', run_retest)
                    self._send_json({'job': job.to_dict()}, HTTPStatus.ACCEPTED)
                    return
                if path == '/api/batch/continue':
                    batch_id = str(data.get('batch') or '')
                    folder = state.resolve_batch_dir(batch_id)
                    layout = str(data.get('layout') or '') or None
                    cam_region = str(data.get('cam_region') or '') or None
                    caption_style = str(data.get('caption_style') or '') or None

                    def run_continue(progress, cancel_event):
                        manifest = continue_batch(
                            folder,
                            layout=layout,
                            cam_region_spec=cam_region,
                            caption_style=caption_style,
                            progress_cb=progress,
                            cancel_event=cancel_event,
                        )
                        return {'batch_id': batch_id, 'clips_count': len(manifest.get('clips', []))}

                    job = state.jobs.submit('continue-batch', f'Generar resto de {batch_id}', run_continue)
                    self._send_json({'job': job.to_dict()}, HTTPStatus.ACCEPTED)
                    return
                if path == '/api/translation/pack':
                    batch_id = str(data.get('batch') or '')
                    clip_id = str(data.get('clip') or '')
                    source_lang = str(data.get('source_lang') or 'en')
                    target_lang = str(data.get('target_lang') or 'es')
                    channel = str(data.get('channel') or 'es_main')
                    context = str(data.get('context') or '')
                    _, clip_dir = state.resolve_clip_dir(batch_id, clip_id)
                    srt_path = clip_dir / 'captions.srt'
                    if not srt_path.is_file():
                        raise ValueError('Este clip no tiene captions.srt para traducir.')
                    out_path = clip_dir / f'translation-{target_lang}.json'
                    translate_pack(SimpleNamespace(
                        srt=srt_path,
                        source_lang=source_lang,
                        target_lang=target_lang,
                        channel=channel,
                        context=context,
                        output=out_path,
                    ))
                    bundle = json.loads(out_path.read_text(encoding='utf-8'))
                    self._send_json({'path': str(out_path), 'bundle': bundle})
                    return
                if path == '/api/translation/save':
                    batch_id = str(data.get('batch') or '')
                    clip_id = str(data.get('clip') or '')
                    bundle = data.get('bundle')
                    if not isinstance(bundle, dict):
                        raise ValueError('Envía el objeto "bundle".')
                    target_lang = str(bundle.get('target_lang') or 'es').strip().lower()
                    _, clip_dir = state.resolve_clip_dir(batch_id, clip_id)
                    out_path = clip_dir / f'translation-{target_lang}.json'
                    save_json(out_path, bundle)
                    self._send_json({'saved': True, 'path': str(out_path)})
                    return
                if path == '/api/translation/apply':
                    batch_id = str(data.get('batch') or '')
                    clip_id = str(data.get('clip') or '')
                    bundle = data.get('bundle')
                    ass_style = str(data.get('ass_style') or 'highlight')
                    layout = str(data.get('layout') or 'split-cam')
                    do_rerender = bool(data.get('rerender', True))
                    if not isinstance(bundle, dict):
                        raise ValueError('Envía el objeto "bundle".')
                    folder, clip_dir = state.resolve_clip_dir(batch_id, clip_id)
                    target_lang = str(bundle.get('target_lang') or 'es').strip().lower()
                    srt_path = clip_dir / 'captions.srt'
                    bundle_path = clip_dir / f'translation-{target_lang}.json'
                    save_json(bundle_path, bundle)
                    out_srt = clip_dir / f'captions.{target_lang}.srt'

                    buf = io.StringIO()
                    with redirect_stdout(buf):
                        translate_apply(SimpleNamespace(
                            srt=srt_path,
                            bundle=bundle_path,
                            output=out_srt,
                            ass_style=ass_style if ass_style not in {'srt', 'none'} else None,
                            layout=layout,
                        ))
                    warnings = [line for line in buf.getvalue().splitlines() if line.startswith('Aviso:')]

                    job_dict = None
                    if do_rerender:
                        def run_rerender(progress, cancel_event):
                            edit_clip(
                                folder,
                                clip_id,
                                caption_style=ass_style,
                                srt_path=out_srt,
                                progress_cb=progress,
                                cancel_event=cancel_event,
                            )
                            return {'batch_id': batch_id, 'clip_id': clip_id}

                        job = state.jobs.submit('rerender', f'Aplicar subtítulos {clip_id}', run_rerender)
                        job_dict = job.to_dict()
                    self._send_json({'srt': str(out_srt), 'warnings': warnings, 'job': job_dict})
                    return
                if path == '/api/agent-review':
                    agent = str(data.get('agent') or 'claude')
                    if agent not in {'claude', 'codex', 'both'}:
                        raise ValueError('Agente inválido. Usa claude, codex o both.')
                    batch_id = str(data.get('batch') or '')
                    clip_id = str(data.get('clip') or '')
                    target_lang = str(data.get('target_lang') or 'es').strip().lower()
                    task = str(data.get('task') or 'Revisa naturalidad EN↔ES, jerga gamer y límite de 36 caracteres.')
                    _, clip_dir = state.resolve_clip_dir(batch_id, clip_id)
                    target_file = clip_dir / f'translation-{target_lang}.json'
                    if not target_file.is_file():
                        target_file = clip_dir / 'captions.srt'
                    if not target_file.is_file():
                        raise ValueError('No hay archivo de subtítulos ni paquete de traducción para revisar.')

                    def run_review(progress, cancel_event):
                        progress(f'Consultando {agent}', 30, f'Enviando {target_file.name} a {agent}...')
                        completed = REGISTRY.run_managed(
                            [
                                sys.executable,
                                str(AGENT_REVIEW_SCRIPT),
                                '--agent', agent,
                                '--role', 'qa-reviewer',
                                '--input', str(target_file),
                                '--task', task,
                                '--with-glossary',
                            ],
                            cwd=state.root_dir,
                            timeout=240,
                            cancel_event=cancel_event,
                        )
                        if completed.returncode != 0:
                            raise RuntimeError((completed.stderr or '').strip() or f'{agent} devolvió código {completed.returncode}')
                        handoff_paths = re.findall(r'guardado en (.+)$', completed.stdout or '', flags=re.M)
                        reports = []
                        for hp in handoff_paths:
                            p = Path(hp.strip())
                            if p.is_file():
                                reports.append({'path': str(p), 'content': p.read_text(encoding='utf-8')})
                        progress('Completado', 100, f'Revisión de {agent} lista')
                        return {'reports': reports}

                    job = state.jobs.submit('agent-review', f'Revisión {agent} · {clip_id}', run_review)
                    self._send_json({'job': job.to_dict()}, HTTPStatus.ACCEPTED)
                    return
                if path == '/api/approve':
                    batch_id = str(data.get('batch') or '')
                    clip_id = str(data.get('clip') or '')
                    folder = state.resolve_batch_dir(batch_id)
                    platforms = data.get('platforms') or ['youtube', 'tiktok']
                    if not isinstance(platforms, list) or not platforms:
                        raise ValueError('Selecciona al menos una plataforma.')
                    approve(SimpleNamespace(
                        run=folder,
                        clip=clip_id,
                        title=str(data.get('title') or ''),
                        caption=str(data.get('caption') or ''),
                        platform=[str(p) for p in platforms],
                        publish_at=str(data.get('publish_at') or '').strip() or None,
                    ))
                    manifest_path = folder / 'manifest.json'
                    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
                    for c in manifest.get('clips', []):
                        if c['id'] == clip_id:
                            c['review_status'] = 'approved'
                            if data.get('title'):
                                c['display_title'] = str(data['title']).strip()[:80]
                    save_json(manifest_path, manifest)
                    _, clips = read_run(folder)
                    clip = next(item for item in clips if item['id'] == clip_id)
                    self._send_json({'approval': clip.get('approval')})
                    return
                if path == '/api/queue':
                    batch_id = str(data.get('batch') or '')
                    folder = state.resolve_batch_dir(batch_id)
                    buf = io.StringIO()
                    with redirect_stdout(buf):
                        queue(SimpleNamespace(run=folder))
                    self._send_json({'message': buf.getvalue().strip(), 'csv': str(folder / 'publicacion.csv')})
                    return
                if path == '/api/shutdown':
                    log_event('server', 'shutdown_requested', {}, logs_dir=state.workspace.logs_dir)
                    state.jobs.cancel_all()
                    state.recorder.stop()
                    state.shutdown_event.set()
                    self._send_json({'ok': True})
                    return
                self._send_json({'error': 'Endpoint no encontrado.'}, HTTPStatus.NOT_FOUND)
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                return
            except FileNotFoundError as exc:
                self._send_json({'error': str(exc)}, HTTPStatus.NOT_FOUND)
            except ValueError as exc:
                record_error(f'api.POST.{path}', exc, logs_dir=state.workspace.logs_dir)
                self._send_json({'error': str(exc)}, HTTPStatus.BAD_REQUEST)
            except Exception as exc:  # pragma: no cover
                record_error(f'api.POST.{path}', exc, logs_dir=state.workspace.logs_dir)
                self._send_json({'error': f'Error: {exc}'}, HTTPStatus.INTERNAL_SERVER_ERROR)

    return Handler


def create_server(
    host: str = '127.0.0.1',
    port: int = 8765,
    root_dir: Path | None = None,
    workspace_dir: Path | None = None,
):
    state = ClipForgeAppState(root_dir=root_dir, workspace_dir=workspace_dir)
    server = ThreadingHTTPServer((host, port), make_handler(state))
    return server, state
