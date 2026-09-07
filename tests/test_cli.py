from __future__ import annotations

import tempfile
import unittest
from io import StringIO
from datetime import date
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner
from rich.console import Console

from lecture_util.cli import _execute_run, app, normalize_tags
from lecture_util.configuration import AppConfig
from lecture_util.errors import LectureUtilError
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
        semester_start="2026-08-31",
        title="압축성 유동",
        llm_model=None,
        tags=["os", "exam"],
        whisper_model="large-v3",
        language="ko",
        device="cpu",
        prompt=DEFAULT_PROMPT,
        force=False,
    )


def configured_defaults(
    vault: Path = Path("/configured-vault"),
    video_root: Path = Path("/configured-videos"),
) -> AppConfig:
    return AppConfig(
        vault_root=vault,
        video_root=video_root,
        semester_start="2026-08-31",
        whisper_model="turbo",
        language="ko",
        device="cuda",
        llm_model="gpt-test",
        reasoning_effort="high",
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
        config = configured_defaults()
        with (
            patch("lecture_util.cli.load_config", return_value=config),
            patch("lecture_util.cli._execute_run") as execute,
        ):
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
        self.assertEqual(selected.semester_start, "2026-08-31")
        self.assertEqual(selected.whisper_model, "turbo")
        self.assertEqual(selected.language, "ko")
        self.assertEqual(selected.device, "cuda")
        self.assertEqual(selected.llm_model, "gpt-test")
        self.assertEqual(selected.reasoning_effort, "high")
        self.assertEqual(execute.call_args.kwargs["vault_root"], config.vault_root)
        self.assertEqual(execute.call_args.kwargs["video_root"], config.video_root)

    def test_run_accepts_custom_semester_start(self) -> None:
        config = configured_defaults()
        with (
            patch("lecture_util.cli.load_config", return_value=config),
            patch("lecture_util.cli._execute_run") as execute,
        ):
            result = self.runner.invoke(
                app,
                [
                    "run",
                    URL,
                    "--course",
                    COURSE,
                    "--date",
                    "2026-09-14",
                    "--semester-start",
                    "2026-09-07",
                    "--title",
                    "압축성 유동",
                ],
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(execute.call_args.args[0].semester_start, "2026-09-07")

    def test_run_cli_processing_options_override_configured_defaults(self) -> None:
        config = configured_defaults()
        with (
            patch("lecture_util.cli.load_config", return_value=config),
            patch("lecture_util.cli._execute_run") as execute,
        ):
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
                    "--whisper-model",
                    "large-v3",
                    "--language",
                    "en",
                    "--device",
                    "cpu",
                    "--llm-model",
                    "",
                    "--reasoning-effort",
                    "",
                ],
            )

        self.assertEqual(result.exit_code, 0, result.output)
        selected = execute.call_args.args[0]
        self.assertEqual(selected.whisper_model, "large-v3")
        self.assertEqual(selected.language, "en")
        self.assertEqual(selected.device, "cpu")
        self.assertIsNone(selected.llm_model)
        self.assertIsNone(selected.reasoning_effort)

    def test_run_without_config_explains_how_to_onboard(self) -> None:
        with (
            patch(
                "lecture_util.cli.load_config",
                side_effect=LectureUtilError(
                    "lecture-util is not configured. Run 'lecture-util onboard' first."
                ),
            ),
            patch("lecture_util.cli._execute_run") as execute,
        ):
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
                ],
            )

        self.assertEqual(result.exit_code, 1)
        self.assertIn("lecture-util onboard", result.output)
        execute.assert_not_called()

    def test_help_exposes_single_lecture_vault_options(self) -> None:
        result = self.runner.invoke(app, ["run", "--help"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("--course", result.output)
        self.assertIn("--date", result.output)
        self.assertIn("--semester-start", result.output)
        self.assertIn("--title", result.output)
        self.assertNotIn("--input", result.output)
        self.assertNotIn("--output-dir", result.output)
        self.assertIn("--llm-model", result.output)

    def test_download_uses_configured_video_root_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vault = root / "vault"
            (vault / COURSES_DIRECTORY / COURSE / "Lectures").mkdir(parents=True)
            config = configured_defaults(vault, root / "videos")
            cache = root / "cache"

            with (
                patch("lecture_util.cli.load_config", return_value=config),
                patch("lecture_util.cli.download_stage") as download,
                patch("lecture_util.cli.audio_stage") as audio,
            ):
                result = self.runner.invoke(
                    app,
                    [
                        "download",
                        URL,
                        "--course",
                        COURSE,
                        "--date",
                        "2026-09-08",
                        "--title",
                        "압축성 유동",
                        "--output-dir",
                        str(cache),
                    ],
                )

            self.assertEqual(result.exit_code, 0, result.output)
            paths = download.call_args.args[0]
            self.assertEqual(paths.root.parent, cache)
            self.assertEqual(
                paths.video,
                root / "videos" / COURSE / "2주차" / "압축성 유동.mp4",
            )
            self.assertEqual(audio.call_args.args[0], paths)
            self.assertEqual(download.call_args.args[1].data["course"], COURSE)
            self.assertEqual(
                download.call_args.args[1].data["lecture_date"],
                "2026-09-08",
            )

    def test_download_defaults_date_to_request_day(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vault = root / "vault"
            (vault / COURSES_DIRECTORY / COURSE / "Lectures").mkdir(parents=True)
            config = configured_defaults(vault, root / "videos")

            with (
                patch("lecture_util.cli.load_config", return_value=config),
                patch("lecture_util.cli.date") as date_type,
                patch("lecture_util.cli.download_stage") as download,
                patch("lecture_util.cli.audio_stage"),
            ):
                date_type.today.return_value = date(2026, 9, 15)
                result = self.runner.invoke(
                    app,
                    [
                        "download",
                        URL,
                        "--course",
                        COURSE,
                        "--title",
                        "압축성 유동",
                        "--output-dir",
                        str(root / "cache"),
                    ],
                )

            self.assertEqual(result.exit_code, 0, result.output)
            self.assertEqual(
                download.call_args.args[0].video,
                root / "videos" / COURSE / "3주차" / "압축성 유동.mp4",
            )

    def test_download_help_requires_course_and_title_but_not_date(self) -> None:
        result = self.runner.invoke(app, ["download", "--help"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("--course", result.output)
        self.assertIn("--title", result.output)
        self.assertIn("--date", result.output)
        self.assertIn("default: today", result.output)

    def test_execute_run_uses_cache_and_publishes_notes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vault = root / "vault [blue] 한글 공백"
            terminal = StringIO()
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
                patch("lecture_util.cli.ConsoleProgressReporter") as reporter,
                patch("lecture_util.cli.console", Console(
                    file=terminal, force_terminal=True, no_color=False, width=1000,
                )),
            ):
                selected = options()
                selected.reasoning_effort = "high"
                _execute_run(
                    selected,
                    vault_root=vault,
                    video_root=root / "videos",
                    cache_root=cache,
                )

            factory.assert_called_once_with(model=None, reasoning_effort="high")
            self.assertEqual(
                reporter.call_args.kwargs["lecture_label"],
                f"{COURSE} · 2026-09-04 압축성 유동",
            )
            self.assertEqual(reporter.call_args.kwargs["source_url"], URL)
            self.assertEqual(run_lecture.call_args.args[0], URL)
            self.assertEqual(run_lecture.call_args.args[1], cache)
            self.assertEqual(
                run_lecture.call_args.kwargs["video_path"],
                root / "videos" / COURSE / "1주차" / "압축성 유동.mp4",
            )
            self.assertEqual(run_lecture.call_args.kwargs["course"], COURSE)
            self.assertEqual(
                run_lecture.call_args.kwargs["lecture_date"], "2026-09-04"
            )
            summary = lecture_dir / "1주차" / "2026-09-04 압축성 유동.md"
            transcript = lecture_dir / "1주차" / "2026-09-04 압축성 유동 전사.md"
            self.assertTrue(summary.is_file())
            self.assertTrue(transcript.is_file())
            # Paths stay literal and uninterrupted by automatic ANSI highlighting.
            self.assertIn(str(summary), terminal.getvalue())
            video = root / "videos" / COURSE / "1주차" / "압축성 유동.mp4"
            self.assertIn(f"Video: {video}", terminal.getvalue())
            self.assertIn("\x1b[1;32mComplete\x1b[0m", terminal.getvalue())

    def test_existing_note_stops_before_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vault = root / "vault"
            lecture_dir = vault / COURSES_DIRECTORY / COURSE / "Lectures"
            lecture_dir.mkdir(parents=True)
            week_dir = lecture_dir / "1주차"
            week_dir.mkdir()
            (week_dir / "2026-09-04 압축성 유동.md").write_text(
                "existing", encoding="utf-8"
            )
            with (
                patch("lecture_util.cli.CodexSummarizer") as factory,
                patch("lecture_util.cli.run_lecture") as run_lecture,
                self.assertRaisesRegex(Exception, "already exists"),
            ):
                _execute_run(
                    options(),
                    vault_root=vault,
                    video_root=root / "videos",
                    cache_root=root / "cache",
                )
            factory.assert_not_called()
            run_lecture.assert_not_called()

    def test_bare_command_passes_tui_options_to_pipeline(self) -> None:
        selected = options()
        config = configured_defaults()
        with (
            patch("lecture_util.cli._interactive_terminal", return_value=True),
            patch("lecture_util.cli.load_config", return_value=config),
            patch("lecture_util.cli.run_tui", return_value=selected),
            patch("lecture_util.cli._execute_run") as execute,
        ):
            result = self.runner.invoke(app, [])
        self.assertEqual(result.exit_code, 0, result.output)
        execute.assert_called_once_with(
            selected,
            vault_root=config.vault_root,
            video_root=config.video_root,
        )

    def test_bare_first_run_onboards_then_opens_lecture_tui(self) -> None:
        selected = options()
        config = configured_defaults()
        with (
            patch("lecture_util.cli._interactive_terminal", return_value=True),
            patch("lecture_util.cli.load_config", return_value=None),
            patch("lecture_util.cli.run_onboarding", return_value=config) as onboarding,
            patch("lecture_util.cli.save_config", return_value=config) as save,
            patch("lecture_util.cli.run_tui", return_value=selected) as tui,
            patch("lecture_util.cli._execute_run") as execute,
        ):
            result = self.runner.invoke(app, [])

        self.assertEqual(result.exit_code, 0, result.output)
        onboarding.assert_called_once()
        save.assert_called_once_with(config)
        tui.assert_called_once_with(config)
        execute.assert_called_once_with(
            selected,
            vault_root=config.vault_root,
            video_root=config.video_root,
        )

    def test_bare_first_run_can_cancel_onboarding(self) -> None:
        with (
            patch("lecture_util.cli._interactive_terminal", return_value=True),
            patch("lecture_util.cli.load_config", return_value=None),
            patch("lecture_util.cli.run_onboarding", return_value=None),
            patch("lecture_util.cli.save_config") as save,
            patch("lecture_util.cli.run_tui") as tui,
        ):
            result = self.runner.invoke(app, [])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Cancelled before setup", result.output)
        save.assert_not_called()
        tui.assert_not_called()

    def test_tui_cancel_does_not_start_pipeline(self) -> None:
        config = configured_defaults()
        with (
            patch("lecture_util.cli._interactive_terminal", return_value=True),
            patch("lecture_util.cli.load_config", return_value=config),
            patch("lecture_util.cli.run_tui", return_value=None),
            patch("lecture_util.cli._execute_run") as execute,
        ):
            result = self.runner.invoke(app, [])
        self.assertEqual(result.exit_code, 0, result.output)
        execute.assert_not_called()
        self.assertIn("Cancelled before processing", result.output)

    def test_onboard_replaces_existing_configuration(self) -> None:
        existing = configured_defaults(Path("/old-vault"))
        updated = configured_defaults(Path("/new-vault"))
        with (
            patch("lecture_util.cli._interactive_terminal", return_value=True),
            patch("lecture_util.cli.load_onboarding_config", return_value=existing),
            patch("lecture_util.cli.run_onboarding", return_value=updated) as onboarding,
            patch("lecture_util.cli.save_config", return_value=updated) as save,
        ):
            result = self.runner.invoke(app, ["onboard"])

        self.assertEqual(result.exit_code, 0, result.output)
        onboarding.assert_called_once_with(existing)
        save.assert_called_once_with(updated)

    def test_onboard_requires_an_interactive_terminal(self) -> None:
        with patch("lecture_util.cli._interactive_terminal", return_value=False):
            result = self.runner.invoke(app, ["onboard"])

        self.assertEqual(result.exit_code, 2)
        self.assertIn("interactive terminal", result.output)


    def test_config_edits_existing_settings_without_starting_lecture(self) -> None:
        existing = configured_defaults()
        updated = configured_defaults(Path("/new-vault"))
        with (
            patch("lecture_util.cli._interactive_terminal", return_value=True),
            patch("lecture_util.cli.load_onboarding_config", return_value=existing),
            patch("lecture_util.cli.run_onboarding", return_value=updated) as editor,
            patch("lecture_util.cli.save_config", return_value=updated) as save,
            patch("lecture_util.cli._execute_run") as execute,
        ):
            result = self.runner.invoke(app, ["config"])
        self.assertEqual(result.exit_code, 0, result.output)
        editor.assert_called_once_with(existing)
        save.assert_called_once_with(updated)
        execute.assert_not_called()
        self.assertIn("Configuration saved", result.output)

    def test_config_cancel_preserves_settings(self) -> None:
        with (
            patch("lecture_util.cli._interactive_terminal", return_value=True),
            patch("lecture_util.cli.load_onboarding_config", return_value=configured_defaults()),
            patch("lecture_util.cli.run_onboarding", return_value=None),
            patch("lecture_util.cli.save_config") as save,
        ):
            result = self.runner.invoke(app, ["config"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Configuration cancelled", result.output)
        save.assert_not_called()

    def test_config_requires_interactive_terminal(self) -> None:
        with (
            patch("lecture_util.cli._interactive_terminal", return_value=False),
            patch("lecture_util.cli.run_onboarding") as editor,
        ):
            result = self.runner.invoke(app, ["config"])
        self.assertEqual(result.exit_code, 2)
        self.assertIn("interactive terminal", result.output)
        editor.assert_not_called()

    def test_config_missing_or_broken_settings_can_be_repaired(self) -> None:
        defaults = configured_defaults()
        for error in (None, LectureUtilError("Invalid configuration")):
            with (
                self.subTest(error=error),
                patch("lecture_util.cli._interactive_terminal", return_value=True),
                patch("lecture_util.cli.load_onboarding_config", return_value=None, side_effect=error),
                patch("lecture_util.cli.default_app_config", return_value=defaults),
                patch("lecture_util.cli.run_onboarding", return_value=defaults) as editor,
                patch("lecture_util.cli.save_config") as save,
            ):
                result = self.runner.invoke(app, ["config"])
                self.assertEqual(result.exit_code, 0, result.output)
                editor.assert_called_once_with(defaults)
                save.assert_called_once_with(defaults)
                if error:
                    self.assertIn("Warning", result.output)

    def test_config_save_failure_returns_error(self) -> None:
        with (
            patch("lecture_util.cli._interactive_terminal", return_value=True),
            patch("lecture_util.cli.load_onboarding_config", return_value=configured_defaults()),
            patch("lecture_util.cli.run_onboarding", return_value=configured_defaults()),
            patch("lecture_util.cli.save_config", side_effect=LectureUtilError("Cannot save")),
        ):
            result = self.runner.invoke(app, ["config"])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("Cannot save", result.output)

    def test_config_help_describes_settings_editor(self) -> None:
        result = self.runner.invoke(app, ["config", "--help"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Edit saved settings", result.output)


if __name__ == "__main__":
    unittest.main()


def test_transcribe_uses_saved_defaults_and_explicit_override(tmp_path):
    from lecture_util.models import Transcript
    transcript = Transcript('ko', 1, 'test', 'turbo', 'turbo', [])
    with (patch('lecture_util.cli.load_config', return_value=configured_defaults()) as load,
          patch('lecture_util.cli.transcription_stage', return_value=transcript) as transcribe):
        result = CliRunner().invoke(app, ['transcribe', str(tmp_path), '--device', 'cpu'])
    assert result.exit_code == 0, result.output
    load.assert_called_once_with(validate_vault=False)
    assert transcribe.call_args.kwargs['model'] == 'turbo'
    assert transcribe.call_args.kwargs['language'] == 'ko'
    assert transcribe.call_args.kwargs['device'] == 'cpu'


def test_summarize_uses_saved_defaults_and_empty_reset(tmp_path):
    with (patch('lecture_util.cli.load_config', return_value=configured_defaults()),
          patch('lecture_util.cli.load_transcript'),
          patch('lecture_util.cli.summary_stage') as summarize):
        result = CliRunner().invoke(app, ['summarize', str(tmp_path), '--llm-model', ''])
    assert result.exit_code == 0, result.output
    backend = summarize.call_args.args[4]
    assert backend.model is None
    assert backend.reasoning_effort == 'high'
