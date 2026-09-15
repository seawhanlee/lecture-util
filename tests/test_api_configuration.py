from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from unittest.mock import Mock, patch

import pytest
from textual.widgets import Checkbox, Input, Label, Select
from typer.testing import CliRunner

from lecture_util.cli import app as cli
from lecture_util.configuration import AppConfig, app_config_from_dict, validate_app_config
from lecture_util.errors import LectureUtilError
from lecture_util.form_ui import TranscriptionSettings
from lecture_util.models import RunOptions, Transcript
from lecture_util.onboarding import OnboardingApp
from lecture_util.recovery import load_request, save_request, transcription_options
from lecture_util.state import lecture_id
from lecture_util.tui import LectureSetupApp
from lecture_util.vault import COURSES_DIRECTORY


@pytest.fixture
def config(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    (vault / COURSES_DIRECTORY / "Course" / "Lectures").mkdir(parents=True)
    monkeypatch.setattr("lecture_util.credentials.credential_status", lambda: "Test key registered")
    monkeypatch.setattr("lecture_util.credentials._backend", Mock(side_effect=AssertionError("No real key store in tests")))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    return AppConfig(vault, tmp_path / "videos", "2026-08-31", transcription_provider="openai")


def run_options(config):
    return RunOptions("https://example.com/a.m3u8", "Course", "2026-09-07", "Title", None,
                      None, "large-v3", "ko", "auto", "Prompt", False,
                      semester_start=config.semester_start, transcription_provider="openai",
                      openai_transcription_model="gpt-4o-mini-transcribe")


def test_config_roundtrip_and_legacy_defaults(config):
    assert app_config_from_dict(config.to_dict()) == validate_app_config(config)
    legacy = config.to_dict()
    legacy.pop("transcription_provider")
    legacy.pop("openai_transcription_model")
    assert app_config_from_dict(legacy).transcription_provider == "local"
    assert app_config_from_dict(config.to_dict(), video_only=True).transcription_provider == "local"
    with pytest.raises(LectureUtilError, match="Choose local"):
        validate_app_config(replace(config, transcription_provider="unknown"))
    with pytest.raises(LectureUtilError, match="supported OpenAI"):
        validate_app_config(replace(config, openai_transcription_model="unknown"))
    # An unavailable local model must not prevent API transcription.
    validate_app_config(replace(config, whisper_model="/missing/local-model", device="mlx", compute_type="int8"))


def test_onboarding_platform_key_save_and_model(config):
    async def scenario():
        app = OnboardingApp(config)
        with patch("lecture_util.credentials.save_api_key") as save:
            async with app.run_test(size=(110, 40)) as pilot:
                assert not app.query_one("#local-transcription").display
                assert app.query_one("#api-transcription").display
                assert app.query_one("#openai-api-key", Input).password
                app.query_one("#openai-api-key", Input).value = "fake-key"
                app.query_one("#openai-transcription-model", Select).value = "whisper-1"
                await pilot.pause()
                assert "SRT" in str(app.query_one("#transcription-output", Label).render())
                await pilot.click("#save")
            save.assert_called_once_with("fake-key", delete=False)
            assert app.return_value.openai_transcription_model == "whisper-1"
            assert "fake-key" not in json.dumps(app.return_value.to_dict())
    asyncio.run(scenario())


def test_key_save_failure_preserves_form_and_cancel(config):
    async def scenario():
        app = OnboardingApp(config)
        with patch("lecture_util.credentials.save_api_key", side_effect=LectureUtilError("Storage locked")) as save:
            async with app.run_test(size=(110, 40)) as pilot:
                app.query_one("#openai-api-key", Input).value = "fake-key"
                app.query_one("#openai-transcription-model", Select).value = "gpt-4o-mini-transcribe"
                await pilot.click("#save")
                await pilot.pause()
                assert app.is_running
                assert app.query_one("#openai-api-key", Input).value == "fake-key"
                assert app.query_one("#error", Label).display
                await pilot.press("escape")
            assert app.return_value is None
            assert save.call_count == 1
    asyncio.run(scenario())


def test_key_delete_and_cancel_without_save(config):
    async def scenario():
        with patch("lecture_util.credentials.save_api_key") as save:
            app = OnboardingApp(config)
            async with app.run_test(size=(110, 40)) as pilot:
                app.query_one("#delete-api-key", Checkbox).value = True
                await pilot.press("escape")
            save.assert_not_called()
            app = OnboardingApp(config)
            async with app.run_test(size=(110, 40)) as pilot:
                app.query_one("#delete-api-key", Checkbox).value = True
                await pilot.click("#save")
            save.assert_called_once_with("", delete=True)
    asyncio.run(scenario())


def test_lecture_form_restores_platform_and_toggles(config):
    async def scenario():
        original = run_options(config)
        app = LectureSetupApp(config=replace(config, transcription_provider="local"), initial=original)
        async with app.run_test(size=(110, 40)) as pilot:
            assert app.query_one("#transcription-provider", Select).value == "openai"
            assert app.query_one("#openai-transcription-model", Select).value == "gpt-4o-mini-transcribe"
            assert app._build_options().transcription_provider == "openai"
            assert "OpenAI" in app.query_one(TranscriptionSettings).preview()
            app.query_one("#transcription-provider", Select).value = "local"
            await pilot.pause()
            assert app.query_one("#local-transcription").display
            assert not app.query_one("#api-transcription").display
            assert app._build_options().transcription_provider == "local"
            app.query_one("#batch-size", Input).value = "4"
            app.query_one("#transcription-provider", Select).value = "openai"
            await pilot.pause()
            assert app._build_options().batch_size == 4
    asyncio.run(scenario())


def test_run_and_standalone_inherit_and_override(config, tmp_path):
    args = ["run", "https://example.com/a.m3u8", "--course", "Course", "--date", "2026-09-07", "--title", "Title"]
    with patch("lecture_util.cli.load_config", return_value=config), patch("lecture_util.cli._execute_run") as run:
        result = CliRunner().invoke(cli, args)
        assert result.exit_code == 0, result.output
        assert run.call_args.args[0].transcription_provider == "openai"
        result = CliRunner().invoke(cli, args + ["--openai-transcription-model", "whisper-1"])
        assert result.exit_code == 0, result.output
        assert run.call_args.args[0].openai_transcription_model == "whisper-1"
        result = CliRunner().invoke(cli, args + ["--transcription-provider", "local", "--device", "cpu"])
        assert result.exit_code == 0, result.output
        assert run.call_args.args[0].transcription_provider == "local"
    with patch("lecture_util.cli.load_config", return_value=config), patch("lecture_util.cli.transcription_stage", return_value=Transcript("ko", 1, "openai", "test", "test", [])) as transcribe:
        result = CliRunner().invoke(cli, ["transcribe", str(tmp_path), "--openai-transcription-model", "whisper-1"])
        assert result.exit_code == 0, result.output
        assert transcribe.call_args.kwargs["transcription_provider"] == "openai"
        assert transcribe.call_args.kwargs["openai_transcription_model"] == "whisper-1"


@pytest.mark.parametrize("flag,value", [("--whisper-model", "turbo"), ("--device", "auto"),
                                       ("--compute-type", "auto"), ("--batch-size", "0"), ("--beam-size", "default")])
def test_explicit_local_flags_rejected_for_api(config, tmp_path, flag, value):
    with patch("lecture_util.cli.load_config", return_value=config), patch("lecture_util.cli.transcription_stage") as transcribe:
        result = CliRunner().invoke(cli, ["transcribe", str(tmp_path), flag, value])
        assert result.exit_code != 0 and "only to local" in result.output
        transcribe.assert_not_called()


def test_resume_preserves_api_and_legacy_request(config, tmp_path):
    options = run_options(config)
    root = tmp_path / f"lecture-{lecture_id(options.url)}"
    save_request(root, replace(options, force=True), config.vault_root, config.video_root)
    restored, _, _ = load_request(root)
    assert restored == options
    assert transcription_options(restored).selected_model == "gpt-4o-mini-transcribe"
    with patch("lecture_util.cli.load_config", side_effect=AssertionError("must not load defaults")), patch("lecture_util.cli._execute_run") as run:
        result = CliRunner().invoke(cli, ["resume", str(root)])
        assert result.exit_code == 0, result.output
        assert run.call_args.args[0] == options
    data = json.loads((root / "request.json").read_text())
    del data["options"]["transcription_provider"]
    del data["options"]["openai_transcription_model"]
    (root / "request.json").write_text(json.dumps(data))
    assert load_request(root)[0].transcription_provider == "local"


def test_doctor_and_preflight_skip_local_backends(config, monkeypatch):
    from lecture_util.doctor import run_checks
    from lecture_util.transcription import preflight_transcription
    with patch("lecture_util.configuration.load_config", return_value=config), patch("lecture_util.api_transcription.resolve_api_key", return_value="test"), patch("lecture_util.doctor.importlib.util.find_spec", side_effect=AssertionError("must not load local models")), patch("lecture_util.transcription.detect_device", side_effect=AssertionError("must not inspect GPU")):
        assert preflight_transcription(transcription_options(run_options(config))) == "openai"
        assert any(check.name == "OpenAI transcription" and check.ok for check in run_checks())


def test_api_video_only_never_requests_credentials(config, tmp_path):
    selected = replace(run_options(config), video_only=True)
    from lecture_util.cli import _execute_run
    with patch("lecture_util.credentials.resolve_api_key", side_effect=AssertionError("no API key needed")), patch("lecture_util.cli._execute_download") as download:
        _execute_run(selected, vault_root=config.vault_root, video_root=config.video_root, cache_root=tmp_path)
        download.assert_called_once()


def test_api_standalone_cancel_exit_code(config, tmp_path):
    with patch("lecture_util.cli.load_config", return_value=config), patch("lecture_util.cli.transcription_stage", side_effect=KeyboardInterrupt()):
        result = CliRunner().invoke(cli, ["transcribe", str(tmp_path)])
        assert result.exit_code == 130
