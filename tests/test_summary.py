from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from lecture_util.errors import LectureUtilError
from lecture_util.models import Segment, Transcript
from lecture_util.progress import ProgressEvent
from lecture_util.state import create_workspace
from lecture_util.summary import (
    CORRECTION_DEVELOPER_PROMPT,
    DEFAULT_PROMPT,
    DEVELOPER_PROMPT,
    summarize_transcript,
    summary_fingerprint,
    summary_stage,
)
from lecture_util.transcription import save_transcript


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

    def test_revised_format_regenerates_cached_summary_with_custom_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths, state = create_workspace(
                "https://example.com/index.m3u8", Path(directory),
            )
            paths.transcript_markdown.write_text("Transcript", encoding="utf-8")
            transcript = sample_transcript(2)
            summarizer = FakeSummarizer()
            custom_prompt = "Focus on definitions."
            with patch("lecture_util.summary.DEVELOPER_PROMPT", "Return plain Markdown."):
                summary_stage(
                    transcript, paths.transcript_markdown, paths.summary,
                    state, summarizer, prompt=custom_prompt,
                )
            summary_stage(
                transcript, paths.transcript_markdown, paths.summary,
                state, summarizer, prompt=custom_prompt,
            )
            summary_stage(
                transcript, paths.transcript_markdown, paths.summary,
                state, summarizer, prompt=custom_prompt,
            )
            self.assertEqual(summarizer.prompts, [custom_prompt, custom_prompt])
            self.assertEqual(summarizer.developer_prompts[-1], DEVELOPER_PROMPT)
            self.assertIn("Obsidian Flavored Markdown", summarizer.developer_prompts[-1])
            self.assertEqual(paths.summary.read_text(), "# Note 2\n\nA compact result.\n")

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

    def test_summary_cached_returns_false_on_corrupt_transcript(self) -> None:
        from lecture_util.pipeline import summary_cached
        with tempfile.TemporaryDirectory() as directory:
            paths, state = create_workspace(
                "https://example.com/index.m3u8", Path(directory),
            )
            state.complete_stage("summary", status="complete", fingerprint="fp", sha256="hash")
            paths.transcript_json.write_text("{corrupt json")
            paths.transcript_markdown.write_text("# Transcript")
            paths.summary.write_text("# Summary")
            self.assertFalse(summary_cached(paths, state, FakeSummarizer(), DEFAULT_PROMPT))


if __name__ == "__main__":
    unittest.main()


@pytest.fixture
def summary_workspace(tmp_path):
    paths, state = create_workspace("https://example.com/index.m3u8", tmp_path)
    transcript = Transcript.from_dict(sample_transcript(2).to_dict())
    save_transcript(transcript, paths.transcript_json, paths.transcript_markdown,
                    paths.transcript_srt)
    return paths, state, transcript


@pytest.mark.parametrize("draft", ["[[broken", "", "本文\n$$\nx"])
def test_summary_corrects_once_using_same_model_and_source(summary_workspace, draft):
    import json
    paths, state, transcript = summary_workspace
    summarizer = FakeSummarizer(reasoning_effort="high")
    summarizer.generate = Mock(side_effect=[draft, "# 그대로\n[[#그대로|링크]]"])
    events = []
    result = summary_stage(transcript, paths.transcript_markdown, paths.summary,
                           state, summarizer, prompt="사용자 강조점", progress=events.append)
    assert result == "# 그대로\n[[#그대로|링크]]\n"
    assert paths.summary.read_text() == result
    assert state.stage_complete("summary")
    assert summarizer.generate.call_count == 2
    first, correction = summarizer.generate.call_args_list
    assert first.args[0] == DEVELOPER_PROMPT
    assert correction.args[0] == CORRECTION_DEVELOPER_PROMPT
    assert correction.args[2] == paths.transcript_markdown
    request = json.loads(correction.args[1])
    assert request["draft"] == draft
    assert request["original_request"] == "사용자 강조점"
    assert request["issues"][0]["line"] in {1, 2}
    assert any(event.status == "warning" for event in events)
    assert summarizer.model == "fake-model" and summarizer.reasoning_effort == "high"


@pytest.mark.parametrize("failure", ["[[still broken", LectureUtilError("Model failed"),
                                    KeyboardInterrupt()])
def test_failed_correction_preserves_previous_file_and_stages(summary_workspace, failure):
    paths, state, transcript = summary_workspace
    paths.summary.write_text("Previous result")
    state.complete_stage("transcription", sha256=state.digest(paths.transcript_json))
    before = dict(state.data["stages"]["transcription"])
    summarizer = FakeSummarizer()
    summarizer.generate = Mock(side_effect=["[[broken", failure])
    expected = KeyboardInterrupt if isinstance(failure, KeyboardInterrupt) else LectureUtilError
    with pytest.raises(expected) as caught:
        summary_stage(transcript, paths.transcript_markdown, paths.summary, state, summarizer)
    if isinstance(failure, str):
        assert "line 1 [unclosed_wikilink]" in str(caught.value)
    assert summarizer.generate.call_count == 2
    assert paths.summary.read_text() == "Previous result"
    assert state.data["stages"]["summary"]["status"] == "failed"
    assert state.data["stages"]["transcription"] == before


