import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from textual.widgets import Collapsible, Input, Label, Select

from lecture_util.codex_models import ModelCatalog
from lecture_util.configuration import AppConfig
from lecture_util.onboarding import OnboardingApp
from lecture_util.tui import LectureSetupApp
from lecture_util.vault import COURSES_DIRECTORY


class ModelPickerTests(unittest.IsolatedAsyncioTestCase):
    async def test_both_forms_preserve_user_choice_then_select_and_submit(self):
        for form in (OnboardingApp, LectureSetupApp):
            with self.subTest(form=form.__name__), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / COURSES_DIRECTORY / "Course" / "Lectures").mkdir(parents=True)
                config = AppConfig(
                    vault_root=root, video_root=root / "../videos",
                    semester_start="2026-08-31", llm_model="old-model",
                    reasoning_effort="high",
                )
                app = form(config) if form is OnboardingApp else form(config=config)
                ready = asyncio.Event()

                async def lookup():
                    await ready.wait()
                    return ModelCatalog(
                        (("New model", "new-model"),), "Using cached models.",
                        {"new-model": ("low", "medium")},
                    )

                with patch("lecture_util.form_ui.discover_models", lookup):
                    async with app.run_test(size=(120, 40)) as pilot:
                        for section in app.query(Collapsible):
                            section.collapsed = False
                        select = app.query_one("#llm-model", Select)
                        self.assertEqual(select.value, "old-model")
                        effort = app.query_one("#reasoning-effort", Select)
                        self.assertEqual(effort.value, "high")
                        select.value = ""
                        ready.set()
                        await app.workers.wait_for_complete()
                        await pilot.pause()
                        self.assertEqual(select.value, "")
                        self.assertEqual(effort.value, "high")
                        self.assertIn("cached", str(app.query_one("#model-status", Label).render()))
                        select.value = "old-model"
                        await pilot.pause()
                        self.assertIn("old-model", str(app.query_one("#preview-content", Label).render()))
                        select.value = ""
                        select.focus()
                        await pilot.press("enter", "down", "enter")
                        self.assertEqual(select.value, "new-model")
                        self.assertEqual(effort.value, "")
                        effort.focus()
                        await pilot.press("enter", "down", "enter")
                        self.assertEqual(effort.value, "low")
                        self.assertFalse(select.expanded)
                        await pilot.pause()
                        self.assertIn("new-model", str(app.query_one("#preview-content", Label).render()))
                        if form is LectureSetupApp:
                            app.query_one("#source", Input).value = "https://example.com/index.m3u8"
                            app.query_one("#lecture-date", Input).value = "2026-09-04"
                            app.query_one("#lecture-title", Input).value = "Lecture"
                            build = app._build_options
                        else:
                            build = app._build_config
                        self.assertEqual(build().llm_model, "new-model")
                        self.assertEqual(build().reasoning_effort, "low")
                        self.assertIn("Thinking effort: low", str(
                            app.query_one("#preview-content", Label).render(),
                        ))
                        effort.value = ""
                        self.assertIsNone(build().reasoning_effort)
                        select.value = ""
                        self.assertIsNone(build().llm_model)
                        self.assertEqual(config.llm_model, "old-model")

    async def test_exiting_form_cancels_pending_discovery(self):
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def lookup():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        with tempfile.TemporaryDirectory() as directory:
            app = OnboardingApp(AppConfig(
                vault_root=Path(directory), video_root=Path(directory) / "videos",
                semester_start="2026-08-31",
            ))
            with patch("lecture_util.form_ui.discover_models", lookup):
                async with app.run_test() as pilot:
                    await started.wait()
                    await pilot.press("escape")
                await asyncio.wait_for(cancelled.wait(), timeout=1)
