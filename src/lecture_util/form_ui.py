"""Shared layout and interaction for the setup forms."""
from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widget import Widget
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Checkbox, Collapsible, Input, Label, OptionList, Select, TextArea

from lecture_util.errors import LectureUtilError

T = TypeVar("T")
R = TypeVar("R")


class FormApp(App[T]):
    BINDINGS = [Binding("space", "select_option", show=False, priority=True)]
    ENABLE_COMMAND_PALETTE = False
    heading = "lecture-util"
    description = ""
    submit_id = "run"
    submit_label = "Run lecture"
    shortcut = "Ctrl+R"
    error_field: str | None = None
    error_value: object = None
    CSS = """
    Screen { background: $background; align-horizontal: center; }
    #header { width: 120; max-width: 100%; height: auto; padding: 1 3; }
    #title { text-style: bold; color: $accent; }
    #description { color: $text-muted; width: 100%; }
    #workspace { width: 120; max-width: 100%; height: 1fr; }
    #columns { layout: horizontal; height: auto; width: 100%; padding: 0 3; }
    #form { width: 2fr; height: auto; padding-right: 3; }
    #preview { width: 1fr; height: auto; background: $surface; padding: 1 2; }
    #preview-title { text-style: bold; color: $accent; margin-bottom: 1; }
    #preview-content { width: 100%; height: auto; }
    .field-label { margin-top: 1; color: $text-muted; }
    Input, Select { width: 100%; }
    Input { border: tall $surface-lighten-2; }
    Input:focus { border: tall $accent; }
    TextArea { height: 7; border: round $surface-lighten-2; }
    Collapsible { margin-top: 1; padding: 0; border: none; }
    .invalid { border: tall $error; }
    #error { display: none; width: 120; max-width: 100%; height: auto;
             max-height: 6; padding: 0 3; color: $error; }
    #actions { width: 120; max-width: 100%; height: 3; padding: 0 3; }
    #key-hint { width: 1fr; color: $text-muted; content-align: left middle; height: 3; }
    #actions Button { margin-left: 1; min-width: 10; }
    .compact #columns { layout: vertical; padding: 0 2; }
    .compact #form { width: 100%; padding-right: 0; }
    .compact #preview { width: 100%; margin-top: 1; }
    .compact #header { padding: 1 2; }
    .compact .field-label { margin-top: 0; }
    .compact #actions { padding: 0 1; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="header"):
            yield Label(self.heading, id="title")
            yield Label(self.description, id="description")
        with VerticalScroll(id="workspace"):
            with Container(id="columns"):
                with Vertical(id="form"):
                    yield from self.compose_fields()
                with Vertical(id="preview"):
                    yield Label("AT A GLANCE", id="preview-title")
                    yield Label("", id="preview-content", markup=False)
        yield Label("", id="error", markup=False)
        with Horizontal(id="actions"):
            yield Label(
                f"Tab Navigate · {self.shortcut} {self.submit_label} · Esc Cancel",
                id="key-hint",
            )
            yield Button("Cancel", id="cancel")
            yield Button(self.submit_label, id=self.submit_id, variant="primary")

    def compose_fields(self) -> ComposeResult:
        raise NotImplementedError

    def on_mount(self) -> None:
        self.screen.set_class(self.size.width < 100, "compact")
        self.refresh_preview()

    def on_resize(self, event: events.Resize) -> None:
        self.screen.set_class(event.size.width < 100, "compact")

    def refresh_preview(self) -> None:
        pass

    def value(self, field: str) -> str:
        return self.query_one(f"#{field}", Input).value.strip()

    def on_input_changed(self, event: Input.Changed) -> None:
        self._changed(event.input)

    def on_select_changed(self, event: Select.Changed) -> None:
        self._changed(event.select)

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        self._changed(event.text_area)

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        self._changed(event.checkbox)

    def _changed(self, widget: Widget) -> None:
        if not self.is_mounted:
            return
        current_value = (
            widget.text if isinstance(widget, TextArea)
            else getattr(widget, "value", None)
        )
        if widget.id == self.error_field and current_value != self.error_value:
            widget.remove_class("invalid")
            self.query_one("#error", Label).display = False
        self.refresh_preview()

    def checked(self, field: str, operation: Callable[[], R]) -> R:
        """Associate domain validation with a field without parsing error strings."""
        self.error_field = field
        return operation()

    def show_error(self, error: LectureUtilError) -> None:
        for widget in self.query(".invalid"):
            widget.remove_class("invalid")
        label = self.query_one("#error", Label)
        label.update(str(error))
        label.display = True
        if self.error_field:
            widget = self.query_one(f"#{self.error_field}")
            self.error_value = (widget.text if isinstance(widget, TextArea)
                                else getattr(widget, "value", None))
            widget.add_class("invalid")
            for parent in widget.ancestors:
                if isinstance(parent, Collapsible):
                    parent.collapsed = False
            self.call_after_refresh(widget.focus)
            self.call_after_refresh(widget.scroll_visible)

    def action_select_option(self) -> None:
        if isinstance(self.focused, OptionList):
            self.focused.action_select()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "select_option":
            return isinstance(self.focused, OptionList)
        return super().check_action(action, parameters)

    def action_cancel(self) -> None:
        for select in self.query(Select):
            if select.expanded:
                select.expanded = False
                select.focus()
                return
        self.exit()
