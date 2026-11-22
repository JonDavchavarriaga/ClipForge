from dataclasses import dataclass
from pathlib import Path

from clipforge.pipeline.highlights import Cue, WordTiming

CAPTION_STYLES = ('srt', 'highlight', 'bold', 'neon')


@dataclass(frozen=True)
class AssPreset:
    fontname: str
    fontsize: int
    primary_rgb: tuple[int, int, int]
    accent_ass_bgr: str
    outline_rgb: tuple[int, int, int]
    outline: float
    shadow: float
    word_highlight: bool
    uppercase: bool = False


ASS_PRESETS: dict[str, AssPreset] = {
    'highlight': AssPreset(
        fontname='Arial',
        fontsize=74,
        primary_rgb=(255, 255, 255),
        accent_ass_bgr='&H0015CCFA&',  # Vibrant amber/yellow (#FACC15) in ASS BGR
        outline_rgb=(12, 12, 16),
        outline=6.0,
        shadow=2.5,
        word_highlight=True,
    ),
    'neon': AssPreset(
        fontname='Arial',
        fontsize=76,
        primary_rgb=(248, 250, 252),
        accent_ass_bgr='&H00F8E122&',  # Electric cyan (#22E1F8) in ASS BGR
        outline_rgb=(10, 14, 24),
        outline=6.5,
        shadow=3.0,
        word_highlight=True,
        uppercase=True,
    ),
    'bold': AssPreset(
        fontname='Arial',
        fontsize=70,
        primary_rgb=(255, 255, 255),
        accent_ass_bgr='&H00FFFFFF&',
        outline_rgb=(0, 0, 0),
        outline=5.5,
        shadow=2.0,
        word_highlight=False,
    ),
}


def sanitize_ass_text(text: str) -> str:
    """Escape ASS control characters so untrusted captions cannot inject override tags."""
    return (
        text.replace('\\', '＼')
        .replace('{', '｛')
        .replace('}', '｝')
        .strip()
    )


def estimate_word_timings(cue: Cue) -> list[WordTiming]:
    """Use exact Whisper word timings when present; otherwise distribute proportionally across the cue."""
    if cue.words:
        return [
            WordTiming(word.start, word.end, sanitize_ass_text(word.text))
            for word in cue.words
            if sanitize_ass_text(word.text)
        ]

    tokens = [sanitize_ass_text(token) for token in cue.text.split() if sanitize_ass_text(token)]
    if not tokens:
        return []
    duration = max(cue.end - cue.start, 0.08)
    weights = [max(len(token), 2) for token in tokens]
    total_weight = sum(weights)
    cursor = cue.start
    estimated: list[WordTiming] = []
    for index, (token, weight) in enumerate(zip(tokens, weights)):
        end = cue.end if index == len(tokens) - 1 else cursor + duration * (weight / total_weight)
        estimated.append(WordTiming(cursor, max(cursor + 0.04, end), token))
        cursor = end
    return estimated


def _wrap_indices(words: list[str], max_chars: int = 26) -> list[list[int]]:
    """Group word indices into at most 2 balanced display lines for 9:16 mobile viewing."""
    if not words:
        return []
    lines: list[list[int]] = [[]]
    current_len = 0
    for idx, word in enumerate(words):
        added = len(word) + (1 if lines[-1] else 0)
        if lines[-1] and current_len + added > max_chars and len(lines) < 2:
            lines.append([idx])
            current_len = len(word)
        else:
            lines[-1].append(idx)
            current_len += added
    return lines


def _format_lines_with_active(words: list[str], lines: list[list[int]], active_idx: int, accent_bgr: str) -> str:
    rendered_lines = []
    for line_indices in lines:
        parts = []
        for idx in line_indices:
            token = words[idx]
            if idx == active_idx:
                parts.append(f'{{\\1c{accent_bgr}\\fscx108\\fscy108}}{token}{{\\r}}')
            else:
                parts.append(token)
        rendered_lines.append(' '.join(parts))
    return r'\N'.join(rendered_lines)


def build_ass(cues: list[Cue], style_name: str = 'highlight', *, layout: str = 'face'):
    """Build a 1080x1920 pysubs2 SSAFile with dynamic Shorts/TikTok styling."""
    if style_name not in ASS_PRESETS:
        valid = ', '.join(sorted(ASS_PRESETS))
        raise ValueError(f'Estilo ASS no soportado: {style_name}. Usa: {valid}.')
    try:
        import pysubs2
    except ImportError as error:
        raise ValueError('Instala requirements.txt para generar subtítulos ASS con pysubs2.') from error

    preset = ASS_PRESETS[style_name]
    subs = pysubs2.SSAFile()
    subs.info['PlayResX'] = '1080'
    subs.info['PlayResY'] = '1920'
    subs.info['WrapStyle'] = '2'
    subs.info['ScaledBorderAndShadow'] = 'yes'

    margin_v = 380 if layout == 'split-cam' else 320
    style = pysubs2.SSAStyle(
        fontname=preset.fontname,
        fontsize=preset.fontsize,
        primarycolor=pysubs2.Color(*preset.primary_rgb, 0),
        outlinecolor=pysubs2.Color(*preset.outline_rgb, 0),
        backcolor=pysubs2.Color(0, 0, 0, 120),
        bold=True,
        outline=preset.outline,
        shadow=preset.shadow,
        alignment=pysubs2.Alignment.BOTTOM_CENTER,
        marginl=72,
        marginr=72,
        marginv=margin_v,
    )
    subs.styles['Default'] = style

    for cue in cues:
        words = estimate_word_timings(cue)
        if not words:
            continue
        display_tokens = [w.text.upper() if preset.uppercase else w.text for w in words]
        lines = _wrap_indices(display_tokens, max_chars=24 if preset.uppercase else 28)

        if not preset.word_highlight:
            text = r'\N'.join(' '.join(display_tokens[idx] for idx in line) for line in lines)
            start_ms = max(0, round(cue.start * 1000))
            end_ms = max(start_ms + 80, round(cue.end * 1000))
            subs.events.append(pysubs2.SSAEvent(start=start_ms, end=end_ms, text=text, style='Default'))
            continue

        cue_end_ms = max(80, round(cue.end * 1000))
        for index, word in enumerate(words):
            start_ms = max(0, round(word.start * 1000))
            next_start_ms = round(words[index + 1].start * 1000) if index + 1 < len(words) else cue_end_ms
            end_ms = max(start_ms + 40, min(cue_end_ms, max(round(word.end * 1000), next_start_ms)))
            text = _format_lines_with_active(display_tokens, lines, index, preset.accent_ass_bgr)
            subs.events.append(pysubs2.SSAEvent(start=start_ms, end=end_ms, text=text, style='Default'))

    return subs


def write_ass(cues: list[Cue], destination: Path, style_name: str = 'highlight', *, layout: str = 'face') -> Path:
    """Write a styled .ass subtitle file for the given clip-relative cues."""
    subs = build_ass(cues, style_name, layout=layout)
    subs.save(str(destination), encoding='utf-8')
    return destination
