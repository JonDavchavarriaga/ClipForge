import math
import os
import re
import shutil
from statistics import median
import subprocess
import sys
import threading
import time
import wave
from array import array
from dataclasses import dataclass
from pathlib import Path

from clipforge.logging_utils import ensure_safe_stdio, log_event
from clipforge.pipeline.highlights import Cue, WordTiming, group_words_into_cues
from clipforge.process_manager import BACKGROUND_FLAGS, CREATE_NO_WINDOW, REGISTRY

NO_WINDOW_FLAGS = CREATE_NO_WINDOW

# Idle-unloading cache for WhisperModel so clip-level alignment is fast without hogging RAM
_MODEL_LOCK = threading.RLock()
_CACHED_MODEL_NAME: str | None = None
_CACHED_MODEL = None
_MODEL_LAST_USED: float = 0.0
_MODEL_IDLE_SECONDS = 300.0


@dataclass(frozen=True)
class Media:
    duration: float
    audio_tracks: int
    width: int = 0
    height: int = 0


@dataclass(frozen=True)
class CamRegion:
    """Normalized [0..1] rectangle locating the streamer webcam on the source canvas."""
    x: float
    y: float
    w: float
    h: float


CAM_PRESETS: dict[str, CamRegion] = {
    'top-left': CamRegion(0.0, 0.0, 0.34, 0.38),
    'top-right': CamRegion(0.66, 0.0, 0.34, 0.38),
    'bottom-left': CamRegion(0.0, 0.62, 0.34, 0.38),
    'bottom-right': CamRegion(0.66, 0.62, 0.34, 0.38),
}


def parse_cam_region(spec: str | None) -> CamRegion | None:
    """Parse --cam-region preset ('auto', 'top-left', ...) or normalized 'x,y,w,h'."""
    if not spec or spec.strip().lower() == 'auto':
        return None
    cleaned = spec.strip().lower()
    if cleaned in CAM_PRESETS:
        return CAM_PRESETS[cleaned]
    parts = [part.strip() for part in cleaned.split(',')]
    if len(parts) != 4:
        valid = ', '.join(['auto', *CAM_PRESETS.keys()])
        raise ValueError(f'Usa --cam-region con uno de ({valid}) o cuatro números normalizados x,y,w,h.')
    try:
        x, y, w, h = (float(part) for part in parts)
    except ValueError as error:
        raise ValueError('Los valores de --cam-region x,y,w,h deben ser numéricos entre 0 y 1.') from error
    if not (0.0 <= x < 1.0 and 0.0 <= y < 1.0 and 0.08 <= w <= 1.0 and 0.08 <= h <= 1.0):
        raise ValueError('Cada coordenada de --cam-region debe estar en [0, 1] con ancho y alto >= 0.08.')
    if x + w > 1.001 or y + h > 1.001:
        raise ValueError('La región --cam-region excede los límites del video (x+w y y+h deben ser <= 1).')
    return CamRegion(x, y, min(w, 1.0 - x), min(h, 1.0 - y))


def ffmpeg():
    binary = os.environ.get('FFMPEG_BINARY') or shutil.which('ffmpeg')
    if binary:
        return str(Path(binary).resolve())
    try:
        from imageio_ffmpeg import get_ffmpeg_exe
    except ImportError as error:
        raise ValueError('Instala requirements.txt o agrega FFmpeg al PATH.') from error
    return get_ffmpeg_exe()


def _ffmpeg_threads() -> str:
    cpus = os.cpu_count() or 4
    return str(max(2, min(4, cpus - 1)))


def run(args, cwd=None, *, cancel_event: threading.Event | None = None):
    cmd = [ffmpeg(), '-nostdin', '-hide_banner', '-loglevel', 'error', *map(str, args)]
    result = REGISTRY.run_managed(cmd, cwd=cwd, cancel_event=cancel_event, low_priority=True)
    if result.returncode:
        raise ValueError(f'FFmpeg: {(result.stderr or "").strip()}')


