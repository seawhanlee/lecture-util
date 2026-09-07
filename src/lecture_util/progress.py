from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal


ProgressStatus = Literal["start", "update", "complete", "cached", "warning", "failed"]


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    stage: str
    status: ProgressStatus
    message: str
    processed_seconds: float | None = None
    total_seconds: float | None = None
    elapsed_seconds: float | None = None
    phase: str | None = None


ProgressCallback = Callable[[ProgressEvent], None]


def report(
    callback: ProgressCallback | None,
    stage: str,
    status: ProgressStatus,
    message: str,
    *, processed_seconds: float | None = None, total_seconds: float | None = None,
    elapsed_seconds: float | None = None, phase: str | None = None,
) -> None:
    if callback is not None:
        callback(ProgressEvent(stage=stage, status=status, message=message,
                               processed_seconds=processed_seconds, total_seconds=total_seconds,
                               elapsed_seconds=elapsed_seconds, phase=phase))


def format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, remaining = divmod(round(seconds), 60)
    if minutes < 60:
        return f"{minutes}m {remaining:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.1f} {unit}"
        value /= 1024
    raise AssertionError("unreachable")
