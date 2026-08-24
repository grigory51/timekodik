from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, Field, model_validator

from .common import ROOT
from .models import NonNegativeSeconds


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
    local_source: Path

    @model_validator(mode="after")
    def validate_interval(self) -> ArtifactConfig:
        if self.end_seconds < self.start_seconds:
            raise ValueError("end_seconds must be greater than or equal to start_seconds")
        return self


class EpisodeConfig(BaseModel):
    episode_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")]
    content_start_seconds: NonNegativeSeconds = 0
    source_dir: Path
    transcription_model: Path
    output_dir: Path
    tracks: list[TrackConfig]
    artifacts: list[ArtifactConfig] = []

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
                                if artifact.local_source.is_absolute()
                                else ROOT / artifact.local_source
                            )
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
