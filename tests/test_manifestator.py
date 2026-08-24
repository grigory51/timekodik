import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import click
from click.testing import CliRunner
from pydantic import ValidationError

from manifestator.audio import parse_loudness, split_speech, track_offsets
from manifestator.cli import cli
from manifestator.config import EpisodeConfig, config_for_source, load_config
from manifestator.model import ensure_transcription_model
from manifestator.models import (
    TranscriptDocument,
    TranscriptEdit,
    TranscriptEdits,
    TranscriptSegment,
)
from manifestator.transcript import (
    apply_transcript_edits,
    merge_sentence_fragments,
    trim_transcript,
)


class ManifestatorTest(unittest.TestCase):
    def test_cli_runs_all_stages_after_confirmation(self) -> None:
        config = load_config(Path(__file__).parent.parent / "episode.example.toml")
        with (
            patch("manifestator.cli.load_config", return_value=config),
            patch("manifestator.cli.doctor") as doctor,
            patch("manifestator.cli.mix") as mix,
            patch("manifestator.cli.transcribe") as transcribe,
            patch("manifestator.cli.clean_transcript") as clean_transcript,
            patch("manifestator.cli.summarize") as summarize,
            patch("manifestator.cli.build_manifest") as build_manifest,
        ):
            result = CliRunner().invoke(
                cli,
                ["--config", "episode.example.toml"],
                input="y\ny\ny\ny\ny\n",
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.output.count("Продолжить?"), 5)
        doctor.assert_called_once_with(config)
        mix.assert_called_once_with(config, False)
        transcribe.assert_called_once_with(config, False)
        clean_transcript.assert_called_once_with(config, False)
        summarize.assert_called_once_with(config, False)
        build_manifest.assert_called_once_with(config)

    def test_audio_path_uses_zero_config_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "Мой подкаст.mp3"
            audio.touch()
            config = config_for_source(audio)
            with (
                patch("manifestator.cli.config_for_source", return_value=config),
                patch("manifestator.cli.doctor") as doctor,
                patch("manifestator.cli.mix") as mix,
                patch("manifestator.cli.transcribe") as transcribe,
                patch("manifestator.cli.clean_transcript") as clean_transcript,
                patch("manifestator.cli.summarize") as summarize,
                patch("manifestator.cli.build_manifest") as build_manifest,
            ):
                result = CliRunner().invoke(
                    cli,
                    [str(audio)],
                    input="y\ny\ny\ny\n",
                )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.output.count("Продолжить?"), 4)
        self.assertEqual(config.episode_id, "Мой подкаст")
        self.assertEqual(track_offsets(config), {audio.name: 0.0})
        doctor.assert_called_once_with(config)
        mix.assert_not_called()
        transcribe.assert_called_once_with(config, False)
        clean_transcript.assert_called_once_with(config, False)
        summarize.assert_called_once_with(config, False)
        build_manifest.assert_called_once_with(config)

    def test_directory_discovers_roles_and_teamspeak_offsets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "Выпуск"
            source.mkdir()
            (source / "notes.txt").touch()
            first = "capture_2026-08-21_11-29-13.953201.wav"
            second = "playback_guest_user_6_2026-08-21_11-29-16.953201.wav"
            (source / first).touch()
            (source / second).touch()

            config = config_for_source(source)
            regular_source = Path(directory) / "Обычная запись"
            regular_source.mkdir()
            (regular_source / "Ведущий.mp3").touch()
            (regular_source / "Гость.wav").touch()
            regular_config = config_for_source(regular_source)
            empty_source = Path(directory) / "Пустая запись"
            empty_source.mkdir()
            with self.assertRaisesRegex(click.ClickException, "нет аудиофайлов"):
                config_for_source(empty_source)

        self.assertEqual(config.episode_id, "Выпуск")
        self.assertEqual([track.file for track in config.tracks], [first, second])
        self.assertEqual(
            [(track.speaker, track.name) for track in config.tracks],
            [("capture", "capture"), ("guest_user", "guest user")],
        )
        self.assertEqual(track_offsets(config), {first: 0.0, second: 3.0})
        self.assertEqual(
            track_offsets(regular_config),
            {"Ведущий.mp3": 0.0, "Гость.wav": 0.0},
        )

    def test_model_is_downloaded_once_and_verified(self) -> None:
        payload = b"model"
        response = io.BytesIO(payload)
        response.headers = {"Content-Length": str(len(payload))}  # type: ignore[attr-defined]
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / "model.gguf"
            with (
                patch("manifestator.model.default_model_path", return_value=model),
                patch("manifestator.model.MODEL_SHA256", hashlib.sha256(payload).hexdigest()),
                patch("manifestator.model.urlopen", return_value=response) as urlopen,
            ):
                self.assertEqual(ensure_transcription_model(model), model)
                self.assertEqual(ensure_transcription_model(model), model)

            self.assertEqual(model.read_bytes(), payload)
            urlopen.assert_called_once()

    def test_audio_helpers(self) -> None:
        self.assertEqual(
            split_speech([(1.0, 51.0)]),
            [(1.0, 25.0), (25.0, 49.0), (49.0, 51.0)],
        )
        self.assertEqual(split_speech([(1.0, 3.0), (3.5, 5.0)]), [(1.0, 5.0)])
        self.assertEqual(
            parse_loudness(
                'noise\n{"input_i":"-21.0","input_lra":"4.0","input_tp":"-2.0",'
                '"input_thresh":"-31.0","target_offset":"0.1"}\n'
            )["target_offset"],
            "0.1",
        )

    def test_transcript_edits_preserve_segments(self) -> None:
        segment = TranscriptSegment(
            id="speaker-00001",
            speaker="speaker",
            startSeconds=3267,
            endSeconds=3289,
            text="АСТ-Парс",
        )
        cleaned = apply_transcript_edits(
            [segment],
            TranscriptEdits(
                segments=[TranscriptEdit(id="speaker-00001", text="ast.parse")]
            ),
        )
        self.assertEqual(
            TranscriptDocument(episodeId="telecom-162", segments=cleaned).model_dump(),
            {
                "episodeId": "telecom-162",
                "segments": [
                    {
                        "id": "speaker-00001",
                        "speaker": "speaker",
                        "startSeconds": 3267.0,
                        "endSeconds": 3289.0,
                        "text": "ast.parse",
                    }
                ],
            },
        )
        with self.assertRaises(click.ClickException):
            apply_transcript_edits(
                [segment],
                TranscriptEdits(segments=[TranscriptEdit(id="wrong-id", text="ast.parse")]),
            )

    def test_sentence_fragments_are_merged_for_manifest(self) -> None:
        segments = [
            TranscriptSegment(
                id="speaker-00001",
                speaker="speaker",
                startSeconds=0,
                endSeconds=24,
                text="Завершённая мысль. Начало следующей",
            ),
            TranscriptSegment(
                id="speaker-00002",
                speaker="speaker",
                startSeconds=24,
                endSeconds=48,
                text="мысли. Начало третьей",
            ),
            TranscriptSegment(
                id="speaker-00003",
                speaker="speaker",
                startSeconds=48,
                endSeconds=60,
                text="мысли.",
            ),
            TranscriptSegment(
                id="guest-00001",
                speaker="guest",
                startSeconds=60,
                endSeconds=72,
                text="Другой спикер",
            ),
        ]

        merged = merge_sentence_fragments(segments)

        self.assertEqual(
            [segment.id for segment in merged],
            ["speaker-00001", "speaker-00002", "guest-00001"],
        )
        self.assertEqual(
            merged[0].text,
            "Завершённая мысль. Начало следующей мысли.",
        )
        self.assertEqual(merged[0].endSeconds, merged[1].startSeconds)
        self.assertEqual(merged[1].text, "Начало третьей мысли.")

    def test_episode_id_is_safe_path_component(self) -> None:
        config = load_config(Path(__file__).parent.parent / "episode.example.toml")
        self.assertTrue(config.artifacts[0].local_source.is_absolute())
        self.assertEqual(config.audio_output.name, "example.mp3")
        self.assertEqual(config.audio_output.parent.name, "build")
        self.assertEqual(config.manifest_output, config.output_dir / "manifest.json")
        invalid = config.model_dump()
        invalid["episode_id"] = "../other-episode"
        with self.assertRaisesRegex(ValidationError, "episode_id"):
            EpisodeConfig.model_validate(invalid)

    def test_trim_transcript_starts_at_selected_segment(self) -> None:
        transcript = TranscriptDocument(
            episodeId="episode",
            segments=[
                TranscriptSegment(
                    id="internal",
                    speaker="host",
                    startSeconds=90,
                    endSeconds=98,
                    text="Внутренний разговор",
                ),
                TranscriptSegment(
                    id="intro",
                    speaker="host",
                    startSeconds=99.196,
                    endSeconds=123.135,
                    text="Всем привет",
                ),
            ],
        )
        trimmed = trim_transcript(transcript, 99.196)
        self.assertEqual([segment.id for segment in trimmed.segments], ["host-00001"])
        self.assertEqual(trimmed.segments[0].startSeconds, 0)
        self.assertEqual(trimmed.segments[0].endSeconds, 23.939)


if __name__ == "__main__":
    unittest.main()
