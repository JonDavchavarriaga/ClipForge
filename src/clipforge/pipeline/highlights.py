import bisect
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class WordTiming:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    text: str
    words: tuple[WordTiming, ...] = ()


@dataclass(frozen=True)
class Candidate:
    start: float
    end: float
    score: float | None
    reason: str


def seconds(value):
    parts = value.replace(',', '.').strip().split(':')
    if not 1 <= len(parts) <= 3 or any(not re.fullmatch(r'\d+(?:\.\d+)?', part) for part in parts):
        raise ValueError(f'Tiempo inválido: {value}')
    return sum(float(part) * 60 ** index for index, part in enumerate(reversed(parts)))


def read_srt(path):
    cues = []
    for block in re.split(r'\n[ \t]*\n', path.read_text(encoding='utf-8-sig').strip()):
        lines = block.splitlines()
        for index, line in enumerate(lines):
            if '-->' not in line:
                continue
            edges = [part.split() for part in line.split('-->', 1)]
            if not all(edges):
                raise ValueError(f'Marca de tiempo SRT inválida: {line}')
            start, end = (seconds(edge[0]) for edge in edges)
            body = '\n'.join(part.strip() for part in lines[index + 1:] if part.strip())
            if end > start and body:
                cues.append(Cue(start, end, body))
            break
    if not cues:
        raise ValueError('El archivo SRT no contiene subtítulos válidos.')
    return sorted(cues, key=lambda cue: cue.start)


def timestamp(value):
    minutes, ms = divmod(round(value * 1000), 60_000)
    hours, minutes = divmod(minutes, 60)
    whole, ms = divmod(ms, 1000)
    return f'{hours:02}:{minutes:02}:{whole:02},{ms:03}'


def clip_cues(cues, start, end):
    """Slice cues and optional word-level timings into clip-relative [0, end - start] timestamps."""
    rebased = []
    for cue in cues:
        left = max(cue.start, start) - start
        right = min(cue.end, end) - start
        if right - left < 0.08:
            continue
        words = []
        for word in cue.words:
            w_left = max(word.start, start) - start
            w_right = min(word.end, end) - start
            token = word.text.strip()
            if w_right > w_left and token:
                words.append(WordTiming(w_left, w_right, token))
        rebased.append(Cue(left, right, cue.text, tuple(words)))
    return rebased


def make_srt(cues, start, end):
    blocks = []
    for cue in clip_cues(cues, start, end):
        body = ' '.join(cue.text.split()).replace('{', '｛').replace('}', '｝')
        blocks.append(f'{len(blocks) + 1}\n{timestamp(cue.start)} --> {timestamp(cue.end)}\n{body}\n')
    return '\n'.join(blocks)


def detect_muted_ranges(energy, *, threshold: float = 0.04, min_silence_seconds: int = 8) -> list[tuple[float, float]]:
    """Detect sustained muted stretches (e.g. Twitch commercial ad breaks or DMCA muted audio).

    `energy` is sampled at 1 value per second in [0.0, 1.0]. Any contiguous run of
    `min_silence_seconds` or more below `threshold` is flagged as a muted/commercial block.
    """
    if not energy:
        return []
    ranges: list[tuple[float, float]] = []
    run_start: int | None = None
    for sec, level in enumerate(energy):
        if level <= threshold:
            if run_start is None:
                run_start = sec
        else:
            if run_start is not None and (sec - run_start) >= min_silence_seconds:
                ranges.append((float(run_start), float(sec)))
            run_start = None
    if run_start is not None and (len(energy) - run_start) >= min_silence_seconds:
        ranges.append((float(run_start), float(len(energy))))
    return ranges


def muted_overlap_ratio(start: float, end: float, muted_ranges: list[tuple[float, float]]) -> float:
    length = max(end - start, 0.01)
    overlap = 0.0
    for m_start, m_end in muted_ranges:
        left = max(start, m_start)
        right = min(end, m_end)
        if right > left:
            overlap += right - left
    return overlap / length


