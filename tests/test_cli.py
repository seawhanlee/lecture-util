from __future__ import annotations

import unittest
from unittest.mock import patch

from typer.testing import CliRunner

from lecture_util.cli import app, normalize_tags


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

    def test_interactive_wizard_builds_shared_options(self) -> None:
        answers = "\n".join(
            [
                "",  # single source
                URL,
                "",  # title
                "os, exam, os",
                "",  # output
                "n",  # force
                "cpu",
                "",  # large-v3
                "ko",
                "",  # configured model
                "",  # default prompt
                "n",  # advanced
                "y",  # start
                "",
            ]
        )
        with (
            patch("lecture_util.cli._interactive_terminal", return_value=True),
            patch("lecture_util.cli._execute_run") as execute,
        ):
            result = self.runner.invoke(app, [], input=answers)
        self.assertEqual(result.exit_code, 0, result.output)
        options = execute.call_args.args[0]
        self.assertEqual(options.urls, [URL])
        self.assertEqual(options.tags, ["os", "exam"])
        self.assertEqual(options.device, "cpu")
        self.assertEqual(options.language, "ko")

    def test_interactive_cancel_does_not_start_pipeline(self) -> None:
        answers = "\n".join(
            [
                "",
                URL,
                "",
                "",
                "",
                "n",
                "cpu",
                "",
                "",
                "",
                "",
                "n",
                "n",
                "",
            ]
        )
        with (
            patch("lecture_util.cli._interactive_terminal", return_value=True),
            patch("lecture_util.cli._execute_run") as execute,
        ):
            result = self.runner.invoke(app, [], input=answers)
        self.assertEqual(result.exit_code, 0, result.output)
        execute.assert_not_called()
        self.assertIn("Cancelled before processing", result.output)


if __name__ == "__main__":
    unittest.main()
