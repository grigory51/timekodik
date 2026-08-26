import hashlib
import io
import json
import tempfile
import unittest
from importlib.resources import files
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import patch

import click
import numpy as np
from click.testing import CliRunner
from pydantic import ValidationError

from manifestator.alignment import (
    align_seconds,
    align_transcripts,
    attribute_final_transcript,
    refine_anchor,
)
from manifestator.audio import parse_loudness, split_speech, track_offsets
from manifestator.cli import cli
from manifestator.common import codex_json, process_lock
from manifestator.config import (
    ArtifactConfig,
    EpisodeConfig,
    config_for_source,
    load_config,
)
from manifestator.glossary_tui import glossary_cli, update_glossary_corpus
from manifestator.model import ensure_transcription_model
from manifestator.models import (
    AlignmentAnchor,
    AlignmentDocument,
    ChaptersDocument,
    GlossaryCandidate,
    GlossaryDocument,
    TranscriptDocument,
    TranscriptEdit,
    TranscriptEdits,
    TranscriptSegment,
)
from manifestator.review import (
    merge_glossary,
    relevant_glossary,
    review_transcript,
    whisper_hypotheses,
)
from manifestator.stages import build_manifest
from manifestator.transcript import (
    apply_partial_transcript_edits,
    merge_sentence_fragments,
    trim_transcript,
)
from manifestator.transcription import timestamped_sentences


