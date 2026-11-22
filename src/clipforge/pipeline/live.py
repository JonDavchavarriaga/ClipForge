"""Live stream buffer, full long-form recording (1h-2h+), embed parser & automatic reaction clipper.

Uses yt-dlp to resolve live streams (Twitch, Kick, YouTube Live) and FFmpeg `-c copy`
MPEG-TS segmentation for near-zero CPU overhead. Supports:
  - Embedded live stream player URLs so the user watches directly inside ClipForge Studio
  - Full live recording assembly (`full_recording.mp4`) saved to `workspace/live_recordings/`
  - Manual instant clips AND automatic reaction/audio-spike clips saved to `workspace/live_clips/`
  - Persistent error logging to `workspace/logs/errors.jsonl`
"""

from array import array
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Callable
from urllib.parse import parse_qs, urlparse

from clipforge.logging_utils import log_event, log_info, record_error
from clipforge.pipeline.media import NO_WINDOW_FLAGS, ffmpeg, run as run_ffmpeg
from clipforge.process_manager import BACKGROUND_FLAGS, REGISTRY
from clipforge.publishing.publication import save_json

SENSITIVITY_DB = {
    'low': 5.5,
    'medium': 3.8,
    'high': 2.4,
}
MUTED_AD_DB = -52.0


def detect_platform(url: str) -> str:
    host = (urlparse(url).hostname or '').lower()
    if 'twitch.tv' in host:
        return 'twitch'
    if 'kick.com' in host:
        return 'kick'
    if 'youtube.com' in host or 'youtu.be' in host:
        return 'youtube'
    return 'stream'


def validate_stream_url(url: str) -> str:
    cleaned = url.strip()
    parsed = urlparse(cleaned)
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        raise ValueError('Indica una URL válida HTTP/HTTPS del stream en vivo (ej. https://twitch.tv/canal).')
    return cleaned


def parse_stream_embed(url: str, host_parent: str = '127.0.0.1') -> dict:
    """Return platform, channel slug, and iframe embed URL to watch the stream inside the app."""
    cleaned = validate_stream_url(url)
    parsed = urlparse(cleaned)
    platform = detect_platform(cleaned)
    parts = [part for part in parsed.path.split('/') if part]

    if platform == 'twitch' and parts:
        channel = re.sub(r'[^A-Za-z0-9_]', '', parts[0])
        embed_url = (
            f'https://player.twitch.tv/?channel={channel}'
            f'&parent={host_parent}&parent=localhost&muted=false'
        )
        return {'platform': 'twitch', 'channel': channel, 'embed_url': embed_url, 'url': cleaned}

    if platform == 'kick' and parts:
        channel = re.sub(r'[^A-Za-z0-9_-]', '', parts[0])
        return {
            'platform': 'kick',
            'channel': channel,
            'embed_url': f'https://player.kick.com/{channel}',
            'url': cleaned,
        }

    if platform == 'youtube':
        video_id = ''
        if 'youtu.be' in (parsed.hostname or '').lower() and parts:
            video_id = parts[0]
        elif parts and parts[0] == 'live' and len(parts) > 1:
            video_id = parts[1]
        else:
            video_id = (parse_qs(parsed.query).get('v') or [''])[0]
        video_id = re.sub(r'[^A-Za-z0-9_-]', '', video_id)
        if video_id:
            return {
                'platform': 'youtube',
                'channel': video_id,
                'embed_url': f'https://www.youtube.com/embed/{video_id}?autoplay=1',
                'url': cleaned,
            }

    return {'platform': platform, 'channel': parts[0] if parts else 'live', 'embed_url': '', 'url': cleaned}


