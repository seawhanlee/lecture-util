"""OpenAI credentials kept outside settings and recovery artifacts."""
from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from keyring.backend import KeyringBackend

from lecture_util.errors import LectureUtilError

SERVICE = "lecture-util"
ACCOUNT = "openai-api-key"


def _backend() -> KeyringBackend:
    # Select only OS-backed implementations; never accept a plaintext fallback.
    try:
        if sys.platform == "darwin":
            from keyring.backends.macOS import Keyring
        elif sys.platform == "linux":
            from keyring.backends.SecretService import Keyring
        else:
            raise LectureUtilError("OS credential storage is supported on macOS and Linux.")
        backend = Keyring()
        if backend.priority <= 0:
            raise RuntimeError("unavailable")
        return backend
    except LectureUtilError:
        raise
    except Exception:
        raise LectureUtilError(
            "OS credential storage is unavailable. Unlock macOS Keychain or Linux Secret Service; "
            "alternatively set OPENAI_API_KEY."
        ) from None


def stored_api_key() -> str | None:
    try:
        return _backend().get_password(SERVICE, ACCOUNT)
    except LectureUtilError:
        raise
    except Exception:
        raise LectureUtilError("Could not read the OpenAI key from OS credential storage.") from None


def resolve_api_key() -> str:
    key = os.environ.get("OPENAI_API_KEY", "").strip() or stored_api_key()
    if not key:
        raise LectureUtilError("OpenAI API key is missing. Register it with lecture-util config or set OPENAI_API_KEY.")
    return key


def save_api_key(value: str | None, *, delete: bool = False) -> None:
    """Blank means keep; deletion must be explicitly selected by the user."""
    if not delete and not (value and value.strip()):
        return
    try:
        backend = _backend()
        if delete:
            if backend.get_password(SERVICE, ACCOUNT) is not None:
                backend.delete_password(SERVICE, ACCOUNT)
        else:
            backend.set_password(SERVICE, ACCOUNT, value.strip())
    except LectureUtilError:
        raise
    except Exception:
        raise LectureUtilError("Could not update the OpenAI key in OS credential storage. Your settings were not saved.") from None


def credential_status() -> str:
    if os.environ.get("OPENAI_API_KEY", "").strip():
        return "OPENAI_API_KEY is set and takes precedence over the stored key."
    try:
        return "OpenAI key registered." if stored_api_key() else "No OpenAI key registered."
    except LectureUtilError as error:
        return str(error)
