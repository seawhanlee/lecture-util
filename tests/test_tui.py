from __future__ import annotations

import tempfile
import unittest
import pytest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from textual.widgets import Collapsible, Input, Label, Select, TextArea

from lecture_util.configuration import AppConfig
from lecture_util.summary import DEFAULT_PROMPT
from lecture_util.tui import LectureSetupApp, _week_monday
from lecture_util.vault import COURSES_DIRECTORY


URL = "https://example.com/lecture/index.m3u8"
COURSE = "공기역학특론"


def create_vault(root: Path) -> None:
    (root / COURSES_DIRECTORY / COURSE / "Lectures").mkdir(parents=True)


def configured_app(vault: Path) -> LectureSetupApp:
    return LectureSetupApp(
        config=AppConfig(
            vault_root=vault,
            video_root=vault.parent / "videos",
            semester_start="2026-09-01",
            whisper_model="turbo",
            language="ko",
            device="cpu",
            llm_model="gpt-test",
        )
    )


class TuiTests(unittest.IsolatedAsyncioTestCase):
    def test_week_monday_handles_week_and_year_boundaries(self) -> None:
        self.assertEqual(_week_monday(date(2026, 9, 7)), "2026-09-07")
        self.assertEqual(_week_monday(date(2026, 9, 13)), "2026-09-07")
        self.assertEqual(_week_monday(date(2026, 1, 1)), "2025-12-29")

    async def test_lecture_date_defaults_to_current_week_monday(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory)
            create_vault(vault)
            app = LectureSetupApp(
                config=AppConfig(
                    vault_root=vault,
                    video_root=vault.parent / "videos",
                    semester_start="2026-08-31",
                )
            )
            with patch(
                "lecture_util.tui._week_monday",
                return_value="2026-08-31",
            ):
                async with app.run_test(size=(100, 40)):
                    value = app.query_one("#lecture-date", Input).value
                    semester_start = app.query_one("#semester-start", Input).value

        self.assertEqual(value, "2026-08-31")
        self.assertEqual(semester_start, "2026-08-31")

    async def test_configured_processing_defaults_are_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory)
            create_vault(vault)
            app = configured_app(vault)

            async with app.run_test(size=(100, 40)):
                semester_start = app.query_one("#semester-start", Input).value
                device = app.query_one("#device", Select).value
                whisper_model = app.query_one("#whisper-model", Input).value
                language = app.query_one("#language", Input).value
                llm_model = app.query_one("#llm-model", Select).value

        self.assertEqual(semester_start, "2026-09-01")
        self.assertEqual(device, "cpu")
        self.assertEqual(whisper_model, "turbo")
        self.assertEqual(language, "ko")
        self.assertEqual(llm_model, "gpt-test")

    async def test_default_form_builds_vault_run_options(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory)
            create_vault(vault)
            app = LectureSetupApp(vault)
            async with app.run_test(size=(100, 40)) as pilot:
                app.query_one("#lecture-date", Input).value = "2026-09-04"
                app.query_one("#semester-start", Input).value = "2026-08-31"
                app.query_one("#lecture-title", Input).value = "압축성 유동"
                app.query_one("#source", Input).value = URL
                app.query_one("#tags", Input).value = " os, exam, os "
                await pilot.click("#run")

        options = app.return_value
        self.assertIsNotNone(options)
        assert options is not None
        self.assertEqual(options.url, URL)
        self.assertEqual(options.course, COURSE)
        self.assertEqual(options.lecture_date, "2026-09-04")
        self.assertEqual(options.semester_start, "2026-08-31")
        self.assertEqual(options.title, "압축성 유동")
        self.assertEqual(options.tags, ["os", "exam"])
        self.assertEqual(options.device, "auto")
        self.assertEqual(options.whisper_model, "large-v3")
        self.assertEqual(options.language, "auto")
        self.assertEqual(options.prompt, DEFAULT_PROMPT)
        self.assertFalse(options.force)

    async def test_course_choices_and_prompt_file_are_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            create_vault(root)
            (root / COURSES_DIRECTORY / "문제해결을 위한 글쓰기" / "Lecture").mkdir(
                parents=True
            )
            prompt_file = root / "prompt.md"
            prompt_file.write_text("Create exam notes.", encoding="utf-8")

            app = LectureSetupApp(root)
            self.assertEqual(
                [course.name for course in app.courses],
                [COURSE, "문제해결을 위한 글쓰기"],
            )
            async with app.run_test(size=(100, 40)):
                app.query_one("#lecture-date", Input).value = "2026-09-04"
                app.query_one("#semester-start", Input).value = "2026-09-01"
                app.query_one("#lecture-title", Input).value = "강의개요"
                app.query_one("#source", Input).value = URL
                app.query_one("#prompt-mode", Select).value = "file"
                app.query_one("#prompt-file", Input).value = str(prompt_file)
                options = app._build_options()

        self.assertEqual(options.course, COURSE)
        self.assertEqual(options.semester_start, "2026-09-01")
        self.assertEqual(options.prompt, "Create exam notes.")

    async def test_space_opens_and_selects_course(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            create_vault(root)
            second_course = "문제해결을 위한 글쓰기"
            (root / COURSES_DIRECTORY / second_course / "Lecture").mkdir(
                parents=True
            )
            app = LectureSetupApp(root)

            async with app.run_test(size=(100, 40)) as pilot:
                course = app.query_one("#course", Select)
                course.focus()
                await pilot.press("space")
                self.assertTrue(course.expanded)

                await pilot.press("down", "space")
                self.assertEqual(course.value, second_course)
                self.assertFalse(course.expanded)

    async def test_space_remains_available_in_text_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            create_vault(root)
            app = LectureSetupApp(root)

            async with app.run_test(size=(100, 40)) as pilot:
                title = app.query_one("#lecture-title", Input)
                title.focus()
                await pilot.press("a", "space", "b")
                self.assertEqual(title.value, "a b")

    async def test_inline_prompt_mode_is_reflected_in_options(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory)
            create_vault(vault)
            app = LectureSetupApp(vault)
            async with app.run_test(size=(100, 40)):
                app.query_one("#lecture-date", Input).value = "2026-09-04"
                app.query_one("#semester-start", Input).value = "2026-08-31"
                app.query_one("#lecture-title", Input).value = "강의개요"
                app.query_one("#source", Input).value = URL
                app.query_one("#prompt-mode", Select).value = "inline"
                app.query_one("#inline-prompt", TextArea).text = "한국어로 요약해"
                options = app._build_options()

        self.assertEqual(options.prompt, "한국어로 요약해")

    async def test_ctrl_r_submits_even_when_select_is_focused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory)
            create_vault(vault)
            app = LectureSetupApp(vault)
            async with app.run_test(size=(100, 40)) as pilot:
                app.query_one("#lecture-date", Input).value = "2026-09-04"
                app.query_one("#semester-start", Input).value = "2026-08-31"
                app.query_one("#lecture-title", Input).value = "강의개요"
                app.query_one("#source", Input).value = URL
                app.query_one("#course", Select).focus()
                await pilot.press("ctrl+r")

        options = app.return_value
        self.assertIsNotNone(options)
        assert options is not None
        self.assertEqual(options.course, COURSE)

    async def test_submit_shows_validation_error_without_exiting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory)
            create_vault(vault)
            app = LectureSetupApp(vault)
            async with app.run_test(size=(100, 40)) as pilot:
                app.query_one("#lecture-date", Input).value = ""
                app.query_one("#source", Input).value = URL
                await pilot.press("ctrl+r")
                await pilot.pause()
                error = app.query_one("#error", Label)
                self.assertTrue(error.display)
                self.assertIn("YYYY-MM-DD", str(error.render()))
                self.assertTrue(app.is_running)

    async def test_existing_note_can_be_corrected_before_tui_exits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory)
            create_vault(vault)
            existing = (
                vault
                / COURSES_DIRECTORY
                / COURSE
                / "Lectures"
                / "1주차"
                / "2026-09-04 압축성 유동.md"
            )
            existing.parent.mkdir()
            existing.write_text("existing", encoding="utf-8")
            app = LectureSetupApp(vault)
            async with app.run_test(size=(100, 40)) as pilot:
                app.query_one("#lecture-date", Input).value = "2026-09-04"
                app.query_one("#semester-start", Input).value = "2026-08-31"
                app.query_one("#lecture-title", Input).value = "압축성 유동"
                app.query_one("#source", Input).value = URL
                await pilot.click("#run")
                await pilot.pause()
                error = app.query_one("#error", Label)
                self.assertIn("already exists", str(error.render()))
                self.assertTrue(app.is_running)

                app.query_one("#lecture-title", Input).value = "천음속 유동"
                app._submit()

        assert app.return_value is not None
        self.assertEqual(app.return_value.title, "천음속 유동")

    async def test_escape_cancels(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory)
            create_vault(vault)
            app = LectureSetupApp(vault)
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.press("escape")
        self.assertIsNone(app.return_value)


