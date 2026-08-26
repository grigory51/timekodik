from __future__ import annotations

import json
import os
import subprocess
import tempfile
from contextlib import contextmanager
from fcntl import LOCK_EX, LOCK_NB, flock
from importlib.resources import as_file, files
from pathlib import Path
from typing import Any, Iterator, Literal

import click
from pydantic import BaseModel
from rich.console import Console


ROOT = Path(__file__).resolve().parent.parent
CONSOLE = Console(stderr=True)


def print_stage(index: int, total: int, title: str, description: str) -> None:
    CONSOLE.print()
    CONSOLE.rule(f"[bold cyan]{index}/{total}  {title}", align="left")
    CONSOLE.print(f"[dim]{description}[/dim]")


@contextmanager
def process_lock(path: Path) -> Iterator[None]:
    """Не допустить одновременную запись результатов одного выпуска."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as lock:
        try:
            flock(lock, LOCK_EX | LOCK_NB)
        except BlockingIOError as error:
            lock.seek(0)
            holder = lock.read().strip() or "неизвестен"
            raise click.ClickException(
                f"Этот выпуск уже обрабатывает manifestator (PID {holder})"
            ) from error
        lock.seek(0)
        lock.truncate()
        lock.write(str(os.getpid()))
        lock.flush()
        yield


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


def is_up_to_date(output: Path, inputs: list[Path]) -> bool:
    return output.is_file() and all(
        source.is_file() and source.stat().st_mtime <= output.stat().st_mtime
        for source in inputs
    )


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
    schema_name: str,
    output_dir: Path,
    *,
    model: str,
    reasoning_effort: Literal["low", "high"] = "high",
    service_tier: Literal["fast"] | None = None,
) -> str:
    schema_resource = files("manifestator").joinpath("schemas", schema_name)
    with as_file(schema_resource) as schema:
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
                        model,
                        "-c",
                        f"model_reasoning_effort={reasoning_effort}",
                        *(
                            ["-c", 'service_tier="fast"']
                            if service_tier == "fast"
                            else []
                        ),
                        "--output-schema",
                        str(schema),
                        "--output-last-message",
                        str(temporary_path),
                        "-C",
                        str(ROOT),
                        "-",
                    ],
                    input_text=prompt,
                    capture=True,
                    announce=False,
                )
            except subprocess.CalledProcessError as error:
                if error.stdout:
                    click.echo(error.stdout, err=True)
                if error.stderr:
                    click.echo(error.stderr, err=True)
                raise
            return temporary_path.read_text(encoding="utf-8")
        finally:
            temporary_path.unlink(missing_ok=True)
