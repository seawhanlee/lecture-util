from __future__ import annotations

import platform
import json
from dataclasses import replace
from datetime import date
from rich.console import Console
from rich.text import Text
from pathlib import Path
from time import monotonic

from lecture_util.errors import LectureUtilError
from lecture_util.console_progress import ConsoleProgressReporter
from lecture_util.recovery import save_request, resume_message
from lecture_util.summarizers import CodexSummarizer
from lecture_util.state import workspace_lock, lecture_id
from lecture_util.vault import (
    DEFAULT_VAULT_ROOT, default_cache_root, default_semester_start,
    resolve_course, validate_lecture_date, validate_semester_start, validate_title,
    published_lecture_paths, ensure_paths_available, lecture_video_path,
    publish_lecture_notes, _publication_record,
)
from lecture_util.media import download_hls, extract_audio, resolve_source, tool_version
from lecture_util.models import LecturePaths, LectureSource, Transcript, TranscriptionOptions, RunOptions
from lecture_util.progress import ProgressCallback, format_duration, format_size, report
from lecture_util.state import RunState, create_workspace, file_digest
from lecture_util.summarizers import Summarizer
from lecture_util.summary import DEFAULT_PROMPT, summary_stage
from lecture_util.transcription import save_transcript, transcribe_audio, preflight_transcription


def download_stage(
    paths: LecturePaths,
    state: RunState,
    *,
    force: bool = False,
    progress: ProgressCallback | None = None,
) -> None:
    previous = state.data.get("stages", {}).get("download", {})
    video_digest = state.digest(paths.video) if paths.video.is_file() else None
    if (not force and state.stage_complete("download") and paths.video.is_file()
            and previous.get("output") == str(paths.video)
            and previous.get("sha256", video_digest) == video_digest):
        if "sha256" not in previous:
            state.complete_stage("download", sha256=video_digest)
        report(
            progress,
            "download",
            "cached",
            f"Reusing video ({format_size(paths.video.stat().st_size)})",
        )
        return
    authorized_retry = (previous.get("status") in {"failed", "running"}
                        and previous.get("replace_authorized") is True
                        and previous.get("output") == str(paths.video))
    if not force and paths.video.exists() and not authorized_retry:
        raise LectureUtilError(
            "Video file already exists but is not a completed download for this URL. "
            f"Use --force to replace it: {paths.video}"
        )
    started = monotonic()
    report(progress, "download", "start", "Downloading HLS video")
    state.start_stage("download", output=str(paths.video),
                      replace_authorized=force or authorized_retry)
    try:
        download_hls(state.url, paths.video, progress=progress)
    except BaseException as error:
        state.fail_stage("download", error)
        report(
            progress,
            "download",
            "failed",
            f"Download failed after {format_duration(monotonic() - started)}",
        )
        raise
    state.complete_stage(
        "download",
        output=str(paths.video),
        sha256=state.digest(paths.video),
        yt_dlp_version=tool_version("yt-dlp"),
        ffmpeg_version=tool_version("ffmpeg"),
    )
    report(
        progress,
        "download",
        "complete",
        f"Downloaded {format_size(paths.video.stat().st_size)} "
        f"in {format_duration(monotonic() - started)}",
    )


def audio_stage(
    paths: LecturePaths,
    state: RunState,
    *,
    force: bool = False,
    progress: ProgressCallback | None = None,
) -> None:
    source = state.data.get("source", {})
    local = source.get("kind") in {"audio", "video"}
    input_path = Path(source["location"]) if local else paths.video
    source_digest = state.digest(input_path)
    previous = state.data.get("stages", {}).get("audio", {})
    if (not force and state.stage_complete("audio") and paths.audio.is_file()
            and previous.get("input_sha256") == source_digest
            and previous.get("sha256") == state.digest(paths.audio)):
        report(
            progress,
            "audio",
            "cached",
            f"Reusing prepared audio ({format_size(paths.audio.stat().st_size)})",
        )
        return
    verb = "Normalizing" if source.get("kind") == "audio" else "Extracting"
    started = monotonic()
    report(progress, "audio", "start", f"{verb} 16 kHz mono audio")
    state.start_stage("audio", input_sha256=source_digest)
    try:
        extract_audio(input_path, paths.audio)
    except BaseException as error:
        state.fail_stage("audio", error)
        report(
            progress,
            "audio",
            "failed",
            f"Audio extraction failed after {format_duration(monotonic() - started)}",
        )
        raise
    state.complete_stage("audio", output=str(paths.audio), sample_rate=16000, channels=1,
                         sha256=state.digest(paths.audio))
    report(
        progress,
        "audio",
        "complete",
        f"Prepared {format_size(paths.audio.stat().st_size)} "
        f"in {format_duration(monotonic() - started)}",
    )


