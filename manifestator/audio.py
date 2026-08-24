from __future__ import annotations

import array
import json
import re
import wave
from datetime import datetime
from pathlib import Path
from typing import Any

import click

from .common import ROOT, require_file, run
from .config import EpisodeConfig
from .models import SpeechChunk


TIMESTAMP_PATTERN = re.compile(r"_(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.\d{6})\.wav$")
SILENCE_START_PATTERN = re.compile(r"silence_start: ([0-9.]+)")
SILENCE_END_PATTERN = re.compile(r"silence_end: ([0-9.]+)")
LOUDNESS_PATTERN = re.compile(r"\{\s*\"input_i\".*?\}", re.DOTALL)


def ffprobe_duration(path: Path) -> float:
    result = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture=True,
    )
    return float(result.stdout.strip())


def track_timestamp(filename: str) -> datetime:
    match = TIMESTAMP_PATTERN.search(filename)
    if not match:
        raise click.ClickException(f"В имени дорожки нет timestamp: {filename}")
    return datetime.strptime(match.group(1), "%Y-%m-%d_%H-%M-%S.%f")


def track_offsets(config: EpisodeConfig) -> dict[str, float]:
    if len(config.tracks) == 1 or not all(
        TIMESTAMP_PATTERN.search(track.file) for track in config.tracks
    ):
        return {track.file: 0.0 for track in config.tracks}
    timestamps = {track.file: track_timestamp(track.file) for track in config.tracks}
    origin = min(timestamps.values())
    return {
        filename: (timestamp - origin).total_seconds()
        for filename, timestamp in timestamps.items()
    }


def mix_filter(config: EpisodeConfig, loudness: dict[str, str] | None = None) -> str:
    offsets = track_offsets(config)
    inputs: list[str] = []
    filters: list[str] = []
    for index, track in enumerate(config.tracks):
        output = f"voice{index}"
        delay_ms = round(offsets[track.file] * 1000)
        filters.append(
            f"[{index}:a]aresample=48000,highpass=f=70,"
            "acompressor=threshold=0.1:ratio=3:attack=20:release=250:makeup=2,"
            f"adelay={delay_ms}:all=1[{output}]"
        )
        inputs.append(f"[{output}]")

    loudnorm = "loudnorm=I=-19:LRA=7:TP=-1.5"
    if loudness:
        loudnorm += (
            f":measured_I={loudness['input_i']}"
            f":measured_LRA={loudness['input_lra']}"
            f":measured_TP={loudness['input_tp']}"
            f":measured_thresh={loudness['input_thresh']}"
            f":offset={loudness['target_offset']}:linear=true"
        )
    loudnorm += ":print_format=json"
    filters.append(
        "".join(inputs)
        + f"amix=inputs={len(inputs)}:duration=longest:normalize=0,"
        + f"atrim=start={config.content_start_seconds},asetpts=PTS-STARTPTS,"
        + f"{loudnorm}[mixed]"
    )
    return ";".join(filters)


def ffmpeg_inputs(config: EpisodeConfig) -> list[str]:
    command: list[str] = []
    for track in config.tracks:
        command.extend(["-i", str(config.source_dir / track.file)])
    return command


def parse_loudness(stderr: str) -> dict[str, str]:
    matches = LOUDNESS_PATTERN.findall(stderr)
    if not matches:
        raise click.ClickException("ffmpeg не вернул loudnorm statistics")
    raw: dict[str, Any] = json.loads(matches[-1])
    required = ("input_i", "input_lra", "input_tp", "input_thresh", "target_offset")
    if any(key not in raw for key in required):
        raise click.ClickException("loudnorm statistics неполны")
    return {key: str(raw[key]) for key in required}


def detect_speech(path: Path, duration: float) -> list[tuple[float, float]]:
    result = run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-af",
            "silencedetect=noise=-42dB:d=0.35",
            "-f",
            "null",
            "-",
        ],
        capture=True,
    )
    silence_starts = [float(value) for value in SILENCE_START_PATTERN.findall(result.stderr)]
    silence_ends = [float(value) for value in SILENCE_END_PATTERN.findall(result.stderr)]
    silences: list[tuple[float, float]] = []
    end_index = 0
    for start in silence_starts:
        while end_index < len(silence_ends) and silence_ends[end_index] < start:
            end_index += 1
        end = silence_ends[end_index] if end_index < len(silence_ends) else duration
        silences.append((start, min(end, duration)))
        end_index += 1

    speech: list[tuple[float, float]] = []
    cursor = 0.0
    for silence_start, silence_end in silences:
        if silence_start - cursor >= 0.18:
            speech.append((cursor, silence_start))
        cursor = max(cursor, silence_end)
    if duration - cursor >= 0.18:
        speech.append((cursor, duration))
    return speech


def split_speech(
    intervals: list[tuple[float, float]],
    max_seconds: float = 24.0,
) -> list[tuple[float, float]]:
    chunks: list[tuple[float, float]] = []
    pending: tuple[float, float] | None = None
    for start, end in intervals:
        if pending and end - pending[0] <= max_seconds:
            pending = (pending[0], end)
            continue
        if pending:
            chunks.append(pending)

        cursor = start
        while end - cursor > max_seconds:
            chunks.append((cursor, cursor + max_seconds))
            cursor += max_seconds
        pending = (cursor, end) if end - cursor >= 0.18 else None
    if pending:
        chunks.append(pending)
    return chunks


def prepare_chunks(config: EpisodeConfig, force: bool) -> list[SpeechChunk]:
    work_dir = ROOT / "build" / "stt" / "chunks" / config.episode_id
    work_dir.mkdir(parents=True, exist_ok=True)
    offsets = track_offsets(config)
    chunks: list[SpeechChunk] = []

    for track in config.tracks:
        source = config.source_dir / track.file
        require_file(source, "Дорожка")
        duration = ffprobe_duration(source)
        content_start = max(0, config.content_start_seconds - offsets[track.file])
        intervals = split_speech(
            [
                (max(start, content_start), end)
                for start, end in detect_speech(source, duration)
                if end > content_start
            ]
        )
        speaker_dir = work_dir / track.speaker
        speaker_dir.mkdir(parents=True, exist_ok=True)
        for index, (start, end) in enumerate(intervals):
            chunk_path = speaker_dir / f"{index:05d}-{round(start * 1000):010d}.wav"
            if force or not chunk_path.is_file():
                run(
                    [
                        "ffmpeg",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-y",
                        "-ss",
                        f"{start:.3f}",
                        "-i",
                        str(source),
                        "-t",
                        f"{end - start:.3f}",
                        "-ar",
                        "16000",
                        "-ac",
                        "1",
                        "-c:a",
                        "pcm_s16le",
                        str(chunk_path),
                    ],
                    announce=False,
                )
            offset = offsets[track.file]
            chunks.append(
                SpeechChunk(
                    path=chunk_path.resolve(),
                    speaker=track.speaker,
                    startSeconds=start + offset,
                    endSeconds=end + offset,
                )
            )
    return chunks


def load_pcm(path: Path) -> array.array[float]:
    with wave.open(str(path), "rb") as source:
        if (
            source.getnchannels() != 1
            or source.getsampwidth() != 2
            or source.getframerate() != 16000
        ):
            raise click.ClickException(f"Ожидался PCM s16le 16 kHz mono: {path}")
        samples = array.array("h")
        samples.frombytes(source.readframes(source.getnframes()))
    return array.array("f", (sample / 32768.0 for sample in samples))
