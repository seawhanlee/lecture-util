from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

from lecture_util.errors import LectureUtilError, TranscriptionOptionError
from lecture_util.models import TranscriptionOptions
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
    compute_type: str = "auto"
    batch_size: int = 0
    beam_size: int | None = None

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
    validate_transcription_options(TranscriptionOptions(
        whisper_model, language, config.device, config.compute_type,
        config.batch_size, config.beam_size,
    ))
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
        compute_type=config.compute_type, batch_size=config.batch_size, beam_size=config.beam_size,
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
    video_only: bool = False,
) -> AppConfig:
    if not isinstance(data, dict):
        raise LectureUtilError("Configuration must be a JSON object.")
    if data.get("version") != CONFIG_VERSION:
        raise LectureUtilError(
            f"Unsupported configuration version: {data.get('version')!r}."
        )
    if video_only:
        data = {**data, "whisper_model": "large-v3", "language": "auto",
                "device": "auto", "llm_model": None, "reasoning_effort": None}
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
            compute_type=data.get("compute_type", "auto"),
            batch_size=data.get("batch_size", 0),
            beam_size=data.get("beam_size"),
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
    video_only: bool = False,
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
        return app_config_from_dict(data, validate_vault=validate_vault, video_only=video_only)
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


# Whisper's shared multilingual tokenizer codes; no model import on CLI startup.
LANGUAGE_CODES = frozenset(
    "auto af am ar as az ba be bg bn bo br bs ca cs cy da de el en es et eu fa fi "
    "fo fr gl gu ha haw he hi hr ht hu hy id is it ja jw ka kk km kn ko la lb ln "
    "lo lt lv mg mi mk ml mn mr ms mt my ne nl nn no oc pa pl ps pt ro ru sa sd "
    "si sk sl sn so sq sr su sv sw ta te tg th tk tl tr tt uk ur uz vi yi yo zh yue".split()
)
COMPUTE_TYPES = ("auto", "float16", "float32", "int8", "int8_float16")


def validate_transcription_options(options: TranscriptionOptions) -> None:
    if not isinstance(options.model, str) or not options.model.strip():
        raise TranscriptionOptionError("Enter a Whisper model.", "whisper-model")
    if not isinstance(options.language, str) or options.language not in LANGUAGE_CODES:
        raise TranscriptionOptionError("Unsupported lecture language; use auto or a Whisper language code such as ko/en.", "language")
    if options.device not in SUPPORTED_DEVICES:
        raise TranscriptionOptionError(f"Unsupported device: {options.device}", "device")
    if options.compute_type not in COMPUTE_TYPES:
        raise TranscriptionOptionError(f"Unsupported compute type: {options.compute_type}", "compute-type")
    if type(options.batch_size) is not int or options.batch_size < 0:
        raise TranscriptionOptionError("Batch size must be a non-negative integer (0 disables batching).", "batch-size")
    if options.beam_size is not None and (type(options.beam_size) is not int or options.beam_size < 1):
        raise TranscriptionOptionError("Beam size must be a positive integer or blank for the default.", "beam-size")
    if options.device == "mlx" and (options.compute_type != "auto" or options.batch_size or options.beam_size is not None):
        raise TranscriptionOptionError("MLX requires compute type auto, batch size 0 and default beam size.", "device")
    if options.model.startswith(("/", "./", "../", "~")) and not Path(options.model).expanduser().is_dir():
        raise TranscriptionOptionError(f"Local Whisper model directory does not exist: {options.model}", "whisper-model")


def resolve_beam_size(value: str | None, default: int | None) -> int | None:
    if value is None:
        return default
    if value.strip() in {"", "default"}:
        return None
    try:
        result = int(value)
        if result < 1:
            raise ValueError("not positive")
        return result
    except ValueError as error:
        raise TranscriptionOptionError("Beam size must be a positive integer or default.", "beam-size") from error
