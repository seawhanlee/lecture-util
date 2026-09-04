from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from lecture_util.configuration import (
    AppConfig,
    default_config_path,
    load_config,
    save_config,
)
from lecture_util.errors import LectureUtilError
from lecture_util.vault import COURSES_DIRECTORY


COURSE = "공기역학특론"


def create_vault(root: Path) -> None:
    (root / COURSES_DIRECTORY / COURSE / "Lectures").mkdir(parents=True)


def config_for(vault: Path) -> AppConfig:
    return AppConfig(
        vault_root=vault,
        semester_start="2026-08-31",
        whisper_model="turbo",
        language="ko",
        device="cuda",
        llm_model="gpt-test",
    )


def test_default_config_path_honors_xdg_config_home(tmp_path: Path) -> None:
    with patch.dict("os.environ", {"XDG_CONFIG_HOME": str(tmp_path)}):
        assert default_config_path() == tmp_path / "lecture-util" / "config.json"


def test_config_round_trip_normalizes_and_preserves_values(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    create_vault(vault)
    path = tmp_path / "config" / "config.json"

    saved = save_config(config_for(vault), path)
    loaded = load_config(path)

    assert loaded == saved
    assert loaded is not None
    assert loaded.vault_root == vault.resolve()
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 1


def test_missing_required_config_explains_how_to_onboard(tmp_path: Path) -> None:
    with pytest.raises(LectureUtilError, match="lecture-util onboard"):
        load_config(tmp_path / "missing.json", required=True)


def test_invalid_config_explains_how_to_repair_it(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(LectureUtilError, match="lecture-util onboard"):
        load_config(path)


def test_config_requires_an_immediately_usable_vault(tmp_path: Path) -> None:
    empty_vault = tmp_path / "empty"
    empty_vault.mkdir()

    with pytest.raises(LectureUtilError, match="Courses directory does not exist"):
        save_config(config_for(empty_vault), tmp_path / "config.json")


def test_invalid_device_is_rejected_when_loading(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    create_vault(vault)
    path = tmp_path / "config.json"
    data = config_for(vault).to_dict()
    data["device"] = "tpu"
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(LectureUtilError, match="transcription device"):
        load_config(path)
