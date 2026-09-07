from __future__ import annotations

import platform
from pathlib import Path
from time import monotonic

from lecture_util.errors import LectureUtilError
from lecture_util.media import download_hls, extract_audio, resolve_source, tool_version
from lecture_util.models import LecturePaths, LectureSource, Transcript, TranscriptionOptions
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
    video_digest = file_digest(paths.video) if paths.video.is_file() else None
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
    if not force and paths.video.exists():
        raise LectureUtilError(
            "Video file already exists but is not a completed download for this URL. "
            f"Use --force to replace it: {paths.video}"
        )
    started = monotonic()
    report(progress, "download", "start", "Downloading HLS video")
    state.start_stage("download")
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
        sha256=file_digest(paths.video),
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
    source_digest = file_digest(input_path)
    previous = state.data.get("stages", {}).get("audio", {})
    if (not force and state.stage_complete("audio") and paths.audio.is_file()
            and previous.get("input_sha256") == source_digest
            and previous.get("sha256") == file_digest(paths.audio)):
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
                         sha256=file_digest(paths.audio))
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
    source_digest = file_digest(paths.audio)
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
        sha256=file_digest(paths.transcript_json),
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
    from lecture_util.configuration import validate_transcription_options
    from lecture_util.media import require_executable
    options = TranscriptionOptions(model, language, device, compute_type, batch_size, beam_size)
    validate_transcription_options(options)
    if force or not transcription_cached(paths, state, options):
        preflight_transcription(options)
    if source.kind == "hls" and (
        force or not state.stage_complete("download") or not paths.video.is_file()
    ):
        require_executable("yt-dlp")
        require_executable("ffmpeg")
    if force or not state.stage_complete("audio") or not paths.audio.is_file():
        require_executable("ffmpeg")
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
        and stage.get("input_sha256") == file_digest(paths.audio)
    )
