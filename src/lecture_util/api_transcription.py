"""OpenAI file transcription with bounded uploads and recoverable chunks."""
from __future__ import annotations

import hashlib
import json
import math
import shutil
import struct
import time
import wave
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openai import OpenAI

from lecture_util.credentials import resolve_api_key
from lecture_util.errors import LectureUtilError
from lecture_util.models import Segment, Transcript, TranscriptionOptions
from lecture_util.progress import ProgressCallback, report
from lecture_util.state import atomic_write_json, file_digest

CHUNK_SECONDS = 180
MAX_UPLOAD_BYTES = 25_000_000


def preflight_openai() -> None:
    resolve_api_key()
    try:
        import openai  # noqa: F401
    except ImportError:
        raise LectureUtilError("OpenAI SDK is not installed. Run uv sync.") from None


def _client() -> OpenAI:
    from openai import OpenAI
    return OpenAI(api_key=resolve_api_key(), base_url="https://api.openai.com/v1",
                  max_retries=0, timeout=120.0)


def _boundary(pcm: bytes, rate: int) -> int:
    """Choose the last >=300ms silence in the final ten seconds of mono PCM."""
    frames = len(pcm) // 2
    block = max(1, rate // 10)
    silence_start: int | None = None
    candidate = frames
    for start in range(max(0, frames - 10 * rate), frames, block):
        samples = [value[0] for value in struct.iter_unpack("<h", pcm[2 * start:2 * min(start + block, frames)])]
        quiet = samples and sum(value * value for value in samples) / len(samples) <= 327 ** 2
        if quiet:
            if silence_start is None:
                silence_start = start
            if start + len(samples) - silence_start >= rate * 0.3:
                candidate = (silence_start + start + len(samples)) // 2
        else:
            silence_start = None
    return candidate


def _request(client: OpenAI, path: Path, options: TranscriptionOptions) -> dict[str, Any]:
    from openai import APIConnectionError, APIStatusError, OpenAIError
    if path.stat().st_size > MAX_UPLOAD_BYTES:
        raise LectureUtilError("Transcription upload exceeds OpenAI's 25 MB limit.")
    kwargs: dict[str, Any] = {"model": options.openai_transcription_model, "response_format": "json"}
    if options.openai_transcription_model == "whisper-1":
        kwargs.update(response_format="verbose_json", timestamp_granularities=["segment"])
    if options.language != "auto":
        kwargs["language"] = options.language
    for attempt in range(3):
        try:
            with path.open("rb") as audio:
                result = client.audio.transcriptions.create(file=audio, **kwargs).model_dump()
                if not isinstance(result, dict):
                    raise LectureUtilError("OpenAI returned an invalid transcription response.")
                return result
        except (APIConnectionError, APIStatusError) as error:
            status = getattr(error, "status_code", None)
            quota = getattr(error, "code", None) == "insufficient_quota"
            retryable = isinstance(error, APIConnectionError) or status in {408, 409, 429} or (status is not None and status >= 500)
            if retryable and not quota and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            if status in {401, 403}:
                message = "OpenAI authentication failed. Check the API key and project permissions in lecture-util config."
            elif status == 429:
                message = "OpenAI quota or rate limit reached. Check API billing/limits and resume later."
            elif isinstance(error, APIConnectionError):
                message = "OpenAI connection failed or timed out. Check your connection and resume."
            else:
                message = f"OpenAI transcription request failed (HTTP {status}). Check model access and resume."
            # API error bodies can contain credentials or private request content.
            raise LectureUtilError(message) from None
        except OpenAIError:
            raise LectureUtilError("OpenAI could not process the transcription response. Resume to retry.") from None
    raise AssertionError("unreachable")


def _result(data: dict[str, Any], options: TranscriptionOptions, offset: float,
            duration: float) -> Transcript:
    timed = options.openai_transcription_model == "whisper-1"
    try:
        if not isinstance(data, dict) or not isinstance(data.get("text"), str):
            raise ValueError("missing text")
        if timed:
            if not isinstance(data.get("segments"), list):
                raise ValueError("missing segments")
            segments = []
            for item in data["segments"]:
                start, end = float(item["start"]), float(item["end"])
                if not math.isfinite(start) or not math.isfinite(end) or not 0 <= start <= end <= duration + 1:
                    raise ValueError("invalid timestamps")
                if not isinstance(item["text"], str):
                    raise ValueError("invalid text")
                segments.append(Segment(offset + min(start, duration), offset + min(end, duration), item["text"].strip()))
        else:
            segments = [Segment(None, None, data["text"].strip())] if data["text"].strip() else []
        language = data.get("language") or (options.language if options.language != "auto" else "unknown")
        if not isinstance(language, str):
            raise ValueError("invalid language")
    except (KeyError, ValueError, TypeError):
        raise LectureUtilError("OpenAI returned an invalid transcription response. Resume to retry this chunk.") from None
    return Transcript(language, duration, "openai", options.selected_model, options.selected_model,
                      segments, requested_options=options.to_dict(),
                      effective_options={**options.to_dict(), "timestamps": timed})


def _read_chunk(path: Path, identity: dict[str, Any], options: TranscriptionOptions,
                offset: float, duration: float) -> Transcript | None:
    if not path.exists():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        if record["identity"] != identity:
            return None
        response = record["response"]
        digest = hashlib.sha256(json.dumps(response, sort_keys=True).encode()).hexdigest()
        if record["sha256"] != digest:
            raise ValueError("changed content")
        return _result(response, options, offset, duration)
    except (OSError, ValueError, KeyError, TypeError, LectureUtilError):
        raise LectureUtilError(f"Invalid OpenAI chunk cache: {path}. Use --force to regenerate it.") from None


def transcribe_openai(audio: Path, options: TranscriptionOptions, *, force: bool = False,
                      progress: ProgressCallback | None = None) -> Transcript:
    identity = {"audio_sha256": file_digest(audio), **options.to_dict()}
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    cache = audio.parent / "openai-chunks" / fingerprint
    segments: list[Segment] = []
    languages: list[str] = []
    client = None
    try:
        if force and cache.exists():
            shutil.rmtree(cache)
        with wave.open(str(audio), "rb") as source, TemporaryDirectory(prefix="lecture-openai-") as temporary:
            if source.getnchannels() != 1 or source.getsampwidth() != 2 or source.getframerate() != 16000:
                raise LectureUtilError("OpenAI transcription requires the extracted 16 kHz mono PCM WAV. Extract audio again.")
            rate, total = source.getframerate(), source.getnframes()
            if not total:
                raise LectureUtilError("Cannot transcribe an empty audio file.")
            cursor = index = 0
            while cursor < total:
                source.setpos(cursor)
                pcm = source.readframes(min(CHUNK_SECONDS * rate, total - cursor))
                frames = len(pcm) // 2
                if not frames:
                    raise LectureUtilError("The extracted WAV is truncated. Extract audio again.")
                if cursor + frames < total:
                    frames = _boundary(pcm, rate)
                offset, duration = cursor / rate, frames / rate
                chunk_identity = {**identity, "start_frame": cursor, "frames": frames}
                record_path = cache / f"{index:05d}.json"
                result = None if force else _read_chunk(record_path, chunk_identity, options, offset, duration)
                if result is None:
                    report(progress, "transcription", "update", f"Sending OpenAI chunk {index + 1}", phase="upload")
                    chunk = Path(temporary) / "chunk.wav"
                    with wave.open(str(chunk), "wb") as target:
                        target.setparams(source.getparams())
                        target.writeframes(pcm[:2 * frames])
                    if client is None:
                        client = _client()
                    response = _request(client, chunk, options)
                    result = _result(response, options, offset, duration)
                    atomic_write_json(record_path, {
                        "identity": chunk_identity, "response": response,
                        "sha256": hashlib.sha256(json.dumps(response, sort_keys=True).encode()).hexdigest(),
                    })
                segments.extend(result.segments)
                languages.append(result.language)
                cursor += frames
                index += 1
                report(progress, "transcription", "update", f"Completed OpenAI chunk {index}",
                       processed_seconds=cursor / rate, total_seconds=total / rate, phase="inference")
    except (wave.Error, EOFError, OSError):
        raise LectureUtilError("Could not read or prepare the OpenAI audio chunks. Check the extracted WAV and cache directory.") from None
    finally:
        if client is not None:
            client.close()
    language = max(dict.fromkeys(languages), key=languages.count)
    return Transcript(language, total / rate, "openai", options.selected_model, options.selected_model,
                      segments, requested_options=options.to_dict(),
                      effective_options={**options.to_dict(), "timestamps": options.selected_model == "whisper-1"})
