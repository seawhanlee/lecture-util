from __future__ import annotations

import hashlib
import json
import os
import tempfile
import fcntl
from contextlib import contextmanager
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lecture_util.errors import LectureUtilError
from lecture_util.models import LecturePaths, LectureSource


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def lecture_id(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:10]


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def file_digest(path: Path) -> str:
    try:
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()
    except OSError as error:
        raise LectureUtilError(f"Could not read artifact {path}: {error}") from error


@contextmanager
def workspace_lock(root: Path) -> Iterator[None]:
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".lock").open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise LectureUtilError(f"Another process is using {root}.") from error
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def atomic_write_json(path: Path, data: Any) -> None:
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


class RunState:
    def __init__(
        self,
        paths: LecturePaths,
        *,
        url: str | None = None,
        title: str | None = None,
        course: str | None = None,
        lecture_date: str | None = None,
        published_summary: Path | None = None,
        published_transcript: Path | None = None,
        tags: list[str] | None = None,
    ) -> None:
        self.paths = paths
        if paths.state.exists():
            try:
                self.data: dict[str, Any] = json.loads(paths.state.read_text(encoding="utf-8"))
                if (not isinstance(self.data, dict)
                        or not isinstance(self.data.get("stages"), dict)
                        or any(not isinstance(stage, dict)
                               for stage in self.data["stages"].values())):
                    raise ValueError("invalid state structure")
            except (OSError, ValueError) as error:
                raise LectureUtilError(
                    f"Could not load state {paths.state}: {error}. "
                    "Restore a valid run.json backup before retrying."
                ) from error
        else:
            self.data = {
                "version": 1,
                "url": url,
                "title": title,
                "tags": tags or [],
                "created_at": utc_now(),
                "updated_at": utc_now(),
                "stages": {},
            }
        if url is not None:
            self.data["url"] = url
        if title is not None:
            self.data["title"] = title
        if course is not None:
            self.data["course"] = course
        if lecture_date is not None:
            self.data["lecture_date"] = lecture_date
        if published_summary is not None:
            self.data["published_summary"] = str(published_summary)
        if published_transcript is not None:
            self.data["published_transcript"] = str(published_transcript)
        if tags is not None:
            self.data["tags"] = tags
        self.save()

    @property
    def url(self) -> str:
        value = self.data.get("url")
        if not value:
            raise ValueError(f"Missing URL in {self.paths.state}")
        return str(value)

    def save(self) -> None:
        self.data["updated_at"] = utc_now()
        atomic_write_json(self.paths.state, self.data)

    def stage_complete(self, name: str) -> bool:
        return self.data.get("stages", {}).get(name, {}).get("status") == "complete"

    def start_stage(self, name: str, **details: Any) -> None:
        stages = ("download", "audio", "transcription", "summary")
        if name in stages:
            for dependent in stages[stages.index(name) + 1:]:
                if dependent in self.data["stages"]:
                    self.data["stages"][dependent]["status"] = "stale"
        self.data.setdefault("stages", {})[name] = {
            "status": "running",
            "started_at": utc_now(),
            **details,
        }
        self.save()

    def complete_stage(self, name: str, **details: Any) -> None:
        previous = self.data.setdefault("stages", {}).get(name, {})
        self.data["stages"][name] = {
            **previous,
            "status": "complete",
            "completed_at": utc_now(),
            "error": None,
            **details,
        }
        self.save()

    def fail_stage(self, name: str, error: BaseException) -> None:
        previous = self.data.setdefault("stages", {}).get(name, {})
        self.data["stages"][name] = {
            **previous,
            "status": "failed",
            "failed_at": utc_now(),
            "error": str(error),
        }
        self.save()


def create_workspace(
    url: str,
    output_dir: Path,
    *,
    video_path: Path | None = None,
    source: LectureSource | None = None,
    title: str | None = None,
    course: str | None = None,
    lecture_date: str | None = None,
    published_summary: Path | None = None,
    published_transcript: Path | None = None,
    tags: list[str] | None = None,
) -> tuple[LecturePaths, RunState]:
    paths = LecturePaths(
        output_dir / f"lecture-{lecture_id(source.cache_key if source else url)}",
        video_path=video_path,
    )
    paths.root.mkdir(parents=True, exist_ok=True)
    state = RunState(
        paths,
        url=url,
        title=title,
        course=course,
        lecture_date=lecture_date,
        published_summary=published_summary,
        published_transcript=published_transcript,
        tags=tags,
    )

    if source is not None:
        state.data["source"] = {
            "kind": source.kind, "location": source.location,
            "content_hash": source.content_hash,
        }
        state.save()
    return paths, state

@contextmanager
def directory_lock(path: Path) -> Iterator[None]:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    except OSError as error:
        raise LectureUtilError(f"Lecture directory unavailable: {path}") from error
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise LectureUtilError(f"Another publication is using {path}.") from error
        yield
    finally:
        os.close(descriptor)
