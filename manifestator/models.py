from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator


NonNegativeSeconds = Annotated[float, Field(ge=0)]
GlossaryStatus = Literal["pending", "confirmed", "ignored"]


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


class TranscribedDocument(TranscriptDocument):
    model: str


class AlignmentAnchor(BaseModel):
    sourceSeconds: NonNegativeSeconds
    finalSeconds: NonNegativeSeconds
    confidence: Annotated[float, Field(ge=0, le=1)]


class AlignmentDocument(BaseModel):
    episodeId: str
    sourceDurationSeconds: NonNegativeSeconds
    finalDurationSeconds: NonNegativeSeconds
    anchors: list[AlignmentAnchor]
    droppedSegmentIds: list[str]


class TranscriptEdit(BaseModel):
    id: Annotated[str, Field(min_length=1)]
    text: Annotated[str, Field(min_length=1)]


class TranscriptEdits(BaseModel):
    segments: list[TranscriptEdit]


class GlossaryCandidate(BaseModel):
    heard: Annotated[str, Field(min_length=1)]
    suggested: Annotated[str, Field(min_length=1)]
    preferred: str | None = None
    status: GlossaryStatus = "pending"
    segmentIds: list[str]
    context: str


class GlossaryDocument(BaseModel):
    candidates: list[GlossaryCandidate]


class TranscriptReview(TranscriptEdits):
    glossaryCandidates: list[GlossaryCandidate] = Field(default_factory=list)


class TranscriptEditsCheckpoint(TranscriptReview):
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
    model: str | None = None

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
