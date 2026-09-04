from __future__ import annotations

from pathlib import Path
from typing import cast

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, Input, Label, OptionList, Select

from lecture_util.configuration import (
    AppConfig,
    default_app_config,
    validate_app_config,
)
from lecture_util.errors import LectureUtilError


DEVICE_OPTIONS = (
    ("Auto", "auto"),
    ("Apple MLX", "mlx"),
    ("NVIDIA CUDA", "cuda"),
    ("CPU", "cpu"),
)


class OnboardingApp(App[AppConfig]):
    BINDINGS = [
        Binding("enter", "submit", "Save", priority=True),
        Binding("space", "select_option", "Select option", show=False, priority=True),
        Binding("escape", "cancel", "Cancel"),
    ]
    ENABLE_COMMAND_PALETTE = False

    CSS = """
    Screen {
        background: $background;
        align: center top;
    }

    #form {
        width: 68;
        max-width: 100%;
        height: 1fr;
        padding: 1 2 0 2;
    }

    #title {
        text-style: bold;
        margin-bottom: 1;
    }

    #description {
        margin-bottom: 1;
        color: $text-muted;
    }

    .field-label {
        margin-top: 1;
    }

    Input, Select {
        width: 100%;
    }

    #error {
        display: none;
        width: 68;
        max-width: 100%;
        padding: 0 2;
        color: $error;
    }

    #actions {
        width: 68;
        max-width: 100%;
        height: 3;
        align-horizontal: right;
        padding: 0 2;
    }

    #actions Button {
        margin-left: 1;
    }
    """

    def __init__(self, initial: AppConfig | None = None) -> None:
        super().__init__()
        self.initial = initial or default_app_config()

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="form"):
            yield Label("Set up lecture-util", id="title")
            yield Label(
                "Choose the defaults used when publishing lectures.",
                id="description",
            )
            yield Label("Obsidian Vault path", classes="field-label")
            yield Input(value=str(self.initial.vault_root), id="vault-root")
            yield Label("Semester start date (YYYY-MM-DD)", classes="field-label")
            yield Input(value=self.initial.semester_start, id="semester-start")
            yield Label("Transcription device", classes="field-label")
            yield Select(
                DEVICE_OPTIONS,
                value=self.initial.device,
                allow_blank=False,
                id="device",
            )
            yield Label("Whisper model", classes="field-label")
            yield Input(value=self.initial.whisper_model, id="whisper-model")
            yield Label("Lecture language", classes="field-label")
            yield Input(value=self.initial.language, id="language")
            yield Label(
                "Codex model (blank uses Codex configured default)",
                classes="field-label",
            )
            yield Input(value=self.initial.llm_model or "", id="llm-model")

        yield Label("", id="error")
        with Horizontal(id="actions"):
            yield Button("Cancel", id="cancel")
            yield Button("Save", id="save")

    def on_mount(self) -> None:
        self.query_one("#vault-root", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.action_cancel()
        elif event.button.id == "save":
            self._submit()

    def action_cancel(self) -> None:
        self.exit()

    def action_submit(self) -> None:
        self._submit()

    def action_select_option(self) -> None:
        focused = self.focused
        if isinstance(focused, OptionList):
            focused.action_select()

    def check_action(
        self,
        action: str,
        parameters: tuple[object, ...],
    ) -> bool | None:
        if action == "select_option":
            return isinstance(self.focused, OptionList)
        return super().check_action(action, parameters)

    def _submit(self) -> None:
        error_label = self.query_one("#error", Label)
        try:
            config = self._build_config()
        except LectureUtilError as error:
            error_label.update(str(error))
            error_label.display = True
            return
        error_label.display = False
        self.exit(config)

    def _build_config(self) -> AppConfig:
        vault_value = self.query_one("#vault-root", Input).value.strip()
        if not vault_value:
            raise LectureUtilError("Enter an Obsidian Vault path.")
        device = cast(str, self.query_one("#device", Select).value)
        return validate_app_config(
            AppConfig(
                vault_root=Path(vault_value),
                semester_start=self.query_one("#semester-start", Input).value,
                whisper_model=self.query_one("#whisper-model", Input).value,
                language=self.query_one("#language", Input).value,
                device=device,
                llm_model=self.query_one("#llm-model", Input).value or None,
            )
        )


def run_onboarding(initial: AppConfig | None = None) -> AppConfig | None:
    return OnboardingApp(initial).run()
