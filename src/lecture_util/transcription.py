from __future__ import annotations

import ctypes
import gc
import importlib
import json
import platform
import math
import wave
import shutil
import subprocess
from dataclasses import replace
from time import monotonic
from pathlib import Path
from typing import Any

from lecture_util.errors import DependencyError, LectureUtilError
from lecture_util.models import Segment, Transcript, TranscriptionOptions
from lecture_util.progress import ProgressCallback, report
from lecture_util.state import atomic_write_json, atomic_write_text


MLX_MODELS = {
    "large-v3": "mlx-community/whisper-large-v3-mlx",
    "turbo": "mlx-community/whisper-large-v3-turbo",
}

_CUDA_LIBRARY_HANDLES: list[Any] = []
_CUDA_LIBRARIES = (
    ("nvidia.cublas", "libcublasLt.so.12"),
    ("nvidia.cublas", "libcublas.so.12"),
    ("nvidia.cudnn", "libcudnn.so.9"),
)


def detect_device(requested: str = "auto") -> str:
    if requested not in {"auto", "mlx", "cuda", "cpu"}:
        raise LectureUtilError(f"Unsupported device: {requested}")
    system = platform.system()
    machine = platform.machine().lower()
    if requested == "cpu":
        return requested
    if requested == "mlx":
        if system != "Darwin" or machine != "arm64":
            raise DependencyError("The MLX device requires Apple Silicon macOS.")
        return requested
    if requested == "cuda":
        if system != "Linux" or machine not in {"x86_64", "amd64"}:
            raise DependencyError("The CUDA device requires x86_64 Linux with an NVIDIA GPU.")
        _require_nvidia_gpu()
        return requested
    if system == "Darwin" and machine == "arm64":
        return "mlx"
    if system == "Linux" and machine in {"x86_64", "amd64"}:
        _require_nvidia_gpu()
        return "cuda"
    raise DependencyError(
        "Automatic acceleration is supported only on Apple Silicon macOS and NVIDIA x86_64 Linux. "
        "Pass --device cpu explicitly to use the CPU."
    )