class TuiInteractionTests(unittest.IsolatedAsyncioTestCase):
    async def test_arrows_leave_processing_mode_after_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory)
            create_vault(vault)
            app = LectureSetupApp(vault)
            async with app.run_test(size=(120, 40)) as pilot:
                app.query_one("#source", Input).value = URL
                await pilot.pause()
                mode = app.query_one("#processing-mode", Select)
                for confirm in ("enter", "space"):
                    with self.subTest(confirm=confirm):
                        mode.value = "full"
                        mode.focus()
                        await pilot.press(confirm, "down")
                        self.assertTrue(mode.expanded)
                        await pilot.press(confirm)
                        self.assertEqual(mode.value, "video")
                        self.assertFalse(mode.expanded)
                        self.assertIs(app.focused, mode)

                        await pilot.press("up")
                        self.assertEqual(app.focused.id, "source")
                        self.assertFalse(mode.expanded)

                        mode.focus()
                        await pilot.press("tab")
                        next_field = app.focused
                        mode.focus()
                        await pilot.press("down")
                        self.assertIs(app.focused, next_field)
                        self.assertFalse(mode.expanded)

                        mode.focus()
                        await pilot.press(confirm, "up", confirm)
                        self.assertEqual(mode.value, "full")
                        self.assertFalse(mode.expanded)

    async def test_enter_selects_and_escape_only_closes_dropdown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory)
            create_vault(vault)
            app = LectureSetupApp(vault)
            async with app.run_test(size=(120, 40)) as pilot:
                course = app.query_one("#course", Select)
                course.focus()
                await pilot.press("enter")
                self.assertTrue(course.expanded)
                await pilot.press("escape")
                self.assertFalse(course.expanded)
                self.assertTrue(app.is_running)
                await pilot.press("enter", "enter")
                self.assertFalse(course.expanded)
                self.assertTrue(app.is_running)

    async def test_multiline_prompt_keeps_enter_and_space(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory)
            create_vault(vault)
            app = LectureSetupApp(vault)
            async with app.run_test(size=(120, 40)) as pilot:
                for section in app.query(Collapsible):
                    section.collapsed = False
                app.query_one("#prompt-mode", Select).value = "inline"
                await pilot.pause()
                prompt = app.query_one("#inline-prompt", TextArea)
                prompt.focus()
                await pilot.press("a", "space", "b", "enter", "c")
                self.assertEqual(prompt.text, "a b\nc")
                self.assertTrue(app.is_running)

    async def test_hidden_error_reveals_field_and_preserves_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory)
            create_vault(vault)
            app = LectureSetupApp(vault)
            async with app.run_test(size=(80, 24)) as pilot:
                app.query_one("#source", Input).value = URL
                app.query_one("#lecture-title", Input).value = "강의 [intro]"
                app.query_one("#semester-start", Input).value = "wrong"
                await pilot.press("ctrl+r")
                await pilot.pause()
                field = app.query_one("#semester-start", Input)
                self.assertIs(app.focused, field)
                self.assertTrue(field.has_class("invalid"))
                self.assertTrue(all(not parent.collapsed for parent in field.ancestors
                                    if isinstance(parent, Collapsible)))
                self.assertEqual(app.value("lecture-title"), "강의 [intro]")
                field.value = "2026-08-31"
                await pilot.pause()
                self.assertFalse(field.has_class("invalid"))

    async def test_responsive_layout_and_live_preview(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory)
            create_vault(vault)
            app = LectureSetupApp(vault)
            async with app.run_test(size=(120, 40)) as pilot:
                app.query_one("#lecture-title", Input).value = "압축성 유동 [intro]" * 4
                await pilot.pause()
                form = app.query_one("#form")
                preview = app.query_one("#preview")
                self.assertGreater(preview.region.x, form.region.x)
                self.assertIn("[intro]", str(app.query_one("#preview-content", Label).render()))
                await pilot.resize_terminal(80, 24)
                await pilot.pause()
                self.assertGreater(preview.virtual_region.y, form.virtual_region.y)
                run = app.query_one("#run")
                self.assertLessEqual(run.region.bottom, 24)
                self.assertLessEqual(run.region.right, 80)