class ManifestatorTest(unittest.TestCase):
    def test_json_schemas_are_packaged_resources(self) -> None:
        schemas = files("manifestator").joinpath("schemas")
        self.assertTrue(schemas.joinpath("chapters.schema.json").is_file())
        self.assertTrue(schemas.joinpath("transcript-review.schema.json").is_file())

    def test_process_lock_rejects_second_writer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "episode.lock"
            with process_lock(path):
                with self.assertRaisesRegex(click.ClickException, "уже обрабатывает"):
                    with process_lock(path):
                        self.fail("Второй writer получил lock")

    def test_codex_fast_service_tier_is_passed_to_cli(self) -> None:
        command: list[str] = []

        def fake_run(arguments: list[str], **_: object) -> None:
            command.extend(arguments)
            output = Path(arguments[arguments.index("--output-last-message") + 1])
            output.write_text("{}", encoding="utf-8")

        with tempfile.TemporaryDirectory() as directory:
            with patch("manifestator.common.run", side_effect=fake_run):
                codex_json(
                    "prompt",
                    "chapters.schema.json",
                    Path(directory),
                    model="gpt-5.6-luna",
                    service_tier="fast",
                )

        self.assertIn('service_tier="fast"', command)

    def test_partial_transcript_edits_leave_other_segments_unchanged(self) -> None:
        segments = [
            TranscriptSegment(
                id="speaker-00001",
                speaker="speaker",
                startSeconds=0,
                endSeconds=1,
                text="МетаПе",
            ),
            TranscriptSegment(
                id="speaker-00002",
                speaker="speaker",
                startSeconds=1,
                endSeconds=2,
                text="Без изменений",
            ),
        ]

        edited = apply_partial_transcript_edits(
            segments,
            TranscriptEdits(
                segments=[TranscriptEdit(id="speaker-00001", text="meetup'e")]
            ),
        )

        self.assertEqual([segment.text for segment in edited], ["meetup'e", "Без изменений"])

    def test_whisper_words_are_assigned_to_one_primary_segment(self) -> None:
        primary = [
            TranscriptSegment(
                id="speaker-00001",
                speaker="speaker",
                startSeconds=0,
                endSeconds=2,
                text="Первая фраза.",
            ),
            TranscriptSegment(
                id="speaker-00002",
                speaker="speaker",
                startSeconds=2,
                endSeconds=4,
                text="Вторая фраза.",
            ),
        ]
        words = [
            TranscriptSegment(
                id="word-1",
                speaker="speaker",
                startSeconds=0.5,
                endSeconds=1,
                text=" Первая",
            ),
            TranscriptSegment(
                id="word-2",
                speaker="speaker",
                startSeconds=2.2,
                endSeconds=2.8,
                text=" Вторая",
            ),
        ]

        self.assertEqual(
            whisper_hypotheses(primary, words),
            {
                "speaker-00001": "Первая",
                "speaker-00002": "Вторая",
            },
        )

    def test_transcript_batches_are_reviewed_in_parallel_and_keep_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            segments = [
                TranscriptSegment(
                    id=f"speaker-{index}",
                    speaker="speaker",
                    startSeconds=float(index),
                    endSeconds=float(index + 1),
                    text=f"Фраза {index}",
                )
                for index in range(2)
            ]
            document = TranscriptDocument(episodeId="episode", segments=segments)
            config = EpisodeConfig(
                episode_id="episode",
                source_dir=root,
                transcription_model=root / "model.gguf",
                output_dir=root / "output",
                tracks=[],
            )
            barrier = Barrier(2)

            def codex_json(*args: object, **kwargs: object) -> str:
                barrier.wait(timeout=5)
                return '{"segments":[],"glossaryCandidates":[]}'

            with (
                patch("manifestator.config.ROOT", root),
                patch("manifestator.review.ROOT", root),
                patch("manifestator.review.TRANSCRIPT_REVIEW_BATCH_SIZE", 1),
                patch("manifestator.review.codex_json", side_effect=codex_json),
            ):
                config.transcript_output.parent.mkdir(parents=True)
                config.transcript_output.write_text(
                    document.model_dump_json(),
                    encoding="utf-8",
                )
                config.whisper_transcript_output.write_text(
                    document.model_dump_json(),
                    encoding="utf-8",
                )
                cleaned, _ = review_transcript(
                    config,
                    "source",
                    True,
                )

            self.assertEqual(
                [segment.id for segment in cleaned.segments],
                ["speaker-0", "speaker-1"],
            )

    def test_glossary_merge_preserves_manual_preference(self) -> None:
        existing = GlossaryDocument(
            candidates=[
                GlossaryCandidate(
                    heard="Нугдеф / Ногдева",
                    suggested="NOC Dev",
                    preferred="NocDev",
                    status="confirmed",
                    segmentIds=["speaker-00001"],
                    context="Ведущий разработчик Нугдеф.",
                )
            ]
        )
        detected = [
            GlossaryCandidate(
                heard="нугдев",
                suggested="NOCDev",
                segmentIds=["speaker-00002"],
                context="Команда Ногдева.",
            )
        ]

        merged = merge_glossary(existing, detected)

        self.assertEqual(len(merged.candidates), 1)
        self.assertEqual(merged.candidates[0].preferred, "NocDev")
        self.assertEqual(
            merged.candidates[0].segmentIds,
            ["speaker-00001", "speaker-00002"],
        )

        self.assertEqual(
            relevant_glossary(merged, ["Ведущий разработчик Ногдева"]),
            {"Нугдеф / Ногдева / нугдев": "NocDev"},
        )

    def test_token_timestamps_form_final_sentences(self) -> None:
        result = SimpleNamespace(
            tokens=[
                SimpleNamespace(text="▁Первая", t0_ms=100, t1_ms=300),
                SimpleNamespace(text="▁фраза", t0_ms=400, t1_ms=600),
                SimpleNamespace(text=".", t0_ms=600, t1_ms=640),
                SimpleNamespace(text="▁Вторая", t0_ms=900, t1_ms=1100),
                SimpleNamespace(text=".", t0_ms=1100, t1_ms=1140),
            ]
        )

        self.assertEqual(
            timestamped_sentences(result),
            [(0.1, 0.64, "Первая фраза."), (0.9, 1.14, "Вторая.")],
        )

    def test_cli_runs_all_stages_without_prompts(self) -> None:
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
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertNotIn("Продолжить?", result.output)
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
                )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertNotIn("Продолжить?", result.output)
        self.assertEqual(config.episode_id, "Мой подкаст")
        self.assertEqual(track_offsets(config), {audio.name: 0.0})
        doctor.assert_called_once_with(config)
        mix.assert_not_called()
        transcribe.assert_called_once_with(config, False)
        clean_transcript.assert_called_once_with(config, False)
        summarize.assert_called_once_with(config, False)
        build_manifest.assert_called_once_with(config)

    def test_glossary_cli_opens_separate_editor(self) -> None:
        config = SimpleNamespace(
            glossary_output=Path(__file__),
            glossary_corpus_output=Path("glossary.json"),
        )
        with (
            patch("manifestator.glossary_tui.load_config", return_value=config),
            patch(
                "manifestator.glossary_tui.load_glossary",
                return_value=GlossaryDocument(
                    candidates=[
                        GlossaryCandidate(
                            heard="Нугдеф",
                            suggested="NocDev",
                            segmentIds=["speaker-00001"],
                            context="Контекст",
                        )
                    ]
                ),
            ),
            patch("manifestator.glossary_tui.edit_glossary", return_value=True) as edit,
        ):
            result = CliRunner().invoke(
                glossary_cli,
                ["--config", "episode.example.toml"],
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Словарь сохранён", result.output)
        edit.assert_called_once_with(config)

    def test_confirmed_glossary_terms_update_shared_corpus(self) -> None:
        old = GlossaryCandidate(
            heard="Нугдеф",
            suggested="NOC Dev",
            preferred="NOC Dev",
            status="confirmed",
            segmentIds=["speaker-00001"],
            context="Старая разметка",
        )
        corrected = old.model_copy(
            update={"preferred": "NocDev", "context": "Новый контекст"}
        )

        corpus = update_glossary_corpus(
            GlossaryDocument(candidates=[old]),
            [corrected],
        )

        self.assertEqual(len(corpus.candidates), 1)
        self.assertEqual(corpus.candidates[0].preferred, "NocDev")
        self.assertEqual(corpus.candidates[0].context, "Новый контекст")
        self.assertEqual(
            update_glossary_corpus(
                corpus,
                [corrected.model_copy(update={"status": "ignored", "preferred": None})],
            ).candidates,
            [],
        )

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

    def test_final_audio_alignment_drops_cut_segments(self) -> None:
        source = TranscriptDocument(
            episodeId="episode",
            segments=[
                TranscriptSegment(
                    id="host-00001",
                    speaker="host",
                    startSeconds=0,
                    endSeconds=10,
                    text="Всем привет это начало",
                ),
                TranscriptSegment(
                    id="host-00002",
                    speaker="host",
                    startSeconds=10,
                    endSeconds=20,
                    text="Внутренний разговор удалён",
                ),
                TranscriptSegment(
                    id="guest-00001",
                    speaker="guest",
                    startSeconds=20,
                    endSeconds=30,
                    text="Теперь полезная часть выпуска",
                ),
            ],
        )
        final = TranscriptDocument(
            episodeId="episode",
            segments=[
                TranscriptSegment(
                    id="final-00001",
                    speaker="final",
                    startSeconds=5,
                    endSeconds=15,
                    text="Всем привет это начало",
                ),
                TranscriptSegment(
                    id="final-00002",
                    speaker="final",
                    startSeconds=15,
                    endSeconds=25,
                    text="Теперь полезная часть выпуска",
                ),
            ],
        )

        alignment = align_transcripts(source, final)

        self.assertEqual(alignment.droppedSegmentIds, ["host-00002"])
        self.assertEqual(align_seconds(22, alignment), 17)

    def test_acoustic_alignment_refines_text_anchor(self) -> None:
        sample_rate = 800
        source = np.random.default_rng(42).normal(0, 0.2, 16 * sample_rate).astype(np.float32)
        final = np.concatenate(
            (np.zeros(3 * sample_rate, dtype=np.float32), source)
        )

        anchor = refine_anchor(
            source,
            final,
            AlignmentAnchor(
                sourceSeconds=8,
                finalSeconds=9,
                confidence=0.8,
            ),
        )

        self.assertIsNotNone(anchor)
        assert anchor is not None
        self.assertAlmostEqual(anchor.finalSeconds, 11, places=2)
        self.assertGreater(anchor.confidence, 0.99)

    def test_final_audio_is_canonical_and_roles_only_assign_speakers(self) -> None:
        source = TranscriptDocument(
            episodeId="episode",
            segments=[
                TranscriptSegment(
                    id="host-00001",
                    speaker="host",
                    startSeconds=0,
                    endSeconds=10,
                    text="Фраза ведущего. Сейчас немного сбился.",
                ),
                TranscriptSegment(
                    id="guest-00001",
                    speaker="guest",
                    startSeconds=0,
                    endSeconds=10,
                    text="Ответ гостя.",
                ),
            ],
        )
        final = TranscriptDocument(
            episodeId="episode",
            segments=[
                TranscriptSegment(
                    id="final-00001",
                    speaker="final",
                    startSeconds=0,
                    endSeconds=2,
                    text="Фраза ведущего.",
                ),
                TranscriptSegment(
                    id="final-00002",
                    speaker="final",
                    startSeconds=3,
                    endSeconds=5,
                    text="Ответ гостя.",
                ),
            ],
        )
        alignment = AlignmentDocument(
            episodeId="episode",
            sourceDurationSeconds=10,
            finalDurationSeconds=10,
            anchors=[
                AlignmentAnchor(
                    sourceSeconds=5,
                    finalSeconds=5,
                    confidence=1,
                )
            ],
            droppedSegmentIds=[],
        )

        attributed = attribute_final_transcript(final, source, alignment)

        self.assertEqual(
            [(segment.speaker, segment.text) for segment in attributed.segments],
            [("host", "Фраза ведущего."), ("guest", "Ответ гостя.")],
        )
        self.assertNotIn(
            "Сейчас немного сбился",
            " ".join(segment.text for segment in attributed.segments),
        )

    def test_role_text_never_replaces_final_text(self) -> None:
        source = TranscriptDocument(
            episodeId="episode",
            segments=[
                TranscriptSegment(
                    id="host-00001",
                    speaker="host",
                    startSeconds=0,
                    endSeconds=5,
                    text="Фраза из финала.",
                )
            ],
        )
        final = TranscriptDocument(
            episodeId="episode",
            segments=[
                TranscriptSegment(
                    id="final-00001",
                    speaker="final",
                    startSeconds=0,
                    endSeconds=2,
                    text="Фраза из финала",
                ),
                TranscriptSegment(
                    id="final-00002",
                    speaker="final",
                    startSeconds=50,
                    endSeconds=52,
                    text="Далёкая фраза из финала.",
                ),
            ],
        )
        alignment = AlignmentDocument(
            episodeId="episode",
            sourceDurationSeconds=5,
            finalDurationSeconds=52,
            anchors=[
                AlignmentAnchor(
                    sourceSeconds=2.5,
                    finalSeconds=2.5,
                    confidence=1,
                )
            ],
            droppedSegmentIds=[],
        )

        attributed = attribute_final_transcript(final, source, alignment)

        self.assertEqual(
            [segment.text for segment in attributed.segments],
            ["Фраза из финала", "Далёкая фраза из финала."],
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

    def test_short_sentences_from_same_speaker_form_readable_block(self) -> None:
        segments = [
            TranscriptSegment(
                id="host-00001",
                speaker="host",
                startSeconds=0,
                endSeconds=1,
                text="Всем привет.",
            ),
            TranscriptSegment(
                id="host-00002",
                speaker="host",
                startSeconds=1.2,
                endSeconds=4,
                text="Это новый выпуск.",
            ),
            TranscriptSegment(
                id="guest-00001",
                speaker="guest",
                startSeconds=4.2,
                endSeconds=5,
                text="Привет.",
            ),
        ]

        merged = merge_sentence_fragments(segments)

        self.assertEqual(
            [(segment.speaker, segment.text) for segment in merged],
            [
                ("host", "Всем привет. Это новый выпуск."),
                ("guest", "Привет."),
            ],
        )

    def test_leading_ellipsis_continues_previous_fragment(self) -> None:
        merged = merge_sentence_fragments(
            [
                TranscriptSegment(
                    id="host-00001",
                    speaker="host",
                    startSeconds=0,
                    endSeconds=1,
                    text="все знания",
                ),
                TranscriptSegment(
                    id="host-00002",
                    speaker="host",
                    startSeconds=1,
                    endSeconds=2,
                    text="...мира выкатят.",
                ),
            ]
        )

        self.assertEqual(merged[0].text, "все знания ...мира выкатят.")

    def test_episode_id_is_safe_path_component(self) -> None:
        config = load_config(Path(__file__).parent.parent / "episode.example.toml")
        source = config.artifacts[0].local_source
        self.assertIsNotNone(source)
        assert source is not None
        self.assertTrue(source.is_absolute())
        self.assertTrue(config.artifacts[1].local_sources[0].is_absolute())
        self.assertEqual(config.audio_output.name, "example.mp3")
        self.assertEqual(config.audio_output.parent.name, "build")
        self.assertEqual(
            config.transcript_output.name,
            "example.transcript.gigaam-v3-e2e-rnnt-Q8_0.json",
        )
        self.assertEqual(
            config.whisper_transcript_output.name,
            "example.transcript.whisper-turbo.json",
        )
        self.assertEqual(config.manifest_output, config.output_dir / "manifest.json")
        invalid = config.model_dump()
        invalid["episode_id"] = "../other-episode"
        with self.assertRaisesRegex(ValidationError, "episode_id"):
            EpisodeConfig.model_validate(invalid)

    def test_manifest_is_compatible_with_timekodik_0_1_0(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "photo.png"
            image.write_bytes(b"png")
            config = EpisodeConfig(
                episode_id="episode",
                source_dir=root,
                output_dir=root / "output",
                tracks=[],
                artifacts=[
                    ArtifactConfig(
                        id="photos",
                        type="gallery",
                        title="Фотографии",
                        start_seconds=10,
                        end_seconds=20,
                        local_sources=[image],
                    )
                ],
            )
            transcript = TranscriptDocument(episodeId="episode", segments=[])
            with (
                patch("manifestator.stages.load_transcript", return_value=transcript),
                patch(
                    "manifestator.stages.load_chapters",
                    return_value=ChaptersDocument(chapters=[]),
                ),
                patch("manifestator.stages.write_debug_stage"),
            ):
                build_manifest(config)

            manifest = json.loads(config.manifest_output.read_text(encoding="utf-8"))

        self.assertEqual(
            manifest["episode"],
            {"id": "episode", "audioUrl": "", "durationSeconds": 20.0},
        )
        self.assertEqual(
            manifest["artifacts"][0]["source"],
            {
                "url": "artifacts/photos-1.png",
                "urls": ["artifacts/photos-1.png"],
            },
        )

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
