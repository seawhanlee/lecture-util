from __future__ import annotations

from pathlib import Path

from lecture_util.errors import LectureUtilError
from lecture_util.media import validate_hls_url
from lecture_util.summary import DEFAULT_PROMPT


def normalize_tags(values: list[str] | None) -> list[str] | None:
    if values is None:
        return None
    normalized: list[str] = []
    for value in values:
        for candidate in value.split(","):
            tag = candidate.strip()
            if tag and tag not in normalized:
                normalized.append(tag)
    return normalized


def read_urls(url: str | None, input_file: Path | None) -> list[str]:
    if (url is None) == (input_file is None):
        raise LectureUtilError("Provide exactly one URL or --input URL_LIST_FILE.")
    if url is not None:
        validate_hls_url(url)
        return [url]
    assert input_file is not None
    try:
        lines = input_file.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise LectureUtilError(f"Could not read URL list {input_file}: {error}") from error
    urls = [
        line.strip()
        for line in lines
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not urls:
        raise LectureUtilError("The URL list does not contain any lecture URLs.")
    for lecture_url in urls:
        validate_hls_url(lecture_url)
    return urls


def resolve_prompt(prompt: str | None, prompt_file: Path | None) -> str:
    if prompt is not None and prompt_file is not None:
        raise LectureUtilError("--prompt and --prompt-file cannot be used together.")
    if prompt_file is not None:
        try:
            return prompt_file.read_text(encoding="utf-8")
        except OSError as error:
            raise LectureUtilError(f"Could not read prompt file {prompt_file}: {error}") from error
    return prompt if prompt is not None else DEFAULT_PROMPT
