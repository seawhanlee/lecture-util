from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from lecture_util.cli import _execute_run, app, normalize_tags
from lecture_util.models import RunOptions
from lecture_util.summary import DEFAULT_PROMPT


URL = "https://example.com/lecture/index.m3u8"


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

    def test_noninteractive_tags_are_passed_to_shared_options(self) -> None:
        with patch("lecture_util.cli._execute_run") as execute:
            result = self.runner.invoke(
                app,
                [
                    "run",
                    URL,
                    "--tag",
                    " os,exam ",
                    "--tag",
                    "os",
                ],
            )
        self.assertEqual(result.exit_code, 0, result.output)
        options = execute.call_args.args[0]
        self.assertEqual(options.tags, ["os", "exam"])
        self.assertEqual(options.urls, [URL])

    def test_help_has_no_backend_or_api_options(self) -> None:
        result = self.runner.invoke(app, ["run", "--help"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertNotIn("--summarizer", result.output)
        self.assertNotIn("--base-url", result.output)
        self.assertNotIn("--api-key-env", result.output)
        self.assertIn("--llm-model", result.output)
        self.assertNotIn("--chunk-chars", result.output)

    def test_each_url_gets_a_distinct_codex_instance(self) -> None:
        options = RunOptions(
            urls=[URL, "https://example.com/lecture-2/index.m3u8"],
            llm_model="test-model",
            output_dir=Path("output"),
            title=None,
            tags=None,
            whisper_model="large-v3",
            language="auto",
            device="cpu",
            prompt="summarize",
            force=False,
        )
        first = object()
        second = object()
        with (
            patch("lecture_util.cli.CodexSummarizer", side_effect=[first, second]) as factory,
            patch("lecture_util.cli.run_lecture") as run_lecture,
        ):
            run_lecture.return_value.root = Path("output/lecture")
            _execute_run(options)
        self.assertEqual(factory.call_count, 2)
        self.assertIs(run_lecture.call_args_list[0].args[2], first)
        self.assertIs(run_lecture.call_args_list[1].args[2], second)

    def test_bare_command_passes_tui_options_to_pipeline(self) -> None:
        options = RunOptions(
            urls=[URL],
            llm_model=None,
            output_dir=Path("output"),
            title=None,
            tags=["os", "exam"],
            whisper_model="large-v3",
            language="ko",
            device="cpu",
            prompt=DEFAULT_PROMPT,
            force=False,
        )
        with (
            patch("lecture_util.cli._interactive_terminal", return_value=True),
            patch("lecture_util.cli.run_tui", return_value=options),
            patch("lecture_util.cli._execute_run") as execute,
        ):
            result = self.runner.invoke(app, [])
        self.assertEqual(result.exit_code, 0, result.output)
        execute.assert_called_once_with(options)

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
