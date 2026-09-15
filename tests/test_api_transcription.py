from __future__ import annotations

import json
import wave
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from lecture_util import api_transcription as api
from lecture_util.errors import LectureUtilError
from lecture_util.models import LecturePaths, Segment, Transcript, TranscriptionOptions
from lecture_util.pipeline import transcription_cached, transcription_stage
from lecture_util.state import RunState
from lecture_util.transcription import load_transcript, restore_transcript_files, save_transcript


def audio_file(path, seconds=1, amplitude=1000):
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(int(amplitude).to_bytes(2, "little", signed=True) * (16000 * seconds))
    return path


@pytest.fixture
def options():
    return TranscriptionOptions(transcription_provider="openai")


@pytest.fixture
def client(monkeypatch):
    client = Mock()
    client.audio.transcriptions.create.return_value.model_dump.return_value = {"text": "전사 본문"}
    monkeypatch.setattr(api, "_client", lambda: client)
    return client


def test_api_request_and_untimed_output(tmp_path, options, client):
    paths = LecturePaths(tmp_path)
    audio_file(paths.audio)
    events = []
    transcript = api.transcribe_openai(paths.audio, options, progress=events.append)
    assert transcript.segments == [Segment(None, None, "전사 본문")]
    assert transcript.language == "unknown"
    assert transcript.duration == 1
    kwargs = client.audio.transcriptions.create.call_args.kwargs
    assert kwargs["model"] == "gpt-4o-transcribe"
    assert kwargs["response_format"] == "json"
    assert "language" not in kwargs and "timestamp_granularities" not in kwargs
    assert kwargs["file"].closed
    assert events[-1].processed_seconds == 1
    client.close.assert_called_once()
    paths.transcript_srt.write_text("old subtitles")
    save_transcript(transcript, paths.transcript_json, paths.transcript_markdown, paths.transcript_srt)
    assert not paths.transcript_srt.exists()
    assert "전사 본문" in paths.transcript_markdown.read_text()
    assert "00:00" not in paths.transcript_markdown.read_text()
    assert load_transcript(paths.transcript_json) == transcript
    paths.transcript_markdown.unlink()
    restore_transcript_files(transcript, paths.transcript_markdown, paths.transcript_srt)
    assert paths.transcript_markdown.exists() and not paths.transcript_srt.exists()


def test_whisper_offsets_and_srt(tmp_path, options, client, monkeypatch):
    monkeypatch.setattr(api, "CHUNK_SECONDS", 1)
    options = replace(options, openai_transcription_model="whisper-1", language="ko")
    client.audio.transcriptions.create.return_value.model_dump.return_value = {
        "text": "안녕", "language": "korean", "segments": [{"start": 0, "end": 1, "text": "안녕"}],
    }
    paths = LecturePaths(tmp_path)
    audio_file(paths.audio, 2)
    transcript = api.transcribe_openai(paths.audio, options)
    assert [(s.start, s.end) for s in transcript.segments] == [(0, 1), (1, 2)]
    assert client.audio.transcriptions.create.call_args.kwargs["timestamp_granularities"] == ["segment"]
    assert client.audio.transcriptions.create.call_args.kwargs["language"] == "ko"
    save_transcript(transcript, paths.transcript_json, paths.transcript_markdown, paths.transcript_srt)
    assert "00:00:01,000 --> 00:00:02,000" in paths.transcript_srt.read_text()
    paths.transcript_srt.unlink()
    restore_transcript_files(transcript, paths.transcript_markdown, paths.transcript_srt)
    assert paths.transcript_srt.exists()


def test_failed_chunk_resume_and_force(tmp_path, options, client, monkeypatch):
    monkeypatch.setattr(api, "CHUNK_SECONDS", 1)
    audio = audio_file(tmp_path / "audio.wav", 3)
    response = SimpleNamespace(model_dump=lambda: {"text": "part"})
    client.audio.transcriptions.create.side_effect = [response, LectureUtilError("failed")]
    with pytest.raises(LectureUtilError, match="failed"):
        api.transcribe_openai(audio, options)
    assert len(list((tmp_path / "openai-chunks").rglob("*.json"))) == 1
    client.audio.transcriptions.create.side_effect = None
    client.audio.transcriptions.create.reset_mock()
    result = api.transcribe_openai(audio, options)
    assert len(result.segments) == 3
    assert client.audio.transcriptions.create.call_count == 2
    client.audio.transcriptions.create.reset_mock()
    api.transcribe_openai(audio, options, force=True)
    assert client.audio.transcriptions.create.call_count == 3


