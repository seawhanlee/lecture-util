from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx

from lecture_util.errors import DependencyError, LectureUtilError


class Summarizer(Protocol):
    name: str
    model: str | None

    def generate(self, developer_prompt: str, user_prompt: str) -> str: ...


def _require_output(content: str | None, backend: str) -> str:
    if content and content.strip():
        return content.strip()
    raise LectureUtilError(f"{backend} returned an empty response.")


@dataclass(slots=True)
class OpenAIChatSummarizer:
    model: str
    base_url: str = "https://api.openai.com/v1"
    api_key_env: str = "OPENAI_API_KEY"
    name: str = "openai"

    def generate(self, developer_prompt: str, user_prompt: str) -> str:
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise LectureUtilError(
                f"Environment variable {self.api_key_env} is not set for the OpenAI-compatible API."
            )
        try:
            from openai import OpenAI

            client = OpenAI(api_key=api_key, base_url=self.base_url, timeout=600)
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "developer", "content": developer_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
        except Exception as error:
            raise LectureUtilError(f"OpenAI-compatible summarization failed: {error}") from error
        return _require_output(response.choices[0].message.content, self.name)


@dataclass(slots=True)
class OllamaSummarizer:
    model: str
    base_url: str = "http://localhost:11434"
    name: str = "ollama"

    @property
    def chat_url(self) -> str:
        base = self.base_url.rstrip("/")
        return f"{base}/chat" if base.endswith("/api") else f"{base}/api/chat"

    def generate(self, developer_prompt: str, user_prompt: str) -> str:
        try:
            response = httpx.post(
                self.chat_url,
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": developer_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "stream": False,
                },
                timeout=600,
            )
            response.raise_for_status()
            content = response.json()["message"]["content"]
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
            raise LectureUtilError(f"Ollama summarization failed: {error}") from error
        return _require_output(str(content), self.name)


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


def parse_opencode_jsonl(output: str) -> str:
    parts: list[str] = []
    for line in output.splitlines():
        try:
            event: dict[str, Any] = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        event_type = str(event.get("type", ""))
        part = event.get("part")
        if event_type == "text" and isinstance(part, dict) and isinstance(part.get("text"), str):
            parts.append(part["text"])
        elif event_type in {"message", "assistant"}:
            message = event.get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                parts.append(message["content"])
    return "".join(parts).strip()


@dataclass(slots=True)
class OpenCodeSummarizer:
    model: str | None = None
    name: str = "opencode"

    def generate(self, developer_prompt: str, user_prompt: str) -> str:
        opencode = _require_cli("opencode")
        with tempfile.TemporaryDirectory(prefix="lecture-util-opencode-") as directory:
            argv = [opencode, "run", "--format", "json", "--dir", directory]
            if self.model:
                argv.extend(["--model", self.model])
            result = subprocess.run(
                argv,
                input=_agent_prompt(developer_prompt, user_prompt),
                capture_output=True,
                text=True,
                check=False,
            )
        if result.returncode != 0:
            detail = result.stderr.strip()[-2000:] or f"exit status {result.returncode}"
            raise LectureUtilError(f"OpenCode summarization failed: {detail}")
        return _require_output(parse_opencode_jsonl(result.stdout), self.name)


def create_summarizer(
    backend: str,
    *,
    model: str | None,
    base_url: str | None = None,
    api_key_env: str = "OPENAI_API_KEY",
) -> Summarizer:
    if backend == "openai":
        if not model:
            raise LectureUtilError("--llm-model is required for the OpenAI-compatible backend.")
        if not os.environ.get(api_key_env):
            raise LectureUtilError(
                f"Environment variable {api_key_env} is not set for the OpenAI-compatible API."
            )
        return OpenAIChatSummarizer(
            model=model,
            base_url=base_url or "https://api.openai.com/v1",
            api_key_env=api_key_env,
        )
    if backend == "ollama":
        if not model:
            raise LectureUtilError("--llm-model is required for the Ollama backend.")
        return OllamaSummarizer(model=model, base_url=base_url or "http://localhost:11434")
    if backend == "codex":
        return CodexSummarizer(model=model)
    if backend == "opencode":
        return OpenCodeSummarizer(model=model)
    raise LectureUtilError(f"Unsupported summarizer: {backend}")
