from __future__ import annotations

import hashlib
import importlib
import json
import math
import shutil
from collections import Counter
from importlib.util import find_spec
from pathlib import Path
from typing import Any

import click

from .audio import (
    ffmpeg_inputs,
    ffprobe_duration,
    load_pcm,
    mix_filter,
    parse_loudness,
    prepare_chunks,
)
from .common import ROOT, atomic_json, codex_json, require_file, run
from .config import EpisodeConfig, config_for_source, load_config
from .debug import debug_path, write_debug_stage
from .model import ensure_transcription_model
from .models import (
    ChaptersDocument,
    TranscriptDocument,
    TranscriptEdits,
    TranscriptEditsCheckpoint,
    TranscriptSegment,
)
from .transcript import (
    apply_transcript_edits,
    load_chapters,
    load_transcript,
    merge_sentence_fragments,
    write_transcript_markdown,
)


CLEAN_TRANSCRIPT_BATCH_SIZE = 50


@click.command()
@click.argument(
    "source_path",
    required=False,
    type=click.Path(path_type=Path, exists=True),
)
@click.option(
    "--config",
    "config_path",
    type=click.Path(path_type=Path, dir_okay=False),
    help="Использовать расширенный TOML-конфиг.",
)
@click.option("--force", is_flag=True, help="Пересобрать готовые результаты этапов.")
def cli(source_path: Path | None, config_path: Path | None, force: bool) -> None:
    """Создать manifest из аудиофайла или каталога с ролевыми дорожками."""
    if source_path and config_path:
        raise click.UsageError("Передайте SOURCE_PATH или --config, но не оба сразу")
    if source_path:
        config = config_for_source(source_path)
    else:
        config_path = config_path or ROOT / "episode.toml"
        if not config_path.is_file():
            raise click.UsageError("Передайте аудиофайл или каталог с дорожками")
        config = load_config(config_path)
    if source_path:
        click.echo(f"Найдено дорожек: {len(config.tracks)}")
        for track in config.tracks:
            click.echo(f"  {track.name}: {track.file}")
    doctor(config)
    if not click.confirm("Проверка завершена. Продолжить?", default=True):
        return
    if len(config.tracks) > 1:
        mix(config, force)
        if not click.confirm("Сведение завершено. Продолжить?", default=True):
            return
    else:
        click.echo("Сведение не требуется: используется готовая аудиодорожка")
    transcribe(config, force)
    if not click.confirm("Транскрибация завершена. Продолжить?", default=True):
        return
    clean_transcript(config, force)
    if not click.confirm("Очистка транскрипта завершена. Продолжить?", default=True):
        return
    summarize(config, force)
    if not click.confirm("Таймкоды готовы. Продолжить?", default=True):
        return
    build_manifest(config)


