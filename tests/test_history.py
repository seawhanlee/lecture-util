from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from textual.widgets import DataTable, Label

from lecture_util.history import ConfirmClearScreen, HistoryAction, HistoryApp, HistoryScreen
from lecture_util.state import workspace_lock


def seed(
    root: Path, name: str, *, title: str, updated_at: str, stages: dict | None = None,
    request: bool = True, published: bool = False,
) -> Path:
    workspace = root / f"lecture-{name}"
    workspace.mkdir(parents=True)
    (workspace / "run.json").write_text(json.dumps({
        "url": f"https://example.com/{name}/index.m3u8", "title": title,
        "course": "Fluids", "lecture_date": "2026-09-07", "updated_at": updated_at,
        "stages": stages or {},
    }), encoding="utf-8")
    if request:
        (workspace / "request.json").write_text("{}", encoding="utf-8")
    if published:
        (workspace / "publication.json").write_text(json.dumps({"status": "complete"}), encoding="utf-8")
    return workspace


def setup_cache(root: Path) -> Path:
    seed(root, "failed", title="Failed lecture", updated_at="2026-10-03T00:00:00+00:00",
         stages={"transcription": {"status": "failed"}})
    seed(root, "published", title="Published lecture", updated_at="2026-10-02T00:00:00+00:00",
         published=True)
    seed(root, "norequest", title="Video only", updated_at="2026-10-01T00:00:00+00:00", request=False)
    return root


async def loaded(app: HistoryApp, pilot) -> HistoryScreen:
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()
    screen = app.screen
    assert isinstance(screen, HistoryScreen)
    return screen


def message(screen: HistoryScreen) -> str:
    return str(screen.query_one("#history-message", Label).render())


class HistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_lists_workspaces_newest_first(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = setup_cache(Path(temp_dir).resolve())
            app = HistoryApp(cache)
            async with app.run_test(size=(120, 40)) as pilot:
                screen = await loaded(app, pilot)
                table = screen.query_one(DataTable)
                titles = [table.get_row_at(index)[2] for index in range(table.row_count)]
            self.assertEqual(titles, ["Failed lecture", "Published lecture", "Video only"])

    async def test_empty_cache_shows_empty_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir).resolve() / "missing"
            app = HistoryApp(missing)
            async with app.run_test(size=(120, 40)) as pilot:
                screen = await loaded(app, pilot)
                detail = str(screen.query_one("#history-detail", Label).render())
            self.assertIn("No cached lectures", detail)

    async def test_resume_returns_failed_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = setup_cache(Path(temp_dir).resolve())
            app = HistoryApp(cache)
            async with app.run_test(size=(120, 40)) as pilot:
                await loaded(app, pilot)
                await pilot.press("r")
                await pilot.pause()
            self.assertEqual(app.return_value, HistoryAction("resume", (cache / "lecture-failed").resolve()))

    async def test_enter_resumes_highlighted_row(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = setup_cache(Path(temp_dir).resolve())
            app = HistoryApp(cache)
            async with app.run_test(size=(120, 40)) as pilot:
                await loaded(app, pilot)
                await pilot.press("enter")
                await pilot.pause()
            self.assertEqual(app.return_value, HistoryAction("resume", (cache / "lecture-failed").resolve()))

    async def test_published_row_cannot_resume_but_can_be_reused(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = setup_cache(Path(temp_dir).resolve())
            app = HistoryApp(cache)
            async with app.run_test(size=(120, 40)) as pilot:
                screen = await loaded(app, pilot)
                await pilot.press("down", "r")
                await pilot.pause()
                self.assertIs(app.screen, screen)
                self.assertIn("Already published", message(screen))
                await pilot.press("e")
                await pilot.pause()
            self.assertEqual(app.return_value, HistoryAction("edit", (cache / "lecture-published").resolve()))

    async def test_row_without_request_cannot_resume_or_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = setup_cache(Path(temp_dir).resolve())
            app = HistoryApp(cache)
            async with app.run_test(size=(120, 40)) as pilot:
                screen = await loaded(app, pilot)
                await pilot.press("down", "down", "r")
                await pilot.pause()
                self.assertIn("No saved run request", message(screen))
                await pilot.press("e")
                await pilot.pause()
                self.assertIs(app.screen, screen)
                await pilot.press("escape")
                await pilot.pause()
            self.assertIsNone(app.return_value)

    async def test_running_workspace_actions_are_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = setup_cache(Path(temp_dir).resolve())
            app = HistoryApp(cache)
            with workspace_lock(cache / "lecture-failed"):
                async with app.run_test(size=(120, 40)) as pilot:
                    screen = await loaded(app, pilot)
                    for key in ("r", "e", "c"):
                        await pilot.press(key)
                        await pilot.pause()
                        self.assertIs(app.screen, screen)
                        self.assertIn("running in another process", message(screen))
            self.assertTrue((cache / "lecture-failed").is_dir())

    async def test_clear_requires_confirmation_and_refreshes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = setup_cache(Path(temp_dir).resolve())
            app = HistoryApp(cache)
            async with app.run_test(size=(120, 40)) as pilot:
                screen = await loaded(app, pilot)
                await pilot.press("c")
                await pilot.pause()
                self.assertIsInstance(app.screen, ConfirmClearScreen)
                await pilot.press("escape")
                await pilot.pause()
                self.assertTrue((cache / "lecture-failed").is_dir())

                await pilot.press("c")
                await pilot.pause()
                await pilot.click("#delete-cache")
                await pilot.pause()
                await app.workers.wait_for_complete()
                await pilot.pause()
                self.assertFalse((cache / "lecture-failed").exists())
                self.assertEqual(screen.query_one(DataTable).row_count, 2)
                self.assertIn("Cleared cache", message(screen))

    async def test_clear_keeps_other_workspaces(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = setup_cache(Path(temp_dir).resolve())
            app = HistoryApp(cache)
            async with app.run_test(size=(120, 40)) as pilot:
                await loaded(app, pilot)
                await pilot.press("c", "y")
                await pilot.pause()
                await app.workers.wait_for_complete()
            self.assertTrue((cache / "lecture-published").is_dir())
            self.assertTrue((cache / "lecture-norequest").is_dir())
