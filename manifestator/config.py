from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Annotated

import click
from pydantic import BaseModel, Field, field_validator, model_validator

from .common import ROOT
from .model import default_model_path
from .models import NonNegativeSeconds


AUDIO_SUFFIXES = {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav"}
TEAMSPEAK_TRACK_PATTERN = re.compile(
    r"^(?:playback_)?(?P<speaker>.+?)(?:_\d+)?_"
    r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.\d{6}$"
)


class TrackConfig(BaseModel):
    file: str
    speaker: str
    name: str


class ArtifactConfig(BaseModel):
    id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")]
    type: Annotated[str, Field(min_length=1)]
    title: Annotated[str, Field(min_length=1)]
    start_seconds: NonNegativeSeconds
    end_seconds: NonNegativeSeconds
    local_source: Path | None = None
    local_sources: list[Path] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_interval(self) -> ArtifactConfig:
        if self.end_seconds < self.start_seconds:
            raise ValueError("end_seconds must be greater than or equal to start_seconds")
        if self.local_source is not None and self.local_sources:
            raise ValueError("use either local_source or local_sources")
        if self.local_source is None and not self.local_sources:
            raise ValueError("local_source or local_sources is required")
        if self.type == "gallery" and self.local_source is not None:
            self.local_sources = [self.local_source]
            self.local_source = None
        elif self.type != "gallery" and self.local_sources:
            raise ValueError("local_sources is supported only for gallery artifacts")
        return self


class EpisodeConfig(BaseModel):
    episode_id: Annotated[str, Field(min_length=1)]
    content_start_seconds: NonNegativeSeconds = 0
    source_dir: Path
    transcription_model: Path = Field(default_factory=default_model_path)
    output_dir: Path
    tracks: list[TrackConfig]
    artifacts: list[ArtifactConfig] = []

    @field_validator("episode_id")
    @classmethod
    def validate_episode_id(cls, value: str) -> str:
        if value in {".", ".."} or any(character in value for character in "/\\\0"):
            raise ValueError("episode_id must be a safe path component")
        return value

    @property
    def audio_output(self) -> Path:
        return ROOT / "build" / f"{self.episode_id}.mp3"

    @property
    def transcript_output(self) -> Path:
        return ROOT / "build" / f"{self.episode_id}.transcript.json"

    @property
    def transcript_markdown_output(self) -> Path:
        return ROOT / "build" / f"{self.episode_id}.transcript.md"

    @property
    def clean_transcript_output(self) -> Path:
        return ROOT / "build" / f"{self.episode_id}.transcript.clean.json"

    @property
    def clean_transcript_markdown_output(self) -> Path:
        return ROOT / "build" / f"{self.episode_id}.transcript.clean.md"

    @property
    def chapters_output(self) -> Path:
        return ROOT / "build" / f"{self.episode_id}.chapters.json"

    @property
    def manifest_output(self) -> Path:
        return self.output_dir / "manifest.json"

    def resolve_paths(self) -> EpisodeConfig:
        source_dir = self.source_dir.expanduser()
        transcription_model = self.transcription_model.expanduser()
        output_dir = self.output_dir.expanduser()
        return self.model_copy(
            update={
                "source_dir": (
                    source_dir if source_dir.is_absolute() else ROOT / source_dir
                ),
                "transcription_model": (
                    transcription_model
                    if transcription_model.is_absolute()
                    else ROOT / transcription_model
                ),
                "output_dir": (
                    output_dir if output_dir.is_absolute() else ROOT / output_dir
                ),
                "artifacts": [
                    artifact.model_copy(
                        update={
                            "local_source": (
                                artifact.local_source.expanduser()
                                if artifact.local_source is not None
                                and artifact.local_source.is_absolute()
                                else ROOT / artifact.local_source
                                if artifact.local_source is not None
                                else None
                            ),
                            "local_sources": [
                                source.expanduser()
                                if source.is_absolute()
                                else ROOT / source
                                for source in artifact.local_sources
                            ],
                        }
                    )
                    for artifact in self.artifacts
                ],
            }
        )


def load_config(path: Path) -> EpisodeConfig:
    with path.open("rb") as source:
        raw = tomllib.load(source)
    return EpisodeConfig.model_validate(raw).resolve_paths()


def config_for_source(path: Path) -> EpisodeConfig:
    source = path.expanduser().resolve()
    if source.is_file():
        episode_id = source.stem
        source_dir = source.parent
        tracks = [TrackConfig(file=source.name, speaker="speaker", name="Спикер")]
    else:
        episode_id = source.name
        source_dir = source
        audio_files = sorted(
            (
                item
                for item in source.iterdir()
                if item.is_file() and item.suffix.lower() in AUDIO_SUFFIXES
            ),
            key=lambda item: item.name.casefold(),
        )
        if not audio_files:
            raise click.ClickException(f"В каталоге нет аудиофайлов: {source}")

        tracks = []
        speakers: set[str] = set()
        for audio_file in audio_files:
            match = TEAMSPEAK_TRACK_PATTERN.fullmatch(audio_file.stem)
            name = match.group("speaker") if match else audio_file.stem
            base_speaker = re.sub(r"[^\w.-]+", "-", name).strip("-.") or "speaker"
            speaker = base_speaker
            suffix = 2
            while speaker in speakers:
                speaker = f"{base_speaker}-{suffix}"
                suffix += 1
            speakers.add(speaker)
            tracks.append(
                TrackConfig(
                    file=audio_file.name,
                    speaker=speaker,
                    name=name.replace("_", " "),
                )
            )

    return EpisodeConfig(
        episode_id=episode_id,
        source_dir=source_dir,
        output_dir=Path.cwd() / "output" / episode_id,
        tracks=tracks,
    ).resolve_paths()