if __name__ == "__main__":
    unittest.main()


class LocalMediaTuiTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_source_validation_preserves_input(self) -> None:
        from lecture_util.models import LectureSource
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory)
            create_vault(vault)
            app = configured_app(vault)
            async with app.run_test(size=(100, 40)):
                path = str(vault / '없는 녹음.m4a')
                app.query_one('#source', Input).value = path
                app._submit()
                self.assertEqual(app.error_field, 'source')
                self.assertEqual(app.query_one('#source', Input).value, path)
                app.query_one('#lecture-title', Input).value = '녹음 강의'
                app.query_one('#lecture-date', Input).value = '2026-09-07'
                source = LectureSource('audio', path, 'hash')
                with patch('lecture_util.tui.resolve_source', return_value=source):
                    options = app._build_options()
                self.assertEqual(options.source, source)
                self.assertEqual(options.url, path)


class VideoOnlyTuiTests(unittest.IsolatedAsyncioTestCase):
    async def test_video_only_skips_processing_validation_and_note_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory) / 'vault'
            create_vault(vault)
            app = configured_app(vault)
            async with app.run_test(size=(100, 40)) as pilot:
                app.query_one('#source', Input).value = URL
                await pilot.pause()
                app.query_one('#processing-mode', Select).value = 'video'
                app.query_one('#lecture-title', Input).value = 'Lecture'
                app.query_one('#lecture-date', Input).value = '2026-09-07'
                app.query_one('#whisper-model', Input).value = ''
                app.query_one('#language', Input).value = ''
                with patch('lecture_util.tui.ensure_paths_available') as check:
                    result = app._build_options()
                    self.assertTrue(result.video_only)
                    check.assert_not_called()
                app.refresh_preview()
                self.assertIn('VIDEO DESTINATION', str(app.query_one('#preview-content', Label).render()))
                app.query_one('#source', Input).value = '/tmp/local.wav'
                await pilot.pause()
                self.assertTrue(app.query_one('#processing-mode', Select).disabled)
                self.assertEqual(app.query_one('#processing-mode', Select).value, 'full')


