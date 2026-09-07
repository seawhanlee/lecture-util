import asyncio
import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

import pytest

from lecture_util.codex_models import ModelCatalog, _catalog, _query_models, discover_models


class QueryTests(unittest.IsolatedAsyncioTestCase):
    def process(self, responses):
        process = Mock()
        process.returncode = None
        process.stdin.drain = AsyncMock()
        process.stdout.readline = AsyncMock(side_effect=[
            (json.dumps(response) + "\n").encode() for response in responses
        ] + [b""])
        process.wait = AsyncMock(return_value=0)
        return process

    async def test_pages_notifications_hidden_and_duplicates(self):
        process = self.process([
            {"id": 1, "result": {}},
            {"method": "notice"},
            {"id": 2, "result": {"data": [
                {"model": "one", "displayName": "One"},
                {"model": "hidden", "hidden": True},
            ], "nextCursor": "next"}},
            {"id": 3, "result": {"data": [
                {"model": "one"}, {"model": "two"}, {}, None,
            ], "nextCursor": None}},
        ])
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
            self.assertEqual((await _query_models()).models, (("One", "one"), ("two", "two")))
        sent = [json.loads(call.args[0]) for call in process.stdin.write.call_args_list]
        self.assertEqual(sent[1]["method"], "initialized")
        self.assertEqual(sent[-1]["params"]["cursor"], "next")
        process.terminate.assert_called_once()
        process.wait.assert_awaited_once()

    async def test_server_errors_and_exit_reap_process(self):
        from lecture_util.errors import LectureUtilError

        for responses in ([], [{"id": 1, "error": {"message": "failed"}}]):
            with self.subTest(responses=responses):
                process = self.process(responses)
                with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
                    with self.assertRaises(LectureUtilError):
                        await _query_models()
                process.terminate.assert_called_once()
                process.wait.assert_awaited_once()

    async def test_cancellation_reaps_process(self):
        process = self.process([])
        started = asyncio.Event()

        async def blocked():
            started.set()
            await asyncio.Event().wait()

        process.stdout.readline = blocked
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
            task = asyncio.create_task(_query_models())
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        process.terminate.assert_called_once()
        process.wait.assert_awaited_once()


def test_live_models_do_not_read_cache(monkeypatch):
    monkeypatch.setattr("lecture_util.codex_models._query_models", AsyncMock(
        return_value=ModelCatalog((("One", "one"),), "Loaded"),
    ))
    with patch("pathlib.Path.read_text", side_effect=AssertionError("cache read")):
        assert asyncio.run(discover_models()).models == (("One", "one"),)


@pytest.mark.parametrize("error", [FileNotFoundError(), TimeoutError(), ValueError()])
def test_failed_lookup_uses_visible_cached_models(tmp_path, monkeypatch, error):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "models_cache.json").write_text(json.dumps({"models": [
        {"slug": "one", "display_name": "One", "visibility": "list"},
        {"slug": "secret", "visibility": "hide"},
    ]}))
    monkeypatch.setattr("lecture_util.codex_models._query_models", AsyncMock(side_effect=error))
    result = asyncio.run(discover_models())
    assert result.models == (("One", "one"),)
    assert "cached" in result.status


@pytest.mark.parametrize("contents", [None, "{", "[]", '{"models": null}'])
def test_missing_or_invalid_cache_is_nonfatal(tmp_path, monkeypatch, contents):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    if contents is not None:
        (tmp_path / "models_cache.json").write_text(contents)
    monkeypatch.setattr("lecture_util.codex_models._query_models", AsyncMock(side_effect=FileNotFoundError()))
    assert asyncio.run(discover_models()).models == ()


@pytest.mark.parametrize("cached", [False, True])
def test_catalog_reads_model_specific_efforts(cached):
    entry = ({
        "slug": "one", "visibility": "list",
        "supported_reasoning_levels": [{"effort": "low"}, {"effort": "ultra"}],
    } if cached else {
        "model": "one", "supportedReasoningEfforts": [
            {"reasoningEffort": "low"}, {"reasoningEffort": "ultra"},
        ],
    })
    catalog = _catalog([entry], "Loaded", cached=cached)
    assert catalog.efforts == {"one": ("low", "ultra")}
