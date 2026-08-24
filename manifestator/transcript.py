from __future__ import annotations

import math
from pathlib import Path

import click

from .common import require_file
from .config import EpisodeConfig
from .models import (
    ChaptersDocument,
    TranscriptDocument,
    TranscriptEdits,
    TranscriptSegment,
)


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


def apply_transcript_edits(
    segments: list[TranscriptSegment],
    edits: TranscriptEdits,
) -> list[TranscriptSegment]:
    if [segment.id for segment in segments] != [edit.id for edit in edits.segments]:
        raise click.ClickException("Codex потерял или переставил сегменты транскрипта")
    if any(not edit.text.strip() for edit in edits.segments):
        raise click.ClickException("Codex вернул пустой текст сегмента")
    return [
        segment.model_copy(update={"text": edit.text.strip()})
        for segment, edit in zip(segments, edits.segments, strict=True)
    ]


def load_chapters(config: EpisodeConfig) -> ChaptersDocument:
    require_file(config.chapters_output, "Главы")
    return ChaptersDocument.model_validate_json(
        config.chapters_output.read_text(encoding="utf-8")
    )
