import unittest
from pathlib import Path

import click
from pydantic import ValidationError

from manifestator.audio import parse_loudness, split_speech
from manifestator.config import EpisodeConfig, load_config
from manifestator.models import (
    TranscriptDocument,
    TranscriptEdit,
    TranscriptEdits,
    TranscriptSegment,
)
from manifestator.transcript import apply_transcript_edits, trim_transcript


class ManifestatorTest(unittest.TestCase):
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
            id="ozhegov-00001",
            speaker="ozhegov",
            startSeconds=3267,
            endSeconds=3289,
            text="АСТ-Парс",
        )
        cleaned = apply_transcript_edits(
            [segment],
            TranscriptEdits(
                segments=[TranscriptEdit(id="ozhegov-00001", text="ast.parse")]
            ),
        )
        self.assertEqual(
            TranscriptDocument(episodeId="telecom-162", segments=cleaned).model_dump(),
            {
                "episodeId": "telecom-162",
                "segments": [
                    {
                        "id": "ozhegov-00001",
                        "speaker": "ozhegov",
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