def doctor(config: EpisodeConfig) -> None:
    """Проверить локальные инструменты и исходные файлы."""
    for executable in ("ffmpeg", "ffprobe", "codex"):
        if shutil.which(executable) is None:
            raise click.ClickException(f"Не найден executable: {executable}")
    for track in config.tracks:
        require_file(config.source_dir / track.file, "Дорожка")
    if find_spec("transcribe_cpp") is None:
        raise click.ClickException("Python binding transcribe_cpp не установлен")
    ensure_transcription_model(config.transcription_model)
    require_file(config.transcription_model, "Модель транскрибации")
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
    """Локально транскрибировать роли через transcribe.cpp."""
    if config.transcript_output.exists() and not force:
        click.echo(f"Уже готово: {config.transcript_output}")
        return
    require_file(config.transcription_model, "Модель транскрибации")
    chunks = prepare_chunks(config, force)
    if not chunks:
        raise click.ClickException("Не найдено фрагментов с речью")

    work_dir = ROOT / "build" / "stt"
    transcribe_cpp: Any = importlib.import_module("transcribe_cpp")
    segments: list[TranscriptSegment] = []
    counters: dict[str, int] = {}
    raw_path = work_dir / "transcribe.raw.jsonl"
    with raw_path.open("w", encoding="utf-8") as raw_output:
        with transcribe_cpp.Model(
            str(config.transcription_model),
            backend="metal",
        ) as model:
            with model.session() as session:
                for index, chunk in enumerate(chunks, start=1):
                    result = session.run(
                        load_pcm(chunk.path),
                        timestamps="none",
                        language="ru",
                    )
                    text = result.text.strip()
                    raw_output.write(
                        json.dumps(
                            {"file": str(chunk.path), "text": text},
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                    if not text:
                        continue
                    number = counters.get(chunk.speaker, 0) + 1
                    counters[chunk.speaker] = number
                    segments.append(
                        TranscriptSegment(
                            id=f"{chunk.speaker}-{number:05d}",
                            speaker=chunk.speaker,
                            startSeconds=round(
                                chunk.startSeconds - config.content_start_seconds,
                                3,
                            ),
                            endSeconds=round(
                                chunk.endSeconds - config.content_start_seconds,
                                3,
                            ),
                            text=text,
                        )
                    )
                    if index % 100 == 0:
                        click.echo(f"Распознано {index}/{len(chunks)} фрагментов")

    segments.sort(
        key=lambda segment: (
            segment.startSeconds,
            segment.endSeconds,
            segment.speaker,
        )
    )
    document = TranscriptDocument(episodeId=config.episode_id, segments=segments)
    atomic_json(config.transcript_output, document)
    write_transcript_markdown(config, document, config.transcript_markdown_output)
    write_debug_stage(
        config,
        "transcribe",
        {
            "segments": len(segments),
            "speakers": dict(Counter(segment.speaker for segment in segments)),
            "json": debug_path(config.transcript_output),
            "markdown": debug_path(config.transcript_markdown_output),
        },
    )
    click.echo(f"Готово: {len(segments)} сегментов")


def clean_transcript(config: EpisodeConfig, force: bool) -> None:
    """Исправить ошибки STT и убрать речевой мусор через Codex Luna."""
    if config.clean_transcript_output.exists() and not force:
        click.echo(f"Уже готово: {config.clean_transcript_output}")
        return
    transcript = load_transcript(config.transcript_output)
    schema = ROOT / "schemas" / "transcript-edits.schema.json"
    checkpoint_dir = ROOT / "build" / "clean-transcript" / config.episode_id
    cleaned_segments: list[TranscriptSegment] = []
    total_batches = math.ceil(len(transcript.segments) / CLEAN_TRANSCRIPT_BATCH_SIZE)

    for offset in range(0, len(transcript.segments), CLEAN_TRANSCRIPT_BATCH_SIZE):
        segments = transcript.segments[offset : offset + CLEAN_TRANSCRIPT_BATCH_SIZE]
        batch = TranscriptDocument(episodeId=transcript.episodeId, segments=segments)
        batch_json = batch.model_dump_json()
        input_hash = hashlib.sha256(batch_json.encode()).hexdigest()
        checkpoint_path = checkpoint_dir / f"{offset:05d}.json"
        prompt = (
            "Ты редактор транскрипта русского технического подкаста про сетевую автоматизацию. "
            "Исправь явные ошибки распознавания, особенно названия продуктов, технологий, Python API, "
            "модулей, классов и методов: например, «АСТ-Парс» в подходящем контексте — `ast.parse`. "
            "Убери бессодержательные «э-э», «эм», повторы слов и оборванные начала фраз. "
            "Сохрани смысл, факты, сомнения говорящего вроде «по-моему», стиль и ненормативную лексику. "
            "Не пересказывай, не сокращай содержательные мысли, не переводи и ничего не выдумывай. "
            "Не объединяй, не дроби и не переставляй сегменты. Верни для каждого входного сегмента "
            "ровно один объект с тем же id и очищенным text, в исходном порядке. Если не уверен в "
            "исправлении термина, оставь исходный вариант. Не исправляй имена, бренды и аббревиатуры "
            "по догадке. Верни только JSON по переданной schema.\n\n"
            + batch_json
        )
        checkpoint = (
            TranscriptEditsCheckpoint.model_validate_json(
                checkpoint_path.read_text(encoding="utf-8")
            )
            if checkpoint_path.is_file() and not force
            else None
        )
        if checkpoint and checkpoint.inputHash == input_hash:
            edits = TranscriptEdits(segments=checkpoint.segments)
        else:
            edits = TranscriptEdits.model_validate_json(
                codex_json(
                    prompt,
                    schema,
                    config.clean_transcript_output.parent,
                    reasoning_effort="low",
                )
            )
        cleaned_segments.extend(apply_transcript_edits(segments, edits))
        if not checkpoint or checkpoint.inputHash != input_hash:
            atomic_json(
                checkpoint_path,
                TranscriptEditsCheckpoint(inputHash=input_hash, segments=edits.segments),
            )
        click.echo(
            f"Очищена пачка {offset // CLEAN_TRANSCRIPT_BATCH_SIZE + 1}/{total_batches}"
        )

    cleaned = TranscriptDocument(
        episodeId=transcript.episodeId,
        segments=cleaned_segments,
    )
    atomic_json(config.clean_transcript_output, cleaned)
    write_transcript_markdown(
        config,
        cleaned,
        config.clean_transcript_markdown_output,
    )
    changes = [
        {
            "id": before.id,
            "speaker": before.speaker,
            "startSeconds": before.startSeconds,
            "before": before.text,
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
        },
    )
    click.echo(f"Готово: {config.clean_transcript_output}")


def summarize(config: EpisodeConfig, force: bool) -> None:
    """Построить темы и таймкоды через Codex Luna."""
    if config.chapters_output.exists() and not force:
        click.echo(f"Уже готово: {config.chapters_output}")
        return
    transcript = load_transcript(config.clean_transcript_output)
    schema = ROOT / "schemas" / "chapters.schema.json"
    prompt = (
        "Ты редактор русского технического подкаста. По транскрипту выдели крупные содержательные главы. "
        "Используй только факты из текста. Границы должны совпадать с существующими таймкодами сегментов, "
        "главы должны идти по порядку и не пересекаться. Названия короткие, summary — 1-2 предложения. "
        "Верни только JSON по переданной schema.\n\n"
        + transcript.model_dump_json()
    )
    chapters = ChaptersDocument.model_validate_json(
        codex_json(prompt, schema, config.chapters_output.parent)
    )
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
    transcript = load_transcript(config.clean_transcript_output)
    transcript_segments = merge_sentence_fragments(transcript.segments)
    chapters = load_chapters(config)
    speakers = {track.speaker: {"name": track.name} for track in config.tracks}
    artifacts: list[dict[str, Any]] = []
    artifacts_dir = config.output_dir / "artifacts"
    if artifacts_dir.exists():
        shutil.rmtree(artifacts_dir)
    artifacts_dir.mkdir(parents=True)
    for artifact in config.artifacts:
        require_file(artifact.local_source, f"Артефакт {artifact.id}")
        filename = f"{artifact.id}{artifact.local_source.suffix}"
        shutil.copy2(artifact.local_source, artifacts_dir / filename)
        common = {
            "id": artifact.id,
            "type": artifact.type,
            "startSeconds": artifact.start_seconds,
            "endSeconds": artifact.end_seconds,
            "title": artifact.title,
        }
        artifacts.append({**common, "source": {"url": f"artifacts/{filename}"}})
    manifest: dict[str, Any] = {
        "schemaVersion": 1,
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
