from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from statistics import median
from urllib.parse import urlparse
from urllib.request import urlopen

import click
import numpy as np
from numpy.typing import NDArray
from tqdm import tqdm

from .common import ROOT, require_file, run
from .config import EpisodeConfig
from .models import (
    AlignmentAnchor,
    AlignmentDocument,
    TranscriptDocument,
    TranscriptSegment,
)


TOKEN_PATTERN = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)
SENTENCE_PATTERN = re.compile(r".+?(?:[.!?…]+(?=\s|$)|$)", re.DOTALL)
ACOUSTIC_SAMPLE_RATE = 800
ACOUSTIC_TEMPLATE_SECONDS = 8.0
ACOUSTIC_SEARCH_RADIUS_SECONDS = 35.0
ACOUSTIC_MIN_CONFIDENCE = 0.5
SENTENCE_MATCH_WINDOW_SECONDS = 15.0


@dataclass(frozen=True)
class TokenReference:
    value: str
    segment_index: int
    seconds: float


@dataclass(frozen=True)
class SpeakerCandidate:
    speaker: str
    tokens: list[str]
    final_seconds: float


def final_audio_path(config: EpisodeConfig, force: bool) -> Path:
    """Вернуть локальный путь, скачав remote master при необходимости."""
    if not config.final_audio:
        raise click.ClickException("Финальное аудио не задано")
    parsed = urlparse(config.final_audio)
    if parsed.scheme not in {"http", "https"}:
        path = Path(config.final_audio)
        require_file(path, "Финальное аудио")
        return path

    suffix = Path(parsed.path).suffix or ".audio"
    path = ROOT / "build" / "final-audio" / f"{config.episode_id}{suffix}"
    if path.is_file() and not force:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with urlopen(config.final_audio) as response, temporary.open("wb") as output:
        total = int(response.headers.get("Content-Length", 0)) or None
        with tqdm(
            total=total,
            desc="Скачивание финального аудио",
            unit="B",
            unit_scale=True,
            dynamic_ncols=True,
        ) as progress:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
                progress.update(len(chunk))
    temporary.replace(path)
    return path


def transcript_tokens(segments: list[TranscriptSegment]) -> list[TokenReference]:
    references: list[TokenReference] = []
    for segment_index, segment in enumerate(segments):
        tokens = [match.group().casefold() for match in TOKEN_PATTERN.finditer(segment.text)]
        duration = segment.endSeconds - segment.startSeconds
        for token_index, token in enumerate(tokens):
            references.append(
                TokenReference(
                    value=token,
                    segment_index=segment_index,
                    seconds=(
                        segment.startSeconds
                        + duration * (token_index + 0.5) / len(tokens)
                    ),
                )
            )
    return references


def align_transcripts(
    source: TranscriptDocument,
    final: TranscriptDocument,
) -> AlignmentDocument:
    """Перенести ролевые сегменты на временную шкалу финального монтажа."""
    source_tokens = transcript_tokens(source.segments)
    final_tokens = transcript_tokens(final.segments)
    matcher = SequenceMatcher(
        None,
        [token.value for token in source_tokens],
        [token.value for token in final_tokens],
    )
    matches: dict[int, list[tuple[float, float]]] = {}
    for block in matcher.get_matching_blocks():
        if block.size == 0:
            continue
        if block.size == 1 and len(source_tokens[block.a].value) < 6:
            continue
        for offset in range(block.size):
            source_token = source_tokens[block.a + offset]
            final_token = final_tokens[block.b + offset]
            matches.setdefault(source_token.segment_index, []).append(
                (source_token.seconds, final_token.seconds)
            )

    anchors: list[AlignmentAnchor] = []
    dropped_ids: list[str] = []
    source_token_counts: dict[int, int] = {}
    for token in source_tokens:
        source_token_counts[token.segment_index] = (
            source_token_counts.get(token.segment_index, 0) + 1
        )
    for segment_index, segment in enumerate(source.segments):
        segment_matches = matches.get(segment_index, [])
        if not segment_matches:
            dropped_ids.append(segment.id)
            continue
        offsets = [source_seconds - final_seconds for source_seconds, final_seconds in segment_matches]
        timeline_offset = median(offsets)
        confidence = min(
            1.0,
            len(segment_matches) / max(1, source_token_counts.get(segment_index, 0)),
        )
        anchors.append(
            AlignmentAnchor(
                sourceSeconds=round((segment.startSeconds + segment.endSeconds) / 2, 3),
                finalSeconds=round(
                    max(
                        0,
                        (segment.startSeconds + segment.endSeconds) / 2
                        - timeline_offset,
                    ),
                    3,
                ),
                confidence=round(confidence, 3),
            )
        )

    if len(anchors) < max(1, round(len(source.segments) * 0.6)):
        raise click.ClickException(
            "Финальный transcript сопоставился менее чем с 60% ролевых сегментов"
        )
    return AlignmentDocument(
        episodeId=source.episodeId,
        sourceDurationSeconds=max(
            (segment.endSeconds for segment in source.segments),
            default=0,
        ),
        finalDurationSeconds=max(
            (segment.endSeconds for segment in final.segments),
            default=0,
        ),
        anchors=sorted(anchors, key=lambda anchor: anchor.sourceSeconds),
        droppedSegmentIds=dropped_ids,
    )


