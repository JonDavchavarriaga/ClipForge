from dataclasses import dataclass
from pathlib import Path

from clipforge.pipeline.highlights import Cue, WordTiming

CAPTION_STYLES = ('highlight', 'neon', 'fire', 'silver', 'bold', 'srt', 'none')
SUBTITLE_POSITIONS = ('bottom', 'middle', 'top')


@dataclass(frozen=True)
class AssPreset:
    id: str
    label: str
    description: str
    sample_before: str
    sample_active: str
    sample_after: str
    accent_hex: str
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
        id='highlight',
        label='Amarillo Viral',
        description='Palabra activa en amarillo brillante con efecto pop',
        sample_before='ESTO ES',
        sample_active='BRUTAL',
        sample_after='bro',
        accent_hex='#FACC15',
        fontname='Arial',
        fontsize=74,
        primary_rgb=(255, 255, 255),
        accent_ass_bgr='&H0015CCFA&',
        outline_rgb=(12, 12, 16),
        outline=6.0,
        shadow=2.5,
        word_highlight=True,
    ),
    'neon': AssPreset(
        id='neon',
        label='Cian Gaming',
        description='Mayúsculas intensas con resaltado cian eléctrico',
        sample_before='QUÉ',
        sample_active='JUGADA',
        sample_after='EN GTA',
        accent_hex='#22E1F8',
        fontname='Arial',
        fontsize=76,
        primary_rgb=(248, 250, 252),
        accent_ass_bgr='&H00F8E122&',
        outline_rgb=(10, 14, 24),
        outline=6.5,
        shadow=3.0,
        word_highlight=True,
        uppercase=True,
    ),
    'fire': AssPreset(
        id='fire',
        label='Impacto Naranja',
        description='Resaltado naranja vibrante de alto contraste',
        sample_before='NO',
        sample_active='PUEDE',
        sample_after='SER',
        accent_hex='#FB923C',
        fontname='Arial',
        fontsize=76,
        primary_rgb=(255, 255, 255),
        accent_ass_bgr='&H003C92FB&',
        outline_rgb=(12, 10, 10),
        outline=6.5,
        shadow=3.0,
        word_highlight=True,
        uppercase=True,
    ),
    'silver': AssPreset(
        id='silver',
        label='Gevik Plata',
        description='Estilo elegante blanco y plata metálica',
        sample_before='MIRA',
        sample_active='ESTE',
        sample_after='CLIP',
        accent_hex='#E2E8F0',
        fontname='Arial',
        fontsize=72,
        primary_rgb=(186, 198, 214),
        accent_ass_bgr='&H00FFFFFF&',
        outline_rgb=(8, 10, 14),
        outline=6.0,
        shadow=2.5,
        word_highlight=True,
        uppercase=True,
    ),
    'bold': AssPreset(
        id='bold',
        label='Blanco Limpio',
        description='Bloque limpio sin karaoke, máxima legibilidad',
        sample_before='Subtítulo',
        sample_active='limpio',
        sample_after='clásico',
        accent_hex='#FFFFFF',
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


def style_catalog() -> list[dict]:
    """Return UI metadata to render live visual preview cards for every subtitle style."""
    cards = [
        {
            'id': p.id,
            'label': p.label,
            'description': p.description,
            'sample_before': p.sample_before,
            'sample_active': p.sample_active,
            'sample_after': p.sample_after,
            'accent_hex': p.accent_hex,
            'uppercase': p.uppercase,
            'word_highlight': p.word_highlight,
        }
        for p in ASS_PRESETS.values()
    ]
    cards.append({
        'id': 'none',
        'label': 'Sin subtítulos',
        'description': 'Video limpio sin texto quemado',
        'sample_before': '',
        'sample_active': 'SIN TEXTO',
        'sample_after': '',
        'accent_hex': '#94A3B8',
        'uppercase': True,
        'word_highlight': False,
    })
    return cards


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


def build_ass(
    cues: list[Cue],
    style_name: str = 'highlight',
    *,
    layout: str = 'face',
    offset_ms: int = 0,
    position: str = 'bottom',
):
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

    if position == 'top':
        alignment = pysubs2.Alignment.TOP_CENTER
        margin_v = 680 if layout == 'split-cam' else 240
    elif position == 'middle':
        alignment = pysubs2.Alignment.MIDDLE_CENTER
        margin_v = 80
    else:
        alignment = pysubs2.Alignment.BOTTOM_CENTER
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
        alignment=alignment,
        marginl=72,
        marginr=72,
        marginv=margin_v,
    )
    subs.styles['Default'] = style

    shift = int(offset_ms)
    for cue in cues:
        words = estimate_word_timings(cue)
        if not words:
            continue
        display_tokens = [w.text.upper() if preset.uppercase else w.text for w in words]
        lines = _wrap_indices(display_tokens, max_chars=24 if preset.uppercase else 28)

        if not preset.word_highlight:
            text = r'\N'.join(' '.join(display_tokens[idx] for idx in line) for line in lines)
            start_ms = max(0, round(cue.start * 1000) + shift)
            end_ms = max(start_ms + 80, round(cue.end * 1000) + shift)
            subs.events.append(pysubs2.SSAEvent(start=start_ms, end=end_ms, text=text, style='Default'))
            continue

        cue_end_ms = max(80, round(cue.end * 1000) + shift)
        for index, word in enumerate(words):
            start_ms = max(0, round(word.start * 1000) + shift)
            next_start_ms = round(words[index + 1].start * 1000) + shift if index + 1 < len(words) else cue_end_ms
            end_ms = max(start_ms + 40, min(cue_end_ms, max(round(word.end * 1000) + shift, next_start_ms)))
            text = _format_lines_with_active(display_tokens, lines, index, preset.accent_ass_bgr)
            subs.events.append(pysubs2.SSAEvent(start=start_ms, end=end_ms, text=text, style='Default'))

    return subs


def write_ass(
    cues: list[Cue],
    destination: Path,
    style_name: str = 'highlight',
    *,
    layout: str = 'face',
    offset_ms: int = 0,
    position: str = 'bottom',
) -> Path:
    """Write a styled .ass subtitle file for the given clip-relative cues."""
    subs = build_ass(cues, style_name, layout=layout, offset_ms=offset_ms, position=position)
    subs.save(str(destination), encoding='utf-8')
    return destination