@pytest.fixture(autouse=True)
def isolate_preflight(monkeypatch):
    monkeypatch.setattr("lecture_util.tui.preflight_run", lambda *args: None)


class RecoveryFormTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_preflight_preserves_input(self):
        from lecture_util.errors import DependencyError
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            create_vault(root)
            app = configured_app(root)
            with patch('lecture_util.tui.preflight_run', side_effect=DependencyError('GPU unavailable')):
                async with app.run_test(size=(100, 40)) as pilot:
                    app.query_one('#lecture-date', Input).value = '2026-09-07'
                    app.query_one('#lecture-title', Input).value = 'Title'
                    app.query_one('#source', Input).value = URL
                    await pilot.press('ctrl+r')
                    await app.workers.wait_for_complete()
                    await pilot.pause()
                    assert app.is_running
                    assert app.query_one('#lecture-title', Input).value == 'Title'
                    assert 'GPU unavailable' in str(app.query_one('#error', Label).render())

    async def test_restores_prompt_and_tuning(self):
        from lecture_util.models import RunOptions
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            create_vault(root)
            config = configured_app(root).config
            initial = RunOptions(URL, COURSE, '2026-09-07', 'Saved', 'gpt-test', ['tag'],
                                 'turbo', 'ko', 'cpu', 'Saved\nprompt', False,
                                 '2026-08-31', 'high', 'int8', 4, 1)
            from lecture_util.media import resolve_source
            initial.source = resolve_source(initial.url)
            app = LectureSetupApp(config=config, initial=initial)
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                restored = app._build_options()
                assert restored == initial