def probe(path):
    result = subprocess.run(
        [ffmpeg(), '-nostdin', '-hide_banner', '-i', str(path)],
        capture_output=True, text=True, encoding='utf-8', errors='replace',
        creationflags=BACKGROUND_FLAGS,
    )
    info = result.stderr
    duration = re.search(r'Duration:\s+(\d+):(\d+):([\d.]+)', info)
    video_line = re.search(r'Stream .*Video:.*', info)
    if not duration or not video_line:
        raise ValueError('La entrada debe ser un video local con duración conocida.')
    hours, minutes, secs = map(float, duration.groups())
    dims = re.search(r',\s*(\d{2,5})x(\d{2,5})\b', video_line.group(0))
    width, height = (int(dims.group(1)), int(dims.group(2))) if dims else (0, 0)
    return Media(3600 * hours + 60 * minutes + secs, len(re.findall(r'Stream .*Audio:', info)), width, height)


def _seek_window(start: float, duration: float) -> tuple[float, float, float]:
    """Split seek into fast input seek `pre` + filter-level exact trim `fine` so ASS never burns before cut."""
    safe_start = max(0.0, float(start))
    dur = max(0.5, float(duration))
    if safe_start > 2.5:
        return safe_start - 2.0, 2.0, dur
    return 0.0, safe_start, dur


