from __future__ import annotations

from pathlib import Path

from lecture_util.media import download_hls, extract_audio
from lecture_util.models import LecturePaths, Transcript
from lecture_util.state import RunState, create_workspace
from lecture_util.transcription import save_transcript, transcribe_audio


def download_stage(paths: LecturePaths, state: RunState, *, force: bool = False) -> None:
    if not force and state.stage_complete("download") and paths.video.is_file():
        return
    state.start_stage("download")
    try:
        download_hls(state.url, paths.video)
    except BaseException as error:
        state.fail_stage("download", error)
        raise
    state.complete_stage("download", output=str(paths.video))


def audio_stage(paths: LecturePaths, state: RunState, *, force: bool = False) -> None:
    if not force and state.stage_complete("audio") and paths.audio.is_file():
        return
    state.start_stage("audio")
    try:
        extract_audio(paths.video, paths.audio)
    except BaseException as error:
        state.fail_stage("audio", error)
        raise
    state.complete_stage("audio", output=str(paths.audio), sample_rate=16000, channels=1)


def transcription_stage(
    paths: LecturePaths,
    state: RunState,
    *,
    model: str = "large-v3",
    language: str = "auto",
    device: str = "auto",
    force: bool = False,
) -> Transcript:
    if not force and state.stage_complete("transcription") and paths.transcript_json.is_file():
        from lecture_util.transcription import load_transcript

        return load_transcript(paths.transcript_json)
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
    return transcript


def prepare_lecture(
    url: str,
    output_dir: Path,
    *,
    title: str | None = None,
    tags: list[str] | None = None,
    model: str = "large-v3",
    language: str = "auto",
    device: str = "auto",
    force: bool = False,
) -> tuple[LecturePaths, RunState, Transcript]:
    paths, state = create_workspace(url, output_dir, title=title, tags=tags)
    download_stage(paths, state, force=force)
    audio_stage(paths, state, force=force)
    transcript = transcription_stage(
        paths,
        state,
        model=model,
        language=language,
        device=device,
        force=force,
    )
    return paths, state, transcript
