"""Discover picker-visible models through the installed Codex CLI."""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from lecture_util.errors import LectureUtilError


@dataclass(frozen=True)
class ModelCatalog:
    models: tuple[tuple[str, str], ...]
    status: str
    efforts: dict[str, tuple[str, ...]] = field(default_factory=dict)


def _catalog(entries: object, status: str, *, cached: bool = False) -> ModelCatalog:
    models = _choices(entries, cached=cached)
    visible = {model for _, model in models}
    efforts: dict[str, tuple[str, ...]] = {}
    assert isinstance(entries, list)
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        model = entry.get("slug" if cached else "model")
        if not isinstance(model, str) or model not in visible or model in efforts:
            continue
        levels = entry.get("supported_reasoning_levels" if cached
                           else "supportedReasoningEfforts")
        if isinstance(levels, list):
            values = [level.get("effort" if cached else "reasoningEffort")
                      for level in levels if isinstance(level, dict)]
            efforts[model] = tuple(dict.fromkeys(
                value for value in values if isinstance(value, str) and value
            ))
    return ModelCatalog(models, status, efforts)


def _choices(entries: object, *, cached: bool = False) -> tuple[tuple[str, str], ...]:
    if not isinstance(entries, list):
        raise ValueError("Invalid model list")
    choices: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if (cached and entry.get("visibility") != "list") or entry.get("hidden"):
            continue
        model = entry.get("slug" if cached else "model")
        label = entry.get("display_name" if cached else "displayName")
        if isinstance(model, str) and model.strip():
            choices.setdefault(model, label if isinstance(label, str) and label else model)
    return tuple((label, model) for model, label in choices.items())


async def _query_models() -> ModelCatalog:
    process = await asyncio.create_subprocess_exec(
        "codex", "app-server", stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    assert process.stdin is not None and process.stdout is not None

    async def send(message: dict) -> None:
        process.stdin.write((json.dumps(message) + "\n").encode())
        await process.stdin.drain()

    async def request(request_id: int, method: str, params: dict) -> dict:
        await send({"id": request_id, "method": method, "params": params})
        while line := await process.stdout.readline():
            response = json.loads(line)
            if not isinstance(response, dict) or response.get("id") != request_id:
                continue
            if "error" in response or not isinstance(response.get("result"), dict):
                raise LectureUtilError("Codex model query failed.")
            return response["result"]
        raise LectureUtilError("Codex model server exited.")

    try:
        await request(1, "initialize", {
            "clientInfo": {"name": "lecture_util", "version": "0.1.0"},
        })
        await send({"method": "initialized", "params": {}})
        entries: list = []
        cursor = None
        seen: set[str] = set()
        request_id = 2
        while True:
            params = {"includeHidden": False, "limit": 100}
            if cursor is not None:
                params["cursor"] = cursor
            result = await request(request_id, "model/list", params)
            page = result.get("data")
            if not isinstance(page, list):
                raise ValueError("Invalid model page")
            entries.extend(page)
            cursor = result.get("nextCursor")
            if cursor is None:
                return _catalog(entries, "Models loaded from Codex.")
            if not isinstance(cursor, str) or cursor in seen:
                raise ValueError("Invalid model cursor")
            seen.add(cursor)
            request_id += 1
    finally:
        if process.returncode is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
        try:
            await asyncio.wait_for(process.wait(), timeout=1)
        except TimeoutError:
            process.kill()
            await process.wait()


async def discover_models() -> ModelCatalog:
    try:
        async with asyncio.timeout(10):
            catalog = await _query_models()
        if catalog.models:
            return catalog
    except (OSError, ValueError, LectureUtilError, TimeoutError):
        pass
    codex_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    try:
        data = json.loads((codex_home / "models_cache.json").read_text(encoding="utf-8"))
        catalog = _catalog(
            data.get("models") if isinstance(data, dict) else None,
            "Using cached Codex models; live lookup unavailable.", cached=True,
        )
        if catalog.models:
            return catalog
    except (OSError, ValueError):
        pass
    return ModelCatalog((), "Models unavailable. Use Codex default or the saved model.")
