from __future__ import annotations

from pathlib import Path
from collections.abc import Callable

import typer
from rich.console import Console
from rich.table import Table

from lecture_util.doctor import run_checks
from lecture_util.errors import LectureUtilError
from lecture_util.models import LecturePaths
from lecture_util.pipeline import audio_stage, download_stage, transcription_stage
from lecture_util.state import RunState, create_workspace


app = typer.Typer(no_args_is_help=True, pretty_exceptions_show_locals=False)
console = Console()


def _run_or_exit(action: Callable[[], None]) -> None:
    try:
        action()
    except LectureUtilError as error:
        console.print(f"[red]Error:[/red] {error}")
        raise typer.Exit(1) from error


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
