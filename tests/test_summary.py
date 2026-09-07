from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch

from lecture_util.models import Segment, Transcript
from lecture_util.progress import ProgressEvent
from lecture_util.state import create_workspace
from lecture_util.summary import (
    DEFAULT_PROMPT,
    DEVELOPER_PROMPT,
    summarize_transcript,
    summary_fingerprint,
    summary_stage,
)


@dataclass
class FakeSummarizer:
    model: str | None = "fake-model"
    name: str = "fake"
    prompts: list[str] = field(default_factory=list)
    developer_prompts: list[str] = field(default_factory=list)
    transcript_paths: list[Path] = field(default_factory=list)
    reasoning_effort: str | None = None

    def generate(
        self,
        developer_prompt: str,
        user_prompt: str,
        transcript_path: Path,
    ) -> str:
        self.developer_prompts.append(developer_prompt)
        self.prompts.append(user_prompt)
        self.transcript_paths.append(transcript_path)
        return f"# Note {len(self.prompts)}\n\nA compact result."


def sample_transcript(segment_count: int = 8) -> Transcript:
    return Transcript(
        language="ko",
        duration=float(segment_count),
        engine="test",
        requested_model="large-v3",
        effective_model="large-v3",
        segments=[
            Segment(index, index + 1, f"segment {index} " + "x" * 300)
            for index in range(segment_count)
        ],
    )


class SummaryTests(unittest.TestCase):
    def test_effort_change_regenerates_summary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths, state = create_workspace(
                "https://example.com/index.m3u8", Path(directory),
            )
            paths.transcript_markdown.write_text("Transcript", encoding="utf-8")
            for effort, expected_calls in [("low", 1), ("low", 0), ("high", 1)]:
                summarizer = FakeSummarizer(reasoning_effort=effort)
                summary_stage(
                    sample_transcript(), paths.transcript_markdown,
                    paths.summary, state, summarizer,
                )
                self.assertEqual(len(summarizer.prompts), expected_calls)

    def test_summary_reads_one_transcript_file_in_one_codex_instance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summarizer = FakeSummarizer()
            events: list[ProgressEvent] = []
            transcript_path = Path(directory) / "transcript.md"
            transcript_path.write_text("# Transcript\n\nsegment 0", encoding="utf-8")
            result, fingerprint = summarize_transcript(
                sample_transcript(),
                transcript_path,
                summarizer,
                progress=events.append,
            )
            self.assertTrue(result.startswith("# Note"))
            self.assertEqual(len(fingerprint), 16)
            self.assertEqual(summarizer.prompts, [DEFAULT_PROMPT])
            self.assertEqual(summarizer.developer_prompts, [DEVELOPER_PROMPT])
            self.assertEqual(summarizer.transcript_paths, [transcript_path])
            self.assertNotIn("segment 0", summarizer.prompts[0])
            self.assertNotIn("```", summarizer.prompts[0])
            self.assertTrue(any("transcript.md" in event.message for event in events))

    def test_completed_summary_is_reused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths, state = create_workspace(
                "https://example.com/index.m3u8",
                Path(directory),
            )
            paths.transcript_markdown.write_text("# Transcript\n", encoding="utf-8")
            first = FakeSummarizer()
            first_result = summary_stage(
                sample_transcript(2),
                paths.transcript_markdown,
                paths.summary,
                state,
                first,
            )
            second = FakeSummarizer()
            second_result = summary_stage(
                sample_transcript(2),
                paths.transcript_markdown,
                paths.summary,
                state,
                second,
            )
            self.assertEqual(first_result, second_result)
            self.assertEqual(second.prompts, [])

            paths.transcript_markdown.write_text("# Changed transcript\n", encoding="utf-8")
            third = FakeSummarizer()
            summary_stage(
                sample_transcript(2),
                paths.transcript_markdown,
                paths.summary,
                state,
                third,
            )
            self.assertEqual(third.prompts, [DEFAULT_PROMPT])

    def test_default_prompt_requests_structured_review_notes(self) -> None:
        self.assertIn("핵심 개념", DEFAULT_PROMPT)
        self.assertIn("복습", DEFAULT_PROMPT)
        self.assertIn("정의", DEFAULT_PROMPT)
        self.assertIn("반복되는 설명", DEFAULT_PROMPT)

    def test_developer_prompt_defines_quality_and_output_constraints(self) -> None:
        for instruction in (
            "Read the entire attached transcript",
            "Use only the transcript as evidence",
            "main language of the lecture",
            "obvious transcription error",
            "level-one or level-two headings",
            "transcript timestamps",
        ):
            self.assertIn(instruction, DEVELOPER_PROMPT)

    def test_developer_prompt_participates_in_summary_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transcript_path = Path(directory) / "transcript.md"
            transcript_path.write_text("# Transcript\n", encoding="utf-8")
            transcript = sample_transcript(2)
            summarizer = FakeSummarizer()
            original = summary_fingerprint(
                transcript,
                transcript_path,
                summarizer,
                DEFAULT_PROMPT,
            )
            with patch("lecture_util.summary.DEVELOPER_PROMPT", "Revised instructions"):
                revised = summary_fingerprint(
                    transcript,
                    transcript_path,
                    summarizer,
                    DEFAULT_PROMPT,
                )
            self.assertNotEqual(original, revised)


if __name__ == "__main__":
    unittest.main()
