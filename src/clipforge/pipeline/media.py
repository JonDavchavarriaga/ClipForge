import math
import os
import re
import shutil
from statistics import median
import subprocess
import sys
import wave
from array import array
from dataclasses import dataclass
from pathlib import Path

from clipforge.logging_utils import ensure_safe_stdio
from clipforge.pipeline.highlights import Cue, WordTiming

NO_WINDOW_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0


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


def run(args, cwd=None):
    result = subprocess.run(
        [ffmpeg(), '-nostdin', '-hide_banner', '-loglevel', 'error', *map(str, args)],
        cwd=cwd, capture_output=True, text=True, encoding='utf-8', errors='replace',
        creationflags=NO_WINDOW_FLAGS,
    )
    if result.returncode:
        raise ValueError(f'FFmpeg: {result.stderr.strip()}')


def probe(path):
    result = subprocess.run(
        [ffmpeg(), '-nostdin', '-hide_banner', '-i', str(path)],
        capture_output=True, text=True, encoding='utf-8', errors='replace',
        creationflags=NO_WINDOW_FLAGS,
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


def audio_preview(source, output, track):
    run(['-i', source, '-map', f'0:a:{track}', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', output])
    energy = []
    with wave.open(str(output), 'rb') as wav:
        while block := wav.readframes(16000):
            samples = array('h', block)[::8]
            rms = math.sqrt(sum(value * value for value in samples) / max(1, len(samples)))
            energy.append(min(1.0, max(0.0, (20 * math.log10(max(1, rms) / 32768) + 55) / 45)))
    return energy


def load_wav_float32(path: Path):
    """Read a 16kHz mono 16-bit PCM WAV into a 1D float32 numpy array for faster-whisper.

    Bypasses PyAV (`av.open(..., metadata_errors=...)`) incompatibilities across PyAV versions.
    """
    import numpy as np

    with wave.open(str(path), 'rb') as wav:
        if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getframerate() != 16000:
            raise ValueError('Se esperaba WAV mono 16-bit a 16 kHz para transcripción.')
        raw = wav.readframes(wav.getnframes())
    if not raw:
        return np.zeros(0, dtype=np.float32)
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def speech(path, model_name, language):
    ensure_safe_stdio()
    try:
        from faster_whisper import WhisperModel
    except ImportError as error:
        raise ValueError('Instala requirements.txt para transcribir con faster-whisper.') from error
    audio = load_wav_float32(Path(path))
    if audio.size == 0:
        return []
    model = WhisperModel(model_name, device='cpu', compute_type='int8')
    segments, _ = model.transcribe(
        audio, language=None if language == 'auto' else language,
        word_timestamps=True, vad_filter=True, condition_on_previous_text=False,
    )

    cues = []
    for segment in segments:
        group = []
        for word in segment.words or []:
            if group and (len(group) >= 7 or word.end - group[0].start > 2.7
                          or len(''.join(item.word for item in group) + word.word) > 44):
                timings = tuple(
                    WordTiming(item.start, item.end, item.word.strip())
                    for item in group
                    if item.word.strip()
                )
                cues.append(Cue(group[0].start, group[-1].end, ''.join(item.word for item in group).strip(), timings))
                group.clear()
            group.append(word)
        if group:
            timings = tuple(
                WordTiming(item.start, item.end, item.word.strip())
                for item in group
                if item.word.strip()
            )
            cues.append(Cue(group[0].start, group[-1].end, ''.join(item.word for item in group).strip(), timings))
        elif segment.text.strip() and not segment.words:
            cues.append(Cue(segment.start, segment.end, segment.text.strip()))
    return cues


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


def render(source, target, start, end, track, layout, has_captions, denoise, *, captions_file=None, cam_region=None):
    caption_step = _caption_filter(has_captions, captions_file)
    args = ['-ss', f'{start:.6f}', '-i', source, '-t', f'{end - start:.6f}']

    if layout == 'split-cam':
        region = cam_region or CAM_PRESETS['bottom-right']
        top_crop = (
            f"crop=w='max(2,trunc(iw*{region.w:.6f}/2)*2)':"
            f"h='max(2,trunc(ih*{region.h:.6f}/2)*2)':"
            f"x='trunc(iw*{region.x:.6f}/2)*2':"
            f"y='trunc(ih*{region.y:.6f}/2)*2'"
        )
        post_steps = ['vstack=inputs=2', 'drawbox=x=0:y=638:w=1080:h=4:color=black@0.85:t=fill']
        if caption_step:
            post_steps.append(caption_step)
        graph = (
            '[0:v:0]scale=trunc(iw*sar/2)*2:ih,setsar=1,fps=30,split=2[vcam][vgame];'
            f'[vcam]{top_crop},scale=1080:640:force_original_aspect_ratio=increase,crop=1080:640[top];'
            '[vgame]scale=1080:1280:force_original_aspect_ratio=increase,crop=1080:1280[bottom];'
            f'[top][bottom]{",".join(post_steps)}[vout]'
        )
        args.extend(['-filter_complex', graph, '-map', '[vout]'])
    else:
        filters = ['scale=trunc(iw*sar/2)*2:ih', 'setsar=1', 'fps=30']
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
        args.extend(['-map', '0:v:0', '-vf', ','.join(filters)])

    if track is not None:
        effects = ['highpass=f=65']
        if denoise:
            effects.append('afftdn=nr=10')
        effects.append('loudnorm=I=-16:TP=-1.5:LRA=11')
        args.extend(['-map', f'0:a:{track}', '-af', ','.join(effects), '-c:a', 'aac', '-b:a', '160k', '-ar', '48000'])

    args.extend(['-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-preset', 'veryfast',
                 '-crf', '21', '-movflags', '+faststart', target])
    run(args, cwd=target.parent)
