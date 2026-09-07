"""Rich prompts for starting a lecture directly from a URL."""
from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from typing import TypeVar

from rich.console import Console
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table
from rich.text import Text

from lecture_util.configuration import AppConfig
from lecture_util.errors import LectureUtilError
from lecture_util.models import RunOptions
from lecture_util.media import resolve_source
from lecture_util.summary import DEFAULT_PROMPT
from lecture_util.vault import (
    discover_courses,
    ensure_paths_available,
    lecture_video_path,
    lecture_week,
    published_lecture_paths,
    validate_lecture_date,
    validate_title,
)

T = TypeVar("T")


def _ask_validated(console: Console, label: str, validate: Callable[[str], T],
                   *, default: str | None = None) -> T:
    while True:
        value = Prompt.ask(label, default=default, console=console)
        try:
            return validate(value)
        except LectureUtilError as error:
            console.print(Text(str(error), style="red"))


def prompt_lecture(url: str, config: AppConfig, console: Console) -> RunOptions | None:
    source = resolve_source(url)
    video_only = False
    if source.kind == "hls":
        video_only = Prompt.ask(
            "Processing mode (full: transcribe and publish, video: download only)",
            choices=["full", "video"], default="full", console=console,
        ) == "video"
    courses = discover_courses(config.vault_root)
    table = Table(title="Choose a course")
    table.add_column("Number", justify="right")
    table.add_column("Course")
    for number, course in enumerate(courses, 1):
        table.add_row(str(number), Text(course.name))
    console.print(table)
    number = IntPrompt.ask(
        "Course", choices=[str(index) for index in range(1, len(courses) + 1)],
        default=1, console=console,
    )
    course = courses[number - 1]
    start = date.fromisoformat(config.semester_start)
    default_week = max(1, (date.today() - start).days // 7 + 1)
    while True:
        week = IntPrompt.ask("Lecture week", default=default_week, console=console)
        if 1 <= week <= (date.max - start).days // 7 + 1:
            break
        console.print("Enter a positive week within the supported date range.", style="red")
    week_start = start + timedelta(weeks=week - 1)

    def date_in_week(value: str) -> str:
        selected = validate_lecture_date(value)
        if lecture_week(selected, config.semester_start) != week:
            raise LectureUtilError(f"Choose a lecture date in week {week}.")
        return selected

    lecture_date = _ask_validated(
        console, "Lecture date (YYYY-MM-DD)", date_in_week,
        default=week_start.isoformat(),
    )

    def available_title(value: str) -> str:
        title = validate_title(value)
        if not video_only:
            ensure_paths_available(published_lecture_paths(
                course, lecture_date, title, semester_start=config.semester_start,
            ))
        return title

    title = _ask_validated(console, "Lecture title", available_title)
    paths = published_lecture_paths(
        course, lecture_date, title, semester_start=config.semester_start,
    )
    video = lecture_video_path(
        config.video_root, course, lecture_date, title,
        semester_start=config.semester_start,
    )
    preview = Table(title="Lecture settings", show_header=False)
    for label, value in (
        ("Source", source.location), ("Course", course.name), ("Week", str(week)),
        ("Date", lecture_date), ("Title", title),
        ("Note", str(paths.summary)), ("Video" if source.kind == "hls" else "Original media",
                                      str(video) if source.kind == "hls" else source.location),
        ("Transcription", f"{config.whisper_model} · {config.device} · {config.language}"),
        ("Codex model", config.llm_model or "Codex default"),
        ("Thinking effort", config.reasoning_effort or "Codex default"),
    ):
        if not video_only or label not in {"Note", "Transcription", "Codex model", "Thinking effort"}:
            preview.add_row(label, Text(value))
    console.print(preview)
    if not Confirm.ask("Start processing?", default=True, console=console):
        return None
    return RunOptions(
        video_only=video_only, source=source, url=source.location, course=course.name, lecture_date=lecture_date, title=title,
        semester_start=config.semester_start, llm_model=config.llm_model,
        reasoning_effort=config.reasoning_effort, tags=None,
        whisper_model=config.whisper_model, language=config.language,
        compute_type=config.compute_type, batch_size=config.batch_size, beam_size=config.beam_size,
        device=config.device, prompt=DEFAULT_PROMPT, force=False,
    )
