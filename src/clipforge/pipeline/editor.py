"""Fast integrated local mini-editor for Gevik Clip.

Edits an existing clip in seconds:
  - Trim IN / OUT (start / end seconds relative to source video)
  - Camera region & vertical layout (`split-cam`, `face`, `fit`, `center`)
  - Subtitle visual style (`highlight`, `neon`, `fire`, `silver`, `bold`, `srt`, `none`)
  - Subtitle vertical position (`bottom`, `middle`, `top`) & fine sync offset (`-500..+500 ms`)
  - Audio denoise & volume gain (`-6..+6 dB`)
"""

import json
from pathlib import Path
import shutil
import threading
import time
from typing import Callable

from clipforge.logging_utils import log_event
from clipforge.pipeline.highlights import Cue, WordTiming, clip_cues, make_srt, read_srt
from clipforge.pipeline.media import (
    CamRegion,
    detect_cam_region,
    face_commands,
    generate_thumbnail,
    parse_cam_region,
    probe,
    render,
)
from clipforge.pipeline.subtitles import CAPTION_STYLES, SUBTITLE_POSITIONS, write_ass
from clipforge.publishing.publication import save_json

ProgressFn = Callable[[str, int, str], None]


def _load_plan_cues(folder: Path) -> list[Cue]:
    plan_file = folder / 'plan.json'
    if not plan_file.is_file():
        return []
    plan = json.loads(plan_file.read_text(encoding='utf-8'))
    items: list[Cue] = []
    for c in plan.get('cues') or []:
        words = tuple(WordTiming(float(w['start']), float(w['end']), str(w['text'])) for w in (c.get('words') or []))
        items.append(Cue(float(c['start']), float(c['end']), str(c['text']), words))
    return items


def _shift_cues(cues: list[Cue], delta_seconds: float, max_duration: float) -> list[Cue]:
    """Shift clip-relative cues when trimming start/end without losing user translations."""
    shifted: list[Cue] = []
    for cue in cues:
        left = max(0.0, cue.start - delta_seconds)
        right = min(max_duration, cue.end - delta_seconds)
        if right - left < 0.08:
            continue
        words = tuple(
            WordTiming(max(0.0, w.start - delta_seconds), min(max_duration, w.end - delta_seconds), w.text)
            for w in cue.words
            if min(max_duration, w.end - delta_seconds) > max(0.0, w.start - delta_seconds)
        )
        shifted.append(Cue(left, right, cue.text, words))
    return shifted


