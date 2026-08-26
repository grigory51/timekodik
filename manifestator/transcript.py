from __future__ import annotations

import math
import re
from pathlib import Path

import click

from .common import require_file
from .config import EpisodeConfig
from .models import (
    ChaptersDocument,
    TranscriptDocument,
    TranscriptEdits,
    TranscriptSegment,
    TranscribedDocument,
)


MAX_TRANSCRIPT_BLOCK_SECONDS = 20.0
MAX_TRANSCRIPT_BLOCK_CHARACTERS = 360
MAX_TRANSCRIPT_GAP_SECONDS = 1.5


def format_timestamp(seconds: float) -> str:
    rounded = max(0, math.floor(seconds))
    return f"{rounded // 3600:02d}:{(rounded % 3600) // 60:02d}:{rounded % 60:02d}"


def write_transcript_markdown(
    config: EpisodeConfig,
    transcript: TranscriptDocument,
    output: Path,
) -> None:
    names = {track.speaker: track.name for track in config.tracks}
    lines = [f"# {config.episode_id}", ""]
    for segment in transcript.segments:
        lines.append(
            f"[{format_timestamp(segment.startSeconds)}] **{names.get(segment.speaker, segment.speaker)}:** "
            f"{segment.text}"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n\n".join(lines) + "\n", encoding="utf-8")


def load_transcript(path: Path) -> TranscriptDocument:
    require_file(path, "Транскрипт")
    return TranscriptDocument.model_validate_json(path.read_text(encoding="utf-8"))


def load_transcribed_document(path: Path) -> TranscribedDocument:
    require_file(path, "Транскрипт")
    return TranscribedDocument.model_validate_json(path.read_text(encoding="utf-8"))


def trim_transcript(
    transcript: TranscriptDocument,
    start_seconds: float,
) -> TranscriptDocument:
    counters: dict[str, int] = {}
    segments: list[TranscriptSegment] = []
    for segment in transcript.segments:
        if segment.startSeconds < start_seconds:
            continue
        number = counters.get(segment.speaker, 0) + 1
        counters[segment.speaker] = number
        segments.append(
            segment.model_copy(
                update={
                    "id": f"{segment.speaker}-{number:05d}",
                    "startSeconds": round(segment.startSeconds - start_seconds, 3),
                    "endSeconds": round(segment.endSeconds - start_seconds, 3),
                }
            )
        )
    return TranscriptDocument(
        episodeId=transcript.episodeId,
        segments=segments,
    )


def apply_partial_transcript_edits(
    segments: list[TranscriptSegment],
    edits: TranscriptEdits,
) -> list[TranscriptSegment]:
    source_ids = {segment.id for segment in segments}
    edit_ids = [edit.id for edit in edits.segments]
    duplicates = sorted(
        segment_id for segment_id in set(edit_ids) if edit_ids.count(segment_id) > 1
    )
    unknown = sorted(set(edit_ids) - source_ids)
    if duplicates or unknown:
        details = []
        if unknown:
            details.append(f"неизвестные: {', '.join(unknown)}")
        if duplicates:
            details.append(f"повторяющиеся: {', '.join(duplicates)}")
        raise click.ClickException(
            f"Codex вернул некорректные id ({'; '.join(details)})"
        )
    if any(not edit.text.strip() for edit in edits.segments):
        raise click.ClickException("Codex вернул пустой текст сегмента")
    replacements = {edit.id: edit.text.strip() for edit in edits.segments}
    return [
        segment.model_copy(update={"text": replacements[segment.id]})
        if segment.id in replacements
        else segment
        for segment in segments
    ]


def merge_sentence_fragments(
    segments: list[TranscriptSegment],
) -> list[TranscriptSegment]:
    merged: list[TranscriptSegment] = []
    pending: TranscriptSegment | None = None
    for segment in segments:
        if pending is None:
            pending = segment
            continue

        pending_text = pending.text.rstrip('»”"\')]}')
        if (
            pending.speaker != segment.speaker
            or pending_text.endswith((".", "!", "?", "…"))
        ):
            merged.append(pending)
            pending = segment
            continue

        sentence_end = re.search(r"(?<=\w)[.!?…][»”\"')\]]*", segment.text)
        if sentence_end is None:
            pending = pending.model_copy(
                update={
                    "endSeconds": segment.endSeconds,
                    "text": f"{pending.text.rstrip()} {segment.text.lstrip()}",
                }
            )
            continue

        boundary = round(
            segment.startSeconds
            + (segment.endSeconds - segment.startSeconds)
            * sentence_end.end()
            / len(segment.text),
            3,
        )
        merged.append(
            pending.model_copy(
                update={
                    "endSeconds": boundary,
                    "text": (
                        f"{pending.text.rstrip()} "
                        f"{segment.text[:sentence_end.end()].lstrip()}"
                    ),
                }
            )
        )
        remainder = segment.text[sentence_end.end() :].strip()
        pending = (
            segment.model_copy(
                update={"startSeconds": boundary, "text": remainder}
            )
            if remainder
            else None
        )
    if pending:
        merged.append(pending)

    grouped: list[TranscriptSegment] = []
    for segment in merged:
        previous = grouped[-1] if grouped else None
        if (
            previous is not None
            and previous.speaker == segment.speaker
            and segment.startSeconds - previous.endSeconds
            <= MAX_TRANSCRIPT_GAP_SECONDS
            and segment.endSeconds - previous.startSeconds
            <= MAX_TRANSCRIPT_BLOCK_SECONDS
            and len(previous.text) + len(segment.text) + 1
            <= MAX_TRANSCRIPT_BLOCK_CHARACTERS
        ):
            grouped[-1] = previous.model_copy(
                update={
                    "endSeconds": segment.endSeconds,
                    "text": f"{previous.text.rstrip()} {segment.text.lstrip()}",
                }
            )
        else:
            grouped.append(segment)
    return grouped


def load_chapters(config: EpisodeConfig) -> ChaptersDocument:
    require_file(config.chapters_output, "Главы")
    return ChaptersDocument.model_validate_json(
        config.chapters_output.read_text(encoding="utf-8")
    )
