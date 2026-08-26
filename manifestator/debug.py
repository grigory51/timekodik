from __future__ import annotations

import json
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import click

from .common import ROOT, atomic_json
from .config import EpisodeConfig


DebugStage = Literal[
    "doctor",
    "mix",
    "transcribe",
    "clean-transcript",
    "align-final-audio",
    "summarize",
    "build-manifest",
]
DEBUG_STAGES: tuple[tuple[DebugStage, str], ...] = (
    ("doctor", "Проверка окружения"),
    ("mix", "Сведение дорожек"),
    ("transcribe", "Распознавание речи"),
    ("clean-transcript", "Очистка транскрипта"),
    ("align-final-audio", "Выравнивание по финальному аудио"),
    ("summarize", "Темы и таймкоды"),
    ("build-manifest", "Сборка manifest"),
)


def debug_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return path.name


def write_debug_stage(
    config: EpisodeConfig,
    stage: DebugStage,
    data: dict[str, Any],
) -> None:
    try:
        write_debug_stage_report(config, stage, data)
    except Exception:
        click.echo(f"Debug-report для этапа {stage} не записан", err=True)
        traceback.print_exc()


def write_debug_stage_report(
    config: EpisodeConfig,
    stage: DebugStage,
    data: dict[str, Any],
) -> None:
    labels = dict(DEBUG_STAGES)
    report = {
        "schemaVersion": 1,
        "episodeId": config.episode_id,
        "stage": stage,
        "label": labels[stage],
        "status": "complete",
        "generatedAt": datetime.now(UTC).isoformat(),
        "data": data,
    }
    debug_dir = ROOT / "build" / "debug" / config.episode_id
    atomic_json(debug_dir / f"{stage}.json", report)
    write_debug_script(config, debug_dir)


def write_debug_script(config: EpisodeConfig, debug_dir: Path) -> None:
    reports: dict[str, dict[str, Any]] = {}
    for stage, _ in DEBUG_STAGES:
        path = debug_dir / f"{stage}.json"
        if path.is_file():
            report: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
            reports[stage] = report

    stages = [
        reports.get(
            stage,
            {
                "schemaVersion": 1,
                "episodeId": config.episode_id,
                "stage": stage,
                "label": label,
                "status": "pending",
            },
        )
        for stage, label in DEBUG_STAGES
    ]
    payload = {
        "schemaVersion": 1,
        "episodeId": config.episode_id,
        "stages": stages,
    }
    output = debug_dir / "manifestator-debug.js"
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".js.tmp")
    temporary.write_text(
        "window.TIMEKODIK_MANIFESTATOR_DEBUG = "
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + ";\n",
        encoding="utf-8",
    )
    temporary.replace(output)
