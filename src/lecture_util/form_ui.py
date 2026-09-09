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

from lecture_util.codex_models import ModelCatalog, discover_models
from lecture_util.errors import LectureUtilError

T = TypeVar("T")
R = TypeVar("R")


class CodexModelPicker(Vertical):
    """Shared asynchronous model selector; an empty value means Codex default."""

    DEFAULT_CSS = "CodexModelPicker { height: auto; }"

    def __init__(self, model: str | None, effort: str | None = None) -> None:
        super().__init__()
        self.initial_model = model or ""
        self.initial_effort = effort or ""
        self.catalog = ModelCatalog((), "")
        self._effort_model = self.initial_model

    def compose(self) -> ComposeResult:
        yield Label("Codex model", classes="field-label")
        options = [("Use Codex configured default", "")]
        if self.initial_model:
            options.append((self.initial_model, self.initial_model))
        yield Select(options, value=self.initial_model, allow_blank=False, id="llm-model")
        yield Label("Loading Codex models…", id="model-status", markup=False)
        yield Label("Thinking effort", classes="field-label")
        options = [("Use Codex configured default", "")]
        if self.initial_effort:
            options.append((self.initial_effort, self.initial_effort))
        yield Select(options, value=self.initial_effort,
                     allow_blank=False, id="reasoning-effort")
        yield Label("Loading supported efforts…", id="effort-status", markup=False)

    def on_mount(self) -> None:
        self.run_worker(self._load_models(), exclusive=True)

    async def _load_models(self) -> None:
        catalog = await discover_models()
        self.catalog = catalog
        select = self.query_one("#llm-model", Select)
        selected = select.value
        options = [("Use Codex configured default", ""), *catalog.models]
        known = {value for _, value in options}
        for model in (self.initial_model, selected):
            if isinstance(model, str) and model and model not in known:
                options.append((f"{model} (saved selection)", model))
                known.add(model)
        select.set_options(options)
        select.value = selected
        self.query_one("#model-status", Label).update(catalog.status)
        self._update_efforts()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "llm-model" and self.is_mounted:
            # set_options queues intermediate values; always read the final selection.
            self._update_efforts()

    def _update_efforts(self) -> None:
        model = self.query_one("#llm-model", Select).value
        select = self.query_one("#reasoning-effort", Select)
        selected = select.value
        levels = self.catalog.efforts.get(model) if isinstance(model, str) else None
        known = levels is not None
        options = [("Use Codex configured default", "")]
        options.extend((level, level) for level in (
            levels if known else ("minimal", "low", "medium", "high", "xhigh")
        ))
        status = ("Supported efforts for the selected model." if known else
                  "Support unverified; choose a listed model to check supported efforts.")
        if selected and selected not in {value for _, value in options}:
            if known and model != self._effort_model:
                selected = ""
                status = "Previous effort is unsupported; using Codex configured default."
            elif isinstance(selected, str):
                options.append((f"{selected} (unverified selection)", selected))
                status = "Saved effort is unverified for this model; choose a supported effort."
        self._effort_model = model
        select.set_options(options)
        select.value = selected
        self.query_one("#effort-status", Label).update(status)


class FormApp(App[T]):
    BINDINGS = [
        Binding("space", "select_option", show=False, priority=True),
        Binding("up", "leave_select('previous')", show=False, priority=True),
        Binding("down", "leave_select('next')", show=False, priority=True),
    ]
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

    def selected_model(self) -> str | None:
        value = self.query_one("#llm-model", Select).value
        return value if isinstance(value, str) and value else None

    def selected_effort(self) -> str | None:
        value = self.query_one("#reasoning-effort", Select).value
        return value if isinstance(value, str) and value else None

    def validated_effort(self) -> str | None:
        effort = self.selected_effort()
        model = self.selected_model()
        levels = self.query_one(CodexModelPicker).catalog.efforts.get(model)
        if effort is not None and levels is not None and effort not in levels:
            self.error_field = "reasoning-effort"
            raise LectureUtilError("Choose a supported thinking effort or Codex default.")
        return effort

    def on_input_changed(self, event: Input.Changed) -> None:
        self._changed(event.input)

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "device" and self.is_mounted:
            for tuning in self.query(TranscriptionTuning):
                tuning.set_device(str(event.select.value))
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
        self.error_field = getattr(error, "field", self.error_field)
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

    def action_leave_select(self, direction: str) -> None:
        if direction == "previous":
            self.screen.focus_previous()
        else:
            self.screen.focus_next()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "select_option":
            return isinstance(self.focused, OptionList)
        if action == "leave_select":
            return isinstance(self.focused, Select) and not self.focused.expanded
        return super().check_action(action, parameters)

    def action_cancel(self) -> None:
        for select in self.query(Select):
            if select.expanded:
                select.expanded = False
                select.focus()
                return
        self.exit()


class TranscriptionTuning(Vertical):
    DEFAULT_CSS = "TranscriptionTuning { height: auto; }"

    def __init__(self, compute_type: str = "auto", batch_size: int = 0,
                 beam_size: int | None = None) -> None:
        super().__init__()
        self.initial = (compute_type, batch_size, beam_size)

    def compose(self) -> ComposeResult:
        from lecture_util.configuration import COMPUTE_TYPES
        compute, batch, beam = self.initial
        yield Label("Compute type", classes="field-label")
        yield Select([(item, item) for item in COMPUTE_TYPES], value=compute,
                     allow_blank=False, id="compute-type")
        yield Label("Batch size (0: disabled)", classes="field-label")
        yield Input(value=str(batch), id="batch-size", type="integer")
        yield Label("Beam size (blank: backend default)", classes="field-label")
        yield Input(value=str(beam) if beam is not None else "", id="beam-size", type="integer")
        yield Label("Tuning applies to faster-whisper; MLX uses its defaults.", markup=False)

    def set_device(self, device: str) -> None:
        for widget in self.query("Select, Input"):
            widget.disabled = device == "mlx"
        if device == "mlx":
            self.query_one("#compute-type", Select).value = "auto"
            self.query_one("#batch-size", Input).value = "0"
            self.query_one("#beam-size", Input).value = ""

    def values(self) -> dict:
        try:
            batch = int(self.query_one("#batch-size", Input).value)
        except ValueError as error:
            self.app.error_field = "batch-size"
            raise LectureUtilError("Batch size must be a non-negative integer.") from error
        try:
            raw = self.query_one("#beam-size", Input).value.strip()
            beam = int(raw) if raw else None
        except ValueError as error:
            self.app.error_field = "beam-size"
            raise LectureUtilError("Beam size must be a positive integer or blank.") from error
        return dict(compute_type=self.query_one("#compute-type", Select).value,
                    batch_size=batch, beam_size=beam)
