from __future__ import annotations

import hashlib
import json
import math
from bisect import bisect_right
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

import click
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

from .common import CONSOLE, ROOT, atomic_json, codex_json
from .config import EpisodeConfig
from .models import (
    GlossaryCandidate,
    GlossaryDocument,
    TranscriptDocument,
    TranscriptEdits,
    TranscriptEditsCheckpoint,
    TranscriptReview,
    TranscriptSegment,
)
from .transcript import (
    apply_partial_transcript_edits,
    load_transcript,
    write_transcript_markdown,
)


TRANSCRIPT_REVIEW_BATCH_SIZE = 100
CODEX_REVIEW_WORKERS = 2


def whisper_hypotheses(
    primary: list[TranscriptSegment],
    words: list[TranscriptSegment],
) -> dict[str, str]:
    """Разложить слова Whisper по временным интервалам основного transcript."""
    by_speaker: dict[str, list[TranscriptSegment]] = {}
    for segment in primary:
        by_speaker.setdefault(segment.speaker, []).append(segment)
    starts = {
        speaker: [segment.startSeconds for segment in segments]
        for speaker, segments in by_speaker.items()
    }
    pieces: dict[str, list[str]] = {segment.id: [] for segment in primary}
    for word in words:
        segments = by_speaker.get(word.speaker)
        if not segments:
            continue
        midpoint = (word.startSeconds + word.endSeconds) / 2
        index = bisect_right(starts[word.speaker], midpoint) - 1
        if index >= 0 and midpoint <= segments[index].endSeconds:
            pieces[segments[index].id].append(word.text)
    return {
        segment_id: "".join(segment_pieces).strip()
        for segment_id, segment_pieces in pieces.items()
    }


def load_glossary(path: Path) -> GlossaryDocument:
    if not path.is_file():
        return GlossaryDocument(candidates=[])
    return GlossaryDocument.model_validate_json(path.read_text(encoding="utf-8"))


def combined_glossary(config: EpisodeConfig) -> GlossaryDocument:
    """Объединить накопленный корпус с кандидатами текущего выпуска."""
    return merge_glossary(
        load_glossary(config.glossary_corpus_output),
        load_glossary(config.glossary_output).candidates,
    )


def merge_glossary(
    existing: GlossaryDocument,
    detected: list[GlossaryCandidate],
) -> GlossaryDocument:
    candidates: list[GlossaryCandidate] = []
    term_indexes: dict[str, int] = {}
    heard_indexes: dict[str, int] = {}
    status_priority = {"confirmed": 0, "ignored": 1, "pending": 2}
    source = sorted(
        [*existing.candidates, *detected],
        key=lambda candidate: status_priority[candidate.status],
    )
    for candidate in source:
        term_keys = {
            value.strip().casefold()
            for value in (candidate.suggested, candidate.preferred)
            if value and value.strip()
        }
        heard_variants = [
            variant.strip()
            for variant in candidate.heard.split("/")
            if variant.strip()
        ]
        index = next(
            (term_indexes[key] for key in term_keys if key in term_indexes),
            None,
        )
        if index is None:
            index = next(
                (
                    heard_indexes[variant.casefold()]
                    for variant in heard_variants
                    if variant.casefold() in heard_indexes
                ),
                None,
            )
        if index is None:
            index = len(candidates)
            candidates.append(candidate)
            for key in term_keys:
                term_indexes[key] = index
            for variant in heard_variants:
                heard_indexes[variant.casefold()] = index
            continue

        current = candidates[index]
        display_variants: dict[str, str] = {}
        for variant in [
            *(
                part.strip()
                for part in current.heard.split("/")
                if part.strip()
            ),
            *heard_variants,
        ]:
            display_variants.setdefault(variant.casefold(), variant)
        candidates[index] = current.model_copy(
            update={
                "heard": " / ".join(display_variants.values()),
                "segmentIds": list(
                    dict.fromkeys([*current.segmentIds, *candidate.segmentIds])
                ),
            }
        )
        for key in term_keys:
            term_indexes[key] = index
        for variant in heard_variants:
            heard_indexes[variant.casefold()] = index
    return GlossaryDocument(candidates=candidates)


