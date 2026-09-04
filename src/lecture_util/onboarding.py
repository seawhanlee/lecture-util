from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import cast

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList, Select

from lecture_util.configuration import (
    AppConfig,
    default_app_config,
    validate_app_config,
    video_root_is_in_vault,
)
from lecture_util.errors import LectureUtilError


DEVICE_OPTIONS = (
    ("Auto", "auto"),
    ("Apple MLX", "mlx"),
    ("NVIDIA CUDA", "cuda"),
    ("CPU", "cpu"),
)


class ConfirmVaultVideoScreen(ModalScreen[bool]):
    BINDINGS = [
        Binding("enter", "confirm", "Store videos in Vault", priority=True),
        Binding("escape", "cancel", "Cancel", priority=True),
    ]

    CSS = """
    ConfirmVaultVideoScreen {
        align: center middle;
    }

    #confirm-dialog {
        width: 64;
        height: auto;
        padding: 1 2;
        border: round $warning;
        background: $surface;
    }

    #confirm-question {
        margin-bottom: 1;
    }

    #confirm-actions {
        height: 3;
        align-horizontal: right;
    }

    #confirm-actions Button {
        margin-left: 1;
    }
    """

    def __init__(self, video_root: Path) -> None:
        super().__init__()
        self.video_root = video_root

    def compose(self) -> ComposeResult:
        with Container(id="confirm-dialog"):
            yield Label("Store videos inside the Obsidian Vault?", id="confirm-title")
            yield Label(
                f"Video files under {self.video_root} may be indexed or synced "
                "by Obsidian. Save this location anyway?",
                id="confirm-question",
            )
            with Horizontal(id="confirm-actions"):
                yield Button("Go back", id="cancel-video-root")
                yield Button("Save anyway", id="confirm-video-root")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm-video-root")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


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
            yield Label("Video storage path", classes="field-label")
            yield Input(value=str(self.initial.video_root), id="video-root")
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
        if video_root_is_in_vault(config):
            self.push_screen(
                ConfirmVaultVideoScreen(config.video_root),
                lambda confirmed: self._finish_submit(config, confirmed),
            )
            return
        self.exit(config)

    def _finish_submit(self, config: AppConfig, confirmed: bool | None) -> None:
        if confirmed:
            self.exit(replace(config, video_in_vault_allowed=True))

    def _build_config(self) -> AppConfig:
        vault_value = self.query_one("#vault-root", Input).value.strip()
        if not vault_value:
            raise LectureUtilError("Enter an Obsidian Vault path.")
        video_value = self.query_one("#video-root", Input).value.strip()
        if not video_value:
            raise LectureUtilError("Enter a video storage path.")
        device = cast(str, self.query_one("#device", Select).value)
        return validate_app_config(
            AppConfig(
                vault_root=Path(vault_value),
                video_root=Path(video_value),
                semester_start=self.query_one("#semester-start", Input).value,
                whisper_model=self.query_one("#whisper-model", Input).value,
                language=self.query_one("#language", Input).value,
                device=device,
                llm_model=self.query_one("#llm-model", Input).value or None,
            ),
            allow_unconfirmed_video_root=True,
        )


def run_onboarding(initial: AppConfig | None = None) -> AppConfig | None:
    return OnboardingApp(initial).run()
