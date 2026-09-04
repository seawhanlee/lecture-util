from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from textual.widgets import Input, Label, Select, TextArea

from lecture_util.summary import DEFAULT_PROMPT
from lecture_util.tui import LectureSetupApp


URL = "https://example.com/lecture/index.m3u8"


class TuiTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_form_builds_run_options(self) -> None:
        app = LectureSetupApp()
        async with app.run_test(size=(100, 40)) as pilot:
            app.query_one("#source", Input).value = URL
            app.query_one("#tags", Input).value = " os, exam, os "
            await pilot.click("#run")

        options = app.return_value
        self.assertIsNotNone(options)
        assert options is not None

        self.assertEqual(options.urls, [URL])
        self.assertEqual(options.output_dir, Path("output"))
        self.assertEqual(options.tags, ["os", "exam"])
        self.assertEqual(options.device, "auto")
        self.assertEqual(options.whisper_model, "large-v3")
        self.assertEqual(options.language, "auto")
        self.assertEqual(options.prompt, DEFAULT_PROMPT)
        self.assertFalse(options.force)

    async def test_list_and_prompt_file_modes_are_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            url_list = root / "lectures.txt"
            url_list.write_text(f"# Week 1\n{URL}\n", encoding="utf-8")
            prompt_file = root / "prompt.md"
            prompt_file.write_text("Create exam notes.", encoding="utf-8")

            app = LectureSetupApp()
            async with app.run_test(size=(100, 40)):
                app.query_one("#source-mode", Select).value = "list"
                app.query_one("#source", Input).value = str(url_list)
                app.query_one("#prompt-mode", Select).value = "file"
                app.query_one("#prompt-file", Input).value = str(prompt_file)
                options = app._build_options()

        self.assertEqual(options.urls, [URL])
        self.assertEqual(options.prompt, "Create exam notes.")

    async def test_inline_prompt_mode_is_reflected_in_options(self) -> None:
        app = LectureSetupApp()
        async with app.run_test(size=(100, 40)):
            app.query_one("#source", Input).value = URL
            app.query_one("#prompt-mode", Select).value = "inline"
            app.query_one("#inline-prompt", TextArea).text = "한국어로 요약해"
            options = app._build_options()

        self.assertEqual(options.prompt, "한국어로 요약해")

    async def test_submit_shows_validation_error_without_exiting(self) -> None:
        app = LectureSetupApp()
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.click("#run")
            await pilot.pause()
            error = app.query_one("#error", Label)
            self.assertTrue(error.display)
            self.assertIn("Enter a lecture URL", str(error.render()))
            self.assertTrue(app.is_running)

    async def test_escape_cancels(self) -> None:
        app = LectureSetupApp()
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.press("escape")
        self.assertIsNone(app.return_value)


if __name__ == "__main__":
    unittest.main()