def audio_preview(source, output, track, *, cancel_event: threading.Event | None = None):
    run(['-i', source, '-map', f'0:a:{track}', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', '-y', output],
        cancel_event=cancel_event)
    energy = []
    with wave.open(str(output), 'rb') as wav:
        while block := wav.readframes(16000):
            samples = array('h', block)[::8]
            rms = math.sqrt(sum(value * value for value in samples) / max(1, len(samples)))
            energy.append(min(1.0, max(0.0, (20 * math.log10(max(1, rms) / 32768) + 55) / 45)))
    return energy


def extract_clip_wav(source: Path, start: float, end: float, output_wav: Path, track: int, *, cancel_event=None) -> Path:
    """Extract clip-exact 16kHz mono PCM WAV using identical filtergraph trim as render() for 0ms drift."""
    pre, fine, duration = _seek_window(start, float(end) - float(start))
    args: list = []
    if pre > 0.0:
        args.extend(['-ss', f'{pre:.6f}'])
    args.extend([
        '-i', str(source),
        '-map', f'0:a:{track}',
        '-af', f'atrim=start={fine:.6f}:duration={duration:.6f},asetpts=PTS-STARTPTS,aresample=async=1:first_pts=0',
        '-ac', '1',
        '-ar', '16000',
        '-c:a', 'pcm_s16le',
        '-y',
        output_wav,
    ])
    run(args, cancel_event=cancel_event)
    return output_wav


def load_wav_float32(path: Path):
    """Read a 16kHz mono 16-bit PCM WAV into a 1D float32 numpy array for faster-whisper."""
    import numpy as np

    with wave.open(str(path), 'rb') as wav:
        if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getframerate() != 16000:
            raise ValueError('Se esperaba WAV mono 16-bit a 16 kHz para transcripción.')
        raw = wav.readframes(wav.getnframes())
    if not raw:
        return np.zeros(0, dtype=np.float32)
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def unload_idle_whisper():
    global _CACHED_MODEL, _CACHED_MODEL_NAME
    with _MODEL_LOCK:
        if _CACHED_MODEL is not None and (time.monotonic() - _MODEL_LAST_USED) >= _MODEL_IDLE_SECONDS:
            _CACHED_MODEL = None
            _CACHED_MODEL_NAME = None


def _get_whisper_model(model_name: str):
    global _CACHED_MODEL, _CACHED_MODEL_NAME, _MODEL_LAST_USED
    try:
        from faster_whisper import WhisperModel
    except ImportError as error:
        raise ValueError('Instala requirements.txt para transcribir con faster-whisper.') from error
    with _MODEL_LOCK:
        if _CACHED_MODEL is None or _CACHED_MODEL_NAME != model_name:
            _CACHED_MODEL = WhisperModel(model_name, device='cpu', compute_type='int8', cpu_threads=int(_ffmpeg_threads()))
            _CACHED_MODEL_NAME = model_name
        _MODEL_LAST_USED = time.monotonic()
        return _CACHED_MODEL


# Gevik Clip is an EN<->ES product: Whisper's auto-detection on noisy gameplay audio often guesses
# unrelated languages (seen in practice: Finnish, then Greek, for a Spanish stream).
AUTO_LANGUAGES = ('es', 'en')


def resolve_auto_language(detected: str | None, all_probs) -> str:
    """Keep Whisper's detection when it is ES/EN; otherwise pick the likelier of ES and EN."""
    if detected in AUTO_LANGUAGES:
        return detected
    probs = dict(all_probs or [])
    return max(AUTO_LANGUAGES, key=lambda lang: probs.get(lang, 0.0))


def _probe_windows(audio, sample_rate: int = 16000, window_s: int = 40):
    """Return up to 3 short audio slices (25/50/75%) so probing a 2h stream stays fast."""
    total = len(audio)
    window = window_s * sample_rate
    if total <= 3 * window:
        return [audio]
    import numpy as np

    return [np.ascontiguousarray(audio[int(total * f) - window // 2:int(total * f) + window // 2]) for f in (0.25, 0.5, 0.75)]


def _probe_best_language(model, audio) -> str | None:
    """Transcribe a probe in ES and EN and keep the language Whisper is more confident in."""
    scores: dict[str, float] = {}
    for lang in AUTO_LANGUAGES:
        weighted = 0.0
        total = 0.0
        for chunk in _probe_windows(audio):
            segments, _ = model.transcribe(chunk, language=lang, vad_filter=True, condition_on_previous_text=False)
            for segment in segments:
                length = max(0.1, segment.end - segment.start)
                weighted += segment.avg_logprob * length
                total += length
        if total:
            scores[lang] = weighted / total
    if not scores:
        return None
    return max(scores, key=scores.get)


# Per-segment voice filter: streams mix the streamer's voice (e.g. ES) with game dialogue in another
# language (e.g. EN). Whisper's language detector separates them reliably (~0.99) on each segment.
FOREIGN_MIN_SECONDS = 0.6
FOREIGN_OTHER_MIN_PROB = 0.5
FOREIGN_KEEP_MAX_PROB = 0.2


def is_foreign_speech(probs, keep: str) -> bool:
    """True when the audio is clearly the *other* EN/ES language and clearly not the streamer's."""
    other = next((lang for lang in AUTO_LANGUAGES if lang != keep), None)
    p = dict(probs or [])
    return other is not None and p.get(other, 0.0) >= FOREIGN_OTHER_MIN_PROB and p.get(keep, 0.0) < FOREIGN_KEEP_MAX_PROB


def drop_foreign_segments(model, audio, segments, keep: str, sample_rate: int = 16000):
    """Remove Whisper segments spoken in the other language (game dialogue / NPCs / cinematics).

    Segments shorter than FOREIGN_MIN_SECONDS are too short to classify: they inherit the decision of
    the previous classified segment when they follow it closely, otherwise they are kept.
    Returns (kept_segments, dropped_count).
    """
    if keep not in AUTO_LANGUAGES:
        return list(segments), 0
    decisions: list[bool | None] = []
    for segment in segments:
        if segment.end - segment.start < FOREIGN_MIN_SECONDS:
            decisions.append(None)
            continue
        chunk = audio[int(segment.start * sample_rate):int(segment.end * sample_rate)]
        _, _, probs = model.detect_language(audio=chunk)
        decisions.append(is_foreign_speech(probs, keep))
    kept = []
    dropped = 0
    previous_decision = False
    previous_end = -10.0
    for segment, decision in zip(segments, decisions):
        if decision is None:
            decision = previous_decision if segment.start - previous_end < 0.4 else False
        else:
            previous_decision = decision
        previous_end = segment.end
        if decision:
            dropped += 1
        else:
            kept.append(segment)
    return kept, dropped


def transcribe(path, model_name, language, *, voice_filter: bool = False) -> tuple[list[Cue], str | None]:
    """Transcribe a 16 kHz WAV with word timings and return (cues, resolved_language).

    voice_filter=True keeps only speech in the resolved streamer language (ES or EN), dropping game
    dialogue in the other language. It costs ~1 language detection per segment, so use it on clips,
    not on multi-hour recordings.
    """
    ensure_safe_stdio()
    audio = load_wav_float32(Path(path))
    if audio.size == 0:
        return [], None if language == 'auto' else language
    model = _get_whisper_model(model_name)
    options = dict(word_timestamps=True, vad_filter=True, condition_on_previous_text=False)
    segments, info = model.transcribe(audio, language=None if language == 'auto' else language, **options)
    resolved = language
    if language == 'auto':
        detected = getattr(info, 'language', None)
        if detected in AUTO_LANGUAGES:
            resolved = detected
        else:
            # Segments are lazy: nothing was decoded yet. Pick ES/EN by real transcription confidence
            # on a short probe, then re-run forcing that language.
            resolved = _probe_best_language(model, audio) or resolve_auto_language(
                detected, getattr(info, 'all_language_probs', None))
            segments, info = model.transcribe(audio, language=resolved, **options)

    segments = list(segments)
    if voice_filter and resolved in AUTO_LANGUAGES:
        total = len(segments)
        segments, dropped = drop_foreign_segments(model, audio, segments, resolved)
        if dropped:
            log_event('pipeline.asr', 'foreign_speech_filtered', {
                'keep_language': resolved, 'dropped_segments': dropped, 'total_segments': total,
            })

    # Flatten word timings across Whisper segments, then regroup by punctuation/pauses so
    # captions never split mid-phrase or flash 1-word fragments on screen.
    cues: list[Cue] = []
    pending_words: list[WordTiming] = []
    for segment in segments:
        if segment.words:
            pending_words.extend(
                WordTiming(float(item.start), float(item.end), item.word.strip())
                for item in segment.words
                if item.word.strip()
            )
        elif segment.text.strip():
            cues.extend(group_words_into_cues(pending_words))
            pending_words = []
            cues.append(Cue(segment.start, segment.end, segment.text.strip()))
    cues.extend(group_words_into_cues(pending_words))
    return sorted(cues, key=lambda cue: cue.start), resolved


def speech(path, model_name, language, *, voice_filter: bool = False):
    """Backward-compatible wrapper returning only the cues."""
    return transcribe(path, model_name, language, voice_filter=voice_filter)[0]


def detect_cam_region(source, start, duration) -> CamRegion:
    """Locate the streamer webcam overlay via OpenCV face detection, falling back to bottom-right."""
    try:
        import cv2
    except ImportError as error:
        raise ValueError('Instala requirements.txt para detectar la webcam con OpenCV.') from error
    capture = cv2.VideoCapture(str(source))
    detector = cv2.CascadeClassifier(str(Path(cv2.data.haarcascades) / 'haarcascade_frontalface_default.xml'))
    try:
        fps = capture.get(cv2.CAP_PROP_FPS)
        if not capture.isOpened() or not math.isfinite(fps) or fps <= 0 or detector.empty():
            return CAM_PRESETS['bottom-right']
        samples = min(12, max(1, int(math.ceil(duration))))
        interval = duration / samples
        detections = []
        for idx in range(samples):
            capture.set(cv2.CAP_PROP_POS_MSEC, (start + idx * interval) * 1000)
            ok, frame = capture.read()
            if not ok:
                break
            height, width = frame.shape[:2]
            scale = min(1.0, 720 / max(width, height))
            resized = cv2.resize(frame, None, fx=scale, fy=scale) if scale < 1.0 else frame
            gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
            fh, fw = gray.shape
            faces = detector.detectMultiScale(gray, scaleFactor=1.12, minNeighbors=5, minSize=(24, 24))
            if len(faces):
                x, y, w, h = max(faces, key=lambda item: int(item[2]) * int(item[3]))
                detections.append(((x + w / 2) / fw, (y + h / 2) / fh, w / fw, h / fh))
        if not detections:
            return CAM_PRESETS['bottom-right']
        cx = median(item[0] for item in detections)
        cy = median(item[1] for item in detections)
        fw = median(item[2] for item in detections)
        fh = median(item[3] for item in detections)
        box_w = max(0.28, min(0.46, fw * 3.0))
        box_h = max(0.30, min(0.50, fh * 2.8))
        x = max(0.0, min(1.0 - box_w, cx - box_w / 2))
        y = max(0.0, min(1.0 - box_h, cy - box_h / 2))
        return CamRegion(round(x, 4), round(y, 4), round(box_w, 4), round(box_h, 4))
    finally:
        capture.release()


def face_commands(source, start, duration, commands):
    try:
        import cv2
    except ImportError as error:
        raise ValueError('Instala requirements.txt para detectar caras con OpenCV.') from error
    capture = cv2.VideoCapture(str(source))
    detector = cv2.CascadeClassifier(str(Path(cv2.data.haarcascades) / 'haarcascade_frontalface_default.xml'))
    try:
        fps = capture.get(cv2.CAP_PROP_FPS)
        if not capture.isOpened() or not math.isfinite(fps) or fps <= 0 or detector.empty():
            raise ValueError('OpenCV no pudo analizar este archivo. Usa --layout center o --layout fit.')
        capture.set(cv2.CAP_PROP_POS_MSEC, start * 1000)
        center = (0.5, 0.5)
        previous_face = None
        missed = 0
        found = 0
        lines = []
        step = max(1, round(fps / 4))
        for frame_number in range(math.ceil(duration * fps)):
            if not capture.grab():
                break
            if frame_number % step:
                continue
            ok, frame = capture.retrieve()
            if not ok:
                break
            height, width = frame.shape[:2]
            scale = min(1, 720 / max(width, height))
            frame = cv2.resize(frame, None, fx=scale, fy=scale) if scale < 1 else frame
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            frame_h, frame_w = gray.shape
            faces = detector.detectMultiScale(gray, scaleFactor=1.12, minNeighbors=5, minSize=(28, 28))
            options = [((x + w / 2) / frame_w, (y + h / 2) / frame_h, int(w) * int(h)) for x, y, w, h in faces]
            if options:
                if previous_face is None:
                    face = max(options, key=lambda point: point[2])
                else:
                    face = min(options, key=lambda point: math.dist(point[:2], previous_face))
                target = face[:2]
                weight = 1.0 if frame_number == 0 else 0.28
                center = tuple(old + weight * (new - old) for old, new in zip(center, target))
                previous_face = target
                missed = 0
                found += 1
            else:
                missed += 1
                if missed > 6:
                    previous_face = None
                    center = tuple(value + 0.18 * (0.5 - value) for value in center)
            x = crop_position('iw', 'ow', center[0])
            y = crop_position('ih', 'oh', center[1])
            lines.append(f"{frame_number / fps:.3f} crop@follow x '{x}', crop@follow y '{y}';")
        if found:
            commands.write_text('\n'.join(lines), encoding='ascii')
        return found
    finally:
        capture.release()


def crop_position(full, crop, center):
    return f'clip({full}*{center:.6f}-{crop}/2,0,{full}-{crop})'


def _caption_filter(has_captions: bool, captions_file: Path | None) -> str | None:
    if captions_file is not None:
        name = captions_file.name
        if captions_file.suffix.lower() == '.ass':
            return f"ass={name}"
        return (
            f"subtitles={name}:force_style="
            "'FontName=Arial,FontSize=15,Bold=1,Outline=2,Shadow=0,MarginV=58,Alignment=2'"
        )
    if has_captions:
        return "subtitles=captions.srt:force_style='FontName=Arial,FontSize=15,Bold=1,Outline=2,Shadow=0,MarginV=58,Alignment=2'"
    return None


def extract_frame_jpg(source: Path, timestamp_sec: float, destination: Path) -> Path:
    """Extract a single JPEG frame at timestamp_sec so the user can visually select the webcam box."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    safe_ts = max(0.0, float(timestamp_sec))
    run([
        '-ss', f'{safe_ts:.3f}',
        '-i', source,
        '-frames:v', '1',
        '-vf', 'scale=min(1280\\,iw):-2',
        '-q:v', '3',
        '-y',
        destination,
    ])
    if not destination.is_file() or destination.stat().st_size == 0:
        raise ValueError('No se pudo extraer un fotograma de este video en el tiempo indicado.')
    return destination


def generate_thumbnail(video_path: Path, destination: Path) -> Path | None:
    """Create a lightweight vertical thumbnail JPEG (360px wide) for the clip manager."""
    try:
        info = probe(video_path)
        ts = min(max(0.2, info.duration * 0.2), max(0.0, info.duration - 0.1))
        destination.parent.mkdir(parents=True, exist_ok=True)
        run([
            '-ss', f'{ts:.3f}',
            '-i', video_path,
            '-frames:v', '1',
            '-vf', 'scale=360:-2',
            '-q:v', '4',
            '-y',
            destination,
        ])
        return destination if destination.is_file() else None
    except Exception:
        return None


def render(
    source,
    target,
    start,
    end,
    track,
    layout,
    has_captions,
    denoise,
    *,
    captions_file=None,
    cam_region=None,
    cam_split_pct: int = 33,
    volume_db: float = 0.0,
    title_file: Path | None = None,
    cancel_event: threading.Event | None = None,
):
    target = Path(target)
    temp_target = target.with_name(f'.tmp-{target.name}')
    caption_step = _caption_filter(has_captions, captions_file)
    # Static hook title (title.ass, same folder as the output) drawn on top of the captions.
    title_step = f'ass={Path(title_file).name}' if title_file else None
    pre, fine, duration = _seek_window(start, float(end) - float(start))
    args: list = []
    if pre > 0.0:
        args.extend(['-ss', f'{pre:.6f}'])
    args.extend(['-i', str(source)])

    vtrim = f'trim=start={fine:.6f}:duration={duration:.6f},setpts=PTS-STARTPTS'
    if layout == 'split-cam':
        region = cam_region or CAM_PRESETS['bottom-right']
        split_pct = max(20, min(55, int(cam_split_pct or 33)))
        top_h = max(320, min(1060, int(round((1920 * split_pct / 100.0) / 2.0) * 2)))
        bottom_h = 1920 - top_h
        top_crop = (
            f"crop=w='max(2,trunc(iw*{region.w:.6f}/2)*2)':"
            f"h='max(2,trunc(ih*{region.h:.6f}/2)*2)':"
            f"x='trunc(iw*{region.x:.6f}/2)*2':"
            f"y='trunc(ih*{region.y:.6f}/2)*2'"
        )
        post_steps = ['vstack=inputs=2', f'drawbox=x=0:y={top_h - 2}:w=1080:h=4:color=black@0.85:t=fill']
        if caption_step:
            post_steps.append(caption_step)
        if title_step:
            post_steps.append(title_step)
        graph = (
            f'[0:v:0]{vtrim},scale=trunc(iw*sar/2)*2:ih,setsar=1,fps=30,split=2[vcam][vgame];'
            f'[vcam]{top_crop},scale=1080:{top_h}:force_original_aspect_ratio=increase,crop=1080:{top_h}[top];'
            f'[vgame]scale=1080:{bottom_h}:force_original_aspect_ratio=increase,crop=1080:{bottom_h}[bottom];'
            f'[top][bottom]{",".join(post_steps)}[vout]'
        )
        args.extend(['-filter_complex', graph, '-map', '[vout]'])
    else:
        filters = [vtrim, 'scale=trunc(iw*sar/2)*2:ih', 'setsar=1', 'fps=30']
        if layout == 'fit':
            filters.extend([
                'scale=1080:1920:force_original_aspect_ratio=decrease:force_divisible_by=2',
                'pad=1080:1920:(ow-iw)/2:(oh-ih)/2',
            ])
        else:
            if layout == 'face':
                filters.append('sendcmd=f=follow.txt')
            filters.extend([
                "crop@follow=w='trunc(min(iw,ih*9/16)/2)*2':h='trunc(min(ih,iw*16/9)/2)*2':x=(iw-ow)/2:y=(ih-oh)/2",
                'scale=1080:1920',
            ])
        if caption_step:
            filters.append(caption_step)
        if title_step:
            filters.append(title_step)
        args.extend(['-map', '0:v:0', '-vf', ','.join(filters)])

    if track is not None:
        effects = [
            f'atrim=start={fine:.6f}:duration={duration:.6f}',
            'asetpts=PTS-STARTPTS',
            'aresample=async=1:first_pts=0',
            'highpass=f=65',
        ]
        if denoise:
            effects.append('afftdn=nr=10')
        effects.append('loudnorm=I=-16:TP=-1.5:LRA=11')
        if abs(volume_db) >= 0.2:
            effects.append(f'volume={volume_db:.1f}dB')
        args.extend(['-map', f'0:a:{track}', '-af', ','.join(effects), '-c:a', 'aac', '-b:a', '160k', '-ar', '48000'])

    args.extend([
        '-threads', _ffmpeg_threads(),
        '-c:v', 'libx264',
        '-pix_fmt', 'yuv420p',
        '-preset', 'veryfast',
        '-crf', '21',
        '-movflags', '+faststart',
        '-y',
        temp_target,
    ])
    try:
        run(args, cwd=target.parent, cancel_event=cancel_event)
        for attempt in range(6):
            try:
                temp_target.replace(target)
                break
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.2)
    finally:
        temp_target.unlink(missing_ok=True)