def resolve_live_stream(url: str, quality: str = 'best') -> tuple[str, dict]:
    """Resolve a live page URL into a direct media stream URL via yt-dlp."""
    embed = parse_stream_embed(url)
    cleaned = embed['url']
    fmt = 'b[ext=mp4]/b' if quality == 'best' else f'b[height<={quality}]/b'
    result = subprocess.run(
        [
            sys.executable,
            '-m',
            'yt_dlp',
            '--ignore-config',
            '--no-playlist',
            '-g',
            '-f',
            fmt,
            '--',
            cleaned,
        ],
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        timeout=45,
        creationflags=NO_WINDOW_FLAGS,
        check=False,
    )
    if result.returncode != 0:
        err = (result.stderr or result.stdout or '').strip()
        platform = embed['platform']
        if 'cloudflare' in err.lower() or '403' in err or 'challenge' in err.lower():
            raise ValueError(
                f'{platform.title()} bloqueó la descarga directa con verificación anti-bot. '
                'Puedes ver el stream aquí mismo y grabarlo con OBS Studio para procesarlo en ClipForge.'
            )
        raise ValueError(
            f'No se pudo abrir el stream ({platform}). Verifica que el canal esté EN VIVO. Detalle: {err[:220]}'
        )
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        raise ValueError('yt-dlp no devolvió una URL de flujo utilizable para este stream.')
    return lines[0], {**embed, 'resolved_streams': len(lines)}


def measure_segment_db(segment_path: Path) -> float:
    """Measure average RMS loudness in dBFS (-60..0) for a single TS segment quickly."""
    result = subprocess.run(
        [
            ffmpeg(),
            '-nostdin',
            '-hide_banner',
            '-loglevel', 'error',
            '-i', str(segment_path),
            '-vn',
            '-ac', '1',
            '-ar', '8000',
            '-f', 's16le',
            'pipe:1',
        ],
        capture_output=True,
        timeout=15,
        creationflags=NO_WINDOW_FLAGS,
        check=False,
    )
    if result.returncode != 0 or len(result.stdout) < 4:
        return -60.0
    usable = len(result.stdout) - (len(result.stdout) % 2)
    samples = array('h')
    samples.frombytes(result.stdout[:usable])
    if not samples:
        return -60.0
    rms = math.sqrt(sum(s * s for s in samples[::4]) / max(1, len(samples[::4])))
    return max(-60.0, 20.0 * math.log10(max(1.0, rms) / 32768.0))


def detect_audio_spike(
    current_db: float,
    baseline_history: list[float] | deque[float],
    *,
    sensitivity: str = 'medium',
    min_db: float = -28.0,
) -> tuple[bool, float]:
    """Return (triggered, delta_db) when current_db exceeds the rolling baseline sharply."""
    if len(baseline_history) < 3 or current_db < min_db:
        return False, 0.0
    threshold = SENSITIVITY_DB.get(sensitivity, SENSITIVITY_DB['medium'])
    avg_db = sum(baseline_history) / len(baseline_history)
    delta = current_db - avg_db
    return delta >= threshold, round(delta, 2)


def concat_segments_to_mp4(segments: list[Path], destination: Path) -> Path:
    """Losslessly concatenate ordered MPEG-TS segments into an MP4 with faststart."""
    if not segments:
        raise ValueError('Aún no hay suficientes segmentos completos en el buffer. Espera unos segundos.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode='w',
        dir=destination.parent,
        prefix='concat-',
        suffix='.txt',
        encoding='utf-8',
        delete=False,
    ) as handle:
        list_file = Path(handle.name)
        for seg in segments:
            safe_path = seg.resolve().as_posix().replace("'", "'\\''")
            handle.write(f"file '{safe_path}'\n")
    try:
        run_ffmpeg(
            [
                '-f', 'concat',
                '-safe', '0',
                '-i', list_file,
                '-c', 'copy',
                '-bsf:a', 'aac_adtstoasc',
                '-movflags', '+faststart',
                '-y',
                destination,
            ],
            cwd=destination.parent,
        )
    finally:
        list_file.unlink(missing_ok=True)
    return destination


@dataclass
class LiveClipRecord:
    clip_id: str
    path: str
    window_seconds: int
    created_at: str
    platform: str
    url: str
    mode: str = 'manual'
    reason: str = 'Clip manual'


