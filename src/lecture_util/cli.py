from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path
import sys
from time import monotonic

import typer
from rich.console import Console
from rich.table import Table

from lecture_util.configuration import (
    default_app_config,
    default_config_path,
    load_config,
    load_onboarding_config,
    normalize_tags,
    read_urls,
    resolve_prompt,
    save_config,
)
from lecture_util.console_progress import ConsoleProgressReporter
from lecture_util.doctor import run_checks
from lecture_util.errors import LectureUtilError
from lecture_util.media import validate_hls_url
from lecture_util.models import LecturePaths, RunOptions
from lecture_util.onboarding import run_onboarding
from lecture_util.pipeline import audio_stage, download_stage, run_lecture, transcription_stage
from lecture_util.progress import format_duration
from lecture_util.state import RunState, create_workspace
from lecture_util.summarizers import CodexSummarizer
from lecture_util.summary import summary_stage
from lecture_util.transcription import load_transcript
from lecture_util.tui import run_tui
from lecture_util.vault import (
    DEFAULT_VAULT_ROOT,
    default_cache_root,
    default_semester_start,
    ensure_paths_available,
    lecture_video_path,
    publish_lecture_notes,
    published_lecture_paths,
    resolve_course,
    validate_lecture_date,
    validate_semester_start,
    validate_title,
)


app = typer.Typer(
    invoke_without_command=True,
    no_args_is_help=False,
    pretty_exceptions_show_locals=False,
)
console = Console()


def _run_or_exit(action: Callable[[], None]) -> None:
    try:
        action()
    except LectureUtilError as error:
        console.print(f"[red]Error:[/red] {error}")
        raise typer.Exit(1) from error
    except Exception as error:
        console.print(f"[red]Error:[/red] {error}")
        raise typer.Exit(1) from error


def _execute_run(
    options: RunOptions,
    *,
    vault_root: Path = DEFAULT_VAULT_ROOT,
    video_root: Path | None = None,
    cache_root: Path | None = None,
) -> None:
    course = resolve_course(options.course, vault_root)
    lecture_date = validate_lecture_date(options.lecture_date)
    semester_start = validate_semester_start(
        options.semester_start
        or default_semester_start(date.fromisoformat(lecture_date))
    )
    title = validate_title(options.title)
    published = published_lecture_paths(
        course,
        lecture_date,
        title,
        semester_start=semester_start,
    )
    ensure_paths_available(published)
    video = lecture_video_path(
        video_root or default_cache_root(),
        course,
        lecture_date,
        title,
        semester_start=semester_start,
    )

    summarizer = CodexSummarizer(model=options.llm_model)
    console.rule(f"{course.name} · {lecture_date} {title}")
    console.print(options.url)
    started = monotonic()
    with ConsoleProgressReporter(
        ("download", "audio", "transcription", "summary"), console=console,
    ) as progress:
        paths = run_lecture(
            options.url,
            cache_root or default_cache_root(),
            summarizer,
            video_path=video,
            title=title,
            course=course.name,
            lecture_date=lecture_date,
            published_summary=published.summary,
            published_transcript=published.transcript,
            tags=options.tags,
            model=options.whisper_model,
            language=options.language,
            device=options.device,
            prompt=options.prompt,
            force=options.force,
            progress=progress,
        )
        publish_lecture_notes(
            published,
            course=course,
            lecture_date=lecture_date,
            title=title,
            url=options.url,
            summary=paths.summary.read_text(encoding="utf-8"),
            transcript=paths.transcript_markdown.read_text(encoding="utf-8"),
        )
    console.print(
        f"[bold green]Complete[/bold green] {published.summary} "
        f"({format_duration(monotonic() - started)})"
    )


def _interactive_terminal() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


@app.callback()
def root_callback(ctx: typer.Context) -> None:
    """Download, transcribe, and summarize LMS lectures."""
    if ctx.invoked_subcommand is not None:
        return
    if not _interactive_terminal():
        console.print("[red]Error:[/red] no command supplied and stdin/stdout are not interactive.")
        console.print("Run 'lecture-util --help' for usage.")
        raise typer.Exit(2)
    try:
        config = load_config()
        if config is None:
            config = run_onboarding(default_app_config())
            if config is None:
                console.print("Cancelled before setup.")
                return
            config = save_config(config)
            console.print(f"[green]Configuration saved[/green] {default_config_path()}")
        options = run_tui(config)
        if options is None:
            console.print("Cancelled before processing.")
            return
        _execute_run(
            options,
            vault_root=config.vault_root,
            video_root=config.video_root,
        )
    except LectureUtilError as error:
        console.print(f"[red]Error:[/red] {error}")
        raise typer.Exit(1) from error


