from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lecture_util.models import Segment, Transcript
from lecture_util.pipeline import transcription_stage
from lecture_util.state import create_workspace
from lecture_util.transcription import save_transcript


class PipelineTests(unittest.TestCase):
    def test_transcription_is_reused_only_for_matching_options(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths, state = create_workspace(
                "https://example.com/index.m3u8",
                Path(directory),
            )
            paths.audio.touch()
            existing = Transcript(
                language="ko",
                duration=1,
                engine="test",
                requested_model="large-v3",
                effective_model="large-v3",
                segments=[Segment(0, 1, "existing")],
            )
            save_transcript(
                existing,
                paths.transcript_json,
                paths.transcript_markdown,
                paths.transcript_srt,
            )
            state.start_stage(
                "transcription",
                requested_model="large-v3",
                requested_language="auto",
                requested_device="cpu",
            )
            state.complete_stage("transcription")

            with patch("lecture_util.pipeline.transcribe_audio") as transcribe:
                reused = transcription_stage(
                    paths,
                    state,
                    model="large-v3",
                    language="auto",
                    device="cpu",
                )
            transcribe.assert_not_called()
            self.assertEqual(reused.segments[0].text, "existing")

            replacement = Transcript(
                language="en",
                duration=1,
                engine="test",
                requested_model="large-v3",
                effective_model="large-v3",
                segments=[Segment(0, 1, "replacement")],
            )
            with patch("lecture_util.pipeline.transcribe_audio", return_value=replacement) as transcribe:
                result = transcription_stage(
                    paths,
                    state,
                    model="large-v3",
                    language="en",
                    device="cpu",
                )
            transcribe.assert_called_once()
            self.assertEqual(result.segments[0].text, "replacement")


if __name__ == "__main__":
    unittest.main()
