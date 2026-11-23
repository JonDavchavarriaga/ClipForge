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


_SENTENCE_END = re.compile(r'[.!?…]["»”)]*$')
_SOFT_BREAK = re.compile(r'[,;:]["»”)]*$')


def group_words_into_cues(
    words: list[WordTiming],
    *,
    max_words: int = 5,
    max_chars: int = 30,
    max_seconds: float = 2.8,
    pause_seconds: float = 0.7,
) -> list[Cue]:
    """Group word timings into short, readable cues that respect sentence punctuation and pauses.

    Rules (Shorts/TikTok readability):
    - Close a cue after sentence punctuation (. ! ? …) and after commas once it has ≥3 words.
    - Never let a cue exceed `max_words`, `max_chars` or `max_seconds`.
    - A silence longer than `pause_seconds` always starts a new cue.
    - A trailing 1-word fragment that would flash on screen (<0.6s) is merged into the previous cue.
    """
    clean = [WordTiming(w.start, max(w.end, w.start + 0.02), w.text.strip()) for w in words if w.text.strip()]
    groups: list[list[WordTiming]] = []
    current: list[WordTiming] = []

    def flush():
        if current:
            groups.append(list(current))
            current.clear()

    for word in clean:
        if current:
            chars = len(' '.join(w.text for w in current)) + 1 + len(word.text)
            if (
                len(current) >= max_words
                or chars > max_chars
                or word.end - current[0].start > max_seconds
                or word.start - current[-1].end > pause_seconds
            ):
                flush()
        current.append(word)
        if _SENTENCE_END.search(word.text) or (_SOFT_BREAK.search(word.text) and len(current) >= 3):
            flush()
    flush()

    merged: list[list[WordTiming]] = []
    for group in groups:
        if merged and len(group) == 1:
            prev = merged[-1]
            short = group[0].end - group[0].start < 0.6
            close = group[0].start - prev[-1].end < 0.4
            fits = len(' '.join(w.text for w in prev)) + 1 + len(group[0].text) <= max_chars + 8
            if short and close and fits and not _SENTENCE_END.search(prev[-1].text):
                prev.extend(group)
                continue
        merged.append(group)

    return [
        Cue(group[0].start, group[-1].end, ' '.join(w.text for w in group), tuple(group))
        for group in merged
    ]


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


def ends_sentence(text: str) -> bool:
    return bool(_SENTENCE_END.search(text.strip()))


def snap_clip_bounds(start: float, end: float, cues: list[Cue], duration: float, minimum: float, maximum: float,
                     *, reach: float = 6.0) -> tuple[float, float]:
    """Move clip edges so a good clip never starts or ends in the middle of a phrase.

    - End: prefer the end of a sentence (. ! ? …) within `reach` seconds, else a natural pause,
      while keeping the clip length within [minimum, maximum]. A short tail is kept after the last word.
    - Never cut inside a caption: an edge that falls inside a cue moves to that cue's boundary.
    """
    if not cues:
        return start, end

    def pick(options):
        valid = [e for e in options if minimum <= e - start <= maximum and abs(e - end) <= reach]
        return min(valid, key=lambda e: (abs(e - end), -e)) if valid else None

    sentence_ends = [c.end for c in cues if ends_sentence(c.text)]
    pause_ends = [c.end for i, c in enumerate(cues) if i + 1 == len(cues) or cues[i + 1].start - c.end >= 0.6]
    new_end = pick(sentence_ends)
    if new_end is None:
        new_end = pick(pause_ends)
    if new_end is not None:
        end = min(duration, new_end + 0.35)

    for cue in cues:
        if cue.start < end - 0.05 < cue.end:
            if cue.end - start <= maximum:
                end = min(duration, cue.end + 0.35)
            elif cue.start - start >= minimum:
                end = max(start + 0.5, cue.start - 0.05)
            break
    for cue in cues:
        if cue.start < start < cue.end - 0.05:
            start = max(0.0, cue.start - 0.25)
            break
    return start, end


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
    if duration <= maximum + 1:
        # Short source (e.g. a live auto-clip): the whole clip is a candidate, so a good moment isn't chopped.
        lengths.append(duration)
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

            start_snapped, end = snap_clip_bounds(start, end, cues, duration, minimum, maximum)

            # Skip clips that fall inside Twitch commercial / muted sections (>15% muted)
            if muted_ranges and muted_overlap_ratio(start, end, muted_ranges) > 0.15:
                continue

            start_for_score = start
            start = start_snapped
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
            # Longer clips keep the context and the payoff: reward length and a clean sentence ending.
            span = max(1.0, maximum - minimum)
            length_bonus = 0.15 * max(0.0, min(1.0, (length - minimum) / span))
            clean_end = 0.06 if items and ends_sentence(items[-1].text) and end - items[-1].end <= 1.0 else 0.0
            score = (0.50 * activity + 0.25 * volume + hook + length_bonus + clean_end) if cues else volume + length_bonus
            reason = 'Voz, energía, apertura, final de frase y duración' if cues else 'Energía de audio'
            candidates.append(Candidate(start, end, score, reason))
            start = start_for_score

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