def transcription_stage(
    paths: LecturePaths,
    state: RunState,
    *,
    model: str = "large-v3",
    language: str = "auto",
    device: str = "auto",
    compute_type: str = "auto", batch_size: int = 0, beam_size: int | None = None,
    force: bool = False,
    progress: ProgressCallback | None = None,
) -> Transcript:
    from lecture_util.configuration import validate_transcription_options
    options = TranscriptionOptions(model, language, device, compute_type, batch_size, beam_size)
    validate_transcription_options(options)
    if not force and paths.transcript_json.is_file():
        from lecture_util.transcription import load_transcript
        load_transcript(paths.transcript_json)
        previous = state.data.get("stages", {}).get("transcription", {})
        if previous.get("sha256") and previous["sha256"] != state.digest(paths.transcript_json):
            raise LectureUtilError(f"Transcript JSON changed: {paths.transcript_json}. Use --force to regenerate it.")
    source_digest = state.digest(paths.audio)
    if not force and transcription_cached(paths, state, options):
        from lecture_util.transcription import load_transcript

        transcript = load_transcript(paths.transcript_json)
        from lecture_util.transcription import restore_transcript_files
        restore_transcript_files(transcript, paths.transcript_markdown, paths.transcript_srt)
        report(
            progress,
            "transcription",
            "cached",
            f"Reusing {len(transcript.segments)} transcript segments ({transcript.language})",
        )
        return transcript
    started = monotonic()
    report(
        progress,
        "transcription",
        "start",
        f"Transcribing with Whisper {model} on {device} (this may take several minutes)",
    )
    state.start_stage(
        "transcription",
        input_sha256=source_digest,
        options=options.to_dict(), backend_platform=backend_platform(),
        requested_model=model,
        requested_language=language,
        requested_device=device,
    )
    try:
        transcript = transcribe_audio(paths.audio, model=model, language=language, device=device,
                                      compute_type=compute_type, batch_size=batch_size,
                                      beam_size=beam_size, progress=progress)
        save_transcript(
            transcript,
            paths.transcript_json,
            paths.transcript_markdown,
            paths.transcript_srt,
        )
    except BaseException as error:
        state.fail_stage("transcription", error)
        report(
            progress,
            "transcription",
            "failed",
            f"Transcription failed after {format_duration(monotonic() - started)}",
        )
        raise
    state.complete_stage(
        "transcription",
        engine=transcript.engine,
        effective_options=transcript.effective_options,
        sha256=state.digest(paths.transcript_json),
        requested_model=transcript.requested_model,
        effective_model=transcript.effective_model,
        fallback_reason=transcript.fallback_reason,
        language=transcript.language,
        duration=transcript.duration,
    )
    if transcript.fallback_reason:
        report(
            progress,
            "transcription",
            "warning",
            f"Fell back from {transcript.requested_model} to {transcript.effective_model}",
        )
    report(
        progress,
        "transcription",
        "complete",
        f"Created {len(transcript.segments)} segments in {format_duration(monotonic() - started)} "
        f"({transcript.engine}/{transcript.effective_model}, {transcript.language})",
    )
    return transcript


def prepare_lecture(
    url: str,
    output_dir: Path,
    *,
    video_path: Path | None = None,
    source: LectureSource | None = None,
    title: str | None = None,
    course: str | None = None,
    lecture_date: str | None = None,
    published_summary: Path | None = None,
    published_transcript: Path | None = None,
    tags: list[str] | None = None,
    model: str = "large-v3",
    language: str = "auto",
    device: str = "auto",
    compute_type: str = "auto", batch_size: int = 0, beam_size: int | None = None,
    force: bool = False,
    progress: ProgressCallback | None = None,
) -> tuple[LecturePaths, RunState, Transcript]:
    source = source or resolve_source(url)
    paths, state = create_workspace(
        url,
        output_dir,
        video_path=video_path,
        source=source,
        title=title,
        course=course,
        lecture_date=lecture_date,
        published_summary=published_summary,
        published_transcript=published_transcript,
        tags=tags,
    )
    options = TranscriptionOptions(model, language, device, compute_type, batch_size, beam_size)
    preflight_preparation(paths, state, options, force=force)
    if source.kind == "hls":
        download_stage(paths, state, force=force, progress=progress)
    audio_stage(paths, state, force=force, progress=progress)
    transcript = transcription_stage(
        paths,
        state,
        model=model,
        language=language,
        device=device,
        compute_type=compute_type, batch_size=batch_size, beam_size=beam_size,
        force=force,
        progress=progress,
    )
    return paths, state, transcript


