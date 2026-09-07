from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

from lecture_util.errors import LectureUtilError
from lecture_util.media import validate_hls_url
from lecture_util.state import atomic_write_json
from lecture_util.summary import DEFAULT_PROMPT
from lecture_util.vault import (
    DEFAULT_VAULT_ROOT,
    default_semester_start,
    discover_courses,
    validate_semester_start,
)


CONFIG_VERSION = 2
SUPPORTED_DEVICES = ("auto", "mlx", "cuda", "cpu")


@dataclass(frozen=True, slots=True)
class AppConfig:
    vault_root: Path
    video_root: Path
    semester_start: str
    whisper_model: str = "large-v3"
    language: str = "auto"
    device: str = "auto"
    llm_model: str | None = None
    video_in_vault_allowed: bool = False
    reasoning_effort: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["version"] = CONFIG_VERSION
        data["vault_root"] = str(self.vault_root)
        data["video_root"] = str(self.video_root)
        return data


def default_config_path() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME")
    root = Path(config_home).expanduser() if config_home else Path.home() / ".config"
    return root / "lecture-util" / "config.json"


def normalize_reasoning_effort(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise LectureUtilError("Thinking effort must be a string or null.")
    return value.strip() or None


def default_app_config(reference: date | None = None) -> AppConfig:
    return AppConfig(
        vault_root=DEFAULT_VAULT_ROOT,
        video_root=Path.home() / "Videos" / "lecture-util",
        semester_start=default_semester_start(reference),
    )


def _required_string(data: dict[str, Any], key: str, label: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise LectureUtilError(f"Configured {label} must not be empty.")
    return value.strip()


def validate_app_config(
    config: AppConfig,
    *,
    validate_vault: bool = True,
    allow_missing_video_root: bool = True,
    allow_unconfirmed_video_root: bool = False,
) -> AppConfig:
    vault_root = config.vault_root.expanduser()
    if not vault_root.is_absolute():
        vault_root = (Path.cwd() / vault_root).resolve()
    else:
        vault_root = vault_root.resolve()
    video_root = config.video_root.expanduser()
    if not video_root.is_absolute():
        video_root = (Path.cwd() / video_root).resolve()
    else:
        video_root = video_root.resolve()

    semester_start = validate_semester_start(config.semester_start)
    whisper_model = config.whisper_model.strip()
    if not whisper_model:
        raise LectureUtilError("Configured Whisper model must not be empty.")
    language = config.language.strip()
    if not language:
        raise LectureUtilError("Configured lecture language must not be empty.")
    if config.device not in SUPPORTED_DEVICES:
        supported = ", ".join(SUPPORTED_DEVICES)
        raise LectureUtilError(
            f"Configured transcription device must be one of: {supported}."
        )
    llm_model = config.llm_model.strip() if config.llm_model else None

    if validate_vault:
        discover_courses(vault_root)
    if video_root.exists() and not video_root.is_dir():
        raise LectureUtilError(
            f"Video storage path is not a directory: {video_root}"
        )
    if not allow_missing_video_root and not video_root.is_dir():
        raise LectureUtilError(
            f"Video storage directory does not exist: {video_root}"
        )

    video_in_vault = video_root == vault_root or video_root.is_relative_to(vault_root)
    if (
        video_in_vault
        and not config.video_in_vault_allowed
        and not allow_unconfirmed_video_root
    ):
        raise LectureUtilError(
            "Video storage is inside the Obsidian Vault and requires explicit confirmation."
        )

    return AppConfig(
        vault_root=vault_root,
        video_root=video_root,
        semester_start=semester_start,
        whisper_model=whisper_model,
        language=language,
        device=config.device,
        llm_model=llm_model,
        reasoning_effort=normalize_reasoning_effort(config.reasoning_effort),
        video_in_vault_allowed=(
            config.video_in_vault_allowed if video_in_vault else False
        ),
    )


def video_root_is_in_vault(config: AppConfig) -> bool:
    normalized = validate_app_config(
        config,
        validate_vault=False,
        allow_unconfirmed_video_root=True,
    )
    return normalized.video_root == normalized.vault_root or (
        normalized.video_root.is_relative_to(normalized.vault_root)
    )


def app_config_from_dict(
    data: Any,
    *,
    validate_vault: bool = True,
) -> AppConfig:
    if not isinstance(data, dict):
        raise LectureUtilError("Configuration must be a JSON object.")
    if data.get("version") != CONFIG_VERSION:
        raise LectureUtilError(
            f"Unsupported configuration version: {data.get('version')!r}."
        )
    vault_root = _required_string(data, "vault_root", "Vault path")
    video_root = _required_string(data, "video_root", "video storage path")
    semester_start = _required_string(data, "semester_start", "semester start date")
    whisper_model = _required_string(data, "whisper_model", "Whisper model")
    language = _required_string(data, "language", "lecture language")
    device = _required_string(data, "device", "transcription device")
    llm_model_value = data.get("llm_model")
    if llm_model_value is not None and not isinstance(llm_model_value, str):
        raise LectureUtilError("Configured Codex model must be a string or null.")
    video_in_vault_allowed = data.get("video_in_vault_allowed", False)
    if not isinstance(video_in_vault_allowed, bool):
        raise LectureUtilError(
            "Configured Vault video storage confirmation must be true or false."
        )
    return validate_app_config(
        AppConfig(
            vault_root=Path(vault_root),
            video_root=Path(video_root),
            semester_start=semester_start,
            whisper_model=whisper_model,
            language=language,
            device=device,
            llm_model=llm_model_value,
            reasoning_effort=normalize_reasoning_effort(data.get("reasoning_effort")),
            video_in_vault_allowed=video_in_vault_allowed,
        ),
        validate_vault=validate_vault,
    )


def _legacy_app_config_from_dict(data: Any) -> AppConfig:
    if not isinstance(data, dict) or data.get("version") != 1:
        raise LectureUtilError("Configuration cannot be used for onboarding.")
    llm_model = data.get("llm_model")
    if llm_model is not None and not isinstance(llm_model, str):
        raise LectureUtilError("Configured Codex model must be a string or null.")
    return validate_app_config(
        AppConfig(
            vault_root=Path(_required_string(data, "vault_root", "Vault path")),
            video_root=Path.home() / "Videos" / "lecture-util",
            semester_start=_required_string(
                data,
                "semester_start",
                "semester start date",
            ),
            whisper_model=_required_string(data, "whisper_model", "Whisper model"),
            language=_required_string(data, "language", "lecture language"),
            device=_required_string(data, "device", "transcription device"),
            llm_model=llm_model,
        ),
        validate_vault=False,
        allow_unconfirmed_video_root=True,
    )


def load_config(
    path: Path | None = None,
    *,
    required: bool = False,
    validate_vault: bool = True,
) -> AppConfig | None:
    selected_path = path or default_config_path()
    if not selected_path.exists():
        if required:
            raise LectureUtilError(
                "lecture-util is not configured. Run 'lecture-util onboard' first."
            )
        return None
    try:
        data = json.loads(selected_path.read_text(encoding="utf-8"))
        return app_config_from_dict(data, validate_vault=validate_vault)
    except LectureUtilError as error:
        raise LectureUtilError(
            f"Invalid configuration {selected_path}: {error} "
            "Run 'lecture-util onboard' to repair it."
        ) from error
    except (OSError, json.JSONDecodeError) as error:
        raise LectureUtilError(
            f"Could not load configuration {selected_path}: {error}. "
            "Run 'lecture-util onboard' to repair it."
        ) from error


def load_onboarding_config(path: Path | None = None) -> AppConfig | None:
    selected_path = path or default_config_path()
    if not selected_path.exists():
        return None
    try:
        data = json.loads(selected_path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("version") == 1:
            return _legacy_app_config_from_dict(data)
        return app_config_from_dict(data, validate_vault=False)
    except LectureUtilError:
        raise
    except (OSError, json.JSONDecodeError) as error:
        raise LectureUtilError(
            f"Could not load configuration {selected_path}: {error}."
        ) from error


def save_config(config: AppConfig, path: Path | None = None) -> AppConfig:
    selected_path = path or default_config_path()
    try:
        normalized = validate_app_config(config)
        normalized.video_root.mkdir(parents=True, exist_ok=True)
        if not os.access(normalized.video_root, os.W_OK):
            raise LectureUtilError(
                f"Video storage directory is not writable: {normalized.video_root}"
            )
        validated = validate_app_config(
            normalized,
            allow_missing_video_root=False,
        )
        atomic_write_json(selected_path, validated.to_dict())
    except LectureUtilError:
        raise
    except OSError as error:
        raise LectureUtilError(
            f"Could not save configuration {selected_path}: {error}"
        ) from error
    return validated


def normalize_tags(values: list[str] | None) -> list[str] | None:
    if values is None:
        return None
    normalized: list[str] = []
    for value in values:
        for candidate in value.split(","):
            tag = candidate.strip()
            if tag and tag not in normalized:
                normalized.append(tag)
    return normalized


def read_urls(url: str | None, input_file: Path | None) -> list[str]:
    if (url is None) == (input_file is None):
        raise LectureUtilError("Provide exactly one URL or --input URL_LIST_FILE.")
    if url is not None:
        validate_hls_url(url)
        return [url]
    assert input_file is not None
    try:
        lines = input_file.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise LectureUtilError(f"Could not read URL list {input_file}: {error}") from error
    urls = [
        line.strip()
        for line in lines
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not urls:
        raise LectureUtilError("The URL list does not contain any lecture URLs.")
    for lecture_url in urls:
        validate_hls_url(lecture_url)
    return urls


def resolve_prompt(prompt: str | None, prompt_file: Path | None) -> str:
    if prompt is not None and prompt_file is not None:
        raise LectureUtilError("--prompt and --prompt-file cannot be used together.")
    if prompt_file is not None:
        try:
            return prompt_file.read_text(encoding="utf-8")
        except OSError as error:
            raise LectureUtilError(f"Could not read prompt file {prompt_file}: {error}") from error
    return prompt if prompt is not None else DEFAULT_PROMPT
