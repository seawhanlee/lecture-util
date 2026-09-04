from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import sys
from time import monotonic

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from lecture_util.configuration import normalize_tags, read_urls, resolve_prompt
from lecture_util.doctor import run_checks
from lecture_util.errors import LectureUtilError
from lecture_util.media import validate_hls_url
from lecture_util.models import LecturePaths, RunOptions
from lecture_util.pipeline import audio_stage, download_stage, run_lecture, transcription_stage
from lecture_util.progress import ProgressEvent, format_duration
from lecture_util.state import RunState, create_workspace
from lecture_util.summarizers import CodexSummarizer
from lecture_util.summary import summary_stage
from lecture_util.transcription import load_transcript
from lecture_util.tui import run_tui


app = typer.Typer(
    invoke_without_command=True,
    no_args_is_help=False,
    pretty_exceptions_show_locals=False,
)
console = Console()


class ConsoleProgressReporter:
    def __init__(self, stages: tuple[str, ...]) -> None:
        self.positions = {stage: index for index, stage in enumerate(stages, 1)}
        self.total = len(stages)

    def __call__(self, event: ProgressEvent) -> None:
        marker, style = {
            "start": ("→", "cyan"),
            "update": ("·", "blue"),
            "complete": ("✓", "green"),
            "cached": ("↻", "dim"),
            "warning": ("!", "yellow"),
            "failed": ("✗", "red"),
        }[event.status]
        position = self.positions.get(event.stage)
        prefix = f"[{position}/{self.total}]" if position is not None else ""
        line = Text()
        line.append(f"{marker} ", style=style)
        if prefix:
            line.append(f"{prefix} ", style="bold")
        line.append(event.message, style=style if event.status in {"failed", "warning"} else None)
        console.print(line)


def _run_or_exit(action: Callable[[], None]) -> None:
    try:
        action()
    except LectureUtilError as error:
        console.print(f"[red]Error:[/red] {error}")
        raise typer.Exit(1) from error
    except Exception as error:
        console.print(f"[red]Error:[/red] {error}")
        raise typer.Exit(1) from error


def _execute_run(options: RunOptions) -> None:
    failures: list[tuple[str, str]] = []
    for index, lecture_url in enumerate(options.urls, 1):
        summarizer = CodexSummarizer(model=options.llm_model)
        console.rule(f"Lecture {index}/{len(options.urls)}")
        console.print(lecture_url)
        started = monotonic()
        progress = ConsoleProgressReporter(("download", "audio", "transcription", "summary"))
        try:
            paths = run_lecture(
                lecture_url,
                options.output_dir,
                summarizer,
                title=options.title,
                tags=options.tags,
                model=options.whisper_model,
                language=options.language,
                device=options.device,
                prompt=options.prompt,
                force=options.force,
                progress=progress,
            )
            console.print(
                f"[bold green]Complete[/bold green] {paths.root} "
                f"({format_duration(monotonic() - started)})"
            )
        except Exception as error:
            failures.append((lecture_url, str(error)))
            console.print(f"[red]Failed[/red] {lecture_url}: {error}")
    if failures:
        raise LectureUtilError(f"{len(failures)} of {len(options.urls)} lectures failed.")


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
        options = run_tui()
        if options is None:
            console.print("Cancelled before processing.")
            return
        _execute_run(options)
    except LectureUtilError as error:
        console.print(f"[red]Error:[/red] {error}")
        raise typer.Exit(1) from error


@app.command("run")
def run_command(
    url: str | None = typer.Argument(None, help="Public .m3u8 URL"),
    input_file: Path | None = typer.Option(None, "--input", exists=True, dir_okay=False),
    llm_model: str | None = typer.Option(None, "--llm-model"),
    output_dir: Path = typer.Option(Path("output"), "--output-dir", "-o"),
    title: str | None = typer.Option(None, "--title"),
    tag: list[str] | None = typer.Option(None, "--tag"),
    whisper_model: str = typer.Option("large-v3", "--whisper-model"),
    language: str = typer.Option("auto", "--language"),
    device: str = typer.Option("auto", "--device"),
    prompt: str | None = typer.Option(None, "--prompt"),
    prompt_file: Path | None = typer.Option(None, "--prompt-file", exists=True, dir_okay=False),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Download, transcribe, and summarize one lecture or a URL list."""

    def action() -> None:
        urls = read_urls(url, input_file)
        selected_prompt = resolve_prompt(prompt, prompt_file)
        _execute_run(
            RunOptions(
                urls=urls,
                llm_model=llm_model,
                output_dir=output_dir,
                title=title,
                tags=normalize_tags(tag),
                whisper_model=whisper_model,
                language=language,
                device=device,
                prompt=selected_prompt,
                force=force,
            )
        )

    _run_or_exit(action)


@app.command("download")
def download_command(
    url: str = typer.Argument(..., help="Public .m3u8 URL"),
    output_dir: Path = typer.Option(Path("output"), "--output-dir", "-o"),
    title: str | None = typer.Option(None, "--title"),
    tag: list[str] | None = typer.Option(None, "--tag"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Download a lecture and extract transcription-ready audio."""

    def action() -> None:
        validate_hls_url(url)
        paths, state = create_workspace(url, output_dir, title=title, tags=normalize_tags(tag))
        progress = ConsoleProgressReporter(("download", "audio"))
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
        transcript = transcription_stage(
            paths,
            state,
            model=model,
            language=language,
            device=device,
            force=force,
            progress=ConsoleProgressReporter(("transcription",)),
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
        summary_stage(
            load_transcript(paths.transcript_json),
            paths.transcript_markdown,
            paths.summary,
            state,
            summarizer,
            prompt=resolve_prompt(prompt, prompt_file),
            force=force,
            progress=ConsoleProgressReporter(("summary",)),
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
