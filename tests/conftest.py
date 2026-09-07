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
