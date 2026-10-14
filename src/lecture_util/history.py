"""Browse cached lecture runs and choose a recovery action."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, DataTable, Label

from lecture_util.cache import CachedLecture, scan_cached_lectures
from lecture_util.errors import LectureUtilError
from lecture_util.progress import format_size
from lecture_util.state import clear_workspace, is_workspace_locked
from lecture_util.vault import default_cache_root


@dataclass(frozen=True, slots=True)
class HistoryAction:
    """A history selection handed back to the caller of the history screen."""

    kind: Literal["resume", "edit"]
    lecture_dir: Path


def lecture_label(item: CachedLecture) -> str:
    if item.course and item.title:
        return f"{item.course} · {item.title}"
    return item.title or item.url or item.lecture_id


def unavailable_reason(item: CachedLecture, kind: Literal["resume", "edit", "clear"]) -> str | None:
    """Explain why an action cannot run for a row, or return None when it can."""
    if item.is_locked:
        return "This lecture is running in another process."
    if kind == "clear":
        return None
    if not (item.path / "request.json").is_file():
        return "No saved run request; only full runs can be resumed or reused."
    if kind == "resume" and item.is_published:
        return "Already published; press e to reuse its settings for a new lecture."
    return None


class ConfirmClearScreen(ModalScreen[bool]):
    DEFAULT_CSS = """
    ConfirmClearScreen { align: center middle; background: $background 70%; }
    #confirm-dialog { width: 72; max-width: 95%; height: auto; padding: 1 2;
                      background: $surface; border: round $warning; }
    #confirm-title { text-style: bold; color: $warning; }
    #confirm-detail { margin-top: 1; color: $text-muted; }
    #confirm-actions { height: 3; margin-top: 1; align-horizontal: right; }
    #confirm-actions Button { margin-left: 1; }
    """
    BINDINGS = [
        Binding("y", "confirm", "Delete"),
        Binding("escape,n", "cancel", "Keep"),
    ]

    def __init__(self, item: CachedLecture) -> None:
        super().__init__()
        self.item = item

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-dialog"):
            yield Label("Clear this lecture cache?", id="confirm-title")
            yield Label(
                f"{lecture_label(self.item)}\n{self.item.path}\n"
                f"{format_size(self.item.total_size)} will be deleted. "
                "Published Vault notes and stored videos are kept.",
                id="confirm-detail", markup=False,
            )
            with Horizontal(id="confirm-actions"):
                yield Button("Keep", id="keep-cache")
                yield Button("Delete cache", id="delete-cache", variant="error")

    def on_mount(self) -> None:
        self.query_one("#keep-cache", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "delete-cache")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class HistoryScreen(Screen[HistoryAction | None]):
    DEFAULT_CSS = """
    HistoryScreen { align-horizontal: center; }
    #history-header { width: 120; max-width: 100%; height: auto; padding: 1 3; }
    #history-title { text-style: bold; color: $accent; }
    #history-description { color: $text-muted; }
    #history-table { width: 120; max-width: 100%; height: 1fr; margin: 0 3; }
    #history-detail { width: 120; max-width: 100%; height: auto; min-height: 3;
                      padding: 0 3; color: $text-muted; }
    #history-message { width: 120; max-width: 100%; height: auto; padding: 0 3; color: $warning; }
    #history-hint { width: 120; max-width: 100%; height: 1; padding: 0 3; color: $text-muted; }
    """
    BINDINGS = [
        Binding("r", "resume", "Resume"),
        Binding("e", "edit", "Reuse settings"),
        Binding("c", "clear", "Clear cache"),
        Binding("f5", "reload", "Refresh"),
        Binding("escape", "back", "Back"),
    ]

    def __init__(self, cache_root: Path | None = None) -> None:
        super().__init__()
        self.cache_root = (cache_root or default_cache_root()).expanduser().resolve()
        self.lectures: list[CachedLecture] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="history-header"):
            yield Label("Run history", id="history-title")
            yield Label(str(self.cache_root), id="history-description", markup=False)
        yield DataTable(id="history-table", cursor_type="row", zebra_stripes=True)
        yield Label("Loading cached lectures…", id="history-detail", markup=False)
        yield Label("", id="history-message", markup=False)
        yield Label(
            "↑↓ Select · R Resume · E Reuse settings · C Clear cache · F5 Refresh · Esc Back",
            id="history-hint", markup=False,
        )

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        for label in ("Date", "Course", "Title", "Status", "Updated", "Size"):
            table.add_column(label, key=label.lower())
        table.focus()
        self.action_reload()

    def action_reload(self, notice: str = "") -> None:
        self.run_worker(self._load(notice), exclusive=True, group="history-load")

    async def _load(self, notice: str = "") -> None:
        lectures = await asyncio.to_thread(scan_cached_lectures, self.cache_root)
        self.show_lectures(lectures, notice=notice)

    def show_lectures(self, lectures: list[CachedLecture], notice: str = "") -> None:
        self.lectures = lectures
        table = self.query_one(DataTable)
        table.clear()
        for item in lectures:
            table.add_row(
                item.lecture_date or "-",
                item.course or "-",
                item.title or item.url or "-",
                Text.from_markup(item.display_status),
                _format_updated(item.updated_at),
                format_size(item.total_size),
                key=str(item.path),
            )
        if not lectures:
            self._detail(f"No cached lectures in {self.cache_root}.")
        else:
            table.move_cursor(row=0)
            self._describe(lectures[0])
        self._message(notice)

    def selected(self) -> CachedLecture | None:
        table = self.query_one(DataTable)
        if not self.lectures or not 0 <= table.cursor_row < len(self.lectures):
            return None
        return self.lectures[table.cursor_row]

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if 0 <= event.cursor_row < len(self.lectures):
            self._describe(self.lectures[event.cursor_row])

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_resume()

    def action_resume(self) -> None:
        self._choose("resume")

    def action_edit(self) -> None:
        self._choose("edit")

    def action_clear(self) -> None:
        item = self.selected()
        if item is None:
            return
        if reason := unavailable_reason(item, "clear"):
            self._message(reason)
            return
        self.app.push_screen(ConfirmClearScreen(item), lambda confirmed: self._clear(item, confirmed))

    def action_back(self) -> None:
        self.dismiss(None)

    def _choose(self, kind: Literal["resume", "edit"]) -> None:
        item = self.selected()
        if item is None:
            return
        if reason := unavailable_reason(item, kind):
            self._message(reason)
            return
        self.dismiss(HistoryAction(kind, item.path))

    def _clear(self, item: CachedLecture, confirmed: bool | None) -> None:
        if not confirmed:
            return
        # Re-check: another process may have started using the workspace meanwhile.
        if is_workspace_locked(item.path):
            self._message("This lecture is running in another process.")
            return
        try:
            clear_workspace(item.path)
        except LectureUtilError as error:
            self._message(str(error))
            return
        self.action_reload(notice=f"Cleared cache: {lecture_label(item)}")

    def _describe(self, item: CachedLecture) -> None:
        source = item.url or "unknown source"
        actions = [
            name for name, kind in (("R resume", "resume"), ("E reuse", "edit"), ("C clear", "clear"))
            if unavailable_reason(item, kind) is None  # type: ignore[arg-type]
        ]
        self._detail(
            f"{lecture_label(item)}\nSource: {source}\nCache: {item.path}\n"
            f"Available: {', '.join(actions) or 'none'}"
        )

    def _detail(self, text: str) -> None:
        self.query_one("#history-detail", Label).update(text)

    def _message(self, text: str) -> None:
        self.query_one("#history-message", Label).update(text)


def _format_updated(value: datetime | None) -> str:
    if value is None:
        return "-"
    return value.astimezone().strftime("%Y-%m-%d %H:%M")


class HistoryApp(App[HistoryAction | None]):
    """Standalone host for ``lecture-util history``."""

    ENABLE_COMMAND_PALETTE = False

    def __init__(self, cache_root: Path | None = None) -> None:
        super().__init__()
        self.theme = "ansi-dark"
        self.cache_root = cache_root

    def on_mount(self) -> None:
        self.push_screen(HistoryScreen(self.cache_root), self.exit)


def run_history(cache_root: Path | None = None) -> HistoryAction | None:
    return HistoryApp(cache_root).run()
