from __future__ import annotations

from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, Field, model_validator


NonNegativeSeconds = Annotated[float, Field(ge=0)]


class TranscriptSegment(BaseModel):
    id: str
    speaker: str
    startSeconds: NonNegativeSeconds
    endSeconds: NonNegativeSeconds
    text: str

    @model_validator(mode="after")
    def validate_interval(self) -> TranscriptSegment:
        if self.endSeconds < self.startSeconds:
            raise ValueError("endSeconds must be greater than or equal to startSeconds")
        return self


class TranscriptDocument(BaseModel):
    episodeId: str
    segments: list[TranscriptSegment]


class TranscriptEdit(BaseModel):
    id: Annotated[str, Field(min_length=1)]
    text: Annotated[str, Field(min_length=1)]


class TranscriptEdits(BaseModel):
    segments: list[TranscriptEdit]


class TranscriptEditsCheckpoint(TranscriptEdits):
    inputHash: Annotated[str, Field(min_length=64, max_length=64)]


class Chapter(BaseModel):
    id: str
    startSeconds: NonNegativeSeconds
    endSeconds: NonNegativeSeconds
    title: str
    summary: str

    @model_validator(mode="after")
    def validate_interval(self) -> Chapter:
        if self.endSeconds <= self.startSeconds:
            raise ValueError("chapter endSeconds must be greater than startSeconds")
        return self


class ChaptersDocument(BaseModel):
    chapters: list[Chapter]

    @model_validator(mode="after")
    def validate_order(self) -> ChaptersDocument:
        previous_end = 0.0
        for chapter in self.chapters:
            if chapter.startSeconds < previous_end:
                raise ValueError("chapters must be ordered and non-overlapping")
            previous_end = chapter.endSeconds
        return self


class SpeechChunk(BaseModel):
    path: Path
    speaker: str
    startSeconds: NonNegativeSeconds
    endSeconds: NonNegativeSeconds