@pytest.mark.parametrize("fixed", [True, False])
def test_invalid_legacy_cache_is_corrected_once(summary_workspace, fixed):
    from lecture_util.pipeline import summary_cached
    paths, state, transcript = summary_workspace
    summarizer = FakeSummarizer()
    paths.summary.write_text("[[legacy broken")
    state.complete_stage("summary", fingerprint=summary_fingerprint(
        transcript, paths.transcript_markdown, summarizer, DEFAULT_PROMPT,
    ), sha256=state.digest(paths.summary))
    assert not summary_cached(paths, state, summarizer, DEFAULT_PROMPT)
    summarizer.generate = Mock(return_value="[[fixed]]" if fixed else "[[still broken")
    if fixed:
        summary_stage(transcript, paths.transcript_markdown, paths.summary, state, summarizer)
        assert paths.summary.read_text() == "[[fixed]]\n"
        assert summary_cached(paths, state, summarizer, DEFAULT_PROMPT)
        summary_stage(transcript, paths.transcript_markdown, paths.summary, state, summarizer)
    else:
        with pytest.raises(LectureUtilError, match="after one correction"):
            summary_stage(transcript, paths.transcript_markdown, paths.summary, state, summarizer)
        assert paths.summary.read_text() == "[[legacy broken"
    summarizer.generate.assert_called_once()
    assert summarizer.generate.call_args.args[0] == CORRECTION_DEVELOPER_PROMPT


def test_invalid_new_summary_is_never_written(summary_workspace):
    paths, state, transcript = summary_workspace
    summarizer = FakeSummarizer()
    summarizer.generate = Mock(return_value="[[broken")
    with pytest.raises(LectureUtilError):
        summary_stage(transcript, paths.transcript_markdown, paths.summary, state, summarizer)
    assert not paths.summary.exists()


def test_failed_validation_blocks_publication_and_resume_reuses_transcription(tmp_path, monkeypatch):
    from io import StringIO

    from rich.console import Console

    from lecture_util.models import LecturePaths, LectureSource, RunOptions
    from lecture_util.pipeline import execute_run
    from lecture_util.recovery import load_request
    from lecture_util.state import RunState, file_digest, lecture_id
    from lecture_util.vault import COURSES_DIRECTORY

    vault = tmp_path / "vault"
    notes = vault / COURSES_DIRECTORY / "Course" / "Lectures"
    notes.mkdir(parents=True)
    media = tmp_path / "lecture.wav"
    media.write_bytes(b"original recording")
    source = LectureSource("audio", str(media), file_digest(media))
    options = RunOptions(str(media), "Course", "2026-09-07", "Title", "fake-model",
                         None, "large-v3", "ko", "cpu", DEFAULT_PROMPT, False,
                         semester_start="2026-08-31", source=source)
    transcript = Transcript.from_dict(sample_transcript(2).to_dict())
    transcribe = Mock(return_value=transcript)
    extract = Mock(side_effect=lambda source, target: target.write_bytes(b"prepared audio"))
    summarizer = FakeSummarizer()
    summarizer.generate = Mock(side_effect=["[[broken", "[[still broken", "# Valid OFM"])
    monkeypatch.setattr("lecture_util.pipeline.CodexSummarizer", lambda **kwargs: summarizer)
    monkeypatch.setattr("lecture_util.pipeline.preflight_transcription", Mock())
    monkeypatch.setattr("lecture_util.pipeline.transcribe_audio", transcribe)
    monkeypatch.setattr("lecture_util.pipeline.extract_audio", extract)
    monkeypatch.setattr("lecture_util.media.require_executable", lambda name: name)
    monkeypatch.setattr("lecture_util.recovery.resolve_source", lambda url: source)
    cache = tmp_path / "cache"
    videos = tmp_path / "videos"
    console = Console(file=StringIO(), width=120)
    with pytest.raises(LectureUtilError, match="OFM validation failed"):
        execute_run(options, vault_root=vault, video_root=videos, cache_root=cache,
                    console=console)
    paths = LecturePaths(cache / f"lecture-{lecture_id(source.cache_key)}")
    assert not paths.summary.exists()
    assert not list(notes.rglob("*.md"))
    assert not (paths.root / "publication.json").exists()
    assert RunState(paths, read_only=True).data["stages"]["summary"]["status"] == "failed"
    resumed, saved_vault, saved_videos = load_request(paths.root)
    execute_run(resumed, vault_root=saved_vault, video_root=saved_videos,
                cache_root=cache, console=console)
    transcribe.assert_called_once()
    extract.assert_called_once()
    assert summarizer.generate.call_count == 3
    assert len(list(notes.rglob("*.md"))) == 2
    assert media.read_bytes() == b"original recording"
