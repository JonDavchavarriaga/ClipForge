from dataclasses import dataclass
from pathlib import Path

from clipforge.pipeline.highlights import Cue, timestamp

SUPPORTED_LANGUAGES = {'es', 'en', 'pt', 'fr', 'de', 'ja', 'ko', 'auto'}


@dataclass(frozen=True)
class SubtitleLimits:
    max_chars_per_line: int = 36
    max_lines: int = 2
    max_cps: float = 22.0


def validate_language(code: str) -> str:
    cleaned = code.strip().lower()
    if cleaned not in SUPPORTED_LANGUAGES:
        allowed = ', '.join(sorted(SUPPORTED_LANGUAGES))
        raise ValueError(f'Idioma no soportado: {code}. Usa uno de: {allowed}.')
    return cleaned


def build_translation_bundle(
    cues: list[Cue],
    source_lang: str,
    target_lang: str,
    *,
    channel: str = '',
    context: str = '',
    limits: SubtitleLimits | None = None,
) -> dict:
    """Create a structured JSON packet so Gemini/Claude/Codex can translate and review captions safely."""
    src = validate_language(source_lang)
    dst = validate_language(target_lang)
    if dst == 'auto':
        raise ValueError('El idioma destino no puede ser "auto". Usa por ejemplo "es" o "en".')
    cfg = limits or SubtitleLimits()
    items = [
        {
            'id': index,
            'start': round(cue.start, 3),
            'end': round(cue.end, 3),
            'duration': round(cue.end - cue.start, 3),
            'source_text': cue.text,
            'translated_text': '',
        }
        for index, cue in enumerate(cues, 1)
    ]
    return {
        'schema_version': 1,
        'source_lang': src,
        'target_lang': dst,
        'channel': channel,
        'context': context.strip(),
        'limits': {
            'max_chars_per_line': cfg.max_chars_per_line,
            'max_lines': cfg.max_lines,
            'max_cps': cfg.max_cps,
        },
        'cues': items,
    }


def validate_translated_cues(cues: list[Cue], limits: SubtitleLimits | None = None) -> list[str]:
    """Return non-fatal readability warnings for vertical short-form subtitles."""
    cfg = limits or SubtitleLimits()
    warnings: list[str] = []
    for index, cue in enumerate(cues, 1):
        lines = [line.strip() for line in cue.text.splitlines() if line.strip()]
        if not lines:
            warnings.append(f'Cue #{index}: texto vacío.')
            continue
        if len(lines) > cfg.max_lines:
            warnings.append(f'Cue #{index}: tiene {len(lines)} líneas (máximo {cfg.max_lines}).')
        longest = max(len(line) for line in lines)
        if longest > cfg.max_chars_per_line:
            warnings.append(
                f'Cue #{index}: línea de {longest} caracteres (recomendado <= {cfg.max_chars_per_line}).'
            )
        duration = max(cue.end - cue.start, 0.08)
        cps = sum(len(line) for line in lines) / duration
        if cps > cfg.max_cps:
            warnings.append(f'Cue #{index}: velocidad de lectura alta ({cps:.1f} cps > {cfg.max_cps:.1f}).')
    return warnings


def apply_translated_bundle(original_cues: list[Cue], bundle: dict) -> tuple[list[Cue], list[str]]:
    """Map translated items back onto original Cue timestamps with strict 1-to-1 validation."""
    items = bundle.get('cues')
    if not isinstance(items, list) or len(items) != len(original_cues):
        raise ValueError(
            f'El paquete traducido tiene {len(items) if isinstance(items, list) else 0} bloques; '
            f'se esperaban {len(original_cues)}.'
        )
    by_id: dict[int, str] = {}
    for item in items:
        if not isinstance(item, dict) or 'id' not in item:
            raise ValueError('Cada bloque traducido debe ser un objeto con "id" y "translated_text".')
        cue_id = item['id']
        text = str(item.get('translated_text') or '').strip()
        if not text:
            raise ValueError(f'Falta "translated_text" en el bloque #{cue_id}.')
        by_id[int(cue_id)] = text

    translated: list[Cue] = []
    for index, original in enumerate(original_cues, 1):
        if index not in by_id:
            raise ValueError(f'Falta el bloque #{index} en el paquete traducido.')
        clean_text = by_id[index].replace('{', '｛').replace('}', '｝')
        translated.append(Cue(original.start, original.end, clean_text))

    raw_limits = bundle.get('limits') or {}
    limits = SubtitleLimits(
        max_chars_per_line=int(raw_limits.get('max_chars_per_line', 36)),
        max_lines=int(raw_limits.get('max_lines', 2)),
        max_cps=float(raw_limits.get('max_cps', 22.0)),
    )
    return translated, validate_translated_cues(translated, limits)


def write_cues_srt(cues: list[Cue], destination: Path) -> Path:
    """Write Cue list to a UTF-8 SRT file preserving multiline breaks."""
    blocks = []
    for index, cue in enumerate(cues, 1):
        lines = [line.strip() for line in cue.text.splitlines() if line.strip()]
        body = '\n'.join(lines).replace('{', '｛').replace('}', '｝')
        blocks.append(f'{index}\n{timestamp(cue.start)} --> {timestamp(cue.end)}\n{body}\n')
    destination.write_text('\n'.join(blocks), encoding='utf-8')
    return destination
