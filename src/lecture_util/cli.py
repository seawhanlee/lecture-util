from __future__ import annotations

from pathlib import Path
from collections.abc import Callable

import typer
from rich.console import Console
from rich.table import Table

from lecture_util.doctor import run_checks
from lecture_util.errors import LectureUtilError
from lecture_util.models import LecturePaths
from lecture_util.pipeline import audio_stage, download_stage, run_lecture, transcription_stage
from lecture_util.state import RunState, create_workspace
from lecture_util.summarizers import create_summarizer
from lecture_util.summary import DEFAULT_PROMPT, summary_stage
from lecture_util.transcription import load_transcript


app = typer.Typer(no_args_is_help=True, pretty_exceptions_show_locals=False)
console = Console()


def _run_or_exit(action: Callable[[], None]) -> None:
    try:
        action()
    except LectureUtilError as error:
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


def _read_urls(url: str | None, input_file: Path | None) -> list[str]:
    if (url is None) == (input_file is None):
        raise LectureUtilError("Provide exactly one URL or --input URL_LIST_FILE.")
    if url is not None:
        return [url]
    try:
        lines = input_file.read_text(encoding="utf-8").splitlines()  # type: ignore[union-attr]
    except OSError as error:
        raise LectureUtilError(f"Could not read URL list {input_file}: {error}") from error
    urls = [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]
    if not urls:
        raise LectureUtilError("The URL list does not contain any lecture URLs.")
    return urls


@app.command("run")
def run_command(
    url: str | None = typer.Argument(None, help="Public .m3u8 URL"),
    input_file: Path | None = typer.Option(None, "--input", exists=True, dir_okay=False),
    summarizer_name: str = typer.Option(..., "--summarizer"),
    llm_model: str | None = typer.Option(None, "--llm-model"),
    base_url: str | None = typer.Option(None, "--base-url"),
    api_key_env: str = typer.Option("OPENAI_API_KEY", "--api-key-env"),
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
        summarizer = create_summarizer(
            summarizer_name,
            model=llm_model,
            base_url=base_url,
            api_key_env=api_key_env,
        )
        failures: list[tuple[str, str]] = []
        for lecture_url in urls:
            try:
                paths = run_lecture(
                    lecture_url,
                    output_dir,
                    summarizer,
                    title=title,
                    tags=tag,
                    model=whisper_model,
                    language=language,
                    device=device,
                    prompt=selected_prompt,
                    chunk_chars=chunk_chars,
                    force=force,
                )
                console.print(f"[green]Complete[/green] {paths.root}")
            except Exception as error:
                failures.append((lecture_url, str(error)))
                console.print(f"[red]Failed[/red] {lecture_url}: {error}")
        if failures:
            raise LectureUtilError(f"{len(failures)} of {len(urls)} lectures failed.")

    _run_or_exit(action)


@app.command("download")
def download_command(
    url: str = typer.Argument(..., help="Public .m3u8 URL"),
    output_dir: Path = typer.Option(Path("output"), "--output-dir", "-o"),
    title: str | None = typer.Option(None, "--title"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Download a lecture and extract transcription-ready audio."""

    def action() -> None:
        paths, state = create_workspace(url, output_dir, title=title)
        download_stage(paths, state, force=force)
        audio_stage(paths, state, force=force)
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
        )
        console.print(
            f"[green]Transcribed[/green] {len(transcript.segments)} segments with "
            f"{transcript.engine}/{transcript.effective_model}"
        )

    _run_or_exit(action)


@app.command("summarize")
def summarize_command(
    lecture_dir: Path = typer.Argument(..., exists=True, file_okay=False),
    summarizer_name: str = typer.Option(..., "--summarizer"),
    llm_model: str | None = typer.Option(None, "--llm-model"),
    base_url: str | None = typer.Option(None, "--base-url"),
    api_key_env: str = typer.Option("OPENAI_API_KEY", "--api-key-env"),
    prompt: str | None = typer.Option(None, "--prompt"),
    prompt_file: Path | None = typer.Option(None, "--prompt-file", exists=True, dir_okay=False),
    chunk_chars: int = typer.Option(12_000, "--chunk-chars", min=1000),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Summarize an existing transcript."""

    def action() -> None:
        paths = LecturePaths(lecture_dir)
        state = RunState(paths)
        summarizer = create_summarizer(
            summarizer_name,
            model=llm_model,
            base_url=base_url,
            api_key_env=api_key_env,
        )
        summary_stage(
            load_transcript(paths.transcript_json),
            paths.summary,
            paths.summary_work,
            state,
            summarizer,
            prompt=_resolve_prompt(prompt, prompt_file),
            chunk_chars=chunk_chars,
            force=force,
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