def test_api_cache_ignores_device_and_restores_without_key(tmp_path, options, client, monkeypatch):
    paths = LecturePaths(tmp_path)
    audio_file(paths.audio)
    state = RunState(paths)
    first = transcription_stage(paths, state, transcription_provider="openai")
    monkeypatch.setattr(api, "_client", Mock(side_effect=AssertionError("must not need a key")))
    other = replace(options, device="mlx", model="other", compute_type="int8", batch_size=8)
    assert transcription_cached(paths, state, other)
    assert not transcription_cached(paths, state, replace(options, language="ko"))
    assert not transcription_cached(paths, state, replace(options, openai_transcription_model="whisper-1"))
    assert not transcription_cached(paths, state, TranscriptionOptions())
    paths.transcript_markdown.unlink()
    second = transcription_stage(paths, state, transcription_provider="openai", device="cpu")
    assert second == first and paths.transcript_markdown.exists()
    assert not paths.transcript_srt.exists()


def test_changed_audio_and_options_invalidate_chunks(tmp_path, options, client):
    audio = audio_file(tmp_path / "audio.wav")
    api.transcribe_openai(audio, options)
    api.transcribe_openai(audio, replace(options, language="ko"))
    audio_file(audio, amplitude=2000)
    api.transcribe_openai(audio, options)
    assert client.audio.transcriptions.create.call_count == 3


def test_corrupt_chunk_cache_is_rejected(tmp_path, options, client):
    audio = audio_file(tmp_path / "audio.wav")
    api.transcribe_openai(audio, options)
    record = next((tmp_path / "openai-chunks").rglob("*.json"))
    data = json.loads(record.read_text())
    data["response"]["text"] = "edited"
    record.write_text(json.dumps(data))
    with pytest.raises(LectureUtilError, match="--force"):
        api.transcribe_openai(audio, options)
    api.transcribe_openai(audio, options, force=True)


def test_silence_boundary_and_pcm_continuity(tmp_path, options, client):
    rate = 16000
    loud = (1000).to_bytes(2, "little", signed=True)
    pcm = loud * (179 * rate) + b"\0\0" * rate
    assert 179 * rate < api._boundary(pcm, rate) < 180 * rate
    assert api._boundary(loud * 180 * rate, rate) == 180 * rate
    audio = audio_file(tmp_path / "audio.wav", 181)
    with wave.open(str(audio), "wb") as target:
        target.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        target.writeframes(pcm + loud * rate)
    uploads = []
    def capture(**kwargs):
        with wave.open(kwargs["file"], "rb") as source:
            uploads.append(source.readframes(source.getnframes()))
        return SimpleNamespace(model_dump=lambda: {"text": "ok"})
    client.audio.transcriptions.create.side_effect = capture
    api.transcribe_openai(audio, options)
    assert b"".join(uploads) == pcm + loud * rate


def test_oversize_and_empty_inputs(tmp_path, options, client, monkeypatch):
    audio = audio_file(tmp_path / "audio.wav", 0)
    with pytest.raises(LectureUtilError, match="empty"):
        api.transcribe_openai(audio, options)
    audio_file(audio)
    monkeypatch.setattr(api, "MAX_UPLOAD_BYTES", 1)
    with pytest.raises(LectureUtilError, match="25 MB"):
        api.transcribe_openai(audio, options)
    client.audio.transcriptions.create.assert_not_called()


@pytest.mark.parametrize("payload", [{}, {"text": 1}, {"text": "ok", "language": []}])
def test_malformed_response(tmp_path, options, client, payload):
    client.audio.transcriptions.create.return_value.model_dump.return_value = payload
    # An empty detected-language value is equivalent to unavailable.
    if payload.get("language") == []:
        payload["language"] = ["invalid"]
    with pytest.raises(LectureUtilError, match="invalid transcription"):
        api.transcribe_openai(audio_file(tmp_path / "audio.wav"), options)
    assert not list((tmp_path / "openai-chunks").rglob("*.json"))


def test_retries_are_bounded_and_errors_redacted(tmp_path, options, client, monkeypatch):
    from openai import APIConnectionError
    secret = "secret-do-not-show"
    error = APIConnectionError(message=secret, request=Mock())
    client.audio.transcriptions.create.side_effect = error
    monkeypatch.setattr(api.time, "sleep", Mock())
    with pytest.raises(LectureUtilError) as caught:
        api.transcribe_openai(audio_file(tmp_path / "audio.wav"), options)
    assert secret not in str(caught.value)
    assert client.audio.transcriptions.create.call_count == 3
    client.close.assert_called_once()


def test_cancellation_keeps_completed_chunks_and_closes_client(tmp_path, options, client, monkeypatch):
    monkeypatch.setattr(api, "CHUNK_SECONDS", 1)
    client.audio.transcriptions.create.side_effect = [
        SimpleNamespace(model_dump=lambda: {"text": "ok"}), KeyboardInterrupt(),
    ]
    with pytest.raises(KeyboardInterrupt):
        api.transcribe_openai(audio_file(tmp_path / "audio.wav", 2), options)
    assert len(list((tmp_path / "openai-chunks").rglob("*.json"))) == 1
    client.close.assert_called_once()


@pytest.mark.parametrize("times", [(None, 1), (0, None), (-1, 2), (0, float("nan"))])
def test_invalid_nullable_timestamps(tmp_path, times):
    path = tmp_path / "transcript.json"
    transcript = Transcript("ko", 1, "openai", "test", "test", [Segment(*times, "text")])
    path.write_text(json.dumps(transcript.to_dict()))
    with pytest.raises(LectureUtilError, match="timestamps"):
        load_transcript(path)


