"""Desktop notifications and terminal alerts for completed or failed runs."""
from __future__ import annotations

import shutil
import subprocess
import sys
from typing import Any


def notify(title: str, message: str) -> bool:
    """Send a desktop notification; fall back to the terminal bell.

    Returns True if an OS desktop notification command succeeded, False otherwise.
    Never raises an exception.
    """
    cmd = _backend_command(title, message)
    if cmd is not None:
        try:
            subprocess.run(
                cmd,
                check=True,
                timeout=5,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except (OSError, subprocess.SubprocessError):
            pass
    _bell()
    return False


def _backend_command(title: str, message: str) -> list[str] | None:
    if sys.platform == "darwin" and shutil.which("osascript"):
        return [
            "osascript",
            "-e",
            "on run argv",
            "-e",
            "display notification (item 2 of argv) with title (item 1 of argv)",
            "-e",
            "end run",
            title,
            message,
        ]
    if sys.platform.startswith("linux") and shutil.which("notify-send"):
        return ["notify-send", "--app-name=lecture-util", title, message]
    return None


def _bell() -> None:
    try:
        if sys.stdout.isatty():
            sys.stdout.write("\a")
            sys.stdout.flush()
    except OSError:
        pass


def run_succeeded_message(label: str, elapsed_seconds: float) -> str:
    return f"✓ {label} ({_format_duration(elapsed_seconds)})"


def run_failed_message(label: str, error: Any) -> str:
    detail = str(error).strip().splitlines()
    error_summary = detail[0] if detail else "unknown error"
    return f"✗ {label} failed: {error_summary}"


def _format_duration(seconds: float) -> str:
    total_seconds = int(round(seconds))
    minutes, secs = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours}h {minutes}m {secs}s"
    if minutes > 0:
        return f"{minutes}m {secs}s"
    return f"{secs}s"
