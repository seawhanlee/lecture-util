from unittest.mock import Mock

import pytest

from lecture_util import credentials
from lecture_util.errors import LectureUtilError


@pytest.fixture
def backend(monkeypatch):
    backend = Mock()
    backend.get_password.return_value = None
    monkeypatch.setattr(credentials, "_backend", lambda: backend)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    return backend


def test_environment_precedence(backend, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "  environment-key  ")
    assert credentials.resolve_api_key() == "environment-key"
    backend.get_password.assert_not_called()
    assert "environment-key" not in credentials.credential_status()


def test_register_keep_replace_delete(backend):
    credentials.save_api_key("  test-key  ")
    backend.set_password.assert_called_once_with("lecture-util", "openai-api-key", "test-key")
    credentials.save_api_key("")
    assert backend.set_password.call_count == 1
    backend.get_password.return_value = "test-key"
    assert credentials.resolve_api_key() == "test-key"
    credentials.save_api_key("new-key")
    credentials.save_api_key(None, delete=True)
    backend.delete_password.assert_called_once_with("lecture-util", "openai-api-key")


def test_missing_key_and_storage_failure(backend):
    with pytest.raises(LectureUtilError, match="missing"):
        credentials.resolve_api_key()
    backend.set_password.side_effect = RuntimeError("private-key")
    with pytest.raises(LectureUtilError) as error:
        credentials.save_api_key("private-key")
    assert "private-key" not in str(error.value)
    backend.get_password.side_effect = RuntimeError("private-key")
    with pytest.raises(LectureUtilError, match="Could not read"):
        credentials.resolve_api_key()


@pytest.mark.parametrize("provider", ["openai", "typesafe"])
def test_environment_key_without_os_storage(monkeypatch, provider):
    unavailable = Mock(side_effect=LectureUtilError("OS credential storage is unavailable"))
    monkeypatch.setattr(credentials, "_backend", unavailable)
    variable = "OPENAI_API_KEY" if provider == "openai" else "TYPESAFE_API_KEY"
    monkeypatch.setenv(variable, "  environment-key  ")
    resolve = credentials.resolve_api_key if provider == "openai" else credentials.resolve_typesafe_api_key
    assert resolve() == "environment-key"
    unavailable.assert_not_called()


def test_typesafe_missing_key_and_storage_failure(backend):
    assert credentials.resolve_typesafe_api_key(required=False) is None
    with pytest.raises(LectureUtilError, match="missing"):
        credentials.resolve_typesafe_api_key()
    backend.get_password.side_effect = RuntimeError("private-key")
    with pytest.raises(LectureUtilError, match="Could not read") as error:
        credentials.resolve_typesafe_api_key(required=False)
    assert "private-key" not in str(error.value)


def test_unavailable_secret_service_guides_both_providers(monkeypatch):
    from keyring.backends import SecretService

    monkeypatch.setattr(credentials.sys, "platform", "linux")
    monkeypatch.setattr(SecretService, "Keyring", lambda: Mock(priority=0))
    with pytest.raises(LectureUtilError) as error:
        credentials._backend()
    assert "WSL" in str(error.value)
    assert "OPENAI_API_KEY" in str(error.value)
    assert "TYPESAFE_API_KEY" in str(error.value)
