from __future__ import annotations

import ctypes
import gc
import importlib
import json
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any

from lecture_util.errors import DependencyError, LectureUtilError
from lecture_util.models import Segment, Transcript
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
        path_or_hf_repo=MLX_MODELS.get(model, model),
        language=None if language == "auto" else language,
        verbose=None,
    )
    segments = [
        Segment(float(item["start"]), float(item["end"]), str(item["text"]).strip())
        for item in result.get("segments", [])
        if str(item.get("text", "")).strip()
    ]
    duration = max((segment.end for segment in segments), default=0.0)
    return segments, str(result.get("language", language)), duration


def _transcribe_faster(
    audio: Path, model: str, language: str, device: str
) -> tuple[list[Segment], str, float]:
    if device == "cuda":
        _prepare_cuda_libraries()
    faster_whisper = _module(
        "faster_whisper",
        "faster-whisper is not installed. Run 'uv sync' on Linux and install CUDA 12 with cuDNN 9.",
    )
    compute_type = "float16" if device == "cuda" else "int8"
    whisper_model = faster_whisper.WhisperModel(
        model,
        device=device,
        compute_type=compute_type,
    )
    raw_segments, info = whisper_model.transcribe(
        str(audio),
        language=None if language == "auto" else language,
        beam_size=5,
        vad_filter=True,
    )
    segments = [
        Segment(float(item.start), float(item.end), str(item.text).strip())
        for item in raw_segments
        if str(item.text).strip()
    ]
    duration = float(getattr(info, "duration", 0.0))
    return segments, str(getattr(info, "language", language)), duration


def is_out_of_memory(error: BaseException) -> bool:
    if isinstance(error, MemoryError):
        return True
    messages: list[str] = []
    current: BaseException | None = error
    while current is not None:
        messages.append(str(current).lower())
        current = current.__cause__ or current.__context__
    combined = " ".join(messages)
    return any(
        marker in combined
        for marker in (
            "out of memory",
            "cuda_error_out_of_memory",
            "cuda out of memory",
            "metal command buffer",
            "mps backend out of memory",
            "failed to allocate",
        )
    )


def _release_memory(device: str) -> None:
    gc.collect()
    if device == "mlx":
        try:
            mx = importlib.import_module("mlx.core")
            mx.clear_cache()
        except (ImportError, AttributeError):
            pass


def transcribe_audio(
    audio: Path,
    *,
    model: str = "large-v3",
    language: str = "auto",
    device: str = "auto",
) -> Transcript:
    if not audio.is_file():
        raise LectureUtilError(f"Audio file does not exist: {audio}")
    effective_device = detect_device(device)
    engine = engine_for_device(effective_device)

    # Import lazily: CLI startup and non-transcription commands need no Hub setup.
    from huggingface_hub.utils import disable_progress_bars

    def run(selected_model: str) -> tuple[list[Segment], str, float]:
        if effective_device == "mlx":
            return _transcribe_mlx(audio, selected_model, language)
        return _transcribe_faster(audio, selected_model, language, effective_device)

    fallback_reason: str | None = None
    effective_model = model
    # Hub's nested download bars compete with Rich Live for cursor control.
    # Suppress only progress bars, preserving warnings and backend exceptions.
    # The context restores progress on exit and respects explicit Hub settings.
    with disable_progress_bars():
        try:
            segments, detected_language, duration = run(model)
        except BaseException as error:
            if model != "large-v3" or not is_out_of_memory(error):
                raise
            fallback_reason = f"large-v3 ran out of memory: {error}"
            effective_model = "turbo"
            _release_memory(effective_device)
            segments, detected_language, duration = run(effective_model)

    return Transcript(
        language=detected_language,
        duration=duration,
        engine=engine,
        requested_model=model,
        effective_model=effective_model,
        fallback_reason=fallback_reason,
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
        return Transcript.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise LectureUtilError(f"Could not load transcript: {path}: {error}") from error


def restore_transcript_files(transcript: Transcript, markdown: Path, srt: Path) -> None:
    for path, render in ((markdown, transcript_markdown), (srt, transcript_srt)):
        if not path.exists():
            atomic_write_text(path, render(transcript))
