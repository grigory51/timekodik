from __future__ import annotations

import shutil
import sys
from collections import Counter
from importlib.util import find_spec
from pathlib import Path
from platform import machine
from typing import Any
from urllib.parse import urlparse

import click

from .alignment import (
    align_seconds,
    align_transcripts,
    attribute_final_transcript,
    final_audio_path,
    refine_alignment,
)
from .audio import (
    ffmpeg_inputs,
    ffprobe_duration,
    mix_filter,
    parse_loudness,
    prepare_audio_chunks,
    prepare_chunks,
)
from .common import (
    CONSOLE,
    ROOT,
    atomic_json,
    codex_json,
    is_up_to_date,
    require_file,
    run,
)
from .config import EpisodeConfig
from .debug import debug_path, write_debug_stage
from .model import ensure_transcription_model
from .models import AlignmentDocument, ChaptersDocument, TranscribedDocument
from .review import combined_glossary, review_transcript
from .transcription import (
    transcribe_chunks,
    transcribe_whisper_chunks,
    whisper_model_identifier,
)
from .transcript import (
    load_chapters,
    load_transcribed_document,
    load_transcript,
    merge_sentence_fragments,
    write_transcript_markdown,
)


def doctor(config: EpisodeConfig) -> None:
    """Проверить локальные инструменты и исходные файлы."""
    for executable in ("ffmpeg", "ffprobe", "codex"):
        if shutil.which(executable) is None:
            raise click.ClickException(f"Не найден executable: {executable}")
    for track in config.tracks:
        require_file(config.source_dir / track.file, "Дорожка")
    if config.final_audio and urlparse(config.final_audio).scheme not in {"http", "https"}:
        require_file(Path(config.final_audio), "Финальное аудио")
    if find_spec("transcribe_cpp") is None:
        raise click.ClickException("Python binding transcribe_cpp не установлен")
    whisper_package = (
        "mlx_whisper"
        if sys.platform == "darwin" and machine() == "arm64"
        else "faster_whisper"
    )
    if find_spec(whisper_package) is None:
        raise click.ClickException(f"Python package {whisper_package} не установлен")
    ensure_transcription_model(config.transcription_model)
    write_debug_stage(
        config,
        "doctor",
        {
            "tools": {
                executable: shutil.which(executable) is not None
                for executable in ("ffmpeg", "ffprobe", "codex")
            },
            "tracks": len(config.tracks),
            "model": debug_path(config.transcription_model),
        },
    )
    click.echo("Исходные дорожки и модель найдены")


def mix(config: EpisodeConfig, force: bool) -> None:
    """Свести TeamSpeak-дорожки в нормализованный mono MP3."""
    if config.audio_output.exists() and not force:
        click.echo(f"Уже готово: {config.audio_output}")
        return
    for track in config.tracks:
        require_file(config.source_dir / track.file, "Дорожка")

    analysis = run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            *ffmpeg_inputs(config),
            "-filter_complex",
            mix_filter(config),
            "-map",
            "[mixed]",
            "-f",
            "null",
            "-",
        ],
        capture=True,
    )
    loudness = parse_loudness(analysis.stderr)
    config.audio_output.parent.mkdir(parents=True, exist_ok=True)
    temporary = config.audio_output.with_suffix(".tmp.mp3")
    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-y",
            *ffmpeg_inputs(config),
            "-filter_complex",
            mix_filter(config, loudness),
            "-map",
            "[mixed]",
            "-ac",
            "1",
            "-ar",
            "48000",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "128k",
            str(temporary),
        ]
    )
    output_bytes = temporary.stat().st_size
    output_duration = round(ffprobe_duration(temporary), 3)
    temporary.replace(config.audio_output)
    write_debug_stage(
        config,
        "mix",
        {
            "output": debug_path(config.audio_output),
            "bytes": output_bytes,
            "durationSeconds": output_duration,
            "loudness": loudness,
        },
    )
    click.echo(f"Готово: {config.audio_output}")


