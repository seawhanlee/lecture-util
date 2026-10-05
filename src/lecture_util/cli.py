from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import date
import sys
from pathlib import Path

import click
import typer
from typer.core import TyperGroup
from rich.console import Console
from rich.table import Table
from rich.text import Text
from rich.prompt import Confirm, Prompt

from lecture_util.configuration import (
    validate_provider_overrides,
    default_app_config,
    default_config_path,
    load_config,
    load_onboarding_config,
    normalize_tags,
    read_urls,
    resolve_prompt,
    resolve_beam_size,
    save_config,
)
from lecture_util.console_progress import ConsoleProgressReporter
from lecture_util.doctor import run_checks
from lecture_util.errors import LectureUtilError
from lecture_util.interactive import prompt_lecture
from lecture_util.media import resolve_source, validate_hls_url
from lecture_util.models import LecturePaths, RunOptions
from lecture_util.onboarding import run_onboarding
from lecture_util.pipeline import audio_stage, download_stage, transcription_stage
from lecture_util.recovery import load_request
from lecture_util.state import (
    RunState,
    clear_workspace,
    create_workspace,
    lecture_id,
    workspace_lock,
)
from lecture_util.summarizers import CodexSummarizer
from lecture_util.summary import summary_stage
from lecture_util.transcription import load_transcript, restore_transcript_files
from lecture_util.tui import run_tui
from lecture_util.vault import (
    DEFAULT_VAULT_ROOT,
    default_cache_root,
    lecture_video_path,
    resolve_course,
    validate_lecture_date,
    validate_title,
)


class LectureCommandGroup(TyperGroup):
    def resolve_command(
        self, ctx: click.Context, args: list[str],
    ) -> tuple[str | None, click.Command | None, list[str]]:
        if args and (
            args[0].lower().startswith(("https://", "http://"))
            or (not args[0].startswith("-")
                and self.get_command(ctx, args[0]) is None
                and (Path(args[0]).expanduser().is_file()
                     or "/" in args[0] or bool(Path(args[0]).suffix)))
        ):
            return "interactive", self.get_command(ctx, "interactive"), args
        return super().resolve_command(ctx, args)


app = typer.Typer(
    cls=LectureCommandGroup,
    invoke_without_command=True,
    no_args_is_help=False,
    pretty_exceptions_show_locals=False,
)
console = Console()
materials_app = typer.Typer(help="Register and inspect course PDF directories.")
app.add_typer(materials_app, name="materials")


@materials_app.command("add")
def materials_add(
    directory: Path = typer.Argument(..., file_okay=False),
    course: str = typer.Option(..., "--course"),
) -> None:
    """Register a PDF directory for a course without moving originals."""
    def action() -> None:
        from lecture_util.materials import register_directory
        config = load_config(required=True)
        assert config is not None
        register_directory(config.vault_root, course, directory)
        console.print(Text(f"Registered materials: {directory.expanduser().resolve()}"), soft_wrap=True)
    _run_or_exit(action)


@materials_app.command("list")
def materials_list(course: str = typer.Option(..., "--course")) -> None:
    """List directories registered for a course."""
    def action() -> None:
        from lecture_util.materials import registered_directories
        config = load_config(required=True)
        assert config is not None
        resolve_course(course, config.vault_root)
        directories = registered_directories(config.vault_root, course)
        console.print(Text("\n".join(directories) or "No material directories registered."), soft_wrap=True)
    _run_or_exit(action)


@materials_app.command("remove")
def materials_remove(
    directory: Path = typer.Argument(..., file_okay=False),
    course: str = typer.Option(..., "--course"),
) -> None:
    """Unregister a directory; keep its PDFs and analysis caches."""
    def action() -> None:
        from lecture_util.materials import register_directory
        config = load_config(required=True)
        assert config is not None
        register_directory(config.vault_root, course, directory, remove=True)
        console.print(Text(f"Unregistered materials: {directory.expanduser().resolve()}"), soft_wrap=True)
    _run_or_exit(action)