def edit_clip(
    folder: Path,
    clip_id: str,
    *,
    start: float | None = None,
    end: float | None = None,
    layout: str | None = None,
    cam_region_spec: str | None = None,
    caption_style: str | None = None,
    subtitle_position: str | None = None,
    subtitle_offset_ms: int | None = None,
    denoise: bool | None = None,
    volume_db: float | None = None,
    srt_path: Path | None = None,
    progress_cb: ProgressFn | None = None,
    cancel_event: threading.Event | None = None,
) -> dict:
    folder = folder.resolve(strict=True)
    manifest_path = folder / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    clip = next((item for item in manifest.get('clips', []) if item['id'] == clip_id), None)
    if clip is None:
        raise ValueError(f'Clip no encontrado: {clip_id}')

    source = Path(manifest['source']).resolve(strict=True)
    info = probe(source)
    old_start = float(clip['start'])
    old_end = float(clip['end'])

    new_start = old_start if start is None else max(0.0, float(start))
    new_end = old_end if end is None else min(info.duration, float(end))
    if not (0.0 <= new_start < new_end <= info.duration + 0.05) or (new_end - new_start) < 1.0:
        raise ValueError('El recorte IN/OUT debe durar al menos 1 segundo dentro del video.')

    use_layout = layout or clip.get('layout', 'split-cam')
    use_style = caption_style or clip.get('caption_style', 'highlight')
    if use_style not in CAPTION_STYLES:
        raise ValueError(f'Estilo de subtítulos inválido: {use_style}')
    use_pos = subtitle_position or clip.get('subtitle_position', 'bottom')
    if use_pos not in SUBTITLE_POSITIONS:
        use_pos = 'bottom'
    use_offset = int(clip.get('subtitle_offset_ms', 0) if subtitle_offset_ms is None else subtitle_offset_ms)
    use_offset = max(-1500, min(1500, use_offset))
    use_denoise = bool(clip.get('denoise', True) if denoise is None else denoise)
    use_vol = float(clip.get('volume_db', 0.0) if volume_db is None else volume_db)
    use_vol = max(-12.0, min(12.0, use_vol))

    clip_dir = folder / clip_id
    clip_dir.mkdir(exist_ok=True)
    if progress_cb:
        progress_cb('Editando clip', 20, f'Preparando recorte {new_start:.1f}s–{new_end:.1f}s ({use_style})...')

    # Resolve webcam box
    cam_region: CamRegion | None = None
    if use_layout == 'split-cam':
        if cam_region_spec:
            cam_region = parse_cam_region(cam_region_spec) or detect_cam_region(source, new_start, new_end - new_start)
        elif clip.get('cam_region'):
            raw = clip['cam_region']
            cam_region = CamRegion(float(raw['x']), float(raw['y']), float(raw['w']), float(raw['h']))
        else:
            cam_region = detect_cam_region(source, new_start, new_end - new_start)
    elif use_layout == 'face':
        found = face_commands(source, new_start, new_end - new_start, clip_dir / 'follow.txt')
        if not found:
            use_layout = 'center'

    # Resolve subtitles for the edited range
    captions_file: Path | None = None
    trim_changed = abs(new_start - old_start) > 0.05 or abs(new_end - old_end) > 0.05
    active_srt: Path | None = None
    if srt_path is not None and srt_path.is_file():
        active_srt = srt_path.resolve()
        clip['active_captions'] = active_srt.name
    elif clip.get('active_captions') and (clip_dir / clip['active_captions']).is_file():
        active_srt = (clip_dir / clip['active_captions']).resolve()
    elif clip.get('captions') and (folder / clip['captions']).is_file():
        active_srt = (folder / clip['captions']).resolve()

    if use_style != 'none':
        cues_for_clip: list[Cue] = []
        if active_srt and active_srt.is_file():
            existing_cues = read_srt(active_srt)
            if trim_changed and not srt_path:
                # Try plan cues first if not translated, else shift existing clip-relative cues
                if active_srt.name == 'captions.srt':
                    plan_cues = _load_plan_cues(folder)
                    cues_for_clip = clip_cues(plan_cues, new_start, new_end) if plan_cues else _shift_cues(existing_cues, new_start - old_start, new_end - new_start)
                else:
                    cues_for_clip = _shift_cues(existing_cues, new_start - old_start, new_end - new_start)
            else:
                cues_for_clip = existing_cues

        if cues_for_clip:
            if use_style == 'srt':
                out_srt = clip_dir / 'captions.rendered.srt'
                out_srt.write_text(make_srt(cues_for_clip, 0.0, new_end - new_start), encoding='utf-8')
                captions_file = out_srt
            else:
                out_ass = clip_dir / 'captions.edited.ass'
                write_ass(
                    cues_for_clip,
                    out_ass,
                    use_style,
                    layout=use_layout,
                    offset_ms=use_offset,
                    position=use_pos,
                )
                captions_file = out_ass
                clip['captions_ass'] = f'{clip_id}/{out_ass.name}'

    if progress_cb:
        progress_cb('Renderizando edición', 55, f'Renderizando {clip_id} ({use_layout} · {use_style})...')

    target = clip_dir / 'video.mp4'
    t0 = time.monotonic()
    try:
        render(
            source,
            target,
            new_start,
            new_end,
            manifest.get('audio_track'),
            use_layout,
            captions_file is not None,
            use_denoise,
            captions_file=captions_file,
            cam_region=cam_region,
            volume_db=use_vol,
            cancel_event=cancel_event,
        )
    finally:
        (clip_dir / 'follow.txt').unlink(missing_ok=True)

    thumb = generate_thumbnail(target, clip_dir / 'thumb.jpg')
    clip['start'] = round(new_start, 3)
    clip['end'] = round(new_end, 3)
    clip['layout'] = use_layout
    clip['cam_region'] = (
        {'x': cam_region.x, 'y': cam_region.y, 'w': cam_region.w, 'h': cam_region.h} if cam_region else None
    )
    clip['caption_style'] = use_style
    clip['subtitle_position'] = use_pos
    clip['subtitle_offset_ms'] = use_offset
    clip['denoise'] = use_denoise
    clip['volume_db'] = use_vol
    if thumb:
        clip['thumb'] = f'{clip_id}/thumb.jpg'
    save_json(manifest_path, manifest)

    log_event('pipeline.editor', 'clip_edited', {
        'batch': folder.name,
        'clip_id': clip_id,
        'start': clip['start'],
        'end': clip['end'],
        'layout': use_layout,
        'caption_style': use_style,
        'subtitle_position': use_pos,
        'subtitle_offset_ms': use_offset,
        'volume_db': use_vol,
        'render_seconds': round(time.monotonic() - t0, 2),
    })
    if progress_cb:
        progress_cb('Completado', 100, f'{clip_id} actualizado')
    return clip
