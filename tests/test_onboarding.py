from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from textual.widgets import Input, Label, Select

from lecture_util.configuration import AppConfig
from lecture_util.onboarding import OnboardingApp
from lecture_util.vault import COURSES_DIRECTORY


COURSE = "공기역학특론"


def create_vault(root: Path) -> None:
    (root / COURSES_DIRECTORY / COURSE / "Lectures").mkdir(parents=True)


def initial_config(vault: Path) -> AppConfig:
    return AppConfig(
        vault_root=vault,
        video_root=vault.parent / "videos",
        semester_start="2026-08-31",
        whisper_model="large-v3",
        language="auto",
        device="auto",
        llm_model=None,
    )


class OnboardingTests(unittest.IsolatedAsyncioTestCase):
    async def test_onboarding_prefills_and_returns_validated_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory) / "vault"
            create_vault(vault)
            app = OnboardingApp(initial_config(vault))

            async with app.run_test(size=(100, 32)) as pilot:
                self.assertEqual(
                    app.query_one("#vault-root", Input).value,
                    str(vault),
                )
                self.assertEqual(
                    app.query_one("#video-root", Input).value,
                    str(vault.parent / "videos"),
                )
                app.query_one("#semester-start", Input).value = "2026-09-01"
                app.query_one("#device", Select).value = "cpu"
                app.query_one("#whisper-model", Input).value = "turbo"
                app.query_one("#language", Input).value = "ko"
                app.query_one("#llm-model", Select).value = "gpt-test"
                await pilot.click("#save")

            config = app.return_value
            self.assertIsNotNone(config)
            assert config is not None
            self.assertEqual(config.vault_root, vault.resolve())
            self.assertEqual(config.video_root, (vault.parent / "videos").resolve())
            self.assertEqual(config.semester_start, "2026-09-01")
            self.assertEqual(config.device, "cpu")
            self.assertEqual(config.whisper_model, "turbo")
            self.assertEqual(config.language, "ko")
            self.assertEqual(config.llm_model, "gpt-test")

    async def test_onboarding_shows_vault_error_without_exiting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = OnboardingApp(initial_config(Path(directory) / "missing"))

            async with app.run_test(size=(100, 32)) as pilot:
                await pilot.click("#save")
                await pilot.pause()
                error = app.query_one("#error", Label)
                self.assertTrue(error.display)
                self.assertIn("Courses directory does not exist", str(error.render()))
                self.assertTrue(app.is_running)

    async def test_onboarding_escape_cancels(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = OnboardingApp(initial_config(Path(directory) / "missing"))

            async with app.run_test(size=(100, 32)) as pilot:
                await pilot.press("escape")

            self.assertIsNone(app.return_value)

    async def test_video_path_inside_vault_requires_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory) / "vault"
            create_vault(vault)
            app = OnboardingApp(initial_config(vault))

            async with app.run_test(size=(100, 32)) as pilot:
                app.query_one("#video-root", Input).value = str(vault / "Videos")
                await pilot.click("#save")
                await pilot.pause()
                await pilot.click("#confirm-video-root")

            config = app.return_value
            self.assertIsNotNone(config)
            assert config is not None
            self.assertEqual(config.video_root, (vault / "Videos").resolve())
            self.assertTrue(config.video_in_vault_allowed)

    async def test_vault_video_confirmation_can_go_back(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory) / "vault"
            create_vault(vault)
            app = OnboardingApp(initial_config(vault))

            async with app.run_test(size=(100, 32)) as pilot:
                app.query_one("#video-root", Input).value = str(vault / "Videos")
                await pilot.click("#save")
                await pilot.pause()
                await pilot.click("#cancel-video-root")
                await pilot.pause()
                self.assertTrue(app.is_running)
                await pilot.press("escape")

            self.assertIsNone(app.return_value)


class OnboardingInteractionTests(unittest.IsolatedAsyncioTestCase):
    async def test_narrow_form_preview_and_save_shortcut(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory) / "vault"
            create_vault(vault)
            app = OnboardingApp(initial_config(vault))
            async with app.run_test(size=(80, 24)) as pilot:
                field = app.query_one("#language", Input)
                field.value = "ko"
                await pilot.pause()
                self.assertIn("Language: ko", str(app.query_one("#preview-content", Label).render()))
                self.assertLessEqual(app.query_one("#save").region.bottom, 24)
                self.assertLessEqual(app.query_one("#save").region.right, 80)
                await pilot.press("ctrl+s")
            self.assertEqual(app.return_value.language, "ko")

    async def test_validation_moves_to_model_and_allows_correction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory) / "vault"
            create_vault(vault)
            app = OnboardingApp(initial_config(vault))
            async with app.run_test(size=(80, 24)) as pilot:
                field = app.query_one("#whisper-model", Input)
                field.value = ""
                await pilot.press("ctrl+s")
                await pilot.pause()
                self.assertIs(app.focused, field)
                self.assertTrue(field.has_class("invalid"))
                field.value = "turbo"
                await pilot.press("ctrl+s")
            self.assertEqual(app.return_value.whisper_model, "turbo")
