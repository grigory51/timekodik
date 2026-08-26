from __future__ import annotations

import importlib
import json
import re
import sys
from pathlib import Path
from platform import machine
from typing import Any

from tqdm import tqdm

from .audio import load_pcm
from .models import SpeechChunk, TranscriptSegment


SENTENCE_END_PATTERN = re.compile(r"[.!?…]$")


def whisper_model_identifier(model_name: str) -> str:
    if sys.platform == "darwin" and machine() == "arm64" and model_name == "turbo":
        return "mlx-community/whisper-turbo"
    return model_name


def timestamped_sentences(result: Any) -> list[tuple[float, float, str]]:
    """Собрать предложения с точными границами из токенов transcribe.cpp."""
    sentences: list[tuple[float, float, str]] = []
    text = ""
    start_ms: int | None = None
    end_ms = 0
    for token in result.tokens:
        piece = token.text.replace("▁", " ")
        if start_ms is None and any(character.isalnum() for character in piece):
            start_ms = token.t0_ms
        text += piece
        end_ms = token.t1_ms
        if start_ms is not None and SENTENCE_END_PATTERN.search(piece.strip()):
            sentences.append((start_ms / 1000, end_ms / 1000, text.strip()))
            text = ""
            start_ms = None
    if start_ms is not None and text.strip():
        sentences.append((start_ms / 1000, end_ms / 1000, text.strip()))
    return sentences


