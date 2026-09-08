from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
from pathlib import Path
from time import monotonic
from urllib.parse import urlparse

from lecture_util.models import LectureSource
from lecture_util.progress import ProgressCallback, format_size, report
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


DOWNLOAD_PROGRESS_PREFIX = "lecture-util-download:"
POSTPROCESS_PREFIX = "lecture-util-postprocess:"


def run_download_command(
    argv: list[str], *, progress: ProgressCallback | None = None,
) -> None:
    """Drain merged output continuously and retain only a bounded diagnostic tail."""
    try:
        process = subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
    except OSError as error:
        raise CommandError(f"Could not start {argv[0]}: {error}") from error
    diagnostic = ""
    last_update = float("-inf")
    postprocessing = False
    try:
        assert process.stdout is not None
        for line in process.stdout:
            diagnostic = (diagnostic + line)[-2000:]
            if line.startswith(POSTPROCESS_PREFIX):
                if not postprocessing:
                    report(progress, "download", "update", "Finalizing downloaded video")
                    postprocessing = True
                continue
            if not line.startswith(DOWNLOAD_PROGRESS_PREFIX):
                continue
            try:
                payload = json.loads(line[len(DOWNLOAD_PROGRESS_PREFIX):])
                status = payload["status"]
                speed = payload.get("speed")
                if speed in (None, "NA"):
                    speed = None
                if speed is not None:
                    speed = float(speed)
                    if not math.isfinite(speed) or speed < 0:
                        continue
            except (ValueError, TypeError, KeyError, AttributeError):
                continue
            if status == "finished":
                if not postprocessing:
                    report(progress, "download", "update", "Finalizing downloaded video")
                    postprocessing = True
            elif status == "downloading":
                postprocessing = False
                now = monotonic()
                if now - last_update < 1:
                    continue
                last_update = now
                detail = "속도 계산 중" if speed is None else f"{format_size(int(speed))}/s"
                report(progress, "download", "update", f"Downloading · {detail}")
        returncode = process.wait()
        if returncode:
            raise CommandError(
                f"{argv[0]} exited with status {returncode}.\n{diagnostic.strip()}"
            )
    except BaseException:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        raise
    finally:
        if process.stdout is not None:
            process.stdout.close()


def download_hls(
    url: str, destination: Path, *, progress: ProgressCallback | None = None,
) -> None:
    validate_hls_url(url)
    yt_dlp = require_executable("yt-dlp")
    ffmpeg = require_executable("ffmpeg")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.stem}.download{destination.suffix}")
    if temporary.exists():
        temporary.unlink()
    try:
        run_download_command(
            [
                yt_dlp,
                "--newline",
                "--progress",
                "--progress-template",
                'download:lecture-util-download:{"status":%(progress.status)j,"speed":%(progress.speed)j}',
                "--progress-template",
                "postprocess:lecture-util-postprocess:%(progress.status)s",
                "--no-playlist",
                "--no-part",
                "--merge-output-format",
                "mp4",
                "--ffmpeg-location",
                ffmpeg,
                "--output",
                str(temporary),
                url,
            ],
            progress=progress,
        )
        if not temporary.exists():
            raise CommandError("yt-dlp completed without creating the expected video file.")
        temporary.replace(destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def extract_audio(video: Path, destination: Path) -> None:
    ffmpeg = require_executable("ffmpeg")
    if not video.is_file():
        raise LectureUtilError(f"Media file does not exist: {video}")
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


def resolve_source(value: str) -> LectureSource:
    """Validate a URL or inspect local streams without modifying the source."""
    if value.lower().startswith(("http://", "https://")):
        validate_hls_url(value)
        return LectureSource("hls", value)
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise LectureUtilError(f"Media file does not exist: {path}")
    try:
        with path.open("rb") as stream:
            fingerprint = hashlib.file_digest(stream, "sha256").hexdigest()
    except OSError as error:
        raise LectureUtilError(f"Cannot read media file: {path}: {error}") from error
    probe = require_executable("ffprobe")
    require_executable("ffmpeg")
    try:
        result = subprocess.run(
            [probe, "-v", "error", "-show_streams", "-of", "json", str(path)],
            capture_output=True, text=True, check=False,
        )
    except OSError as error:
        raise CommandError(f"Could not inspect media: {error}") from error
    if result.returncode:
        raise LectureUtilError(f"Cannot decode media file: {path}\n{result.stderr[-2000:]}")
    try:
        streams = json.loads(result.stdout)["streams"]
        if not any(item.get("codec_type") == "audio" for item in streams):
            raise LectureUtilError(f"Media file has no audio stream: {path}")
        video = any(
            item.get("codec_type") == "video"
            and not item.get("disposition", {}).get("attached_pic", 0)
            for item in streams
        )
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise LectureUtilError(f"Invalid media inspection result: {path}") from error
    return LectureSource("video" if video else "audio", str(path), fingerprint)