def decode_alignment_audio(
    source: Path,
    output: Path,
    *,
    start_seconds: float = 0,
) -> NDArray[np.float32]:
    """Декодировать аудио в компактный PCM для акустического сопоставления."""
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    if start_seconds:
        command.extend(["-ss", f"{start_seconds:.3f}"])
    command.extend(
        [
            "-i",
            str(source),
            "-af",
            f"highpass=f=80,lowpass=f=350,aresample={ACOUSTIC_SAMPLE_RATE}",
            "-ac",
            "1",
            "-f",
            "f32le",
            str(output),
        ]
    )
    run(command, announce=False)
    return np.fromfile(output, dtype=np.float32)


def normalized_cross_correlation(
    search: NDArray[np.float32],
    template: NDArray[np.float32],
) -> NDArray[np.float64]:
    """Посчитать корреляцию шаблона для всех допустимых позиций окна."""
    centered_template = template.astype(np.float64) - float(template.mean())
    template_norm = float(np.linalg.norm(centered_template))
    if template_norm == 0 or search.size < template.size:
        return np.empty(0, dtype=np.float64)

    search64 = search.astype(np.float64)
    fft_size = 1 << (search.size + template.size - 2).bit_length()
    convolution = np.fft.irfft(
        np.fft.rfft(search64, fft_size)
        * np.fft.rfft(centered_template[::-1], fft_size),
        fft_size,
    )
    numerators = convolution[template.size - 1 : search.size]
    cumulative = np.concatenate(([0.0], np.cumsum(search64)))
    cumulative_squares = np.concatenate(([0.0], np.cumsum(search64 * search64)))
    sums = cumulative[template.size :] - cumulative[: -template.size]
    square_sums = (
        cumulative_squares[template.size :]
        - cumulative_squares[: -template.size]
    )
    variances = np.maximum(square_sums - sums * sums / template.size, 0.0)
    denominators = template_norm * np.sqrt(variances)
    correlations: NDArray[np.float64] = np.divide(
        numerators,
        denominators,
        out=np.zeros_like(numerators),
        where=denominators > 0,
    )
    return correlations


def refine_anchor(
    source: NDArray[np.float32],
    final: NDArray[np.float32],
    anchor: AlignmentAnchor,
) -> AlignmentAnchor | None:
    """Уточнить текстовый anchor по совпадению аудиосигнала."""
    half_template = round(ACOUSTIC_TEMPLATE_SECONDS * ACOUSTIC_SAMPLE_RATE / 2)
    source_center = round(anchor.sourceSeconds * ACOUSTIC_SAMPLE_RATE)
    source_start = source_center - half_template
    source_end = source_center + half_template
    if source_start < 0 or source_end > source.size:
        return None

    radius = round(ACOUSTIC_SEARCH_RADIUS_SECONDS * ACOUSTIC_SAMPLE_RATE)
    final_center = round(anchor.finalSeconds * ACOUSTIC_SAMPLE_RATE)
    search_start = max(0, final_center - radius - half_template)
    search_end = min(final.size, final_center + radius + half_template)
    correlations = normalized_cross_correlation(
        final[search_start:search_end],
        source[source_start:source_end],
    )
    if correlations.size == 0:
        return None
    best_index = int(np.argmax(correlations))
    confidence = float(correlations[best_index])
    if confidence < ACOUSTIC_MIN_CONFIDENCE:
        return None
    matched_center = (search_start + best_index + half_template) / ACOUSTIC_SAMPLE_RATE
    return AlignmentAnchor(
        sourceSeconds=anchor.sourceSeconds,
        finalSeconds=round(matched_center, 3),
        confidence=round(confidence, 3),
    )