def transcribe_chunks(
    model_path: Path,
    chunks: list[SpeechChunk],
    raw_output: Path,
    *,
    description: str,
    time_offset: float = 0,
    sentence_timestamps: bool = False,
) -> list[TranscriptSegment]:
    """Распознать подготовленные фрагменты одной загруженной моделью."""
    transcribe_cpp: Any = importlib.import_module("transcribe_cpp")
    transcribe_cpp.set_log_callback(None)
    counters: dict[str, int] = {}
    segments: list[TranscriptSegment] = []
    raw_output.parent.mkdir(parents=True, exist_ok=True)
    with raw_output.open("w", encoding="utf-8") as output:
        with transcribe_cpp.Model(str(model_path)) as model:
            with model.session() as session:
                for chunk in tqdm(
                    chunks,
                    desc=description,
                    unit="фрагмент",
                    dynamic_ncols=True,
                ):
                    result = session.run(
                        load_pcm(chunk.path),
                        timestamps="token" if sentence_timestamps else "none",
                        language="ru",
                    )
                    text = result.text.strip()
                    output.write(
                        json.dumps(
                            {"file": str(chunk.path), "text": text},
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                    if not text:
                        continue
                    timed_sentences = (
                        timestamped_sentences(result)
                        if sentence_timestamps
                        else [(0.0, chunk.endSeconds - chunk.startSeconds, text)]
                    )
                    for sentence_start, sentence_end, sentence in timed_sentences:
                        number = counters.get(chunk.speaker, 0) + 1
                        counters[chunk.speaker] = number
                        segments.append(
                            TranscriptSegment(
                                id=f"{chunk.speaker}-{number:05d}",
                                speaker=chunk.speaker,
                                startSeconds=round(
                                    chunk.startSeconds + sentence_start - time_offset,
                                    3,
                                ),
                                endSeconds=round(
                                    chunk.startSeconds + sentence_end - time_offset,
                                    3,
                                ),
                                text=sentence,
                            )
                        )
    return sorted(
        segments,
        key=lambda segment: (
            segment.startSeconds,
            segment.endSeconds,
            segment.speaker,
        ),
    )


def transcribe_whisper_chunks(
    model_name: str,
    chunks: list[SpeechChunk],
    raw_output: Path,
    *,
    description: str,
    time_offset: float = 0,
    hotwords: str | None = None,
) -> list[TranscriptSegment]:
    """Распознать те же фрагменты Whisper с временными границами слов."""
    if sys.platform == "darwin" and machine() == "arm64":
        return transcribe_mlx_whisper_chunks(
            model_name,
            chunks,
            raw_output,
            description=description,
            time_offset=time_offset,
            hotwords=hotwords,
        )
    faster_whisper: Any = importlib.import_module("faster_whisper")
    model = faster_whisper.WhisperModel(
        model_name,
        device="auto",
        compute_type="int8",
    )
    counters: dict[str, int] = {}
    words: list[TranscriptSegment] = []
    raw_output.parent.mkdir(parents=True, exist_ok=True)
    with raw_output.open("w", encoding="utf-8") as output:
        for chunk in tqdm(
            chunks,
            desc=description,
            unit="фрагмент",
            dynamic_ncols=True,
        ):
            recognized, _ = model.transcribe(
                str(chunk.path),
                language="ru",
                beam_size=5,
                condition_on_previous_text=False,
                vad_filter=False,
                word_timestamps=True,
                hotwords=hotwords,
            )
            chunk_text = ""
            for recognized_segment in recognized:
                chunk_text += recognized_segment.text
                recognized_words = recognized_segment.words or []
                if not recognized_words and recognized_segment.text.strip():
                    number = counters.get(chunk.speaker, 0) + 1
                    counters[chunk.speaker] = number
                    words.append(
                        TranscriptSegment(
                            id=f"{chunk.speaker}-whisper-{number:05d}",
                            speaker=chunk.speaker,
                            startSeconds=round(
                                chunk.startSeconds
                                + recognized_segment.start
                                - time_offset,
                                3,
                            ),
                            endSeconds=round(
                                chunk.startSeconds
                                + recognized_segment.end
                                - time_offset,
                                3,
                            ),
                            text=recognized_segment.text,
                        )
                    )
                for word in recognized_words:
                    if not word.word.strip():
                        continue
                    number = counters.get(chunk.speaker, 0) + 1
                    counters[chunk.speaker] = number
                    words.append(
                        TranscriptSegment(
                            id=f"{chunk.speaker}-whisper-{number:05d}",
                            speaker=chunk.speaker,
                            startSeconds=round(
                                chunk.startSeconds + word.start - time_offset,
                                3,
                            ),
                            endSeconds=round(
                                chunk.startSeconds + word.end - time_offset,
                                3,
                            ),
                            text=word.word,
                        )
                    )
            output.write(
                json.dumps(
                    {"file": str(chunk.path), "text": chunk_text.strip()},
                    ensure_ascii=False,
                )
                + "\n"
            )
    return sorted(words, key=lambda word: (word.startSeconds, word.endSeconds))


def transcribe_mlx_whisper_chunks(
    model_name: str,
    chunks: list[SpeechChunk],
    raw_output: Path,
    *,
    description: str,
    time_offset: float = 0,
    hotwords: str | None = None,
) -> list[TranscriptSegment]:
    """Распознать фрагменты Whisper через Metal на Apple Silicon."""
    mlx_whisper: Any = importlib.import_module("mlx_whisper")
    model = whisper_model_identifier(model_name)
    counters: dict[str, int] = {}
    words: list[TranscriptSegment] = []
    raw_output.parent.mkdir(parents=True, exist_ok=True)
    with raw_output.open("w", encoding="utf-8") as output:
        for chunk in tqdm(
            chunks,
            desc=description,
            unit="фрагмент",
            dynamic_ncols=True,
        ):
            result: dict[str, Any] = mlx_whisper.transcribe(
                str(chunk.path),
                path_or_hf_repo=model,
                language="ru",
                condition_on_previous_text=False,
                initial_prompt=hotwords,
                word_timestamps=True,
                verbose=None,
            )
            for recognized_segment in result["segments"]:
                recognized_words = recognized_segment.get("words") or [
                    {
                        "start": recognized_segment["start"],
                        "end": recognized_segment["end"],
                        "word": recognized_segment["text"],
                    }
                ]
                for word in recognized_words:
                    text = str(word["word"])
                    if not text.strip():
                        continue
                    number = counters.get(chunk.speaker, 0) + 1
                    counters[chunk.speaker] = number
                    words.append(
                        TranscriptSegment(
                            id=f"{chunk.speaker}-whisper-{number:05d}",
                            speaker=chunk.speaker,
                            startSeconds=round(
                                chunk.startSeconds
                                + float(word["start"])
                                - time_offset,
                                3,
                            ),
                            endSeconds=round(
                                chunk.startSeconds
                                + float(word["end"])
                                - time_offset,
                                3,
                            ),
                            text=text,
                        )
                    )
            output.write(
                json.dumps(
                    {"file": str(chunk.path), "text": result["text"].strip()},
                    ensure_ascii=False,
                )
                + "\n"
            )
    return sorted(words, key=lambda word: (word.startSeconds, word.endSeconds))