def _run_or_exit(action: Callable[[], None]) -> None:
    try:
        action()
    except KeyboardInterrupt:
        console.print("Interrupted.")
        raise typer.Exit(130)
    except LectureUtilError as error:
        console.print(f"[red]Error:[/red] {error}")
        raise typer.Exit(1) from error
    except Exception as error:
        console.print(f"[red]Error:[/red] {error}")
        raise typer.Exit(1) from error


def _execute_download(
    url: str, *, course: str, title: str, lecture_date: str,
    semester_start: str, vault_root: Path, video_root: Path,
    output_dir: Path, tags: list[str] | None = None, force: bool = False,
    video_only: bool = True,
) -> None:
    validate_hls_url(url)
    selected_course = resolve_course(course, vault_root)
    selected_date = validate_lecture_date(lecture_date)
    selected_title = validate_title(title)
    video = lecture_video_path(
        video_root, selected_course, selected_date, selected_title,
        semester_start=semester_start,
    )
    with workspace_lock(output_dir / f"lecture-{lecture_id(url)}"):
        paths, state = create_workspace(
            url, output_dir, video_path=video, title=selected_title,
            course=selected_course.name, lecture_date=selected_date, tags=tags,
        )
        stages = ("download",) if video_only else ("download", "audio")
        with ConsoleProgressReporter(stages, console=console) as progress:
            download_stage(paths, state, force=force, progress=progress)
            if not video_only:
                audio_stage(paths, state, force=force, progress=progress)
    console.print(Text(f"Video: {video}"), highlight=False, soft_wrap=True)
    if not video_only:
        console.print(Text(f"Prepared: {paths.root}"), highlight=False, soft_wrap=True)


def _execute_run(
    options: RunOptions, *, vault_root: Path = DEFAULT_VAULT_ROOT,
    video_root: Path | None = None, cache_root: Path | None = None,
) -> None:
    if options.video_only:
        from lecture_util.materials import resolve_run_materials
        resolve_run_materials(options, vault_root)
        if not options.course:
            raise LectureUtilError("An explicit --course is required.")
        _execute_download(
            options.url, course=options.course, title=options.title,
            lecture_date=options.lecture_date,
            semester_start=options.semester_start or default_semester_start(
                date.fromisoformat(validate_lecture_date(options.lecture_date))
            ),
            vault_root=vault_root, video_root=video_root or default_cache_root(),
            output_dir=cache_root or default_cache_root(), tags=options.tags,
            force=options.force,
        )
        return
    from lecture_util.pipeline import execute_run
    execute_run(options, vault_root=vault_root, video_root=video_root,
                cache_root=cache_root, console=console)


class ActionPrompt(Prompt):
    ALIASES = {
        "c": "clear-cache",
        "clean": "clear-cache",
        "clear": "clear-cache",
        "r": "retry",
        "e": "edit",
        "q": "quit",
    }

    def process_response(self, value: str) -> str:
        value = value.strip().lower()
        value = self.ALIASES.get(value, value)
        return super().process_response(value)


def _interactive_terminal() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


@app.callback()
def root_callback(ctx: typer.Context) -> None:
    """Download, transcribe, and summarize LMS lectures.

    Pass a public .m3u8 URL or local media path directly to choose a course and week interactively.
    """
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
        while options is not None:
            try:
                _execute_run(options, vault_root=config.vault_root, video_root=config.video_root)
                break
            except KeyboardInterrupt:
                console.print("Interrupted; use the resume command above.")
                raise typer.Exit(130)
            except Exception as error:
                console.print(Text(str(error), style="red"))
                choice = ActionPrompt.ask(
                    "Next action",
                    choices=["retry", "clear-cache", "edit", "quit"],
                    default="quit",
                    console=console,
                )
                if choice == "quit":
                    raise typer.Exit(1)
                if choice == "clear-cache":
                    source = options.source or resolve_source(options.url)
                    cache_dir = default_cache_root().resolve() / f"lecture-{lecture_id(source.cache_key)}"
                    clear_workspace(cache_dir)
                    console.print(f"[yellow]Cleared cache:[/yellow] {cache_dir}")
                    options = replace(options, force=True)
                else:
                    options = replace(options, force=False)
                    if choice == "edit":
                        options = run_tui(config, initial=options)
    except LectureUtilError as error:
        console.print(f"[red]Error:[/red] {error}")
        raise typer.Exit(1) from error


