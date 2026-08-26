from __future__ import annotations

from pathlib import Path

import click

from .common import ROOT, print_stage, process_lock
from .config import config_for_source, load_config
from .stages import (
    align_final_audio,
    build_manifest,
    clean_transcript,
    doctor,
    mix,
    summarize,
    transcribe,
)


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
@click.option(
    "--final-audio",
    help="Финальный MP3 для выравнивания таймкодов после монтажа.",
)
@click.option("--force", is_flag=True, help="Пересобрать готовые результаты этапов.")
def cli(
    source_path: Path | None,
    config_path: Path | None,
    final_audio: str | None,
    force: bool,
) -> None:
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
    if final_audio:
        config = config.model_copy(update={"final_audio": final_audio}).resolve_paths()
    with process_lock(config.process_lock):
        total_stages = 7 if config.final_audio else 6
        if source_path:
            click.echo(f"Найдено дорожек: {len(config.tracks)}")
            for track in config.tracks:
                click.echo(f"  {track.name}: {track.file}")
        print_stage(
            1,
            total_stages,
            "Проверка входных данных",
            "Проверяем дорожки, ffmpeg, Codex и локальные ASR-модели.",
        )
        doctor(config)
        print_stage(
            2,
            total_stages,
            "Подготовка аудио",
            (
                "Сводим ролевые дорожки, выравниваем их по времени и нормализуем громкость."
                if len(config.tracks) > 1
                else "Используем готовую аудиодорожку без повторного сведения."
            ),
        )
        if len(config.tracks) > 1:
            mix(config, force)
        else:
            click.echo("Сведение не требуется: используется готовая аудиодорожка")
        print_stage(
            3,
            total_stages,
            "Распознавание исходных дорожек",
            "Распознаём речь двумя независимыми моделями — GigaAM и Whisper. На следующем этапе Codex сравнит обе версии, исправит ошибки и соберёт итоговый транскрипт.",
        )
        transcribe(config, force)
        print_stage(
            4,
            total_stages,
            "Очистка исходного транскрипта",
            f"{config.codex_model} сравнивает обе ASR-гипотезы пачками по 100 сегментов и собирает саджесты словаря.",
        )
        clean_transcript(config, force)
        if config.final_audio:
            print_stage(
                5,
                total_stages,
                "Финальный master",
                "Повторно распознаём опубликованный монтаж: из него берём текст и тайминги, а исходные дорожки назначают роли.",
            )
            align_final_audio(config, force)
        next_stage = 6 if config.final_audio else 5
        print_stage(
            next_stage,
            total_stages,
            "Главы и таймкоды",
            f"{config.codex_model} выделяет крупные темы только по итоговому транскрипту.",
        )
        summarize(config, force)
        print_stage(
            next_stage + 1,
            total_stages,
            "Сборка manifest",
            "Собираем transcript, главы и файлы артефактов в выходной каталог.",
        )
        build_manifest(config)