def transcribe(config: EpisodeConfig, force: bool) -> None:
    """Локально транскрибировать роли через GigaAM и Whisper."""
    gigaam_model = config.transcription_model.name
    whisper_model = whisper_model_identifier(config.secondary_transcription_model)
    if (
        config.transcript_output.exists()
        and config.whisper_transcript_output.exists()
        and not force
    ):
        load_transcribed_document(config.transcript_output)
        load_transcribed_document(config.whisper_transcript_output)
        click.echo(f"Уже готово: {config.transcript_output}")
        return
    require_file(config.transcription_model, "Модель транскрибации")
    chunks = prepare_chunks(config, force)
    if not chunks:
        raise click.ClickException("Не найдено фрагментов с речью")

    if config.transcript_output.exists() and not force:
        document = load_transcribed_document(config.transcript_output)
    else:
        segments = transcribe_chunks(
            config.transcription_model,
            chunks,
            ROOT / "build" / "stt" / f"{config.episode_id}.raw.jsonl",
            description="GigaAM",
            time_offset=config.content_start_seconds,
        )
        document = TranscribedDocument(
            episodeId=config.episode_id,
            segments=segments,
            model=gigaam_model,
        )
        atomic_json(config.transcript_output, document)
        write_transcript_markdown(config, document, config.transcript_markdown_output)
    if config.whisper_transcript_output.exists() and not force:
        whisper = load_transcribed_document(config.whisper_transcript_output)
    else:
        glossary = combined_glossary(config)
        hotwords = ", ".join(
            candidate.preferred
            for candidate in glossary.candidates
            if candidate.preferred
        ) or None
        whisper = TranscribedDocument(
            episodeId=config.episode_id,
            model=whisper_model,
            segments=transcribe_whisper_chunks(
                config.secondary_transcription_model,
                chunks,
                ROOT / "build" / "stt" / f"{config.episode_id}.whisper.raw.jsonl",
                description="Whisper",
                time_offset=config.content_start_seconds,
                hotwords=hotwords,
            ),
        )
        atomic_json(config.whisper_transcript_output, whisper)
    write_debug_stage(
        config,
        "transcribe",
        {
            "segments": len(document.segments),
            "whisperWords": len(whisper.segments),
            "speakers": dict(Counter(segment.speaker for segment in document.segments)),
            "json": debug_path(config.transcript_output),
            "whisperJson": debug_path(config.whisper_transcript_output),
            "markdown": debug_path(config.transcript_markdown_output),
        },
    )
    click.echo(f"Готово: {len(document.segments)} сегментов")


def clean_transcript(config: EpisodeConfig, force: bool) -> None:
    """Сопоставить две ASR-гипотезы и очистить текст через Codex."""
    transcript = load_transcript(config.transcript_output)
    cleaned, hypotheses = review_transcript(config, "source", force)
    changes = [
        {
            "id": before.id,
            "speaker": before.speaker,
            "startSeconds": before.startSeconds,
            "before": before.text,
            "whisper": hypotheses[before.id],
            "after": after.text,
        }
        for before, after in zip(
            transcript.segments,
            cleaned.segments,
            strict=True,
        )
        if before.text != after.text
    ]
    write_debug_stage(
        config,
        "clean-transcript",
        {
            "segments": len(cleaned.segments),
            "changed": len(changes),
            "unchanged": len(cleaned.segments) - len(changes),
            "changes": changes,
            "json": debug_path(config.clean_transcript_output),
            "markdown": debug_path(config.clean_transcript_markdown_output),
            "glossary": debug_path(config.glossary_output),
        },
    )
    click.echo(f"Готово: {config.clean_transcript_output}")


