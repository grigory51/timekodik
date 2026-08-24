from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Literal

import click
from pydantic import BaseModel


ROOT = Path(__file__).resolve().parent.parent


def run(
    command: list[str],
    *,
    capture: bool = False,
    input_text: str | None = None,
    announce: bool = True,
) -> subprocess.CompletedProcess[str]:
    if announce:
        click.echo("$ " + " ".join(command))
    return subprocess.run(
        command,
        check=True,
        text=True,
        input=input_text,
        capture_output=capture,
    )


def require_file(path: Path, label: str) -> None:
    if not path.is_file():
        raise click.ClickException(f"{label} не найден: {path}")


def atomic_json(path: Path, value: BaseModel | dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def codex_json(
    prompt: str,
    schema: Path,
    output_dir: Path,
    *,
    reasoning_effort: Literal["low", "high"] = "high",
) -> str:
    require_file(schema, "JSON Schema")
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".json",
        dir=output_dir,
        delete=False,
    ) as temporary:
        temporary_path = Path(temporary.name)

    try:
        run(
            [
                "codex",
                "exec",
                "--ephemeral",
                "--ignore-user-config",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "-m",
                "gpt-5.6-luna",
                "-c",
                f"model_reasoning_effort={reasoning_effort}",
                "--output-schema",
                str(schema),
                "--output-last-message",
                str(temporary_path),
                "-C",
                str(ROOT),
                "-",
            ],
            input_text=prompt,
        )
        return temporary_path.read_text(encoding="utf-8")
    finally:
        temporary_path.unlink(missing_ok=True)
