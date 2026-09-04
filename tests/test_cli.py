from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from lecture_util.cli import _execute_run, app, normalize_tags
from lecture_util.models import LecturePaths, RunOptions
from lecture_util.summary import DEFAULT_PROMPT
from lecture_util.vault import COURSES_DIRECTORY


URL = "https://example.com/lecture/index.m3u8"
COURSE = "공기역학특론"


def options() -> RunOptions:
    return RunOptions(
        url=URL,
        course=COURSE,
        lecture_date="2026-09-04",
        title="압축성 유동",
        llm_model=None,
        tags=["os", "exam"],
        whisper_model="large-v3",
        language="ko",
        device="cpu",
        prompt=DEFAULT_PROMPT,
        force=False,
    )


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def test_normalize_tags_trims_splits_and_preserves_order(self) -> None:
        self.assertEqual(normalize_tags([" os, exam ", "os", "OS"]), ["os", "exam", "OS"])
        self.assertIsNone(normalize_tags(None))
        self.assertEqual(normalize_tags([" , "]), [])

    def test_non_tty_bare_command_never_waits_for_input(self) -> None:
        result = self.runner.invoke(app, [])
        self.assertEqual(result.exit_code, 2)
        self.assertIn("not interactive", result.output)

    def test_run_metadata_and_tags_are_passed_to_shared_options(self) -> None:
        with patch("lecture_util.cli._execute_run") as execute:
            result = self.runner.invoke(
                app,
                [
                    "run",
                    URL,
                    "--course",
                    COURSE,
                    "--date",
                    "2026-09-04",
                    "--title",
                    "압축성 유동",
                    "--tag",
                    " os,exam ",
                    "--tag",
                    "os",
                ],
            )
        self.assertEqual(result.exit_code, 0, result.output)
        selected = execute.call_args.args[0]
        self.assertEqual(selected.tags, ["os", "exam"])
        self.assertEqual(selected.url, URL)
        self.assertEqual(selected.course, COURSE)
        self.assertEqual(selected.lecture_date, "2026-09-04")

    def test_help_exposes_single_lecture_vault_options(self) -> None:
        result = self.runner.invoke(app, ["run", "--help"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("--course", result.output)
        self.assertIn("--date", result.output)
        self.assertIn("--title", result.output)
        self.assertNotIn("--input", result.output)
        self.assertNotIn("--output-dir", result.output)
        self.assertIn("--llm-model", result.output)

    def test_execute_run_uses_cache_and_publishes_notes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vault = root / "vault"
            cache = root / "cache"
            lecture_dir = vault / COURSES_DIRECTORY / COURSE / "Lectures"
            lecture_dir.mkdir(parents=True)
            cached = LecturePaths(cache / "lecture-test")
            cached.root.mkdir(parents=True)
            cached.summary.write_text("### 핵심\n\n- 마하수\n", encoding="utf-8")
            cached.transcript_markdown.write_text(
                "# Transcript\n\n[00:00:00.000–00:00:01.000] 내용\n",
                encoding="utf-8",
            )

            with (
                patch("lecture_util.cli.CodexSummarizer") as factory,
                patch("lecture_util.cli.run_lecture", return_value=cached) as run_lecture,
            ):
                _execute_run(options(), vault_root=vault, cache_root=cache)

            factory.assert_called_once_with(model=None)
            self.assertEqual(run_lecture.call_args.args[0], URL)
            self.assertEqual(run_lecture.call_args.args[1], cache)
            self.assertEqual(run_lecture.call_args.kwargs["course"], COURSE)
            self.assertEqual(
                run_lecture.call_args.kwargs["lecture_date"], "2026-09-04"
            )
            summary = lecture_dir / "2026-09-04 압축성 유동.md"
            transcript = lecture_dir / "2026-09-04 압축성 유동 전사.md"
            self.assertTrue(summary.is_file())
            self.assertTrue(transcript.is_file())

    def test_existing_note_stops_before_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vault = root / "vault"
            lecture_dir = vault / COURSES_DIRECTORY / COURSE / "Lectures"
            lecture_dir.mkdir(parents=True)
            (lecture_dir / "2026-09-04 압축성 유동.md").write_text(
                "existing", encoding="utf-8"
            )
            with (
                patch("lecture_util.cli.CodexSummarizer") as factory,
                patch("lecture_util.cli.run_lecture") as run_lecture,
                self.assertRaisesRegex(Exception, "already exists"),
            ):
                _execute_run(options(), vault_root=vault, cache_root=root / "cache")
            factory.assert_not_called()
            run_lecture.assert_not_called()

    def test_bare_command_passes_tui_options_to_pipeline(self) -> None:
        selected = options()
        with (
            patch("lecture_util.cli._interactive_terminal", return_value=True),
            patch("lecture_util.cli.run_tui", return_value=selected),
            patch("lecture_util.cli._execute_run") as execute,
        ):
            result = self.runner.invoke(app, [])
        self.assertEqual(result.exit_code, 0, result.output)
        execute.assert_called_once_with(selected)

    def test_tui_cancel_does_not_start_pipeline(self) -> None:
        with (
            patch("lecture_util.cli._interactive_terminal", return_value=True),
            patch("lecture_util.cli.run_tui", return_value=None),
            patch("lecture_util.cli._execute_run") as execute,
        ):
            result = self.runner.invoke(app, [])
        self.assertEqual(result.exit_code, 0, result.output)
        execute.assert_not_called()
        self.assertIn("Cancelled before processing", result.output)


if __name__ == "__main__":
    unittest.main()