def relevant_glossary(
    glossary: GlossaryDocument,
    texts: list[str],
) -> dict[str, str]:
    haystack = " ".join(texts).casefold()
    return {
        candidate.heard: candidate.preferred
        for candidate in glossary.candidates
        if candidate.preferred
        and any(
            variant.strip().casefold() in haystack
            for variant in candidate.heard.split("/")
            if variant.strip()
        )
    }


def review_transcript(
    config: EpisodeConfig,
    target: Literal["source", "final"],
    force: bool,
) -> tuple[TranscriptDocument, dict[str, str]]:
    """Выбрать лучшую из двух ASR-гипотез и собрать спорные термины."""
    if target == "source":
        primary_path = config.transcript_output
        whisper_path = config.whisper_transcript_output
        output_path = config.clean_transcript_output
        markdown_path = config.clean_transcript_markdown_output
    else:
        primary_path = config.final_transcript_output
        whisper_path = config.final_whisper_transcript_output
        output_path = config.final_clean_transcript_output
        markdown_path = config.final_clean_transcript_markdown_output
    primary = load_transcript(primary_path)
    whisper = load_transcript(whisper_path)
    hypotheses = whisper_hypotheses(primary.segments, whisper.segments)

    episode_glossary = load_glossary(config.glossary_output)
    glossary = combined_glossary(config)
    schema = "transcript-review.schema.json"
    checkpoint_dir = (
        ROOT / "build" / "clean-transcript" / config.episode_id / target
    )
    cleaned_segments: list[TranscriptSegment] = []
    detected_terms: list[GlossaryCandidate] = []
    total_batches = math.ceil(len(primary.segments) / TRANSCRIPT_REVIEW_BATCH_SIZE)

    def review_batch(
        offset: int,
    ) -> tuple[list[TranscriptSegment], list[GlossaryCandidate]]:
        segments = primary.segments[offset : offset + TRANSCRIPT_REVIEW_BATCH_SIZE]
        context = [
            *primary.segments[max(0, offset - 2) : offset],
            *primary.segments[
                offset + len(segments) : offset + len(segments) + 2
            ],
        ]
        payload = {
            "episodeId": primary.episodeId,
            "glossary": relevant_glossary(
                glossary,
                [
                    text
                    for segment in segments
                    for text in (segment.text, hypotheses[segment.id])
                ],
            ),
            "context": [segment.model_dump(mode="json") for segment in context],
            "segments": [
                {
                    "id": segment.id,
                    "speaker": segment.speaker,
                    "startSeconds": segment.startSeconds,
                    "endSeconds": segment.endSeconds,
                    "gigaam": segment.text,
                    "whisper": hypotheses[segment.id],
                }
                for segment in segments
            ],
        }
        payload_json = json.dumps(payload, ensure_ascii=False)
        checkpoint_path = checkpoint_dir / f"{offset:05d}.json"
        prompt = (
            "Ты редактор транскрипта русского технического подкаста. Для каждого сегмента даны "
            "независимые гипотезы GigaAM и Whisper. Выбери формулировку, лучше согласующуюся с обеими "
            "гипотезами, соседним контекстом и glossary. Если одна гипотеза содержит существующее, "
            "грамматически и по смыслу подходящее русское слово или выражение, а другая — бессмысленную "
            "фонетическую конструкцию или необоснованную аббревиатуру, предпочти естественный вариант. "
            "Не применяй эту эвристику к терминам из glossary и явно уместным техническим именам или "
            "названиям. Исправь явные ошибки распознавания и убери только бессодержательные междометия "
            "и случайные повторы. Сохрани смысл, факты, стиль, сомнения и ненормативную лексику. Не "
            "пересказывай, не сокращай содержательные мысли, не "
            "объединяй, не дроби и не переставляй сегменты. В segments верни только изменённые "
            "сегменты с исходным id; неизменённые не возвращай. Context дан только для чтения: "
            "никогда не возвращай id из context. Разрешены только id из массива segments, каждый "
            "не более одного раза. Если обе гипотезы сомнительны, не "
            "выдумывай исправление. "
            "Технические термины, имена и названия, в которых гипотезы расходятся или остаётся сомнение, "
            "добавь в glossaryCandidates: heard — вариант из ASR, suggested — предполагаемое написание, "
            "preferred всегда null, segmentIds — связанные сегменты, context — короткая исходная фраза. "
            "Не добавляй обычные слова. Верни только JSON по schema.\n\n"
            + payload_json
        )
        input_hash = hashlib.sha256(
            f"{config.codex_model}\0{prompt}".encode()
        ).hexdigest()
        checkpoint = (
            TranscriptEditsCheckpoint.model_validate_json(
                checkpoint_path.read_text(encoding="utf-8")
            )
            if checkpoint_path.is_file() and not force
            else None
        )
        if checkpoint and checkpoint.inputHash == input_hash:
            review = checkpoint
            edited_segments = apply_partial_transcript_edits(
                segments,
                TranscriptEdits(segments=review.segments),
            )
        else:
            request_prompt = prompt
            for attempt in range(2):
                review = TranscriptReview.model_validate_json(
                    codex_json(
                        request_prompt,
                        schema,
                        output_path.parent,
                        model=config.codex_model,
                        reasoning_effort="low",
                        service_tier=config.codex_service_tier,
                    )
                )
                try:
                    edited_segments = apply_partial_transcript_edits(
                        segments,
                        TranscriptEdits(segments=review.segments),
                    )
                    break
                except click.ClickException as error:
                    if attempt == 1:
                        raise
                    request_prompt = (
                        f"{prompt}\n\nПредыдущий ответ отклонён: {error}. "
                        "Исправь только структуру ответа и верни JSON заново."
                    )
        if not checkpoint or checkpoint.inputHash != input_hash:
            atomic_json(
                checkpoint_path,
                TranscriptEditsCheckpoint(
                    inputHash=input_hash,
                    segments=review.segments,
                    glossaryCandidates=review.glossaryCandidates,
                ),
            )
        return edited_segments, review.glossaryCandidates

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=CONSOLE,
    ) as progress:
        description = (
            f"{config.codex_model}: финальный master"
            if target == "final"
            else f"{config.codex_model}: исходные дорожки"
        )
        description += f" ({CODEX_REVIEW_WORKERS} параллельно)"
        task = progress.add_task(description, total=total_batches)
        offsets = range(0, len(primary.segments), TRANSCRIPT_REVIEW_BATCH_SIZE)
        executor = ThreadPoolExecutor(max_workers=CODEX_REVIEW_WORKERS)
        try:
            for edited_segments, glossary_candidates in executor.map(
                review_batch,
                offsets,
            ):
                cleaned_segments.extend(edited_segments)
                detected_terms.extend(glossary_candidates)
                progress.advance(task)
        finally:
            executor.shutdown(cancel_futures=True)

    cleaned = TranscriptDocument(
        episodeId=primary.episodeId,
        segments=cleaned_segments,
    )
    previous = load_transcript(output_path) if output_path.is_file() else None
    merged_glossary = merge_glossary(episode_glossary, detected_terms)
    if merged_glossary != episode_glossary:
        atomic_json(config.glossary_output, merged_glossary)
    if cleaned != previous:
        atomic_json(output_path, cleaned)
    if cleaned != previous or not markdown_path.is_file():
        write_transcript_markdown(config, cleaned, markdown_path)
    return cleaned, hypotheses