def align_final_audio(config: EpisodeConfig, force: bool) -> None:
    """Перенести ролевой transcript на шкалу финального монтажа."""
    if config.final_transcript_output.is_file():
        load_transcribed_document(config.final_transcript_output)
    if config.final_whisper_transcript_output.is_file():
        load_transcribed_document(config.final_whisper_transcript_output)
    alignment_inputs = [
        config.clean_transcript_output,
        config.final_transcript_output,
        config.final_whisper_transcript_output,
        config.final_clean_transcript_output,
    ]
    if (
        not force
        and is_up_to_date(
            config.aligned_transcript_output,
            alignment_inputs,
        )
        and is_up_to_date(
            config.alignment_output,
            alignment_inputs,
        )
    ):
        click.echo(f"Уже готово: {config.aligned_transcript_output}")
        return
    audio = final_audio_path(config, force)
    chunks = prepare_audio_chunks(
        audio,
        ROOT / "build" / "stt" / "chunks" / config.episode_id / "final",
        "final",
        force,
    )
    if not chunks:
        raise click.ClickException("В финальном аудио не найдено речи")
    if config.final_transcript_output.exists() and not force:
        final_transcript = load_transcribed_document(config.final_transcript_output)
    else:
        final_segments = transcribe_chunks(
            config.transcription_model,
            chunks,
            ROOT / "build" / "stt" / f"{config.episode_id}.final.raw.jsonl",
            description="Финальный master: GigaAM",
            sentence_timestamps=True,
        )
        final_transcript = TranscribedDocument(
            episodeId=config.episode_id,
            segments=final_segments,
            model=config.transcription_model.name,
        )
        atomic_json(config.final_transcript_output, final_transcript)

    if config.final_whisper_transcript_output.exists() and not force:
        final_whisper = load_transcribed_document(config.final_whisper_transcript_output)
    else:
        glossary = combined_glossary(config)
        hotwords = ", ".join(
            candidate.preferred
            for candidate in glossary.candidates
            if candidate.preferred
        ) or None
        final_whisper = TranscribedDocument(
            episodeId=config.episode_id,
            model=whisper_model_identifier(config.secondary_transcription_model),
            segments=transcribe_whisper_chunks(
                config.secondary_transcription_model,
                chunks,
                ROOT / "build" / "stt" / f"{config.episode_id}.final.whisper.raw.jsonl",
                description="Финальный master: Whisper",
                hotwords=hotwords,
            ),
        )
        atomic_json(config.final_whisper_transcript_output, final_whisper)

    final_clean, final_hypotheses = review_transcript(config, "final", force)

    source_transcript = load_transcript(config.clean_transcript_output)
    alignment = align_transcripts(
        source_transcript,
        final_clean,
    )
    alignment = alignment.model_copy(
        update={"finalDurationSeconds": round(ffprobe_duration(audio), 3)}
    )
    source_audio = (
        config.audio_output
        if len(config.tracks) > 1
        else config.source_dir / config.tracks[0].file
    )
    alignment = refine_alignment(
        source_audio,
        audio,
        alignment,
        source_start_seconds=(
            0 if len(config.tracks) > 1 else config.content_start_seconds
        ),
    )
    aligned_transcript = attribute_final_transcript(
        final_clean,
        source_transcript,
        alignment,
    )
    atomic_json(config.aligned_transcript_output, aligned_transcript)
    atomic_json(config.alignment_output, alignment)
    write_transcript_markdown(
        config,
        aligned_transcript,
        config.aligned_transcript_markdown_output,
    )
    write_debug_stage(
        config,
        "align-final-audio",
        {
            "sourceSegments": len(source_transcript.segments),
            "finalSegments": len(final_clean.segments),
            "finalWhisperWords": len(final_whisper.segments),
            "alignedSegments": len(aligned_transcript.segments),
            "droppedSegments": alignment.droppedSegmentIds,
            "speakerAssignments": [
                {
                    "id": segment.id,
                    "speaker": segment.speaker,
                    "startSeconds": segment.startSeconds,
                }
                for segment in aligned_transcript.segments
            ],
            "finalTranscript": debug_path(config.final_transcript_output),
            "finalWhisperTranscript": debug_path(
                config.final_whisper_transcript_output
            ),
            "finalCleanTranscript": debug_path(config.final_clean_transcript_output),
            "finalReviewChanges": [
                {
                    "id": before.id,
                    "before": before.text,
                    "whisper": final_hypotheses[before.id],
                    "after": after.text,
                }
                for before, after in zip(
                    final_transcript.segments,
                    final_clean.segments,
                    strict=True,
                )
                if before.text != after.text
            ],
            "alignedTranscript": debug_path(config.aligned_transcript_output),
            "alignment": debug_path(config.alignment_output),
        },
    )
    click.echo(f"Готово: {config.aligned_transcript_output}")


def summarize(config: EpisodeConfig, force: bool) -> None:
    """Построить темы и таймкоды через Codex."""
    if not force and is_up_to_date(
        config.chapters_output,
        [config.timeline_transcript_output],
    ):
        existing = ChaptersDocument.model_validate_json(
            config.chapters_output.read_text(encoding="utf-8")
        )
        if existing.model == config.codex_model:
            click.echo(f"Уже готово: {config.chapters_output}")
            return
    transcript = load_transcript(config.timeline_transcript_output)
    schema = "chapters.schema.json"
    prompt = (
        "Ты редактор русского технического подкаста. По транскрипту выдели крупные содержательные главы. "
        "Используй только факты из текста. Границы должны совпадать с существующими таймкодами сегментов, "
        "главы должны идти по порядку и не пересекаться. Названия короткие, summary — 1-2 предложения. "
        "Верни только JSON по переданной schema.\n\n"
        + transcript.model_dump_json()
    )
    with CONSOLE.status(
        f"Создание таймкодов ({config.codex_model})",
        spinner="dots",
    ):
        chapters = ChaptersDocument.model_validate_json(
            codex_json(
                prompt,
                schema,
                config.chapters_output.parent,
                model=config.codex_model,
                service_tier=config.codex_service_tier,
            )
        )
    chapters = chapters.model_copy(update={"model": config.codex_model})
    if transcript.segments and chapters.chapters:
        duration = max(segment.endSeconds for segment in transcript.segments)
        if chapters.chapters[-1].endSeconds > duration:
            raise click.ClickException("Codex вернул главу за пределами транскрипта")
        boundaries = {
            timestamp
            for segment in transcript.segments
            for timestamp in (segment.startSeconds, segment.endSeconds)
        }
        if any(
            chapter.startSeconds not in boundaries or chapter.endSeconds not in boundaries
            for chapter in chapters.chapters
        ):
            raise click.ClickException("Codex вернул таймкод вне границ сегментов")
    atomic_json(config.chapters_output, chapters)
    write_debug_stage(
        config,
        "summarize",
        {
            "chapters": [
                chapter.model_dump(mode="json") for chapter in chapters.chapters
            ],
            "output": debug_path(config.chapters_output),
        },
    )
    click.echo(f"Готово: {config.chapters_output}")