@app.command("run")
def run_command(
    url: str = typer.Argument(..., help="Public .m3u8 URL"),
    course: str = typer.Option(..., "--course", help="Course directory name"),
    lecture_date: str = typer.Option(..., "--date", help="Lecture date (YYYY-MM-DD)"),
    semester_start: str | None = typer.Option(
        None,
        "--semester-start",
        help="Override the configured semester start date (YYYY-MM-DD)",
    ),
    title: str = typer.Option(..., "--title", help="Lecture title"),
    llm_model: str | None = typer.Option(
        None,
        "--llm-model",
        help="Override the configured Codex model; pass an empty value for its default",
    ),
    tag: list[str] | None = typer.Option(None, "--tag"),
    whisper_model: str | None = typer.Option(
        None,
        "--whisper-model",
        help="Override the configured Whisper model",
    ),
    language: str | None = typer.Option(
        None,
        "--language",
        help="Override the configured lecture language",
    ),
    device: str | None = typer.Option(
        None,
        "--device",
        help="Override the configured transcription device",
    ),
    prompt: str | None = typer.Option(None, "--prompt"),
    prompt_file: Path | None = typer.Option(None, "--prompt-file", exists=True, dir_okay=False),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Download, transcribe, and publish one lecture to the Obsidian vault."""

    def action() -> None:
        config = load_config(required=True)
        assert config is not None
        validated_url = read_urls(url, None)[0]
        selected_prompt = resolve_prompt(prompt, prompt_file)
        selected_llm_model = (
            config.llm_model
            if llm_model is None
            else llm_model.strip() or None
        )
        _execute_run(
            RunOptions(
                url=validated_url,
                course=course,
                lecture_date=lecture_date,
                semester_start=semester_start or config.semester_start,
                title=title,
                llm_model=selected_llm_model,
                tags=normalize_tags(tag),
                whisper_model=whisper_model or config.whisper_model,
                language=language or config.language,
                device=device or config.device,
                prompt=selected_prompt,
                force=force,
            ),
            vault_root=config.vault_root,
            video_root=config.video_root,
        )

    _run_or_exit(action)


@app.command("onboard")
def onboard_command() -> None:
    """Configure the Obsidian Vault and lecture processing defaults."""
    if not _interactive_terminal():
        console.print(
            "[red]Error:[/red] onboarding requires an interactive terminal."
        )
        raise typer.Exit(2)

    def action() -> None:
        try:
            initial = load_onboarding_config()
        except LectureUtilError as error:
            console.print(f"[yellow]Warning:[/yellow] {error}")
            initial = None
        selected = run_onboarding(initial or default_app_config())
        if selected is None:
            console.print("Onboarding cancelled; configuration was not changed.")
            return
        save_config(selected)
        console.print(f"[green]Configuration saved[/green] {default_config_path()}")

    _run_or_exit(action)


@app.command("download")
def download_command(
    url: str = typer.Argument(..., help="Public .m3u8 URL"),
    course: str = typer.Option(..., "--course", help="Course directory name"),
    title: str = typer.Option(..., "--title", help="Lecture title"),
    lecture_date: str | None = typer.Option(
        None,
        "--date",
        help="Lecture date (YYYY-MM-DD; default: today)",
    ),
    output_dir: Path = typer.Option(
        default_cache_root(),
        "--output-dir",
        "-o",
        help="Cache root for audio, transcripts, and run state",
    ),
    tag: list[str] | None = typer.Option(None, "--tag"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Download a lecture and extract transcription-ready audio."""

    def action() -> None:
        config = load_config(required=True)
        assert config is not None
        validate_hls_url(url)
        selected_course = resolve_course(course, config.vault_root)
        selected_date = validate_lecture_date(
            lecture_date or date.today().isoformat()
        )
        selected_title = validate_title(title)
        video = lecture_video_path(
            config.video_root,
            selected_course,
            selected_date,
            selected_title,
            semester_start=config.semester_start,
        )
        paths, state = create_workspace(
            url,
            output_dir,
            video_path=video,
            title=selected_title,
            course=selected_course.name,
            lecture_date=selected_date,
            tags=normalize_tags(tag),
        )
        with ConsoleProgressReporter(("download", "audio"), console=console) as progress:
            download_stage(paths, state, force=force, progress=progress)
            audio_stage(paths, state, force=force, progress=progress)
        console.print(f"[green]Prepared[/green] {paths.root}")

    _run_or_exit(action)


@app.command("transcribe")
def transcribe_command(
    lecture_dir: Path = typer.Argument(..., exists=True, file_okay=False),
    model: str = typer.Option("large-v3", "--whisper-model"),
    language: str = typer.Option("auto", "--language"),
    device: str = typer.Option("auto", "--device"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Transcribe an already downloaded lecture."""

    def action() -> None:
        paths = LecturePaths(lecture_dir)
        state = RunState(paths)
        with ConsoleProgressReporter(("transcription",), console=console) as progress:
            transcript = transcription_stage(
                paths,
                state,
                model=model,
                language=language,
                device=device,
                force=force,
                progress=progress,
            )
        console.print(
            f"[green]Transcribed[/green] {len(transcript.segments)} segments with "
            f"{transcript.engine}/{transcript.effective_model}"
        )

    _run_or_exit(action)


@app.command("summarize")
def summarize_command(
    lecture_dir: Path = typer.Argument(..., exists=True, file_okay=False),
    llm_model: str | None = typer.Option(None, "--llm-model"),
    prompt: str | None = typer.Option(None, "--prompt"),
    prompt_file: Path | None = typer.Option(None, "--prompt-file", exists=True, dir_okay=False),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Summarize an existing transcript."""

    def action() -> None:
        paths = LecturePaths(lecture_dir)
        state = RunState(paths)
        summarizer = CodexSummarizer(model=llm_model)
        with ConsoleProgressReporter(("summary",), console=console) as progress:
            summary_stage(
                load_transcript(paths.transcript_json),
                paths.transcript_markdown,
                paths.summary,
                state,
                summarizer,
                prompt=resolve_prompt(prompt, prompt_file),
                force=force,
                progress=progress,
            )
        console.print(f"[green]Summarized[/green] {paths.summary}")

    _run_or_exit(action)


@app.command("doctor")
def doctor_command() -> None:
    """Check system tools, platform, and transcription acceleration."""
    table = Table("Check", "Status", "Detail")
    checks = run_checks()
    for check in checks:
        table.add_row(check.name, "[green]OK[/green]" if check.ok else "[red]FAIL[/red]", check.detail)
    console.print(table)
    if any(not check.ok for check in checks):
        raise typer.Exit(1)
