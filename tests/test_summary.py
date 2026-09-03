from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path

from lecture_util.models import Segment, Transcript
from lecture_util.progress import ProgressEvent
from lecture_util.summary import DEFAULT_PROMPT, MERGE_PROMPT, chunk_lines, summarize_transcript


@dataclass
class FakeSummarizer:
    model: str | None = "fake-model"
    name: str = "fake"
    prompts: list[str] = field(default_factory=list)

    def generate(self, developer_prompt: str, user_prompt: str) -> str:
        self.prompts.append(user_prompt)
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
    def test_chunk_lines_preserves_all_content(self) -> None:
        lines = ["a" * 600, "b" * 600, "c" * 600]
        chunks = chunk_lines(lines, 1000)
        self.assertEqual(chunks, lines)

    def test_summary_chunks_and_merges(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summarizer = FakeSummarizer()
            events: list[ProgressEvent] = []
            result, fingerprint = summarize_transcript(
                sample_transcript(),
                summarizer,
                Path(directory),
                chunk_chars=1000,
                progress=events.append,
            )
            self.assertTrue(result.startswith("# Note"))
            self.assertEqual(len(fingerprint), 16)
            self.assertGreater(len(summarizer.prompts), 1)
            self.assertTrue(summarizer.prompts[0].startswith(f"{DEFAULT_PROMPT}\n\n```\n"))
            self.assertIn("segment 0", summarizer.prompts[0])
            self.assertTrue(summarizer.prompts[0].endswith("```"))
            self.assertTrue(any(prompt.startswith(MERGE_PROMPT) for prompt in summarizer.prompts))
            self.assertTrue(any("transcript chunk 1/" in event.message for event in events))
            self.assertTrue(any("Merging notes" in event.message for event in events))

    def test_completed_chunks_are_reused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work_dir = Path(directory)
            first = FakeSummarizer()
            first_result, _ = summarize_transcript(
                sample_transcript(2), first, work_dir, chunk_chars=1000
            )
            second = FakeSummarizer()
            second_result, _ = summarize_transcript(
                sample_transcript(2), second, work_dir, chunk_chars=1000
            )
            self.assertEqual(first_result, second_result)
            self.assertEqual(second.prompts, [])

    def test_default_user_prompt_is_exact_korean_request(self) -> None:
        self.assertEqual(DEFAULT_PROMPT, "이 강의를 요약해")


if __name__ == "__main__":
    unittest.main()
