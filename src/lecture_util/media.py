from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from lecture_util.errors import CommandError, DependencyError, LectureUtilError


def require_executable(name: str) -> str:
    executable = shutil.which(name)
    if not executable:
        raise DependencyError(f"Required executable '{name}' was not found on PATH.")
    return executable


def validate_hls_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise LectureUtilError("The lecture URL must be an http(s) URL.")
    if not parsed.path.lower().endswith(".m3u8"):
        raise LectureUtilError("The lecture URL must point to an .m3u8 playlist.")


def run_command(argv: list[str]) -> None:
    try:
        result = subprocess.run(argv, capture_output=True, text=True, check=False)
    except OSError as error:
        raise CommandError(f"Could not start {argv[0]}: {error}") from error
    if result.returncode != 0:
        detail = (result.stderr.strip() or result.stdout.strip())[-2000:]
        suffix = f"\n{detail}" if detail else ""
        raise CommandError(f"{argv[0]} exited with status {result.returncode}.{suffix}")


def tool_version(name: str) -> str:
    executable = require_executable(name)
    flag = "-version" if name == "ffmpeg" else "--version"
    result = subprocess.run(
        [executable, flag],
        capture_output=True,
        text=True,
        check=False,
    )
    output = result.stdout.strip() or result.stderr.strip()
    return output.splitlines()[0] if output else "unknown"


def download_hls(url: str, destination: Path) -> None:
    validate_hls_url(url)
    yt_dlp = require_executable("yt-dlp")
    ffmpeg = require_executable("ffmpeg")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.stem}.download{destination.suffix}")
    if temporary.exists():
        temporary.unlink()
    run_command(
        [
            yt_dlp,
            "--no-playlist",
            "--no-part",
            "--merge-output-format",
            "mp4",
            "--ffmpeg-location",
            ffmpeg,
            "--output",
            str(temporary),
            url,
        ]
    )
    if not temporary.exists():
        raise CommandError("yt-dlp completed without creating the expected video file.")
    temporary.replace(destination)


def extract_audio(video: Path, destination: Path) -> None:
    ffmpeg = require_executable("ffmpeg")
    if not video.is_file():
        raise LectureUtilError(f"Video file does not exist: {video}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.stem}.extract{destination.suffix}")
    if temporary.exists():
        temporary.unlink()
    run_command(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-y",
            "-i",
            str(video),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            "-f",
            "wav",
            str(temporary),
        ]
    )
    if not temporary.exists():
        raise CommandError("ffmpeg completed without creating the expected audio file.")
    temporary.replace(destination)
