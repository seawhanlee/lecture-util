from __future__ import annotations

from pathlib import Path
from time import monotonic

from lecture_util.media import download_hls, extract_audio, tool_version
from lecture_util.models import LecturePaths, Transcript
from lecture_util.progress import ProgressCallback, format_duration, format_size, report
from lecture_util.state import RunState, create_workspace
from lecture_util.summarizers import Summarizer
from lecture_util.summary import DEFAULT_PROMPT, summary_stage
from lecture_util.transcription import save_transcript, transcribe_audio


def download_stage(
    paths: LecturePaths,
    state: RunState,
    *,
    force: bool = False,
    progress: ProgressCallback | None = None,
) -> None:
    if not force and state.stage_complete("download") and paths.video.is_file():
        report(
            progress,
            "download",
            "cached",
            f"Reusing video ({format_size(paths.video.stat().st_size)})",
        )
        return
    started = monotonic()
    report(progress, "download", "start", "Downloading HLS video")
    state.start_stage("download")
    try:
        download_hls(state.url, paths.video)
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
    if not force and state.stage_complete("audio") and paths.audio.is_file():
        report(
            progress,
            "audio",
            "cached",
            f"Reusing extracted audio ({format_size(paths.audio.stat().st_size)})",
        )
        return
    started = monotonic()
    report(progress, "audio", "start", "Extracting 16 kHz mono audio")
    state.start_stage("audio")
    try:
        extract_audio(paths.video, paths.audio)
    except BaseException as error:
        state.fail_stage("audio", error)
        report(
            progress,
            "audio",
            "failed",
            f"Audio extraction failed after {format_duration(monotonic() - started)}",
        )
        raise
    state.complete_stage("audio", output=str(paths.audio), sample_rate=16000, channels=1)
    report(
        progress,
        "audio",
        "complete",
        f"Extracted {format_size(paths.audio.stat().st_size)} "
        f"in {format_duration(monotonic() - started)}",
    )


def transcription_stage(
    paths: LecturePaths,
    state: RunState,
    *,
    model: str = "large-v3",
    language: str = "auto",
    device: str = "auto",
    force: bool = False,
    progress: ProgressCallback | None = None,
) -> Transcript:
    previous = state.data.get("stages", {}).get("transcription", {})
    same_options = (
        previous.get("requested_model") == model
        and previous.get("requested_language") == language
        and previous.get("requested_device") == device
    )
    if (
        not force
        and state.stage_complete("transcription")
        and same_options
        and paths.transcript_json.is_file()
    ):
        from lecture_util.transcription import load_transcript

        transcript = load_transcript(paths.transcript_json)
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
        requested_model=model,
        requested_language=language,
        requested_device=device,
    )
    try:
        transcript = transcribe_audio(paths.audio, model=model, language=language, device=device)
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
    title: str | None = None,
    course: str | None = None,
    lecture_date: str | None = None,
    published_summary: Path | None = None,
    published_transcript: Path | None = None,
    tags: list[str] | None = None,
    model: str = "large-v3",
    language: str = "auto",
    device: str = "auto",
    force: bool = False,
    progress: ProgressCallback | None = None,
) -> tuple[LecturePaths, RunState, Transcript]:
    paths, state = create_workspace(
        url,
        output_dir,
        title=title,
        course=course,
        lecture_date=lecture_date,
        published_summary=published_summary,
        published_transcript=published_transcript,
        tags=tags,
    )
    download_stage(paths, state, force=force, progress=progress)
    audio_stage(paths, state, force=force, progress=progress)
    transcript = transcription_stage(
        paths,
        state,
        model=model,
        language=language,
        device=device,
        force=force,
        progress=progress,
    )
    return paths, state, transcript


def run_lecture(
    url: str,
    output_dir: Path,
    summarizer: Summarizer,
    *,
    title: str | None = None,
    course: str | None = None,
    lecture_date: str | None = None,
    published_summary: Path | None = None,
    published_transcript: Path | None = None,
    tags: list[str] | None = None,
    model: str = "large-v3",
    language: str = "auto",
    device: str = "auto",
    prompt: str = DEFAULT_PROMPT,
    force: bool = False,
    progress: ProgressCallback | None = None,
) -> LecturePaths:
    paths, state, transcript = prepare_lecture(
        url,
        output_dir,
        title=title,
        course=course,
        lecture_date=lecture_date,
        published_summary=published_summary,
        published_transcript=published_transcript,
        tags=tags,
        model=model,
        language=language,
        device=device,
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
