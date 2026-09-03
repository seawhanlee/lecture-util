from __future__ import annotations

import importlib.util
import platform
import shutil
import subprocess
from dataclasses import dataclass


@dataclass(slots=True)
class Check:
    name: str
    ok: bool
    detail: str


def run_checks() -> list[Check]:
    checks: list[Check] = []
    for executable in ("yt-dlp", "ffmpeg", "codex"):
        path = shutil.which(executable)
        checks.append(Check(executable, path is not None, path or "not found on PATH"))

    system = platform.system()
    machine = platform.machine().lower()
    checks.append(Check("platform", True, f"{system} {machine}"))
    if system == "Darwin" and machine == "arm64":
        available = importlib.util.find_spec("mlx_whisper") is not None
        checks.append(Check("mlx-whisper", available, "installed" if available else "run: uv sync"))
    elif system == "Linux" and machine in {"x86_64", "amd64"}:
        available = importlib.util.find_spec("faster_whisper") is not None
        checks.append(Check("faster-whisper", available, "installed" if available else "run: uv sync"))
        nvidia_smi = shutil.which("nvidia-smi")
        if nvidia_smi:
            probe = subprocess.run(
                [nvidia_smi, "--query-gpu=name,driver_version", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                check=False,
            )
            checks.append(Check("NVIDIA GPU", probe.returncode == 0, probe.stdout.strip() or probe.stderr.strip()))
        else:
            checks.append(Check("NVIDIA GPU", False, "nvidia-smi not found; CPU requires --device cpu"))
    else:
        checks.append(Check("accelerator", False, "unsupported auto-detection platform"))
    return checks
