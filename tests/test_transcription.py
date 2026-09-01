from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lecture_util.models import Segment, Transcript
from lecture_util.transcription import (
    format_timestamp,
    is_out_of_memory,
    transcript_srt,
    transcribe_audio,
)


class TranscriptionTests(unittest.TestCase):
    def test_timestamp_and_srt_rendering(self) -> None:
        self.assertEqual(format_timestamp(3661.234), "01:01:01:234")
        transcript = Transcript(
            language="ko",
            duration=2,
            engine="test",
            requested_model="large-v3",
            effective_model="large-v3",
            segments=[Segment(0, 1.25, "hello")],
        )
        self.assertIn("00:00:00,000 --> 00:00:01,250", transcript_srt(transcript))

    def test_oom_detection_is_specific(self) -> None:
        self.assertTrue(is_out_of_memory(RuntimeError("CUDA out of memory")))
        self.assertTrue(is_out_of_memory(MemoryError()))
        self.assertFalse(is_out_of_memory(RuntimeError("model download failed")))

    def test_large_v3_retries_turbo_only_after_oom(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "audio.wav"
            audio.touch()
            calls: list[str] = []

            def fake_transcribe(
                _audio: Path, model: str, _language: str, _device: str
            ) -> tuple[list[Segment], str, float]:
                calls.append(model)
                if model == "large-v3":
                    raise RuntimeError("CUDA out of memory")
                return [Segment(0, 1, "ok")], "ko", 1

            with (
                patch("lecture_util.transcription.detect_device", return_value="cuda"),
                patch("lecture_util.transcription._transcribe_faster", side_effect=fake_transcribe),
            ):
                result = transcribe_audio(audio)

            self.assertEqual(calls, ["large-v3", "turbo"])
            self.assertEqual(result.effective_model, "turbo")
            self.assertIn("out of memory", result.fallback_reason or "")

    def test_non_oom_error_is_not_retried(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "audio.wav"
            audio.touch()
            with (
                patch("lecture_util.transcription.detect_device", return_value="cuda"),
                patch(
                    "lecture_util.transcription._transcribe_faster",
                    side_effect=RuntimeError("bad model"),
                ) as transcribe,
            ):
                with self.assertRaisesRegex(RuntimeError, "bad model"):
                    transcribe_audio(audio)
            self.assertEqual(transcribe.call_count, 1)


if __name__ == "__main__":
    unittest.main()