@app.command("interactive", hidden=True)
def interactive_command(url: str = typer.Argument(..., metavar="SOURCE", help="Public .m3u8 URL or local media path")) -> None:
    """Choose lecture metadata for a URL or local media file."""
    try:
        source = resolve_source(url)
    except LectureUtilError as error:
        console.print(Text(str(error), style="red"))
        raise typer.Exit(2) from error
    if not _interactive_terminal():
        console.print("A source alone requires an interactive terminal. Use 'lecture-util run --help'.")
        raise typer.Exit(2)

    def action() -> None:
        try:
            config = load_config()
            if config is None:
                config = run_onboarding(default_app_config())
                if config is None:
                    console.print("Cancelled before setup.")
                    return
                config = save_config(config)
            options = prompt_lecture(source.location, config, console)
        except (KeyboardInterrupt, EOFError):
            console.print("Cancelled before processing.")
            return
        if options is None:
            console.print("Cancelled before processing.")
            return
        _execute_run(options, vault_root=config.vault_root, video_root=config.video_root)

    _run_or_exit(action)


@app.command("run")
def run_command(
    url: str = typer.Argument(..., metavar="SOURCE", help="Public .m3u8 URL or local media path"),
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
    reasoning_effort: str | None = typer.Option(
        None, "--reasoning-effort",
        help="Override thinking effort; pass an empty value for the Codex default",
    ),
    whisper_model: str | None = typer.Option(
        None,
        "--whisper-model",
        help="Override the configured Whisper model",
    ),
    transcription_provider: str | None = typer.Option(None, "--transcription-provider", help="local or openai"),
    openai_transcription_model: str | None = typer.Option(None, "--openai-transcription-model", help="OpenAI transcription model"),
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
    prompt_file: Path | None = typer.Option(None, "--prompt-file", dir_okay=False),
    compute_type: str | None = typer.Option(None, "--compute-type", help="auto/float16/float32/int8/int8_float16"),
    batch_size: int | None = typer.Option(None, "--batch-size", min=0, help="0 disables batching"),
    beam_size: str | None = typer.Option(None, "--beam-size", help="Positive integer or default to reset the saved value"),
    force: bool = typer.Option(False, "--force"),
    video_only: bool = typer.Option(False, "--video-only", help="Download video without audio extraction or notes"),
    materials_dir: list[Path] | None = typer.Option(None, "--materials-dir", help="Additional PDF directory (repeatable)"),
    no_materials: bool = typer.Option(False, "--no-materials", help="Use the transcript only"),
) -> None:
    """Process a URL or local media file and publish one lecture to the Obsidian vault."""

    def action() -> None:
        config = (load_config(required=True, video_only=True)
                  if video_only else load_config(required=True))
        assert config is not None
        if no_materials and materials_dir:
            raise LectureUtilError("--no-materials cannot be combined with --materials-dir.")
        if video_only and (materials_dir or no_materials):
            raise LectureUtilError("Video-only mode cannot use material options.")
        provider = transcription_provider if transcription_provider is not None else config.transcription_provider
        if not video_only:
            validate_provider_overrides(provider, whisper_model, device, compute_type, batch_size, beam_size,
                                        openai_transcription_model=openai_transcription_model)
        if video_only:
            validate_hls_url(url)
        source = resolve_source(url)
        validated_url = source.location
        selected_course = course.strip() if (course and course.strip()) else None
        if not selected_course:
            raise LectureUtilError("An explicit --course is required.")
        selected_prompt = "" if video_only else resolve_prompt(prompt, prompt_file)
        selected_llm_model = (
            config.llm_model
            if llm_model is None
            else llm_model.strip() or None
        )
        _execute_run(
            RunOptions(
                url=validated_url,
                source=source,
                video_only=video_only,
                materials_dirs=[str(path.expanduser().resolve()) for path in materials_dir or []],
                no_materials=no_materials,
                course=selected_course,
                lecture_date=lecture_date,
                semester_start=semester_start or config.semester_start,
                title=title,
                llm_model=selected_llm_model,
                reasoning_effort=(config.reasoning_effort if reasoning_effort is None
                                  else reasoning_effort.strip() or None),
                tags=normalize_tags(tag),
                transcription_provider=provider,
                openai_transcription_model=(config.openai_transcription_model if openai_transcription_model is None else openai_transcription_model),
                whisper_model=config.whisper_model if whisper_model is None else whisper_model,
                compute_type=config.compute_type if compute_type is None else compute_type,
                batch_size=config.batch_size if batch_size is None else batch_size,
                beam_size=resolve_beam_size(beam_size, config.beam_size),
                language=config.language if language is None else language,
                device=config.device if device is None else device,
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
    _edit_configuration("Onboarding")


@app.command("config")
def config_command() -> None:
    """Edit saved settings interactively, including the Codex model."""
    _edit_configuration("Configuration")


def _edit_configuration(label: str) -> None:
    if not _interactive_terminal():
        console.print(
            f"[red]Error:[/red] {label.lower()} requires an interactive terminal."
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
            console.print(f"{label} cancelled; configuration was not changed.")
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
    video_only: bool = typer.Option(False, "--video-only", help="Download video without audio extraction or notes"),
) -> None:
    """Download a lecture and extract transcription-ready audio."""

    def action() -> None:
        config = (load_config(required=True, video_only=True)
                  if video_only else load_config(required=True))
        assert config is not None
        validate_hls_url(url)
        _execute_download(
            url, course=course, title=title,
            lecture_date=lecture_date or date.today().isoformat(),
            semester_start=config.semester_start, vault_root=config.vault_root,
            video_root=config.video_root, output_dir=output_dir,
            tags=normalize_tags(tag), force=force, video_only=video_only,
        )

    _run_or_exit(action)


@app.command("transcribe")
def transcribe_command(
    lecture_dir: Path = typer.Argument(..., exists=True, file_okay=False),
    model: str | None = typer.Option(None, "--whisper-model"),
    transcription_provider: str | None = typer.Option(None, "--transcription-provider", help="local or openai"),
    openai_transcription_model: str | None = typer.Option(None, "--openai-transcription-model", help="OpenAI transcription model"),
    language: str | None = typer.Option(None, "--language"),
    device: str | None = typer.Option(None, "--device"),
    compute_type: str | None = typer.Option(None, "--compute-type", help="auto/float16/float32/int8/int8_float16"),
    batch_size: int | None = typer.Option(None, "--batch-size", min=0, help="0 disables batching"),
    beam_size: str | None = typer.Option(None, "--beam-size", help="Positive integer or default to reset the saved value"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Transcribe an already downloaded lecture."""

    def action() -> None:
        config = load_config(validate_vault=False) or default_app_config()
        provider = transcription_provider if transcription_provider is not None else config.transcription_provider
        validate_provider_overrides(provider, model, device, compute_type, batch_size, beam_size,
                                    openai_transcription_model=openai_transcription_model)
        paths = LecturePaths(lecture_dir)
        with workspace_lock(lecture_dir):
            state = RunState(paths)
            with ConsoleProgressReporter(("transcription",), console=console) as progress:
                transcript = transcription_stage(
                    paths,
                    state,
                    transcription_provider=provider,
                    openai_transcription_model=(config.openai_transcription_model if openai_transcription_model is None else openai_transcription_model),
                    model=model if model is not None else config.whisper_model,
                    language=language if language is not None else config.language,
                    device=device if device is not None else config.device,
                    compute_type=config.compute_type if compute_type is None else compute_type,
                    batch_size=config.batch_size if batch_size is None else batch_size,
                    beam_size=resolve_beam_size(beam_size, config.beam_size),
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
    reasoning_effort: str | None = typer.Option(None, "--reasoning-effort"),
    prompt: str | None = typer.Option(None, "--prompt"),
    prompt_file: Path | None = typer.Option(None, "--prompt-file", exists=True, dir_okay=False),
    force: bool = typer.Option(False, "--force"),
    course: str | None = typer.Option(None, "--course", help="Use this course's registered PDF directories"),
    materials_dir: list[Path] | None = typer.Option(None, "--materials-dir", help="Additional PDF directory (repeatable)"),
    no_materials: bool = typer.Option(False, "--no-materials", help="Use the transcript only"),
) -> None:
    """Summarize an existing transcript."""

    def action() -> None:
        config = load_config(validate_vault=False) or default_app_config()
        from lecture_util.materials import discover_materials, registered_directories
        if no_materials and materials_dir:
            raise LectureUtilError("--no-materials cannot be combined with --materials-dir.")
        directories = [str(path.expanduser().resolve()) for path in materials_dir or []]
        if course and not no_materials:
            resolve_course(course, config.vault_root)
            directories = registered_directories(config.vault_root, course) + directories
        files = [] if no_materials else discover_materials(directories)
        paths = LecturePaths(lecture_dir)
        with workspace_lock(lecture_dir):
            state = RunState(paths)
            summarizer = CodexSummarizer(
                model=config.llm_model if llm_model is None else llm_model.strip() or None,
                reasoning_effort=(config.reasoning_effort if reasoning_effort is None
                                  else reasoning_effort.strip() or None),
            )
            transcript = load_transcript(paths.transcript_json)
            restore_transcript_files(transcript, paths.transcript_markdown, paths.transcript_srt)
            stages = ("materials", "summary") if files else ("summary",)
            with ConsoleProgressReporter(stages, console=console) as progress:
                summary_stage(
                    transcript,
                    paths.transcript_markdown,
                    paths.summary,
                    state,
                    summarizer,
                    prompt=resolve_prompt(prompt, prompt_file),
                    force=force,
                    progress=progress,
                    materials_files=files,
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


@app.command("resume")
def resume_command(lecture_dir: Path = typer.Argument(..., exists=True, file_okay=False)) -> None:
    """Resume a recorded run using its original settings and prompt."""
    def action() -> None:
        options, vault, videos = load_request(lecture_dir.resolve())
        _execute_run(options, vault_root=vault, video_root=videos,
                     cache_root=lecture_dir.resolve().parent)
    _run_or_exit(action)


cache_app = typer.Typer(
    name="cache",
    help="Inspect and prune cached media and workspaces.",
    invoke_without_command=True,
    no_args_is_help=False,
)


def _execute_cache_list(cache_root: Path | None = None) -> None:
    def action() -> None:
        from lecture_util.cache import get_storage_summary, scan_cached_lectures
        from lecture_util.progress import format_size

        config = load_config(validate_vault=False)
        video_root = config.video_root if config else None
        root = (cache_root or default_cache_root()).expanduser().resolve()
        lectures = scan_cached_lectures(root)
        summary = get_storage_summary(root, video_root)

        if summary.materials_cache_size:
            console.print(f"Shared PDF analysis cache: {format_size(summary.materials_cache_size)}")
        if not lectures:
            console.print(f"[dim]No cached lectures found in {root}[/dim]")
            if summary.video_root and summary.video_root.is_dir():
                console.print(
                    f"Video storage: {summary.video_count} videos "
                    f"({format_size(summary.total_video_size)}) in {summary.video_root}"
                )
            return

        table = Table(title=f"Cached Lectures ({root})", expand=False)
        table.add_column("ID", style="cyan", no_wrap=True)
        table.add_column("Date", style="dim", no_wrap=True)
        table.add_column("Course", style="bold", overflow="fold")
        table.add_column("Title", overflow="fold")
        table.add_column("Status")
        table.add_column("Audio", justify="right")
        table.add_column("Total", justify="right")

        for item in lectures:
            date_str = item.lecture_date or (
                item.updated_at.strftime("%Y-%m-%d") if item.updated_at else "-"
            )
            course_str = item.course or "-"
            title_str = item.title or "-"
            audio_str = format_size(item.audio_size) if item.audio_size > 0 else "-"
            total_str = format_size(item.total_size)
            table.add_row(
                item.lecture_id,
                date_str,
                course_str,
                title_str,
                item.display_status,
                audio_str,
                total_str,
            )


        console.print(table)
        console.print(
            f"Total: {summary.total_lectures} lectures "
            f"({format_size(summary.total_cache_size)} total, "
            f"{format_size(summary.total_audio_size)} audio)"
        )
        if summary.video_root and summary.video_root.is_dir():
            console.print(
                f"Video storage: {summary.video_count} videos "
                f"({format_size(summary.total_video_size)}) in {summary.video_root}"
            )

    _run_or_exit(action)


def _execute_cache_prune(
    *,
    lecture_ids: list[str] | None,
    all_workspaces: bool,
    audio_only: bool,
    days: int | None,
    dry_run: bool,
    yes: bool,
    cache_root: Path | None,
) -> None:
    def action() -> None:
        from lecture_util.cache import execute_prune, filter_prunable_lectures, scan_cached_lectures
        from lecture_util.progress import format_size

        root = (cache_root or default_cache_root()).expanduser().resolve()
        lectures = scan_cached_lectures(root)
        targets = filter_prunable_lectures(
            lectures,
            lecture_ids=lecture_ids,
            all_workspaces=all_workspaces,
            audio_only=audio_only,
            days=days,
        )

        if not targets:
            console.print("No cached lectures matched the prune criteria.")
            return

        target_bytes = sum(t.audio_size if audio_only else t.total_size for t in targets)
        desc = "audio files" if audio_only else "workspaces"
        console.print(
            f"Found {len(targets)} {desc} to prune ({format_size(target_bytes)}):"
        )
        for item in targets:
            label = (
                f"{item.course} · {item.title}"
                if (item.course and item.title)
                else (item.title or item.url or item.lecture_id)
            )
            size = format_size(item.audio_size if audio_only else item.total_size)
            console.print(f"  • [{item.lecture_id}] {label} ({size})")

        if dry_run:
            console.print(
                f"[yellow]Dry run:[/yellow] Would reclaim {format_size(target_bytes)}. No files were deleted."
            )
            return

        if not yes:
            if not _interactive_terminal():
                raise LectureUtilError("Pruning in non-interactive mode requires --yes.")
            action_prompt = f"Delete {desc} from {len(targets)} cached lecture(s) ({format_size(target_bytes)})?"
            if not Confirm.ask(action_prompt, default=False, console=console):
                console.print("Pruning cancelled.")
                return

        result = execute_prune(targets, audio_only=audio_only, dry_run=False)
        count = result.audio_files_pruned if audio_only else result.workspaces_pruned
        console.print(
            f"[green]Success:[/green] Reclaimed {format_size(result.reclaimed_bytes)} "
            f"across {count} lecture(s)."
        )
        if result.skipped_locked > 0:
            console.print(
                f"[yellow]Warning:[/yellow] Skipped {result.skipped_locked} active (locked) workspace(s)."
            )

    _run_or_exit(action)


@cache_app.callback(invoke_without_command=True)
def cache_callback(
    ctx: typer.Context,
    cache_root: Path | None = typer.Option(
        None, "--cache-root", help="Custom cache root directory",
    ),
) -> None:
    """Inspect and prune cached media and workspaces."""
    if ctx.invoked_subcommand is None:
        _execute_cache_list(cache_root)


@cache_app.command("list")
def cache_list_command(
    cache_root: Path | None = typer.Option(
        None, "--cache-root", help="Custom cache root directory",
    ),
) -> None:
    """List cached lecture workspaces and disk usage."""
    _execute_cache_list(cache_root)


@cache_app.command("prune")
def cache_prune_command(
    lecture_ids: list[str] = typer.Argument(
        None, metavar="[LECTURE_ID...]", help="Specific lecture IDs to prune",
    ),
    all_workspaces: bool = typer.Option(
        False, "--all", "-a", help="Prune all cached workspaces, not just completed ones",
    ),
    audio_only: bool = typer.Option(
        False, "--audio-only", help="Only delete audio.wav, keeping transcripts and summaries",
    ),
    days: int | None = typer.Option(
        None, "--days", "-d", min=0, help="Only prune entries older than N days",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n", help="Show what would be pruned without deleting files",
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Do not prompt for confirmation",
    ),
    cache_root: Path | None = typer.Option(
        None, "--cache-root", help="Custom cache root directory",
    ),
) -> None:
    """Prune cached audio files or entire workspaces to reclaim disk space."""
    _execute_cache_prune(
        lecture_ids=lecture_ids,
        all_workspaces=all_workspaces,
        audio_only=audio_only,
        days=days,
        dry_run=dry_run,
        yes=yes,
        cache_root=cache_root,
    )


app.add_typer(cache_app, name="cache")