def run_lecture(
    url: str,
    output_dir: Path,
    summarizer: Summarizer,
    *,
    video_path: Path | None = None,
    source: LectureSource | None = None,
    title: str | None = None,
    course: str | None = None,
    lecture_date: str | None = None,
    published_summary: Path | None = None,
    published_transcript: Path | None = None,
    tags: list[str] | None = None,
    model: str = "large-v3",
    language: str = "auto",
    device: str = "auto",
    compute_type: str = "auto", batch_size: int = 0, beam_size: int | None = None,
    prompt: str = DEFAULT_PROMPT,
    force: bool = False,
    progress: ProgressCallback | None = None,
) -> LecturePaths:
    from lecture_util.media import require_executable
    source = source or resolve_source(url)
    preview = LecturePaths(output_dir / f"lecture-{lecture_id(source.cache_key)}", video_path=video_path)
    preview_state = RunState(preview, read_only=True)
    if summarizer.name == "codex" and (force or not summary_cached(preview, preview_state, summarizer, prompt)):
        require_executable("codex")
    paths, state, transcript = prepare_lecture(
        url,
        output_dir,
        video_path=video_path,
        source=source,
        title=title,
        course=course,
        lecture_date=lecture_date,
        published_summary=published_summary,
        published_transcript=published_transcript,
        tags=tags,
        model=model,
        language=language,
        device=device,
        compute_type=compute_type, batch_size=batch_size, beam_size=beam_size,
        force=force,
        progress=progress,
    )
    summary_stage(
        transcript,
        paths.transcript_markdown,
        paths.summary,
        state,
        summarizer,
        prompt=prompt,
        force=force,
        progress=progress,
    )
    return paths


def backend_platform() -> str:
    return f"{platform.system()}:{platform.machine().lower()}"


def transcription_cached(paths: LecturePaths, state: RunState, options: TranscriptionOptions) -> bool:
    stage = state.data.get("stages", {}).get("transcription", {})
    return bool(
        stage.get("status") == "complete"
        and stage.get("options") == options.to_dict()
        and stage.get("backend_platform") == backend_platform()
        and paths.audio.is_file() and paths.transcript_json.is_file()
        and stage.get("input_sha256") == state.digest(paths.audio)
    )


def preflight_preparation(paths: LecturePaths, state: RunState,
                          options: TranscriptionOptions, *, force: bool = False) -> None:
    from lecture_util.configuration import validate_transcription_options
    from lecture_util.media import require_executable
    validate_transcription_options(options)
    download = state.data.get("stages", {}).get("download", {})
    video_digest = state.digest(paths.video) if paths.video.is_file() else None
    download_cached = (not force and state.stage_complete("download")
                       and video_digest is not None and download.get("output") == str(paths.video)
                       and download.get("sha256", video_digest) == video_digest)
    local = state.data.get("source", {}).get("kind") in {"audio", "video"}
    if local:
        video_digest = state.digest(Path(state.data["source"]["location"]))
        download_cached = not force
    audio = state.data.get("stages", {}).get("audio", {})
    audio_cached = (download_cached and state.stage_complete("audio") and paths.audio.is_file()
                    and audio.get("input_sha256") == video_digest
                    and audio.get("sha256") == state.digest(paths.audio))
    if not audio_cached or not transcription_cached(paths, state, options):
        preflight_transcription(options)
    if not local and not download_cached:
        require_executable("yt-dlp")
    if not audio_cached:
        require_executable("ffmpeg")


def preflight_run(options: RunOptions, vault_root: Path, video_root: Path) -> None:
    from lecture_util.media import require_executable
    from lecture_util.recovery import transcription_options
    from lecture_util.state import lecture_id
    from lecture_util.vault import default_cache_root, resolve_course, lecture_video_path
    source = options.source or resolve_source(options.url)
    course = resolve_course(options.course, vault_root)
    video = lecture_video_path(video_root, course, options.lecture_date, options.title,
                               semester_start=options.semester_start)
    paths = LecturePaths(default_cache_root() / f"lecture-{lecture_id(source.cache_key)}", video_path=video)
    from lecture_util.vault import _publication_record
    if _publication_record(paths.root / "publication.json").get("status") == "pending":
        return
    if options.video_only:
        require_executable("yt-dlp")
        require_executable("ffmpeg")
        return
    state = RunState(paths, read_only=True)
    state.data["source"] = {"kind": source.kind, "location": source.location}
    preflight_preparation(paths, state, transcription_options(options), force=options.force)
    if options.force or not summary_cached(paths, state, CodexSummarizer(
            model=options.llm_model, reasoning_effort=options.reasoning_effort), options.prompt):
        require_executable("codex")