class LiveRecorder:
    """Manages live stream capture, full 1h-2h+ session recording, and auto-clipping."""

    def __init__(
        self,
        recordings_root: Path,
        *,
        clips_root: Path | None = None,
        logs_dir: Path | None = None,
        segment_seconds: int = 5,
        buffer_minutes: int = 15,
    ):
        self.recordings_root = recordings_root.resolve()
        self.clips_root = (clips_root or recordings_root).resolve()
        self.logs_dir = logs_dir.resolve() if logs_dir else None
        self.segment_seconds = max(2, min(15, int(segment_seconds)))
        self.buffer_minutes = max(1, min(240, int(buffer_minutes)))
        self._lock = threading.RLock()
        self._process: subprocess.Popen | None = None
        self._buffer_dir: Path | None = None
        self._session_dir: Path | None = None
        self._stop_event = threading.Event()
        self._monitor_thread: threading.Thread | None = None
        self._stderr_lines: list[str] = []
        self._started_at: float | None = None
        self._url: str = ''
        self._platform: str = ''
        self._channel: str = ''
        self._embed_url: str = ''
        self._rights: dict = {'kind': 'local', 'note': ''}
        self._save_full_recording: bool = True
        self._auto_clip: bool = False
        self._auto_clip_seconds: int = 45
        self._auto_sensitivity: str = 'medium'
        self._auto_cooldown_seconds: int = 55
        self._on_auto_clip: Callable[[LiveClipRecord], None] | None = None
        self._clips: list[LiveClipRecord] = []
        self._full_recording_path: str = ''
        self._last_error: str = ''
        self._last_db: float = -60.0
        self._in_ad_break: bool = False
        self._muted_segments_count: int = 0

    def is_running(self) -> bool:
        with self._lock:
            return self._process is not None and self._process.poll() is None

    def start(
        self,
        url: str,
        rights_kind: str = 'local',
        rights_note: str = '',
        quality: str = 'best',
        *,
        save_full_recording: bool = True,
        auto_clip: bool = False,
        auto_clip_seconds: int = 45,
        auto_sensitivity: str = 'medium',
        on_auto_clip: Callable[[LiveClipRecord], None] | None = None,
    ) -> dict:
        if rights_kind not in {'local', 'own', 'licensed'}:
            rights_kind = 'local'
        if rights_kind == 'licensed' and not rights_note.strip():
            raise ValueError('Indica el titular y la referencia de la licencia.')

        with self._lock:
            if self.is_running():
                raise ValueError('Ya hay una captura en vivo activa. Detenla antes de iniciar otra.')
            try:
                media_url, meta = resolve_live_stream(url, quality=quality)
            except Exception as exc:
                self._last_error = str(exc)
                record_error('live.start', exc, context={'url': url, 'quality': quality}, logs_dir=self.logs_dir)
                raise

            stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
            slug = re.sub(r'[^A-Za-z0-9_-]', '', meta.get('channel') or 'live')[:32] or 'live'
            session_dir = self.recordings_root / f'{meta["platform"]}-{slug}-{stamp}'
            buffer_dir = session_dir / '_buffer'
            buffer_dir.mkdir(parents=True, exist_ok=True)
            self.clips_root.mkdir(parents=True, exist_ok=True)

            cmd = [
                ffmpeg(),
                '-nostdin',
                '-hide_banner',
                '-loglevel', 'warning',
                '-reconnect', '1',
                '-reconnect_streamed', '1',
                '-reconnect_delay_max', '5',
                '-i', media_url,
                '-c', 'copy',
                '-f', 'segment',
                '-segment_time', str(self.segment_seconds),
                '-segment_format', 'mpegts',
                '-reset_timestamps', '1',
                str(buffer_dir / 'seg_%06d.ts'),
            ]
            self._stderr_lines = []
            self._last_error = ''
            self._full_recording_path = ''
            self._last_db = -60.0
            self._process = REGISTRY.register(subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='replace',
                creationflags=BACKGROUND_FLAGS,
            ))
            self._buffer_dir = buffer_dir
            self._session_dir = session_dir
            self._started_at = time.monotonic()
            self._url = meta['url']
            self._platform = meta['platform']
            self._channel = slug
            self._embed_url = meta.get('embed_url', '')
            self._rights = {'kind': rights_kind, 'note': rights_note.strip()}
            self._save_full_recording = bool(save_full_recording)
            self._auto_clip = bool(auto_clip)
            self._auto_clip_seconds = max(15, min(120, int(auto_clip_seconds)))
            self._auto_sensitivity = auto_sensitivity if auto_sensitivity in SENSITIVITY_DB else 'medium'
            self._on_auto_clip = on_auto_clip
            self._clips = []
            self._stop_event.clear()

            save_json(
                session_dir / 'session.json',
                {
                    'url': self._url,
                    'platform': self._platform,
                    'channel': self._channel,
                    'rights': self._rights,
                    'save_full_recording': self._save_full_recording,
                    'auto_clip': self._auto_clip,
                    'auto_clip_seconds': self._auto_clip_seconds,
                    'auto_sensitivity': self._auto_sensitivity,
                    'started_at': datetime.now(timezone.utc).isoformat(),
                    'segment_seconds': self.segment_seconds,
                },
            )
            log_info('live', f'Sesión iniciada: {self._platform}/{self._channel} ({session_dir})', self.logs_dir)
            threading.Thread(target=self._drain_stderr, daemon=True).start()
            self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
            self._monitor_thread.start()
            return self.status()

    def set_auto_clip(self, enabled: bool, *, seconds: int | None = None, sensitivity: str | None = None) -> dict:
        with self._lock:
            self._auto_clip = bool(enabled)
            if seconds is not None:
                self._auto_clip_seconds = max(15, min(120, int(seconds)))
            if sensitivity in SENSITIVITY_DB:
                self._auto_sensitivity = sensitivity
            return self.status()

    def _drain_stderr(self):
        proc = self._process
        if not proc or not proc.stderr:
            return
        for line in proc.stderr:
            cleaned = line.strip()
            if cleaned:
                with self._lock:
                    self._stderr_lines.append(cleaned)
                    if len(self._stderr_lines) > 40:
                        self._stderr_lines = self._stderr_lines[-40:]
        code = proc.poll()
        if code not in (None, 0) and not self._stop_event.is_set():
            tail = ' | '.join(self._stderr_lines[-3:]) or f'FFmpeg terminó con código {code}'
            with self._lock:
                self._last_error = tail
            record_error('live.ffmpeg', tail, context={'url': self._url, 'exit_code': code}, logs_dir=self.logs_dir)

    def _monitor_loop(self):
        max_segments = max(6, (self.buffer_minutes * 60) // self.segment_seconds + 2)
        analyzed: set[str] = set()
        baseline: deque[float] = deque(maxlen=24)
        pending_spike_at: float | None = None
        pending_reason = ''
        last_auto_clip_at = 0.0
        consecutive_muted = 0
        post_ad_warmup = 0
        tail_wait = max(self.segment_seconds * 2, 10)

        while not self._stop_event.wait(timeout=max(2, self.segment_seconds // 2)):
            with self._lock:
                buffer_dir = self._buffer_dir
                save_full = self._save_full_recording
                auto_on = self._auto_clip
                sensitivity = self._auto_sensitivity
                auto_secs = self._auto_clip_seconds
                cooldown = self._auto_cooldown_seconds
                callback = self._on_auto_clip

            if not buffer_dir or not buffer_dir.is_dir():
                continue

            completed = self._completed_segments()
            if not save_full and len(completed) > max_segments:
                for old in completed[: len(completed) - max_segments]:
                    try:
                        old.unlink(missing_ok=True)
                    except OSError:
                        pass

            if not completed:
                continue

            newest = completed[-1]
            if newest.name not in analyzed:
                analyzed.add(newest.name)
                try:
                    db = measure_segment_db(newest)
                except Exception as exc:
                    record_error('live.audio_meter', exc, context={'segment': newest.name}, logs_dir=self.logs_dir)
                    continue
                with self._lock:
                    self._last_db = round(db, 1)

                # Detect Twitch commercial / muted audio breaks so they never trigger fake clips
                if db <= MUTED_AD_DB:
                    consecutive_muted += 1
                    with self._lock:
                        self._muted_segments_count += 1
                    if consecutive_muted == 2:
                        with self._lock:
                            self._in_ad_break = True
                        pending_spike_at = None
                        log_event('live', 'ad_break_muted_start', {'segment': newest.name, 'db': round(db, 1)}, logs_dir=self.logs_dir)
                    continue

                if consecutive_muted >= 2:
                    with self._lock:
                        self._in_ad_break = False
                    # Ignore first 2 segments after ad break so returning audio doesn't look like a scream spike
                    post_ad_warmup = 2
                    log_event('live', 'ad_break_muted_end', {'segment': newest.name, 'muted_chunks': consecutive_muted, 'db': round(db, 1)}, logs_dir=self.logs_dir)
                consecutive_muted = 0

                if post_ad_warmup > 0:
                    post_ad_warmup -= 1
                    baseline.append(db)
                    continue

                if auto_on:
                    triggered, delta = detect_audio_spike(db, baseline, sensitivity=sensitivity)
                    baseline.append(db)
                    now = time.monotonic()
                    if triggered and pending_spike_at is None and (now - last_auto_clip_at) >= cooldown:
                        pending_spike_at = now
                        pending_reason = f'Auto-clip por pico de reacción (+{delta:.1f} dB)'
                        log_event('live', 'autoclip_spike', {'db': round(db, 1), 'delta_db': delta, 'segment': newest.name}, logs_dir=self.logs_dir)
                else:
                    baseline.append(db)

            if auto_on and pending_spike_at is not None and (time.monotonic() - pending_spike_at) >= tail_wait:
                pending_spike_at = None
                last_auto_clip_at = time.monotonic()
                try:
                    record = self.save_clip(auto_secs, mode='auto', reason=pending_reason)
                    log_event('live', 'autoclip_saved', {'clip_id': record.clip_id, 'path': record.path, 'reason': pending_reason}, logs_dir=self.logs_dir)
                    if callback is not None:
                        callback(record)
                except Exception as exc:
                    record_error('live.autoclip', exc, context={'reason': pending_reason}, logs_dir=self.logs_dir)

    def _completed_segments(self) -> list[Path]:
        if not self._buffer_dir or not self._buffer_dir.is_dir():
            return []
        files = sorted(self._buffer_dir.glob('seg_*.ts'))
        if self.is_running() and len(files) > 1:
            return files[:-1]
        return files

    def save_clip(self, window_seconds: int = 45, *, mode: str = 'manual', reason: str = 'Clip manual') -> LiveClipRecord:
        if not 5 <= window_seconds <= max(self.buffer_minutes * 60, 3600):
            raise ValueError('La duración del clip debe estar entre 5 y 3600 segundos.')
        with self._lock:
            if not self._session_dir or not self._buffer_dir:
                raise ValueError('No hay una sesión en vivo activa ni buffer disponible.')
            completed = self._completed_segments()
            if not completed:
                raise ValueError(
                    f'El buffer aún se está llenando. Espera al menos {self.segment_seconds * 2} segundos.'
                )
            needed = max(1, (window_seconds + self.segment_seconds - 1) // self.segment_seconds)
            selected = completed[-needed:]
            clip_num = len(self._clips) + 1
            stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
            tag = 'auto' if mode == 'auto' else 'clip'
            slug = self._channel or self._platform or 'live'
            clip_id = f'{stamp}-{slug}-{tag}-{clip_num:02}'
            clip_dir = self.clips_root / clip_id
            clip_dir.mkdir(parents=True, exist_ok=True)
            mp4_path = clip_dir / f'{clip_id}.mp4'
            try:
                concat_segments_to_mp4(selected, mp4_path)
            except Exception as exc:
                self._last_error = str(exc)
                record_error('live.save_clip', exc, context={'clip_id': clip_id}, logs_dir=self.logs_dir)
                raise

            save_json(
                clip_dir / 'clip.json',
                {
                    'clip_id': clip_id,
                    'url': self._url,
                    'platform': self._platform,
                    'channel': self._channel,
                    'rights': self._rights,
                    'mode': mode,
                    'reason': reason,
                    'archivo': mp4_path.name,
                    'window_seconds': window_seconds,
                    'segments_used': len(selected),
                    'session_dir': str(self._session_dir),
                    'captured_at': datetime.now(timezone.utc).isoformat(),
                },
            )
            record = LiveClipRecord(
                clip_id=clip_id,
                path=str(mp4_path.resolve()),
                window_seconds=window_seconds,
                created_at=datetime.now(timezone.utc).isoformat(),
                platform=self._platform,
                url=self._url,
                mode=mode,
                reason=reason,
            )
            self._clips.append(record)
            return record

    def stop(self, *, keep_buffer: bool = False) -> dict:
        with self._lock:
            self._stop_event.set()
            proc = self._process
            if proc is not None:
                REGISTRY.terminate_proc(proc)
            self._process = None

            # Assemble full 1h-2h recording into full_recording.mp4 if enabled
            if self._save_full_recording and self._session_dir and self._buffer_dir and self._buffer_dir.is_dir():
                all_segments = [p for p in sorted(self._buffer_dir.glob('seg_*.ts')) if p.stat().st_size > 0]
                if all_segments:
                    full_mp4 = self._session_dir / 'full_recording.mp4'
                    try:
                        concat_segments_to_mp4(all_segments, full_mp4)
                        self._full_recording_path = str(full_mp4.resolve())
                        save_json(
                            self._session_dir / 'session.json',
                            {
                                'url': self._url,
                                'platform': self._platform,
                                'channel': self._channel,
                                'rights': self._rights,
                                'full_recording': full_mp4.name,
                                'segments_count': len(all_segments),
                                'duration_approx_seconds': len(all_segments) * self.segment_seconds,
                                'clips_count': len(self._clips),
                                'stopped_at': datetime.now(timezone.utc).isoformat(),
                            },
                        )
                        log_info('live', f'Grabación completa guardada: {full_mp4}', self.logs_dir)
                        if not keep_buffer:
                            shutil.rmtree(self._buffer_dir, ignore_errors=True)
                    except Exception as exc:
                        self._last_error = f'Error al ensamblar grabación completa: {exc}'
                        record_error('live.assemble_full', exc, context={'session': str(self._session_dir)}, logs_dir=self.logs_dir)
                        # Keep _buffer intact if assembly failed so nothing is lost!
                elif not keep_buffer:
                    shutil.rmtree(self._buffer_dir, ignore_errors=True)
            elif not keep_buffer and self._buffer_dir and self._buffer_dir.is_dir():
                shutil.rmtree(self._buffer_dir, ignore_errors=True)
            return self.status()

    def status(self) -> dict:
        with self._lock:
            running = self.is_running()
            completed = self._completed_segments()
            elapsed = round(time.monotonic() - self._started_at, 1) if self._started_at and running else 0.0
            return {
                'running': running,
                'url': self._url,
                'platform': self._platform,
                'channel': self._channel,
                'embed_url': self._embed_url,
                'rights': self._rights,
                'save_full_recording': self._save_full_recording,
                'full_recording_path': self._full_recording_path,
                'auto_clip': self._auto_clip,
                'auto_clip_seconds': self._auto_clip_seconds,
                'auto_sensitivity': self._auto_sensitivity,
                'last_db': self._last_db,
                'in_ad_break': self._in_ad_break,
                'muted_segments_count': self._muted_segments_count,
                'last_error': self._last_error,
                'session_dir': str(self._session_dir) if self._session_dir else '',
                'elapsed_seconds': elapsed,
                'buffered_segments': len(completed),
                'buffered_seconds': len(completed) * self.segment_seconds,
                'segment_seconds': self.segment_seconds,
                'buffer_minutes': self.buffer_minutes,
                'clips': [vars(item) for item in self._clips],
                'recent_logs': self._stderr_lines[-8:],
            }