def _require_nvidia_gpu() -> None:
    if not shutil.which("nvidia-smi"):
        raise DependencyError(
            "NVIDIA GPU was not detected. Install the NVIDIA driver or pass --device cpu explicitly."
        )
    probe = subprocess.run(
        ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0 or not probe.stdout.strip():
        raise DependencyError(
            "NVIDIA GPU is not available. Fix the driver/runtime or pass --device cpu explicitly."
        )


def engine_for_device(device: str) -> str:
    return "mlx-whisper" if device == "mlx" else "faster-whisper"


def _module(name: str, installation_hint: str) -> Any:
    try:
        return importlib.import_module(name)
    except ImportError as error:
        raise DependencyError(installation_hint) from error


def _bundled_library_path(package: str, filename: str) -> Path:
    module = _module(
        package,
        "CUDA runtime libraries are not installed. Run 'uv sync' on NVIDIA Linux.",
    )
    for package_path in getattr(module, "__path__", ()):
        candidate = Path(package_path) / "lib" / filename
        if candidate.is_file():
            return candidate
    raise DependencyError(
        f"CUDA runtime library {filename} was not found. Run 'uv sync' on NVIDIA Linux."
    )


def _prepare_cuda_libraries() -> None:
    """Make NVIDIA wheel libraries visible to CTranslate2's dynamic loader."""
    if _CUDA_LIBRARY_HANDLES:
        return

    handles: list[Any] = []
    for package, filename in _CUDA_LIBRARIES:
        path = _bundled_library_path(package, filename)
        try:
            handles.append(ctypes.CDLL(str(path), mode=ctypes.RTLD_GLOBAL))
        except OSError as error:
            raise DependencyError(
                f"Could not load CUDA runtime library {path}: {error}"
            ) from error
    _CUDA_LIBRARY_HANDLES.extend(handles)


def _transcribe_mlx(audio: Path, model: str, language: str) -> tuple[list[Segment], str, float]:
    mlx_whisper = _module(
        "mlx_whisper",
        "mlx-whisper is not installed. Run 'uv sync' on an Apple Silicon Mac.",
    )
    result = mlx_whisper.transcribe(
        str(audio),
        path_or_hf_repo=MLX_MODELS.get(model, str(Path(model).expanduser()) if model.startswith("~") else model),
        language=None if language == "auto" else language,
        verbose=None,
    )
    segments = [
        Segment(float(item["start"]), float(item["end"]), str(item["text"]).strip())
        for item in result.get("segments", [])
        if str(item.get("text", "")).strip()
    ]
    try:
        with wave.open(str(audio), "rb") as stream:
            duration = stream.getnframes() / stream.getframerate()
    except (OSError, wave.Error, EOFError):
        duration = max((segment.end for segment in segments), default=0.0)
    _release_memory("mlx")
    return segments, str(result.get("language", language)), duration


def _transcribe_faster(
    audio: Path, model: str, language: str, device: str,
    *, compute_type: str = "auto", batch_size: int = 0, beam_size: int | None = None,
    progress: ProgressCallback | None = None,
) -> tuple[list[Segment], str, float]:
    if device == "cuda":
        _prepare_cuda_libraries()
    faster_whisper = _module(
        "faster_whisper",
        "faster-whisper is not installed. Run 'uv sync' on Linux and install CUDA 12 with cuDNN 9.",
    )
    compute_type = effective_compute_type(device, compute_type)
    report(progress, "transcription", "update", "Preparing model (download/loading)", phase="loading")
    whisper_model = None
    runner = None
    raw_segments = None
    try:
        whisper_model = faster_whisper.WhisperModel(
            str(Path(model).expanduser()) if model.startswith("~") else model,
            device=device, compute_type=compute_type,
        )
        runner = (faster_whisper.BatchedInferencePipeline(model=whisper_model)
                  if batch_size else whisper_model)
        kwargs = {"batch_size": batch_size} if batch_size else {}
        report(progress, "transcription", "update", "Transcribing audio", phase="inference")
        started = monotonic()
        raw_segments, info = runner.transcribe(
            str(audio), language=None if language == "auto" else language,
            beam_size=beam_size or 5, vad_filter=True, **kwargs,
        )
        duration = float(getattr(info, "duration", 0.0))
        segments = []
        position = 0.0
        for item in raw_segments:
            if str(item.text).strip():
                segments.append(Segment(float(item.start), float(item.end), str(item.text).strip()))
            position = max(position, min(float(item.end), duration))
            report(progress, "transcription", "update", "Transcribing audio (estimated position)",
                   processed_seconds=position, total_seconds=duration,
                   elapsed_seconds=monotonic() - started, phase="inference")
        return segments, str(getattr(info, "language", language)), duration
    finally:
        if raw_segments is not None and hasattr(raw_segments, "close"):
            raw_segments.close()
        raw_segments = None
        runner = None
        whisper_model = None


def effective_compute_type(device: str, requested: str) -> str:
    if requested != "auto":
        return requested
    return "float16" if device in {"cuda", "mlx"} else "int8"


def preflight_transcription(options: TranscriptionOptions) -> str:
    from lecture_util.configuration import validate_transcription_options
    validate_transcription_options(options)
    device = detect_device(options.device)
    validate_transcription_options(replace(options, device=device))
    if device == "mlx":
        _module("mlx_whisper", "mlx-whisper is not installed. Run uv sync on Apple Silicon.")
    else:
        if device == "cuda":
            _prepare_cuda_libraries()
        _module("faster_whisper", "faster-whisper is not installed for this Python environment.")
        ct = _module("ctranslate2", "CTranslate2 is not installed.")
        compute = effective_compute_type(device, options.compute_type)
        try:
            supported = ct.get_supported_compute_types(device)
        except (RuntimeError, ValueError) as error:
            raise DependencyError(f"Could not initialize {device}: {error}") from error
        if compute not in supported:
            raise DependencyError(f"Compute type {compute} is unsupported on {device}; supported: {', '.join(sorted(supported))}")
    return device


def is_out_of_memory(error: BaseException) -> bool:
    if isinstance(error, MemoryError):
        return True
    messages: list[str] = []
    current: BaseException | None = error
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        messages.append(str(current).lower())
        current = current.__cause__ or current.__context__
    combined = " ".join(messages)
    return any(
        marker in combined
        for marker in (
            "out of memory",
            "cuda_error_out_of_memory",
            "cuda out of memory",
            "mps backend out of memory",
            "failed to allocate",
        )
    )


def _release_memory(device: str) -> None:
    if device == "mlx":
        try:
            holder = importlib.import_module("mlx_whisper.transcribe").ModelHolder
            holder.model = None
            holder.model_path = None
            gc.collect()
            mx = importlib.import_module("mlx.core")
            mx.clear_cache()
        except (ImportError, AttributeError):
            pass
    gc.collect()


def transcribe_audio(
    audio: Path,
    *,
    model: str = "large-v3",
    language: str = "auto",
    device: str = "auto",
    compute_type: str = "auto", batch_size: int = 0, beam_size: int | None = None,
    progress: ProgressCallback | None = None,
) -> Transcript:
    if not audio.is_file():
        raise LectureUtilError(f"Audio file does not exist: {audio}")
    from lecture_util.configuration import validate_transcription_options
    options = TranscriptionOptions(model, language, device, compute_type, batch_size, beam_size)
    validate_transcription_options(options)
    effective_device = detect_device(device)
    validate_transcription_options(replace(options, device=effective_device))
    engine = engine_for_device(effective_device)

    # Import lazily: CLI startup and non-transcription commands need no Hub setup.
    from huggingface_hub.utils import disable_progress_bars

    def run(selected_model: str) -> tuple[list[Segment], str, float]:
        if effective_device == "mlx":
            report(progress, "transcription", "update", "MLX model preparation and transcription", phase="inference")
            return _transcribe_mlx(audio, selected_model, language)
        kwargs = {}
        if compute_type != "auto" or batch_size or beam_size is not None or progress is not None:
            kwargs = dict(compute_type=compute_type, batch_size=batch_size,
                          beam_size=beam_size, progress=progress)
        return _transcribe_faster(audio, selected_model, language, effective_device, **kwargs)

    fallback_reason: str | None = None
    effective_model = model
    # Hub's nested download bars compete with Rich Live for cursor control.
    # Suppress only progress bars, preserving warnings and backend exceptions.
    # The context restores progress on exit and respects explicit Hub settings.
    with disable_progress_bars():
        try:
            segments, detected_language, duration = run(model)
        except Exception as error:
            if model != "large-v3" or not is_out_of_memory(error):
                raise
            fallback_reason = f"large-v3 ran out of memory: {error}"
            effective_model = "turbo"
        if fallback_reason is not None:
            # The except target and its traceback are gone before allocating the retry.
            _release_memory(effective_device)
            report(progress, "transcription", "warning", "large-v3 exhausted memory; retrying with turbo")
            segments, detected_language, duration = run(effective_model)

    return Transcript(
        language=detected_language,
        duration=duration,
        engine=engine,
        requested_model=model,
        effective_model=effective_model,
        fallback_reason=fallback_reason,
        requested_options=options.to_dict(),
        effective_options={**options.to_dict(), "model": effective_model,
                           "device": effective_device,
                           "compute_type": effective_compute_type(effective_device, compute_type),
                           "beam_size": (beam_size or 5) if effective_device != "mlx" else None},
        segments=segments,
    )


def format_timestamp(seconds: float, *, srt: bool = False) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    separator = "," if srt else ":"
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{separator}{millis:03d}"


def transcript_markdown(transcript: Transcript) -> str:
    lines = ["# Transcript", ""]
    for segment in transcript.segments:
        lines.append(
            f"[{format_timestamp(segment.start)}–{format_timestamp(segment.end)}] {segment.text}"
        )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def transcript_srt(transcript: Transcript) -> str:
    blocks = []
    for index, segment in enumerate(transcript.segments, 1):
        blocks.append(
            "\n".join(
                [
                    str(index),
                    f"{format_timestamp(segment.start, srt=True)} --> "
                    f"{format_timestamp(segment.end, srt=True)}",
                    segment.text,
                ]
            )
        )
    return "\n\n".join(blocks).rstrip() + "\n"


def save_transcript(transcript: Transcript, json_path: Path, markdown_path: Path, srt_path: Path) -> None:
    atomic_write_json(json_path, transcript.to_dict())
    atomic_write_text(markdown_path, transcript_markdown(transcript))
    atomic_write_text(srt_path, transcript_srt(transcript))


def load_transcript(path: Path) -> Transcript:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("segments"), list):
            raise ValueError("expected a transcript object with a segments list")
        transcript = Transcript.from_dict(data)
        if not math.isfinite(transcript.duration) or transcript.duration < 0:
            raise ValueError("invalid transcript duration")
        if any(not math.isfinite(segment.start) or not math.isfinite(segment.end)
               or not 0 <= segment.start <= segment.end for segment in transcript.segments):
            raise ValueError("invalid segment timestamps")
        return transcript
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise LectureUtilError(f"Could not load transcript: {path}: {error}") from error


def restore_transcript_files(transcript: Transcript, markdown: Path, srt: Path) -> None:
    for path, render in ((markdown, transcript_markdown), (srt, transcript_srt)):
        if not path.exists():
            atomic_write_text(path, render(transcript))
