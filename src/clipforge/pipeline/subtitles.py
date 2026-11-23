import re
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


# Safe text width: 1080px canvas minus side margins, using only ~80% of it so captions never touch
# the screen edges (TikTok/Shorts overlay buttons sit on the right side).
_SAFE_TEXT_WIDTH_PX = (1080 - 2 * 72) * 0.80
# Average glyph width (in em) of bold Arial for mixed-case vs. ALL-CAPS text.
_AVG_GLYPH_EM = {False: 0.47, True: 0.62}
MAX_WORDS_PER_PAGE = 7
_SENTENCE_END_RE = re.compile(r'[.!?…]["»”)]*$')
# Pages separated by less than this gap stay on screen continuously (avoids flicker).
_CONTINUOUS_GAP_S = 0.5
_LINGER_S = 0.25
_MIN_PAGE_S = 0.45


def max_line_chars(preset: AssPreset) -> int:
    """Estimate how many characters fit on one caption line for the preset font size."""
    return max(10, int(_SAFE_TEXT_WIDTH_PX / (preset.fontsize * _AVG_GLYPH_EM[preset.uppercase])))


def balanced_lines(tokens: list[str], max_chars: int) -> list[list[int]] | None:
    """Split tokens into 1 or 2 balanced lines that each fit `max_chars`.

    Returns word-index groups, or None if the tokens cannot fit in 2 lines.
    A single (very long) word is always accepted on its own line.
    """
    if not tokens:
        return []
    if len(' '.join(tokens)) <= max_chars or len(tokens) == 1:
        return [list(range(len(tokens)))]
    best: tuple[int, int, int] | None = None
    for split in range(1, len(tokens)):
        top = len(' '.join(tokens[:split]))
        bottom = len(' '.join(tokens[split:]))
        if top > max_chars or bottom > max_chars:
            continue
        # Minimize the longest line; on ties prefer a slightly shorter top line (pyramid shape).
        key = (max(top, bottom), 0 if top <= bottom else 1, split)
        if best is None or key < best:
            best = key
    if best is None:
        return None
    split = best[2]
    return [list(range(split)), list(range(split, len(tokens)))]


def paginate_words(tokens: list[str], max_chars: int, max_words: int = MAX_WORDS_PER_PAGE) -> list[list[int]]:
    """Greedily cut a cue's words into on-screen pages that each fit in 2 balanced lines."""
    pages: list[list[int]] = []
    current: list[int] = []
    for idx, token in enumerate(tokens):
        trial = current + [idx]
        fits = len(trial) <= max_words and balanced_lines([tokens[i] for i in trial], max_chars) is not None
        if current and not fits:
            pages.append(current)
            current = [idx]
        else:
            current = trial
        if _SENTENCE_END_RE.search(token) and len(current) >= 2 and idx < len(tokens) - 1:
            pages.append(current)
            current = []
    if current:
        pages.append(current)
    # Avoid a lonely last word: borrow one word from the previous page when it still fits.
    if len(pages) >= 2 and len(pages[-1]) == 1 and len(pages[-2]) >= 3:
        candidate = [pages[-2][-1], *pages[-1]]
        if balanced_lines([tokens[i] for i in candidate], max_chars) is not None:
            pages[-2] = pages[-2][:-1]
            pages[-1] = candidate
    return pages


@dataclass
class _Page:
    words: list[WordTiming]
    tokens: list[str]
    lines: list[list[int]]
    start: float
    end: float


def _build_pages(cues: list[Cue], preset: AssPreset) -> list[_Page]:
    limit = max_line_chars(preset)
    pages: list[_Page] = []
    for cue in cues:
        words = estimate_word_timings(cue)
        if not words:
            continue
        tokens = [w.text.upper() if preset.uppercase else w.text for w in words]
        for group in paginate_words(tokens, limit):
            page_words = [words[i] for i in group]
            page_tokens = [tokens[i] for i in group]
            lines = balanced_lines(page_tokens, limit) or [list(range(len(page_tokens)))]
            pages.append(_Page(page_words, page_tokens, lines, page_words[0].start, page_words[-1].end))
    pages.sort(key=lambda page: page.start)
    # Smooth timing: fill tiny gaps, linger briefly after speech, guarantee a readable minimum,
    # and never overlap the next page.
    for index, page in enumerate(pages):
        next_start = pages[index + 1].start if index + 1 < len(pages) else None
        end = page.end
        if next_start is not None and next_start - end < _CONTINUOUS_GAP_S:
            end = next_start
        else:
            end = end + _LINGER_S
        end = max(end, page.start + _MIN_PAGE_S)
        if next_start is not None:
            end = min(end, next_start)
        page.end = max(end, page.start + 0.08)
    return pages


