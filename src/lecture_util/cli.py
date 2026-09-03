from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import sys
from time import monotonic

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from lecture_util.doctor import run_checks
from lecture_util.errors import LectureUtilError
from lecture_util.media import validate_hls_url
from lecture_util.models import LecturePaths, RunOptions
from lecture_util.pipeline import audio_stage, download_stage, run_lecture, transcription_stage
from lecture_util.progress import ProgressEvent, format_duration
from lecture_util.state import RunState, create_workspace
from lecture_util.summarizers import CodexSummarizer
from lecture_util.summary import DEFAULT_PROMPT, summary_stage
from lecture_util.transcription import load_transcript


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


def _resolve_prompt(prompt: str | None, prompt_file: Path | None) -> str:
    if prompt is not None and prompt_file is not None:
        raise LectureUtilError("--prompt and --prompt-file cannot be used together.")
    if prompt_file is not None:
        try:
            return prompt_file.read_text(encoding="utf-8")
        except OSError as error:
            raise LectureUtilError(f"Could not read prompt file {prompt_file}: {error}") from error
    return prompt if prompt is not None else DEFAULT_PROMPT


def normalize_tags(values: list[str] | None) -> list[str] | None:
    if values is None:
        return None
    normalized: list[str] = []
    for value in values:
        for candidate in value.split(","):
            tag = candidate.strip()
            if tag and tag not in normalized:
                normalized.append(tag)
    return normalized


def _read_urls(url: str | None, input_file: Path | None) -> list[str]:
    if (url is None) == (input_file is None):
        raise LectureUtilError("Provide exactly one URL or --input URL_LIST_FILE.")
    if url is not None:
        validate_hls_url(url)
        return [url]
    try:
        lines = input_file.read_text(encoding="utf-8").splitlines()  # type: ignore[union-attr]
    except OSError as error:
        raise LectureUtilError(f"Could not read URL list {input_file}: {error}") from error
    urls = [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]
    if not urls:
        raise LectureUtilError("The URL list does not contain any lecture URLs.")
    for lecture_url in urls:
        validate_hls_url(lecture_url)
    return urls


def _execute_run(options: RunOptions) -> None:
    summarizer = CodexSummarizer(model=options.llm_model)
    failures: list[tuple[str, str]] = []
    for index, lecture_url in enumerate(options.urls, 1):
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
                chunk_chars=options.chunk_chars,
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


def _choice(prompt: str, choices: tuple[str, ...], default: str) -> str:
    rendered = "/".join(choices)
    while True:
        answer = typer.prompt(f"{prompt} [{rendered}]", default=default).strip().lower()
        if answer in choices:
            return answer
        console.print(f"[yellow]Choose one of: {rendered}[/yellow]")


def _optional_prompt(label: str) -> str | None:
    value = typer.prompt(label, default="", show_default=False).strip()
    return value or None


def _interactive_terminal() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _interactive_options() -> RunOptions:
    console.print("[bold]lecture-util interactive setup[/bold]")
    source_mode = _choice("Lecture source", ("single", "list"), "single")
    if source_mode == "single":
        source_url = typer.prompt("Public .m3u8 URL").strip()
        urls = _read_urls(source_url, None)
    else:
        input_path = Path(typer.prompt("URL list file").strip()).expanduser()
        urls = _read_urls(None, input_path)

    title = _optional_prompt("Optional title")
    raw_tags = _optional_prompt("Tags (comma-separated, optional)")
    tags = normalize_tags([raw_tags] if raw_tags is not None else None)
    output_dir = Path(typer.prompt("Output directory", default="output")).expanduser()
    force = typer.confirm("Force every stage to run again?", default=False)

    device = _choice("Transcription device", ("auto", "mlx", "cuda", "cpu"), "auto")
    whisper_model = typer.prompt("Whisper model", default="large-v3").strip()
    language = typer.prompt("Lecture language", default="auto").strip()

    llm_model = _optional_prompt("Codex model (blank uses its configured default)")

    prompt_mode = _choice("Summary prompt", ("default", "inline", "file"), "default")
    if prompt_mode == "inline":
        selected_prompt = typer.prompt("Summary instructions").strip()
    elif prompt_mode == "file":
        prompt_path = Path(typer.prompt("Prompt file").strip()).expanduser()
        selected_prompt = _resolve_prompt(None, prompt_path)
    else:
        selected_prompt = DEFAULT_PROMPT

    if typer.confirm("Configure advanced options?", default=False):
        chunk_chars = typer.prompt("Summary chunk size in characters", default=12_000, type=int)
        if chunk_chars < 1000:
            raise LectureUtilError("Summary chunk size must be at least 1000 characters.")
    else:
        chunk_chars = 12_000

    options = RunOptions(
        urls=urls,
        llm_model=llm_model,
        output_dir=output_dir,
        title=title,
        tags=tags,
        whisper_model=whisper_model,
        language=language,
        device=device,
        prompt=selected_prompt,
        chunk_chars=chunk_chars,
        force=force,
    )
    table = Table("Option", "Value", title="Execution plan")
    table.add_row("Lectures", str(len(options.urls)))
    table.add_row("Output", str(options.output_dir))
    table.add_row("Tags", ", ".join(options.tags or []) or "(none)")
    table.add_row("Transcription", f"{options.whisper_model} on {options.device}; {options.language}")
    table.add_row("Summarizer", f"Codex / {options.llm_model or '(configured default)'}")
    table.add_row("Chunk size", str(options.chunk_chars))
    table.add_row("Force", "yes" if options.force else "no")
    console.print(table)
    return options


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
        options = _interactive_options()
        if not typer.confirm("Start processing?", default=True):
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
    chunk_chars: int = typer.Option(12_000, "--chunk-chars", min=1000),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Download, transcribe, and summarize one lecture or a URL list."""

    def action() -> None:
        urls = _read_urls(url, input_file)
        selected_prompt = _resolve_prompt(prompt, prompt_file)
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
                chunk_chars=chunk_chars,
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
    chunk_chars: int = typer.Option(12_000, "--chunk-chars", min=1000),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Summarize an existing transcript."""

    def action() -> None:
        paths = LecturePaths(lecture_dir)
        state = RunState(paths)
        summarizer = CodexSummarizer(model=llm_model)
        summary_stage(
            load_transcript(paths.transcript_json),
            paths.summary,
            paths.summary_work,
            state,
            summarizer,
            prompt=_resolve_prompt(prompt, prompt_file),
            chunk_chars=chunk_chars,
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