def refine_alignment(
    source_audio: Path,
    final_audio: Path,
    alignment: AlignmentDocument,
    *,
    source_start_seconds: float = 0,
) -> AlignmentDocument:
    """Заменить приблизительные текстовые anchors акустически точными."""
    require_file(source_audio, "Исходный микс")
    require_file(final_audio, "Финальное аудио")
    with tempfile.TemporaryDirectory(prefix="timekodik-alignment-") as directory:
        work_dir = Path(directory)
        source = decode_alignment_audio(
            source_audio,
            work_dir / "source.f32",
            start_seconds=source_start_seconds,
        )
        final = decode_alignment_audio(final_audio, work_dir / "final.f32")
        anchors = [
            refined
            for anchor in tqdm(
                alignment.anchors,
                desc="Акустическое выравнивание",
                unit="якорь",
                dynamic_ncols=True,
            )
            if (refined := refine_anchor(source, final, anchor)) is not None
        ]

    if len(anchors) < max(3, round(len(alignment.anchors) * 0.5)):
        raise click.ClickException(
            "Акустически подтвердилось менее 50% временных якорей"
        )
    anchors.sort(key=lambda anchor: anchor.sourceSeconds)
    return alignment.model_copy(update={"anchors": anchors})


def attribute_final_transcript(
    final: TranscriptDocument,
    source: TranscriptDocument,
    alignment: AlignmentDocument,
) -> TranscriptDocument:
    """Назначить спикеров предложениям из финального master."""
    candidates: list[SpeakerCandidate] = []
    for segment in source.segments:
        sentences = [
            match.group().strip()
            for match in SENTENCE_PATTERN.finditer(segment.text)
            if match.group().strip()
        ]
        sentence_tokens = [
            [token.group().casefold() for token in TOKEN_PATTERN.finditer(sentence)]
            for sentence in sentences
        ]
        total_tokens = sum(len(tokens) for tokens in sentence_tokens)
        consumed_tokens = 0
        for sentence, tokens in zip(sentences, sentence_tokens, strict=True):
            if not tokens:
                continue
            source_seconds = segment.startSeconds + (
                segment.endSeconds - segment.startSeconds
            ) * (consumed_tokens + len(tokens) / 2) / total_tokens
            consumed_tokens += len(tokens)
            candidates.append(
                SpeakerCandidate(
                    speaker=segment.speaker,
                    tokens=tokens,
                    final_seconds=align_seconds(source_seconds, alignment),
                )
            )

    segments: list[TranscriptSegment] = []
    for segment in final.segments:
        tokens = [
            token.group().casefold()
            for token in TOKEN_PATTERN.finditer(segment.text)
        ]
        center = (segment.startSeconds + segment.endSeconds) / 2
        nearby = [
            candidate
            for candidate in candidates
            if abs(candidate.final_seconds - center) <= SENTENCE_MATCH_WINDOW_SECONDS
        ]
        candidate = max(
            nearby or candidates,
            key=lambda value: (
                SequenceMatcher(
                    None,
                    tokens,
                    value.tokens,
                    autojunk=False,
                ).ratio(),
                -abs(value.final_seconds - center),
            ),
        )
        segments.append(
            segment.model_copy(update={"speaker": candidate.speaker})
        )
    return TranscriptDocument(episodeId=final.episodeId, segments=segments)


def align_seconds(seconds: float, alignment: AlignmentDocument) -> float:
    """Применить offset ближайшего подтверждённого якоря."""
    if not alignment.anchors:
        raise click.ClickException("В alignment нет временных якорей")
    anchor = min(
        alignment.anchors,
        key=lambda candidate: abs(candidate.sourceSeconds - seconds),
    )
    aligned = seconds - (anchor.sourceSeconds - anchor.finalSeconds)
    return round(min(alignment.finalDurationSeconds, max(0.0, aligned)), 3)