def _format_lines_with_active(words: list[str], lines: list[list[int]], active_idx: int, accent_bgr: str) -> str:
    # Color-only highlight: scaling the active word would reflow the centered line and make it jitter.
    rendered_lines = []
    for line_indices in lines:
        parts = []
        for idx in line_indices:
            token = words[idx]
            if idx == active_idx:
                parts.append(f'{{\\1c{accent_bgr}}}{token}{{\\r}}')
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
    y_pct: int | None = None,
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

    if y_pct is not None:
        clamped_y = max(10, min(92, int(y_pct)))
        alignment = pysubs2.Alignment.BOTTOM_CENTER
        margin_v = max(80, min(1720, round(1920 * (100 - clamped_y) / 100.0)))
    elif position == 'top':
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

    def to_ms(value: float) -> int:
        return max(0, round(value * 1000) + shift)

    for page in _build_pages(cues, preset):
        page_start_ms = to_ms(page.start)
        page_end_ms = max(page_start_ms + 80, to_ms(page.end))

        if not preset.word_highlight:
            text = r'\N'.join(' '.join(page.tokens[idx] for idx in line) for line in page.lines)
            subs.events.append(pysubs2.SSAEvent(start=page_start_ms, end=page_end_ms, text=text, style='Default'))
            continue

        # One event per word; each lasts until the next word starts so the page never blinks.
        for index in range(len(page.words)):
            start_ms = page_start_ms if index == 0 else max(page_start_ms, to_ms(page.words[index].start))
            end_ms = to_ms(page.words[index + 1].start) if index + 1 < len(page.words) else page_end_ms
            end_ms = min(page_end_ms, max(start_ms + 40, end_ms))
            if end_ms <= start_ms:
                continue
            text = _format_lines_with_active(page.tokens, page.lines, index, preset.accent_ass_bgr)
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
    y_pct: int | None = None,
) -> Path:
    """Write a styled .ass subtitle file for the given clip-relative cues."""
    subs = build_ass(cues, style_name, layout=layout, offset_ms=offset_ms, position=position, y_pct=y_pct)
    subs.save(str(destination), encoding='utf-8')
    return destination


TITLE_SECONDS = 5.0
TITLE_FONT_SIZE = 66
TITLE_MAX_LINE_CHARS = 22


def build_title_ass(title: str, *, seconds: float = TITLE_SECONDS, clip_duration: float | None = None):
    """Hook title burned at the top of the 9:16 frame for the first seconds (white on a dark box)."""
    import pysubs2

    text = sanitize_ass_text(' '.join((title or '').split()))
    subs = pysubs2.SSAFile()
    subs.info['PlayResX'] = '1080'
    subs.info['PlayResY'] = '1920'
    subs.info['WrapStyle'] = '2'
    subs.info['ScaledBorderAndShadow'] = 'yes'
    subs.styles['Title'] = pysubs2.SSAStyle(
        fontname='Arial',
        fontsize=TITLE_FONT_SIZE,
        primarycolor=pysubs2.Color(255, 255, 255, 0),
        outlinecolor=pysubs2.Color(10, 10, 14, 60),   # opaque box colour (BorderStyle 3)
        backcolor=pysubs2.Color(0, 0, 0, 160),
        bold=True,
        borderstyle=3,
        outline=18.0,  # box padding
        shadow=0.0,
        alignment=pysubs2.Alignment.TOP_CENTER,
        marginl=90,
        marginr=90,
        marginv=150,  # below the platform's top UI bar
    )
    if not text:
        return subs
    tokens = text.split()
    lines = balanced_lines(tokens, TITLE_MAX_LINE_CHARS)
    if lines is None:
        # Too long for 2 lines: keep the first words that fit and add an ellipsis.
        kept: list[str] = []
        for token in tokens:
            if balanced_lines(kept + [token], TITLE_MAX_LINE_CHARS) is None:
                break
            kept.append(token)
        tokens = kept[:-1] + [kept[-1].rstrip('.,;:') + '…'] if kept else [text[:TITLE_MAX_LINE_CHARS]]
        lines = balanced_lines(tokens, TITLE_MAX_LINE_CHARS) or [list(range(len(tokens)))]
    body = r'\N'.join(' '.join(tokens[i] for i in line) for line in lines)
    end_s = seconds if clip_duration is None else max(0.5, min(seconds, clip_duration))
    subs.events.append(pysubs2.SSAEvent(start=0, end=round(end_s * 1000), text=r'{\fad(150,350)}' + body, style='Title'))
    return subs


def write_title_ass(title: str, destination: Path, *, seconds: float = TITLE_SECONDS,
                    clip_duration: float | None = None) -> Path | None:
    """Write title.ass next to the clip; returns None when there is no title to burn."""
    subs = build_title_ass(title, seconds=seconds, clip_duration=clip_duration)
    if not subs.events:
        destination.unlink(missing_ok=True)
        return None
    subs.save(str(destination), encoding='utf-8')
    return destination
