from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select

from lecture_util.configuration import (
    AppConfig,
    default_app_config,
    validate_app_config,
    video_root_is_in_vault,
)
from lecture_util.errors import LectureUtilError
from lecture_util.form_ui import CodexModelPicker, FormApp
from lecture_util.vault import discover_courses, validate_semester_start


DEVICE_OPTIONS = (
    ("Auto", "auto"),
    ("Apple MLX", "mlx"),
    ("NVIDIA CUDA", "cuda"),
    ("CPU", "cpu"),
)


class ConfirmVaultVideoScreen(ModalScreen[bool]):
    BINDINGS = [
        Binding("ctrl+s", "confirm", "Store videos in Vault", priority=True),
        Binding("escape", "cancel", "Cancel", priority=True),
    ]

    CSS = """
    ConfirmVaultVideoScreen {
        align: center middle;
    }

    #confirm-dialog {
        width: 64;
        max-width: 100%;
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
                markup=False,
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


class OnboardingApp(FormApp[AppConfig]):
    heading = "Set up lecture-util"
    description = "Choose where lectures live and how they are processed."
    submit_id = "save"
    submit_label = "Save settings"
    shortcut = "Ctrl+S"
    BINDINGS = [
        Binding("ctrl+s", "submit", "Save", priority=True),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, initial: AppConfig | None = None) -> None:
        super().__init__()
        self.initial = initial or default_app_config()

    def compose_fields(self) -> ComposeResult:
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
        yield CodexModelPicker(self.initial.llm_model)

    def on_mount(self) -> None:
        super().on_mount()
        self.query_one("#vault-root", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.action_cancel()
        elif event.button.id == "save":
            self._submit()

    def action_submit(self) -> None:
        self._submit()

    def refresh_preview(self) -> None:
        device = self.query_one("#device", Select).value
        self.query_one("#preview-content", Label).update(
            f"STORAGE\nVault\n{self.value('vault-root') or 'Choose a Vault'}"
            f"\n\nVideos\n{self.value('video-root') or 'Choose a folder'}"
            f"\n\nSEMESTER START\n{self.value('semester-start') or 'Choose a date'}"
            f"\n\nTRANSCRIPTION\n{device} · {self.value('whisper-model') or 'Choose a model'}"
            f"\nLanguage: {self.value('language') or 'Choose a language'}"
            f"\n\nSUMMARY\n{self.selected_model() or 'Codex default'}"
        )

    def _submit(self) -> None:
        error_label = self.query_one("#error", Label)
        try:
            config = self._build_config()
        except LectureUtilError as error:
            self.show_error(error)
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
        self.error_field = "vault-root"
        vault_value = self.query_one("#vault-root", Input).value.strip()
        if not vault_value:
            raise LectureUtilError("Enter an Obsidian Vault path.")
        self.checked("vault-root", lambda: discover_courses(Path(vault_value).expanduser()))
        self.error_field = "video-root"
        video_value = self.query_one("#video-root", Input).value.strip()
        if not video_value:
            raise LectureUtilError("Enter a video storage path.")
        if Path(video_value).expanduser().exists() and not Path(video_value).expanduser().is_dir():
            raise LectureUtilError("Video storage path is not a directory.")
        self.checked("semester-start", lambda: validate_semester_start(self.value("semester-start")))
        for field, label in (("whisper-model", "Whisper model"), ("language", "lecture language")):
            self.error_field = field
            if not self.value(field):
                raise LectureUtilError(f"Configured {label} must not be empty.")
        self.error_field = "device"
        device = cast(str, self.query_one("#device", Select).value)
        return validate_app_config(
            AppConfig(
                vault_root=Path(vault_value),
                video_root=Path(video_value),
                semester_start=self.query_one("#semester-start", Input).value,
                whisper_model=self.query_one("#whisper-model", Input).value,
                language=self.query_one("#language", Input).value,
                device=device,
                llm_model=self.selected_model(),
            ),
            allow_unconfirmed_video_root=True,
        )


def run_onboarding(initial: AppConfig | None = None) -> AppConfig | None:
    return OnboardingApp(initial).run()
