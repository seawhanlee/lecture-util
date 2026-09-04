from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lecture_util.models import LecturePaths


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def lecture_id(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:10]


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


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
            self.data: dict[str, Any] = json.loads(paths.state.read_text(encoding="utf-8"))
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
    title: str | None = None,
    course: str | None = None,
    lecture_date: str | None = None,
    published_summary: Path | None = None,
    published_transcript: Path | None = None,
    tags: list[str] | None = None,
) -> tuple[LecturePaths, RunState]:
    paths = LecturePaths(output_dir / f"lecture-{lecture_id(url)}")
    paths.root.mkdir(parents=True, exist_ok=True)
    return paths, RunState(
        paths,
        url=url,
        title=title,
        course=course,
        lecture_date=lecture_date,
        published_summary=published_summary,
        published_transcript=published_transcript,
        tags=tags,
    )
