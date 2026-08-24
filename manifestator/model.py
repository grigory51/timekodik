from __future__ import annotations

import hashlib
import os
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import click


MODEL_FILENAME = "gigaam-v3-e2e-rnnt-Q8_0.gguf"
MODEL_URL = (
    "https://huggingface.co/handy-computer/gigaam-v3-e2e-rnnt-gguf/resolve/"
    "f719d70812344f4d0fb8c11c0887b190501a7465/"
    f"{MODEL_FILENAME}"
)
MODEL_SHA256 = "78d63b47723b7f8d78c6113a6ef983b5a86e2a86f6c273e1f5cb6967b1c4467a"


def default_model_path() -> Path:
    cache_root = Path(
        os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))
    )
    return cache_root / "timekodik" / MODEL_FILENAME


def ensure_transcription_model(path: Path) -> Path:
    if path.is_file():
        return path
    if path != default_model_path():
        raise click.ClickException(f"Модель транскрибации не найдена: {path}")

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".download")
    digest = hashlib.sha256()
    try:
        with urlopen(MODEL_URL) as response, temporary.open("wb") as target:
            content_length = response.headers.get("Content-Length")
            total = int(content_length) if content_length else 0
            with click.progressbar(length=total, label="Скачивание модели GigaAM") as bar:
                while chunk := response.read(1024 * 1024):
                    target.write(chunk)
                    digest.update(chunk)
                    bar.update(len(chunk))
    except (OSError, URLError) as error:
        temporary.unlink(missing_ok=True)
        raise click.ClickException(f"Не удалось скачать модель: {error}") from error

    if digest.hexdigest() != MODEL_SHA256:
        temporary.unlink(missing_ok=True)
        raise click.ClickException("Контрольная сумма скачанной модели не совпала")
    temporary.replace(path)
    return path
