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
