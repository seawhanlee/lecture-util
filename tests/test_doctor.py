from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from lecture_util import credentials, doctor
from lecture_util.cli import app
from lecture_util.errors import LectureUtilError


@pytest.fixture
def unavailable_storage(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        credentials, "_backend",
        Mock(side_effect=LectureUtilError("OS credential storage is unavailable.")),
    )
    monkeypatch.setattr("lecture_util.configuration.load_config", lambda **kwargs: None)
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None if name == "nvidia-smi" else f"/bin/{name}")
    monkeypatch.setattr(doctor.platform, "system", lambda: "Linux")
    monkeypatch.setattr(doctor.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(doctor.importlib.util, "find_spec", lambda name: object())


def test_doctor_continues_local_checks_without_secret_service(unavailable_storage):
    checks = {check.name: check for check in doctor.run_checks()}
    assert not checks["TypeSafe Jev"].ok
    assert "TYPESAFE_API_KEY" in checks["TypeSafe Jev"].detail
    assert "optional" in checks["TypeSafe Jev"].detail
    assert checks["platform"].ok
    assert checks["faster-whisper"].ok
    assert "NVIDIA GPU" in checks


def test_doctor_cli_reports_storage_failure_without_traceback(unavailable_storage):
    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "TypeSafe Jev" in result.output
    assert "faster-whisper" in result.output
    assert "Traceback" not in result.output
    assert isinstance(result.exception, SystemExit)


@pytest.mark.parametrize("has_openai_key", [False, True])
def test_doctor_checks_openai_without_secret_service(unavailable_storage, monkeypatch, has_openai_key):
    config = SimpleNamespace(
        transcription_provider="openai", openai_transcription_model="gpt-4o-transcribe",
    )
    monkeypatch.setattr("lecture_util.configuration.load_config", lambda **kwargs: config)
    # Exercise credential lookup without making requests or loading local models.
    monkeypatch.setattr("lecture_util.api_transcription.preflight_openai", credentials.resolve_api_key)
    local_probe = Mock(side_effect=AssertionError("must not load local models"))
    monkeypatch.setattr(doctor.importlib.util, "find_spec", local_probe)
    if has_openai_key:
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    checks = {check.name: check for check in doctor.run_checks()}
    assert not checks["TypeSafe Jev"].ok
    if has_openai_key:
        assert checks["OpenAI transcription"].ok
    else:
        assert not checks["Transcription configuration"].ok
    local_probe.assert_not_called()


def test_doctor_uses_typesafe_environment_key_without_secret_service(unavailable_storage, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    checks = {check.name: check for check in doctor.run_checks()}
    assert checks["TypeSafe Jev"].ok
