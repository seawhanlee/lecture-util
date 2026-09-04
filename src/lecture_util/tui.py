from __future__ import annotations

from pathlib import Path
from typing import cast

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import (
    Button,
    Checkbox,
    Collapsible,
    Input,
    Label,
    Select,
    TextArea,
)

from lecture_util.configuration import (
    normalize_tags,
    read_urls,
    resolve_prompt,
)
from lecture_util.errors import LectureUtilError
from lecture_util.models import RunOptions
from lecture_util.vault import (
    DEFAULT_VAULT_ROOT,
    discover_courses,
    ensure_paths_available,
    published_lecture_paths,
    resolve_course,
    validate_lecture_date,
    validate_title,
)


class LectureSetupApp(App[RunOptions]):
    BINDINGS = [
        Binding("enter", "submit", "Run", priority=True),
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

    .field-label {
        margin-top: 1;
    }

    Input, Select {
        width: 100%;
    }

    TextArea {
        height: 5;
        border: round $surface-lighten-2;
    }

    Collapsible {
        margin-top: 1;
        padding: 0;
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

    def __init__(self, vault_root: Path = DEFAULT_VAULT_ROOT) -> None:
        super().__init__()
        self.vault_root = vault_root
        self.courses = discover_courses(vault_root)

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="form"):
            yield Label("lecture-util", id="title")
            yield Label("Course", classes="field-label")
            yield Select(
                tuple((course.name, course.name) for course in self.courses),
                value=self.courses[0].name,
                allow_blank=False,
                id="course",
            )
            yield Label("Lecture date (YYYY-MM-DD)", classes="field-label")
            yield Input(placeholder="2026-09-04", id="lecture-date")
            yield Label("Lecture title", classes="field-label")
            yield Input(placeholder="압축성 유동", id="lecture-title")
            yield Label("Public .m3u8 URL", classes="field-label")
            yield Input(placeholder="https://example.com/lecture/index.m3u8", id="source")

            with Collapsible(title="Advanced settings", collapsed=True):
                yield Label("Tags (comma-separated, optional)", classes="field-label")
                yield Input(placeholder="operating-systems, midterm", id="tags")
                yield Checkbox("Force every stage to run again", id="force")
                yield Label("Transcription device", classes="field-label")
                yield Select(
                    (
                        ("Auto", "auto"),
                        ("Apple MLX", "mlx"),
                        ("NVIDIA CUDA", "cuda"),
                        ("CPU", "cpu"),
                    ),
                    value="auto",
                    allow_blank=False,
                    id="device",
                )
                yield Label("Whisper model", classes="field-label")
                yield Input(value="large-v3", id="whisper-model")
                yield Label("Lecture language", classes="field-label")
                yield Input(value="auto", id="language")
                yield Label("Codex model (blank uses configured default)", classes="field-label")
                yield Input(id="llm-model")
                yield Label("Summary prompt", classes="field-label")
                yield Select(
                    (
                        ("Default", "default"),
                        ("Enter instructions", "inline"),
                        ("Prompt file", "file"),
                    ),
                    value="default",
                    allow_blank=False,
                    id="prompt-mode",
                )
                yield TextArea(placeholder="Summary instructions", id="inline-prompt")
                yield Input(placeholder="Path to prompt file", id="prompt-file")

        yield Label("", id="error")
        with Horizontal(id="actions"):
            yield Button("Cancel", id="cancel")
            yield Button("Run", id="run")

    def on_mount(self) -> None:
        self._update_prompt_mode()
        self.query_one("#lecture-date", Input).focus()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "prompt-mode":
            self._update_prompt_mode()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.action_cancel()
        elif event.button.id == "run":
            self._submit()

    def action_cancel(self) -> None:
        self.exit()

    def action_submit(self) -> None:
        self._submit()

    def _select_value(self, selector: str) -> str:
        return cast(str, self.query_one(selector, Select).value)

    def _update_prompt_mode(self) -> None:
        mode = self._select_value("#prompt-mode")
        self.query_one("#inline-prompt", TextArea).display = mode == "inline"
        self.query_one("#prompt-file", Input).display = mode == "file"

    def _submit(self) -> None:
        error_label = self.query_one("#error", Label)
        try:
            options = self._build_options()
        except LectureUtilError as error:
            error_label.update(str(error))
            error_label.display = True
            return
        error_label.display = False
        self.exit(options)

    def _build_options(self) -> RunOptions:
        source_value = self.query_one("#source", Input).value.strip()
        if not source_value:
            raise LectureUtilError("Enter a lecture URL.")
        url = read_urls(source_value, None)[0]

        lecture_date = validate_lecture_date(
            self.query_one("#lecture-date", Input).value
        )
        title = validate_title(self.query_one("#lecture-title", Input).value)
        course_name = self._select_value("#course")
        course = resolve_course(course_name, self.vault_root)
        ensure_paths_available(published_lecture_paths(course, lecture_date, title))

        prompt_mode = self._select_value("#prompt-mode")
        if prompt_mode == "inline":
            prompt = resolve_prompt(self.query_one("#inline-prompt", TextArea).text.strip(), None)
        elif prompt_mode == "file":
            prompt_path = self.query_one("#prompt-file", Input).value.strip()
            if not prompt_path:
                raise LectureUtilError("Enter a summary prompt file.")
            prompt = resolve_prompt(None, Path(prompt_path).expanduser())
        else:
            prompt = resolve_prompt(None, None)

        whisper_model = self.query_one("#whisper-model", Input).value.strip()
        if not whisper_model:
            raise LectureUtilError("Enter a Whisper model.")
        language = self.query_one("#language", Input).value.strip()
        if not language:
            raise LectureUtilError("Enter a lecture language.")

        raw_tags = self.query_one("#tags", Input).value.strip()
        llm_model = self.query_one("#llm-model", Input).value.strip() or None
        return RunOptions(
            url=url,
            course=course_name,
            lecture_date=lecture_date,
            title=title,
            llm_model=llm_model,
            tags=normalize_tags([raw_tags] if raw_tags else None),
            whisper_model=whisper_model,
            language=language,
            device=self._select_value("#device"),
            prompt=prompt,
            force=self.query_one("#force", Checkbox).value,
        )


def run_tui() -> RunOptions | None:
    return LectureSetupApp().run()