@pytest.mark.parametrize("status,code,calls", [(401, None, 1), (403, None, 1),
                                               (429, "insufficient_quota", 1),
                                               (429, "rate_limit_exceeded", 3), (500, None, 3)])
def test_status_errors_and_quota_do_not_leak(tmp_path, options, client, monkeypatch, status, code, calls):
    from openai import APIStatusError
    response = Mock(status_code=status, headers={})
    client.audio.transcriptions.create.side_effect = APIStatusError(
        "private request", response=response, body={"code": code, "message": "private key"},
    )
    monkeypatch.setattr(api.time, "sleep", Mock())
    with pytest.raises(LectureUtilError) as error:
        api.transcribe_openai(audio_file(tmp_path / "audio.wav"), options)
    assert "private" not in str(error.value)
    assert client.audio.transcriptions.create.call_count == calls


def test_force_failure_does_not_reuse_chunks_from_previous_run(tmp_path, options, client, monkeypatch):
    monkeypatch.setattr(api, "CHUNK_SECONDS", 1)
    audio = audio_file(tmp_path / "audio.wav", 2)
    api.transcribe_openai(audio, options)
    client.audio.transcriptions.create.side_effect = [
        SimpleNamespace(model_dump=lambda: {"text": "new"}), KeyboardInterrupt(),
    ]
    with pytest.raises(KeyboardInterrupt):
        api.transcribe_openai(audio, options, force=True)
    client.audio.transcriptions.create.side_effect = None
    client.audio.transcriptions.create.reset_mock()
    api.transcribe_openai(audio, options)
    assert client.audio.transcriptions.create.call_count == 1


def test_full_api_run_resume_summary_and_publish_preserves_original(tmp_path, client, monkeypatch):
    import shutil
    from io import StringIO

    from rich.console import Console

    from lecture_util.models import LectureSource, RunOptions
    from lecture_util.pipeline import execute_run
    from lecture_util.recovery import load_request
    from lecture_util.state import file_digest, lecture_id
    from lecture_util.vault import COURSES_DIRECTORY

    vault = tmp_path / "vault"
    notes = vault / COURSES_DIRECTORY / "Course" / "Lectures"
    notes.mkdir(parents=True)
    original = audio_file(tmp_path / "original.wav")
    original_bytes = original.read_bytes()
    source = LectureSource("audio", str(original), file_digest(original))
    selected = RunOptions(str(original), "Course", "2026-09-07", "Title", None, None,
                          "large-v3", "auto", "auto", "Prompt", False,
                          semester_start="2026-08-31", source=source, transcription_provider="openai")
    summary = Mock()
    summary.name, summary.model, summary.reasoning_effort = "test", "test", None
    summary.generate.side_effect = LectureUtilError("summary failed")
    monkeypatch.setattr("lecture_util.pipeline.CodexSummarizer", lambda **kwargs: summary)
    monkeypatch.setattr("lecture_util.pipeline.extract_audio", shutil.copyfile)
    monkeypatch.setattr("lecture_util.pipeline.transcribe_audio", Mock(side_effect=AssertionError("no local inference")))
    monkeypatch.setattr("lecture_util.media.require_executable", lambda name: name)
    monkeypatch.setattr(api, "resolve_api_key", lambda: "fake")
    monkeypatch.setattr("lecture_util.recovery.resolve_source", lambda url: source)
    cache = tmp_path / "cache"
    output = Console(file=StringIO())
    with pytest.raises(LectureUtilError, match="summary failed"):
        execute_run(selected, vault_root=vault, video_root=tmp_path / "videos", cache_root=cache, console=output)
    assert client.audio.transcriptions.create.call_count == 1
    paths = LecturePaths(cache / f"lecture-{lecture_id(source.cache_key)}")
    assert not paths.transcript_srt.exists()
    paths.transcript_markdown.unlink()
    monkeypatch.setattr(api, "resolve_api_key", Mock(side_effect=AssertionError("cached transcription needs no key")))
    def summarize(developer, prompt, path):
        assert "전사 본문" in path.read_text()
        assert "00:00" not in path.read_text()
        return "### 핵심\n\n강의 요약"
    summary.generate.side_effect = summarize
    restored, restored_vault, videos = load_request(paths.root)
    execute_run(restored, vault_root=restored_vault, video_root=videos, cache_root=cache, console=output)
    assert client.audio.transcriptions.create.call_count == 1
    assert len(list(notes.rglob("*.md"))) == 2
    assert "전사 본문" in next(notes.rglob("*전사.md")).read_text()
    assert original.read_bytes() == original_bytes
    assert not paths.transcript_srt.exists()
    with pytest.raises(LectureUtilError, match="already exists"):
        execute_run(replace(restored, force=True), vault_root=vault, video_root=videos, cache_root=cache, console=output)
    assert client.audio.transcriptions.create.call_count == 1
