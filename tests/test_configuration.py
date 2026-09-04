from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from lecture_util.configuration import (
    AppConfig,
    default_config_path,
    load_config,
    load_onboarding_config,
    save_config,
)
from lecture_util.errors import LectureUtilError
from lecture_util.vault import COURSES_DIRECTORY


COURSE = "공기역학특론"


def create_vault(root: Path) -> None:
    (root / COURSES_DIRECTORY / COURSE / "Lectures").mkdir(parents=True)


def config_for(vault: Path, video_root: Path | None = None) -> AppConfig:
    return AppConfig(
        vault_root=vault,
        video_root=video_root or vault.parent / "videos",
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
    assert loaded.video_root == (tmp_path / "videos").resolve()
    assert loaded.video_root.is_dir()
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 2


def test_v1_config_requires_onboarding_but_preserves_old_defaults(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    create_vault(vault)
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "vault_root": str(vault),
                "semester_start": "2026-08-31",
                "whisper_model": "turbo",
                "language": "ko",
                "device": "cuda",
                "llm_model": "gpt-test",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(LectureUtilError, match="lecture-util onboard"):
        load_config(path)

    recovered = load_onboarding_config(path)
    assert recovered is not None
    assert recovered.vault_root == vault.resolve()
    assert recovered.semester_start == "2026-08-31"
    assert recovered.whisper_model == "turbo"
    assert recovered.video_root == (Path.home() / "Videos" / "lecture-util").resolve()


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


def test_video_path_inside_vault_requires_explicit_confirmation(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    create_vault(vault)
    path = tmp_path / "config.json"

    with pytest.raises(LectureUtilError, match="explicit confirmation"):
        save_config(config_for(vault, vault / "Videos"), path)

    confirmed = config_for(vault, vault / "Videos")
    confirmed = AppConfig(
        vault_root=confirmed.vault_root,
        video_root=confirmed.video_root,
        semester_start=confirmed.semester_start,
        whisper_model=confirmed.whisper_model,
        language=confirmed.language,
        device=confirmed.device,
        llm_model=confirmed.llm_model,
        video_in_vault_allowed=True,
    )
    saved = save_config(confirmed, path)
    assert saved.video_root.is_dir()
    assert saved.video_in_vault_allowed


def test_invalid_device_is_rejected_when_loading(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    create_vault(vault)
    path = tmp_path / "config.json"
    data = config_for(vault).to_dict()
    data["device"] = "tpu"
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(LectureUtilError, match="transcription device"):
        load_config(path)
