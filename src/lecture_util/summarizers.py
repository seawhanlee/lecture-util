from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from lecture_util.errors import DependencyError, LectureUtilError

if TYPE_CHECKING:
    from lecture_util.materials import MaterialContext


class Summarizer(Protocol):
    name: str
    model: str | None

    def generate(
        self,
        developer_prompt: str,
        user_prompt: str,
        transcript_path: Path,
        *,
        materials: MaterialContext | None = None,
    ) -> str: ...


def _require_output(content: str | None, backend: str) -> str:
    if content and content.strip():
        return content.strip()
    raise LectureUtilError(f"{backend} returned an empty response.")


def _require_cli(name: str) -> str:
    executable = shutil.which(name)
    if not executable:
        raise DependencyError(f"The {name} CLI was not found on PATH.")
    return executable


def _agent_prompt(developer_prompt: str, user_prompt: str, transcript_name: str) -> str:
    return (
        f"{developer_prompt}\n\n"
        f"The lecture transcript is attached as the local file `{transcript_name}`. "
        "Read that file, do not modify any files, and return only the requested Obsidian Flavored Markdown.\n\n"
        f"{user_prompt}"
    )


@dataclass(slots=True)
class CodexSummarizer:
    model: str | None = None
    name: str = "codex"
    reasoning_effort: str | None = None

    def generate(
        self,
        developer_prompt: str,
        user_prompt: str,
        transcript_path: Path,
        *,
        materials: MaterialContext | None = None,
    ) -> str:
        if materials is not None:
            if not transcript_path.is_file():
                raise LectureUtilError(f"Transcript file does not exist: {transcript_path}")
            prompt = (
                f"{developer_prompt}\n\nRead the entire lecture transcript at "
                + json.dumps(str(transcript_path.resolve()), ensure_ascii=False)
                + ". Read the selected material pages and use their diagrams and formulas as evidence. "
                "Do not modify the transcript or original PDFs. Return only the requested Markdown.\n"
                + materials.prompt() + "\n\n" + user_prompt
            )
            return self._request(prompt)
        codex = _require_cli("codex")
        if not transcript_path.is_file():
            raise LectureUtilError(f"Transcript file does not exist: {transcript_path}")
        transcript_path = transcript_path.resolve()
        with tempfile.TemporaryDirectory(prefix="lecture-util-codex-") as directory:
            output = Path(directory) / "response.md"
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
                str(transcript_path.parent),
                "--output-last-message",
                str(output),
            ]
            if self.model:
                argv.extend(["--model", self.model])
            if self.reasoning_effort:
                argv.extend([
                    "--config",
                    f"model_reasoning_effort={json.dumps(self.reasoning_effort)}",
                ])
            argv.append("-")
            result = subprocess.run(
                argv,
                input=_agent_prompt(developer_prompt, user_prompt, transcript_path.name),
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

    def generate_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        content = self._request(prompt, schema=schema)
        try:
            data = json.loads(content)
            if not isinstance(data, dict):
                raise ValueError("expected an object")
            return data
        except ValueError as error:
            raise LectureUtilError(f"Codex returned invalid material JSON: {error}") from error

    def _request(self, prompt: str, *, schema: dict[str, Any] | None = None) -> str:
        """Material reads may render PDFs only in a disposable workspace."""
        codex = _require_cli("codex")
        with tempfile.TemporaryDirectory(prefix="lecture-util-materials-") as directory:
            root = Path(directory)
            output = root / "response.txt"
            argv = [codex, "exec", "--sandbox", "workspace-write", "--ephemeral",
                    "--skip-git-repo-check", "--color", "never", "--cd", str(root),
                    "--config", "sandbox_workspace_write.network_access=false",
                    "--config", "sandbox_workspace_write.writable_roots=[]",
                    "--config", "sandbox_workspace_write.exclude_slash_tmp=true",
                    "--config", "sandbox_workspace_write.exclude_tmpdir_env_var=true",
                    "--output-last-message", str(output)]
            if self.model:
                argv.extend(["--model", self.model])
            if self.reasoning_effort:
                argv.extend(["--config", f"model_reasoning_effort={json.dumps(self.reasoning_effort)}"])
            if schema is not None:
                schema_path = root / "schema.json"
                schema_path.write_text(json.dumps(schema), encoding="utf-8")
                argv.extend(["--output-schema", str(schema_path)])
            argv.append("-")
            process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True, start_new_session=True)
            try:
                _, stderr = process.communicate(prompt)
            except BaseException:
                # Codex can have PDF helper children; cancelling must reap the entire group.
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.communicate()
                raise
            if process.returncode != 0:
                raise LectureUtilError(f"Codex material processing failed: {stderr.strip()[-2000:]}")
            try:
                return _require_output(output.read_text(encoding="utf-8"), self.name)
            except OSError as error:
                raise LectureUtilError(f"Codex did not create its response file: {error}") from error