def build_manifest(config: EpisodeConfig) -> None:
    """Собрать manifest и артефакты в один каталог."""
    transcript = load_transcript(config.timeline_transcript_output)
    transcript_segments = merge_sentence_fragments(transcript.segments)
    chapters = load_chapters(config)
    speakers = {track.speaker: {"name": track.name} for track in config.tracks}
    artifacts: list[dict[str, Any]] = []
    artifacts_dir = config.output_dir / "artifacts"
    if artifacts_dir.exists():
        shutil.rmtree(artifacts_dir)
    artifacts_dir.mkdir(parents=True)
    alignment = (
        AlignmentDocument.model_validate_json(
            config.alignment_output.read_text(encoding="utf-8")
        )
        if config.final_audio
        else None
    )
    for artifact in config.artifacts:
        start_seconds = (
            align_seconds(artifact.start_seconds, alignment)
            if alignment
            else artifact.start_seconds
        )
        end_seconds = (
            align_seconds(artifact.end_seconds, alignment)
            if alignment
            else artifact.end_seconds
        )
        common = {
            "id": artifact.id,
            "type": artifact.type,
            "startSeconds": start_seconds,
            "endSeconds": max(start_seconds, end_seconds),
            "title": artifact.title,
        }
        if artifact.type == "gallery":
            urls = []
            for index, gallery_source in enumerate(artifact.local_sources, start=1):
                require_file(gallery_source, f"Артефакт {artifact.id}")
                filename = f"{artifact.id}-{index}{gallery_source.suffix.lower()}"
                shutil.copy2(gallery_source, artifacts_dir / filename)
                urls.append(f"artifacts/{filename}")
            artifacts.append(
                {**common, "source": {"url": urls[0], "urls": urls}}
            )
        else:
            local_source = artifact.local_source
            if local_source is None:
                raise click.ClickException(f"Артефакт {artifact.id} не содержит файл")
            require_file(local_source, f"Артефакт {artifact.id}")
            filename = f"{artifact.id}{local_source.suffix}"
            shutil.copy2(local_source, artifacts_dir / filename)
            artifacts.append({**common, "source": {"url": f"artifacts/{filename}"}})
    interval_ends = (
        [segment.endSeconds for segment in transcript_segments]
        + [chapter.endSeconds for chapter in chapters.chapters]
        + [float(artifact["endSeconds"]) for artifact in artifacts]
    )
    manifest: dict[str, Any] = {
        "schemaVersion": 1,
        # timekodik 0.1.0 требует этот блок, но использует внешний HTMLMediaElement.
        "episode": {
            "id": config.episode_id,
            "audioUrl": "",
            "durationSeconds": (
                alignment.finalDurationSeconds
                if alignment
                else max(interval_ends, default=0.0)
            ),
        },
        "speakers": speakers,
        "transcript": [
            segment.model_dump(mode="json") for segment in transcript_segments
        ],
        "chapters": [
            chapter.model_dump(mode="json") for chapter in chapters.chapters
        ],
        "artifacts": artifacts,
    }
    atomic_json(config.manifest_output, manifest)

    write_debug_stage(
        config,
        "build-manifest",
        {
            "transcriptSegments": len(manifest["transcript"]),
            "chapters": len(manifest["chapters"]),
            "artifacts": len(manifest["artifacts"]),
            "output": debug_path(config.output_dir),
            "manifest": debug_path(config.manifest_output),
        },
    )
    click.echo(f"Готово: {config.output_dir}")