def execute_run(
    options: RunOptions, *, vault_root: Path = DEFAULT_VAULT_ROOT,
    video_root: Path | None = None, cache_root: Path | None = None,
    console: Console | None = None,
) -> None:
    console = console or Console()
    cache = (cache_root or default_cache_root()).resolve()
    source = options.source or resolve_source(options.url)
    options = replace(options, source=source, url=source.location)
    root = cache / f"lecture-{lecture_id(source.cache_key)}"
    with workspace_lock(root):
        try:
            _execute_run_locked(options, vault_root=vault_root.resolve(),
                                video_root=video_root.resolve() if video_root else None, cache_root=cache, console=console)
        except BaseException:
            stage = "preparation"
            try:
                state = RunState(LecturePaths(root), read_only=True)
                stage = next((name for name, item in state.data["stages"].items()
                              if item.get("status") in {"failed", "running"}), stage)
                journal = root / "publication.json"
                if journal.exists():
                    stage = "publication"
            except LectureUtilError:
                pass
            if (root / "request.json").is_file():
                console.print(Text(resume_message(root, stage)))
            raise


def _execute_run_locked(
    options: RunOptions,
    *,
    vault_root: Path = DEFAULT_VAULT_ROOT,
    video_root: Path | None = None,
    cache_root: Path | None = None,
    console: Console,
) -> None:
    source = options.source or resolve_source(options.url)
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
    journal = (cache_root or default_cache_root()) / f"lecture-{lecture_id(source.cache_key)}" / "publication.json"
    ensure_paths_available(published, journal=journal)
    video = lecture_video_path(
        video_root or default_cache_root(),
        course,
        lecture_date,
        title,
        semester_start=semester_start,
    )

    summarizer = CodexSummarizer(
        model=options.llm_model, reasoning_effort=options.reasoning_effort,
    )
    pending = _publication_record(journal).get("status") == "pending"
    if not pending:
        save_request(journal.parent, replace(options, semester_start=semester_start),
                     vault_root, video_root or default_cache_root())
    started = monotonic()
    with ConsoleProgressReporter(
        (("download", "audio", "transcription", "summary", "publication")
         if source.kind == "hls" else ("audio", "transcription", "summary", "publication")),
        console=console,
        lecture_label=f"{course.name} · {lecture_date} {title}",
        source_url=options.url,
    ) as progress:
        if pending:
            paths = LecturePaths(journal.parent)
            summary_text = transcript_text = ""
        else:
            paths = run_lecture(
                options.url,
                cache_root or default_cache_root(),
                summarizer,
                video_path=video if source.kind == "hls" else None,
                source=source,
                title=title,
                course=course.name,
                lecture_date=lecture_date,
                published_summary=published.summary,
                published_transcript=published.transcript,
                tags=options.tags,
                model=options.whisper_model,
                language=options.language,
                device=options.device,
                compute_type=options.compute_type, batch_size=options.batch_size, beam_size=options.beam_size,
                prompt=options.prompt,
                force=options.force,
                progress=progress,
            )
            summary_text = paths.summary.read_text(encoding="utf-8")
            transcript_text = paths.transcript_markdown.read_text(encoding="utf-8")
        publication_state = RunState(LecturePaths(journal.parent))
        publication_state.start_stage("publication")
        report(progress, "publication", "start", "Publishing lecture notes")
        try:
            publish_lecture_notes(
                published,
                course=course,
                lecture_date=lecture_date,
                title=title,
                url=options.url,
                summary=summary_text,
                transcript=transcript_text,
                journal=journal,
            )
        except BaseException as error:
            publication_state.fail_stage("publication", error)
            report(progress, "publication", "failed", "Publication failed; recorded files can be resumed")
            raise
        report(progress, "publication", "complete", "Published both lecture notes")
        publication_state.complete_stage("publication")
    console.print(
        Text.assemble(
            ("Complete", "bold green"),
            f" {published.summary} ({format_duration(monotonic() - started)})",
        ),
        highlight=False,
    )
    label = f"Video: {video}" if source.kind == "hls" else f"Source: {source.location}"
    console.print(Text(label), highlight=False)



def summary_cached(paths: LecturePaths, state: RunState, summarizer: Summarizer, prompt: str) -> bool:
    from lecture_util.summary import summary_fingerprint
    from lecture_util.transcription import load_transcript
    stage = state.data.get("stages", {}).get("summary", {})
    if not (stage.get("status") == "complete" and paths.transcript_json.is_file()
            and paths.transcript_markdown.is_file() and paths.summary.is_file()):
        return False
    return (stage.get("fingerprint") == summary_fingerprint(
        load_transcript(paths.transcript_json), paths.transcript_markdown, summarizer, prompt,
    ) and stage.get("sha256") == state.digest(paths.summary))
