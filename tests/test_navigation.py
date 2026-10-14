from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from textual.app import ComposeResult
from textual.widgets import Button, Collapsible, DataTable, Input, Select, TextArea

from lecture_util.form_ui import FormApp
from lecture_util.history import ConfirmClearScreen, HistoryApp, HistoryScreen
from lecture_util.onboarding import ConfirmVaultVideoScreen, OnboardingApp
from lecture_util.tui import LectureSetupApp
from test_history import loaded, setup_cache
from test_onboarding import initial_config
from test_tui import create_vault


class NavigationForm(FormApp[None]):
    def compose_fields(self) -> ComposeResult:
        yield Button("First", id="first")
        yield Button("Disabled", id="disabled", disabled=True)
        yield Button("Hidden", id="hidden")
        yield Select([("First", "first"), ("Second", "second")],
                     value="first", allow_blank=False, id="choice")


class NavigationTests(unittest.IsolatedAsyncioTestCase):
    async def test_skips_unavailable_controls_and_overrides_menu_search(self) -> None:
        app = NavigationForm()
        async with app.run_test(size=(120, 45)) as pilot:
            app.query_one("#hidden").display = False
            app.query_one("#first").focus()
            await pilot.press("j")
            choice = app.query_one("#choice", Select)
            self.assertIs(app.focused, choice)
            await pilot.press("k")
            self.assertEqual(app.focused.id, "first")
            await pilot.press("j", "enter", "j", "enter")
            self.assertEqual(choice.value, "second")
            self.assertFalse(choice.expanded)
            await pilot.press("escape")

    async def test_form_navigation_preserves_text_and_selects_options(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory)
            create_vault(vault)
            app = LectureSetupApp(vault)
            async with app.run_test(size=(120, 45)) as pilot:
                title = app.query_one("#lecture-title", Input)
                title.focus()
                await pilot.press("j", "k")
                self.assertEqual(title.value, "jk")
                await pilot.press("ctrl+j")
                self.assertEqual(app.focused.id, "source")
                await pilot.press("ctrl+k")
                self.assertIs(app.focused, title)
                self.assertEqual(title.value, "jk")

                app.query_one("#source", Input).value = "https://example.com/index.m3u8"
                await pilot.pause()
                mode = app.query_one("#processing-mode", Select)
                mode.focus()
                await pilot.press("j")
                self.assertFalse(mode.expanded)
                self.assertIsNot(app.focused, mode)
                await pilot.press("ctrl+k")
                self.assertIs(app.focused, mode)
                await pilot.press("space", "j", "k", "j", "space")
                self.assertFalse(mode.expanded)
                self.assertEqual(mode.value, "video")
                await pilot.press("k")
                self.assertEqual(app.focused.id, "source")

                mode.value = "full"
                prompt = app.query_one("#inline-prompt", TextArea)
                for parent in prompt.ancestors:
                    if isinstance(parent, Collapsible):
                        parent.collapsed = False
                app.query_one("#prompt-mode", Select).value = "inline"
                await pilot.pause()
                prompt.focus()
                await pilot.press("j", "k", "enter", "j")
                self.assertEqual(prompt.text, "jk\nj")
                await pilot.press("ctrl+j", "ctrl+k")
                self.assertIs(app.focused, prompt)
                self.assertEqual(prompt.text, "jk\nj")

    async def test_onboarding_and_confirmation_navigation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory)
            create_vault(vault)
            app = OnboardingApp(initial_config(vault))
            async with app.run_test(size=(120, 45)) as pilot:
                field = app.query_one("#vault-root", Input)
                field.focus()
                value = field.value
                await pilot.press("ctrl+j")
                self.assertEqual(app.focused.id, "video-root")
                await pilot.press("ctrl+k")
                self.assertIs(app.focused, field)
                self.assertEqual(field.value, value)
                dialog = ConfirmVaultVideoScreen(vault)
                app.push_screen(dialog)
                await pilot.pause()
                dialog.query_one("#cancel-video-root", Button).focus()
                await pilot.press("j")
                self.assertEqual(dialog.focused.id, "confirm-video-root")
                self.assertIs(app.screen, dialog)
                await pilot.press("k")
                self.assertEqual(dialog.focused.id, "cancel-video-root")
                await pilot.press("escape")
                self.assertIsNot(app.screen, dialog)

    async def test_history_rows_boundaries_and_modal_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = HistoryApp(setup_cache(Path(directory)))
            async with app.run_test(size=(120, 45)) as pilot:
                screen = await loaded(app, pilot)
                table = screen.query_one(DataTable)
                await pilot.press("k")
                self.assertEqual(table.cursor_row, 0)
                await pilot.press("j", "j", "j")
                self.assertEqual(table.cursor_row, 2)
                await pilot.press("k", "c")
                dialog = app.screen
                self.assertIsInstance(dialog, ConfirmClearScreen)
                await pilot.press("j")
                self.assertEqual(dialog.focused.id, "delete-cache")
                self.assertEqual(table.cursor_row, 1)
                self.assertIs(app.screen, dialog)
                await pilot.press("ctrl+k")
                self.assertEqual(dialog.focused.id, "keep-cache")
                await pilot.press("escape")
                self.assertIs(app.screen, screen)
                self.assertEqual(table.row_count, 3)

    async def test_embedded_and_empty_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory)
            create_vault(vault)
            app = LectureSetupApp(vault)
            async with app.run_test(size=(120, 45)) as pilot:
                screen = HistoryScreen(setup_cache(vault / "cache"))
                app.push_screen(screen)
                await pilot.pause()
                await app.workers.wait_for_complete()
                await pilot.press("j")
                self.assertEqual(screen.query_one(DataTable).cursor_row, 1)
                await pilot.press("escape")
            empty = HistoryApp(vault / "empty")
            async with empty.run_test() as pilot:
                screen = await loaded(empty, pilot)
                await pilot.press("j", "k", "ctrl+j", "ctrl+k")
                self.assertEqual(screen.query_one(DataTable).row_count, 0)
