from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from typing import cast

from textual.app import ComposeResult
from textual.binding import Binding
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
    AppConfig,
    default_app_config,
    normalize_tags,
    resolve_prompt,
)
from lecture_util.errors import LectureUtilError
from lecture_util.pipeline import preflight_run
from lecture_util.form_ui import CodexModelPicker, FormApp, TranscriptionTuning
from lecture_util.models import RunOptions, TranscriptionOptions
from lecture_util.configuration import validate_transcription_options
from lecture_util.state import lecture_id
from lecture_util.vault import default_cache_root
from lecture_util.media import resolve_source
from lecture_util.vault import (
    DEFAULT_VAULT_ROOT,
    discover_courses,
    lecture_week,
    lecture_video_path,
    ensure_paths_available,
    published_lecture_paths,
    resolve_course,
    validate_lecture_date,
    validate_semester_start,
    validate_title,
)


def _week_monday(today: date | None = None) -> str:
    current_date = today or date.today()
    return (current_date - timedelta(days=current_date.weekday())).isoformat()


class LectureSetupApp(FormApp[RunOptions]):
    heading = "New lecture"
    description = "Download, transcribe, and turn a lecture into study notes."
    BINDINGS = [
        Binding("ctrl+r", "submit", "Run", priority=True),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(
        self,
        vault_root: Path = DEFAULT_VAULT_ROOT,
        *,
        config: AppConfig | None = None,
        initial: RunOptions | None = None,
    ) -> None:
        super().__init__()
        self.initial = initial
        self.checking = False
        defaults = default_app_config()
        self.config = config or AppConfig(
            vault_root=vault_root,
            video_root=defaults.video_root,
            semester_start=defaults.semester_start,
            whisper_model=defaults.whisper_model,
            language=defaults.language,
            device=defaults.device,
            llm_model=defaults.llm_model,
            reasoning_effort=defaults.reasoning_effort,
        )
        self.vault_root = self.config.vault_root
        self.courses = discover_courses(self.vault_root)

    def compose_fields(self) -> ComposeResult:
        lecture_date = _week_monday()
        yield Label("Course", classes="field-label")
        yield Select(
            tuple((course.name, course.name) for course in self.courses),
            value=self.courses[0].name,
            allow_blank=False,
            type_to_search=False,
            id="course",
        )
        yield Label("Lecture date (YYYY-MM-DD)", classes="field-label")
        yield Input(value=lecture_date, id="lecture-date")
        yield Label("Default: Monday of the current week; edit for the actual lecture date.", markup=False)
        yield Label("Lecture title", classes="field-label")
        yield Input(placeholder="압축성 유동", id="lecture-title")
        yield Label("HLS URL or local media path", classes="field-label")
        yield Input(placeholder="https://…/index.m3u8 or /path/to/lecture.m4a", id="source")

        yield Select(
            [("Transcribe, summarize and publish", "full"), ("Download video only", "video")],
            value="full", allow_blank=False, id="processing-mode", disabled=True,
        )

        with Collapsible(title="Lecture options", collapsed=True):
            yield Label("Semester start date (YYYY-MM-DD)", classes="field-label")
            yield Input(
                value=self.config.semester_start,
                id="semester-start",
            )
            yield Label("Tags (comma-separated, optional)", classes="field-label")
            yield Input(placeholder="operating-systems, midterm", id="tags")
            yield Checkbox("Force every stage to run again", id="force")
        with Collapsible(title="Transcription", collapsed=True):
            yield Label("Transcription device", classes="field-label")
            yield Select(
                (
                    ("Auto", "auto"),
                    ("Apple MLX", "mlx"),
                    ("NVIDIA CUDA", "cuda"),
                    ("CPU", "cpu"),
                ),
                value=self.config.device,
                allow_blank=False,
                id="device",
            )
            yield Label("Whisper model", classes="field-label")
            yield Input(value=self.config.whisper_model, id="whisper-model")
            yield Label("Lecture language", classes="field-label")
            yield Input(value=self.config.language, id="language")
            yield TranscriptionTuning(self.config.compute_type, self.config.batch_size, self.config.beam_size)
        with Collapsible(title="Summary", collapsed=True):
            yield CodexModelPicker(
                self.initial.llm_model if self.initial else self.config.llm_model,
                self.initial.reasoning_effort if self.initial else self.config.reasoning_effort,
            )
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

    def on_mount(self) -> None:
        super().on_mount()
        if self.initial is not None:
            self._restore_input(self.initial)
        self._update_prompt_mode()
        self.query_one("#lecture-date", Input).focus()

    def on_select_changed(self, event: Select.Changed) -> None:
        super().on_select_changed(event)
        if event.select.id == "prompt-mode":
            self._update_prompt_mode()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.action_cancel()
        elif event.button.id == "run":
            self._submit()

    def action_submit(self) -> None:
        self._submit()

    def _select_value(self, selector: str) -> str:
        return cast(str, self.query_one(selector, Select).value)

    def _update_prompt_mode(self) -> None:
        mode = self._select_value("#prompt-mode")
        self.query_one("#inline-prompt", TextArea).display = mode == "inline"
        self.query_one("#prompt-file", Input).display = mode == "file"

    def refresh_preview(self) -> None:
        mode_select = self.query_one("#processing-mode", Select)
        is_url = self.value("source").lower().startswith(("http://", "https://"))
        mode_select.disabled = not is_url
        if not is_url and mode_select.value != "full":
            mode_select.value = "full"
        video_only = mode_select.value == "video"
        course = self._select_value("#course")
        title = self.value("lecture-title") or "Untitled lecture"
        lecture_date = self.value("lecture-date") or "Choose a date"
        destination = "Complete the date and title to preview the note path."
        try:
            paths = published_lecture_paths(
                next(item for item in self.courses if item.name == course),
                validate_lecture_date(self.value("lecture-date")),
                validate_title(self.value("lecture-title")),
                semester_start=validate_semester_start(self.value("semester-start")),
            )
            destination = str(paths.summary)
        except (LectureUtilError, StopIteration):
            pass
        if video_only:
            try:
                destination = str(lecture_video_path(
                    self.config.video_root,
                    next(item for item in self.courses if item.name == course),
                    self.value("lecture-date"), title,
                    semester_start=self.value("semester-start"),
                ))
            except (LectureUtilError, StopIteration):
                destination = "Complete the date and title to preview the video path."
            self.query_one("#preview-content", Label).update(
                f"{course}\n{lecture_date}\n{title}\n\nVIDEO DESTINATION\n{destination}"
            )
            return
        mode = self._select_value("#prompt-mode")
        prompt = {
            "default": "Default instructions",
            "inline": "Custom instructions",
            "file": "Prompt file",
        }[mode]
        rerun = (
            "Re-run every stage" if self.query_one("#force", Checkbox).value
            else "Reuse cached stages"
        )
        whisper_model = self.value("whisper-model") or "Choose a model"
        self.query_one("#preview-content", Label).update(
            f"{course}\n{lecture_date}\n{title}\n\n"
            f"NOTE DESTINATION\n{destination}\n\n"
            f"TRANSCRIPTION\n{self._select_value('#device')} · {whisper_model}"
            f"\nLanguage: {self.value('language') or 'Choose a language'}\n\n"
            f"SUMMARY\n{self.selected_model() or 'Codex default'}"
            f"\nThinking effort: {self.selected_effort() or 'Codex default'}"
            f"\n{prompt}\n\n{rerun}"
        )

    def _submit(self) -> None:
        if self.checking:
            return
        error_label = self.query_one("#error", Label)
        try:
            options = self._build_options()
        except LectureUtilError as error:
            self.show_error(error)
            return
        error_label.display = False
        self.checking = True
        self.query_one("#run", Button).disabled = True
        self.run_worker(self._preflight(options), exclusive=True)

    async def _preflight(self, options: RunOptions) -> None:
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="lecture-preflight")
        try:
            await asyncio.get_running_loop().run_in_executor(
                executor, preflight_run, options, self.vault_root, self.config.video_root,
            )
        except Exception as error:
            self.error_field = "device"
            self.show_error(error if isinstance(error, LectureUtilError) else LectureUtilError(str(error)))
        else:
            self.call_later(self.exit, options)
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
            self.checking = False
            self.query_one("#run", Button).disabled = False

    def _restore_input(self, options: RunOptions) -> None:
        for field, value in {
            "lecture-date": options.lecture_date, "semester-start": options.semester_start,
            "lecture-title": options.title, "source": options.url,
            "tags": ", ".join(options.tags or []), "whisper-model": options.whisper_model,
            "language": options.language, "batch-size": str(options.batch_size),
            "beam-size": str(options.beam_size) if options.beam_size is not None else "",
        }.items():
            self.query_one(f"#{field}", Input).value = value or ""
        self.query_one("#course", Select).value = options.course
        self.query_one("#device", Select).value = options.device
        self.query_one("#compute-type", Select).value = options.compute_type
        self.query_one("#force", Checkbox).value = options.force
        self.query_one("#prompt-mode", Select).value = "inline"
        self.query_one("#inline-prompt", TextArea).text = options.prompt

    def _build_options(self) -> RunOptions:
        self.error_field = "source"
        source_value = self.query_one("#source", Input).value.strip()
        if not source_value:
            raise LectureUtilError("Enter a lecture URL or local media path.")
        source = resolve_source(source_value)

        self.error_field = "lecture-date"
        lecture_date = validate_lecture_date(
            self.query_one("#lecture-date", Input).value
        )
        self.error_field = "semester-start"
        semester_start = validate_semester_start(
            self.query_one("#semester-start", Input).value
        )
        self.error_field = "lecture-title"
        title = validate_title(self.query_one("#lecture-title", Input).value)
        self.error_field = "course"
        course_name = self._select_value("#course")
        course = resolve_course(course_name, self.vault_root)
        self.checked("lecture-date", lambda: lecture_week(lecture_date, semester_start))
        if self._select_value("#processing-mode") == "video":
            if source.kind != "hls":
                self.error_field = "source"
                raise LectureUtilError("Video-only mode requires a public .m3u8 URL.")
            return RunOptions(
                url=source.location, source=source, video_only=True,
                course=course_name, lecture_date=lecture_date, title=title,
                semester_start=semester_start, llm_model=None, tags=None,
                whisper_model="", language="", device="auto", prompt="",
                force=self.query_one("#force", Checkbox).value,
            )
        self.error_field = "lecture-title"
        ensure_paths_available(
            published_lecture_paths(
                course,
                lecture_date,
                title,
                semester_start=semester_start,
            ),
            journal=default_cache_root() / f"lecture-{lecture_id(source.cache_key)}" / "publication.json",
        )

        self.error_field = "prompt-mode"
        prompt_mode = self._select_value("#prompt-mode")
        if prompt_mode == "inline":
            self.error_field = "inline-prompt"
            prompt = resolve_prompt(self.query_one("#inline-prompt", TextArea).text.strip(), None)
        elif prompt_mode == "file":
            self.error_field = "prompt-file"
            prompt_path = self.query_one("#prompt-file", Input).value.strip()
            if not prompt_path:
                raise LectureUtilError("Enter a summary prompt file.")
            prompt = resolve_prompt(None, Path(prompt_path).expanduser())
        else:
            prompt = resolve_prompt(None, None)

        self.error_field = "whisper-model"
        whisper_model = self.query_one("#whisper-model", Input).value.strip()
        if not whisper_model:
            raise LectureUtilError("Enter a Whisper model.")
        self.error_field = "language"
        language = self.query_one("#language", Input).value.strip()
        if not language:
            raise LectureUtilError("Enter a lecture language.")

        tuning = self.query_one(TranscriptionTuning).values()
        self.error_field = "language"
        validate_transcription_options(TranscriptionOptions(
            whisper_model, language, self._select_value("#device"), **tuning,
        ))
        raw_tags = self.query_one("#tags", Input).value.strip()
        llm_model = self.selected_model()
        return RunOptions(
            url=source.location,
            source=source,
            course=course_name,
            lecture_date=lecture_date,
            semester_start=semester_start,
            title=title,
            llm_model=llm_model,
            reasoning_effort=self.validated_effort(),
            tags=normalize_tags([raw_tags] if raw_tags else None),
            **tuning,
            whisper_model=whisper_model,
            language=language,
            device=self._select_value("#device"),
            prompt=prompt,
            force=self.query_one("#force", Checkbox).value,
        )


def run_tui(config: AppConfig | None = None, *, initial: RunOptions | None = None) -> RunOptions | None:
    return LectureSetupApp(config=config, initial=initial).run()