def auto_clip_count(
    duration: float,
    muted_ranges: list[tuple[float, float]] | None = None,
    minimum: float = 20,
    maximum: float = 55,
) -> int:
    """Automatically choose how many vertical clips to extract based on usable stream duration."""
    muted_total = sum(max(0.0, end - start) for start, end in (muted_ranges or []))
    usable = max(0.0, duration - muted_total)
    if usable < minimum * 2:
        return 1
    usable_minutes = usable / 60.0
    if usable_minutes <= 3.5:
        return 1
    if usable_minutes <= 8.0:
        return 2
    if usable_minutes <= 15.0:
        return 3
    # Roughly 1 strong clip every 5-6 minutes of real stream, capped cleanly at 18
    estimated = round(usable_minutes / 5.5)
    max_possible = max(1, int(usable // max(minimum, 15)))
    return max(2, min(18, estimated, max_possible))


def suggest(cues, energy, duration, count, minimum, maximum, *, muted_ranges=None):
    cues = [cue for cue in cues if 0 <= cue.start < duration and cue.end > cue.start]
    cues.sort(key=lambda cue: cue.start)
    if not cues and max(energy, default=0) <= 0.02:
        raise ValueError('No se detectó voz ni energía útil. Indica momentos con --range INICIO,FIN.')
    if muted_ranges is None:
        muted_ranges = detect_muted_ranges(energy)

    auto_mode = count is None or count <= 0
    target_count = auto_clip_count(duration, muted_ranges, minimum, maximum) if auto_mode else count

    if duration < minimum:
        return [Candidate(0, duration, None, 'La fuente dura menos que el mínimo configurado')]
    lengths = list(dict.fromkeys((minimum, (minimum + maximum) / 2, maximum)))
    cue_starts = [cue.start for cue in cues]
    cue_ends = sorted(min(duration, cue.end) for cue in cues)
    cum_energy = [0.0]
    for sample in energy:
        cum_energy.append(cum_energy[-1] + sample)

    # Bound start-point density on long 1h-2h streams so candidate scoring stays fast (<0.5s)
    min_step = 3.0 if duration <= 900 else (5.0 if duration <= 3600 else 8.0)
    starts = {0.0, max(0.0, duration - minimum)}
    if cues:
        last = -min_step
        for cue in cues:
            start = max(0.0, cue.start - 0.2)
            if start - last >= min_step and start + minimum <= duration:
                starts.add(start)
                last = start
    else:
        starts.update(range(0, int(duration - minimum) + 1, int(min_step)))

    candidates = []
    for start in sorted(starts):
        for length in lengths:
            end = min(duration, start + length)
            if cues:
                index = bisect.bisect_left(cue_ends, end)
                nearby = cue_ends[max(0, index - 1):index + 1]
                valid = [edge for edge in nearby if minimum <= edge - start <= maximum and abs(edge - end) <= 2]
                if valid:
                    end = min(valid, key=lambda edge: abs(edge - end))

            # Skip clips that fall inside Twitch commercial / muted sections (>15% muted)
            if muted_ranges and muted_overlap_ratio(start, end, muted_ranges) > 0.15:
                continue

            left = bisect.bisect_left(cue_starts, start)
            right = bisect.bisect_left(cue_starts, end)
            items = cues[left:right]
            length = end - start
            activity = min(1, sum(min(cue.end, end) - cue.start for cue in items) / length)
            level_start = min(len(energy), int(start))
            level_end = min(len(energy), max(level_start, round(end)))
            volume = (cum_energy[level_end] - cum_energy[level_start]) / max(1, level_end - level_start)
            hook = 0.0
            if items:
                first = items[0]
                if first.start - start <= 2:
                    hook = 0.12
                    if re.search(
                        r'[?!¿¡]|\b(mira|resulta|clave|error|por qué|cómo|ojo|wait|watch|bro|insane|no way)\b',
                        first.text,
                        re.I,
                    ):
                        hook += 0.13
            score = 0.50 * activity + 0.25 * volume + hook if cues else volume
            reason = 'Densidad de voz, energía y apertura' if cues else 'Energía de audio'
            candidates.append(Candidate(start, end, score, reason))

    if not candidates:
        raise ValueError('No se encontraron momentos activos fuera de pausas mudas/comerciales. Usa --range INICIO,FIN.')

    selected = []
    min_auto_score = 0.20 if (auto_mode and cues) else 0.0
    for candidate in sorted(candidates, key=lambda item: (-item.score, item.start, item.end)):
        if selected and auto_mode and candidate.score is not None and candidate.score < min_auto_score:
            break
        if any(min(candidate.end, item.end) > max(candidate.start, item.start) for item in selected):
            continue
        selected.append(candidate)
        if len(selected) == target_count:
            break
    return selected
