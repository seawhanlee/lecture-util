"""Keep form tests independent of the user's Codex installation and account."""
from unittest.mock import AsyncMock

import pytest

from lecture_util.codex_models import ModelCatalog


@pytest.fixture(autouse=True)
def mock_form_model_discovery(monkeypatch):
    monkeypatch.setattr(
        "lecture_util.form_ui.discover_models",
        AsyncMock(return_value=ModelCatalog((("Test model", "gpt-test"),), "Models loaded.")),
    )


@pytest.fixture(autouse=True)
def isolate_material_registry(tmp_path, monkeypatch):
    monkeypatch.setattr("lecture_util.materials._registry_path", lambda: tmp_path / "config" / "materials.json")
    monkeypatch.setattr("lecture_util.materials.default_cache_root", lambda: tmp_path / "material-cache")
