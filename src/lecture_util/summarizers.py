from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from lecture_util.errors import DependencyError, LectureUtilError


class Summarizer(Protocol):
    name: str
    model: str | None

    def generate(self, developer_prompt: str, user_prompt: str) -> str: ...


def _require_output(content: str | None, backend: str) -> str:
    if content and content.strip():
        return content.strip()
    raise LectureUtilError(f"{backend} returned an empty response.")


def _require_cli(name: str) -> str:
    executable = shutil.which(name)
    if not executable:
        raise DependencyError(f"The {name} CLI was not found on PATH.")
    return executable


def _agent_prompt(developer_prompt: str, user_prompt: str) -> str:
    return (
        f"{developer_prompt}\n\n"
        "Do not use tools and do not modify any files. Return only the requested Markdown.\n\n"
        f"{user_prompt}"
    )


@dataclass(slots=True)
class CodexSummarizer:
    model: str | None = None
    name: str = "codex"

    def generate(self, developer_prompt: str, user_prompt: str) -> str:
        codex = _require_cli("codex")
        with tempfile.TemporaryDirectory(prefix="lecture-util-codex-") as directory:
            workspace = Path(directory)
            output = workspace / "response.md"
            argv = [
                codex,
                "exec",
                "--sandbox",
                "read-only",
                "--ephemeral",
                "--skip-git-repo-check",
                "--color",
                "never",
                "--cd",
                str(workspace),
                "--output-last-message",
                str(output),
            ]
            if self.model:
                argv.extend(["--model", self.model])
            argv.append("-")
            result = subprocess.run(
                argv,
                input=_agent_prompt(developer_prompt, user_prompt),
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                detail = result.stderr.strip()[-2000:] or f"exit status {result.returncode}"
                raise LectureUtilError(f"Codex summarization failed: {detail}")
            try:
                content = output.read_text(encoding="utf-8")
            except OSError as error:
                raise LectureUtilError(f"Codex did not create its response file: {error}") from error
        return _require_output(content, self.name)
