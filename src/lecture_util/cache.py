"""Cache and storage management for lecture-util workspaces."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from lecture_util.errors import LectureUtilError
from lecture_util.state import clear_workspace, clear_workspace_audio, is_workspace_locked
from lecture_util.vault import default_cache_root


@dataclass(frozen=True, slots=True)
class CachedLecture:
    path: Path
    lecture_id: str
    url: str | None
    title: str | None
    course: str | None
    lecture_date: str | None
    updated_at: datetime | None
    status: str
    audio_size: int
    total_size: int
    is_published: bool
    is_locked: bool

    @property
    def display_status(self) -> str:
        if self.is_locked:
            return "[bold cyan]Running[/bold cyan]"
        if self.is_published:
            return "[green]Published[/green]"
        match self.status:
            case "summarized":
                return "[cyan]Summarized[/cyan]"
            case "transcribed":
                return "[blue]Transcribed[/blue]"
            case "audio_ready":
                return "[yellow]Audio Ready[/yellow]"
            case "downloaded":
                return "[yellow]Downloaded[/yellow]"
            case "failed":
                return "[red]Failed[/red]"
            case "interrupted":
                return "[magenta]Interrupted[/magenta]"
            case "corrupt":
                return "[dim red]Corrupt[/dim red]"
            case _:
                return "[dim]Unknown[/dim]"


@dataclass(frozen=True, slots=True)
class StorageSummary:
    cache_root: Path
    total_lectures: int
    published_lectures: int
    total_cache_size: int
    total_audio_size: int
    video_root: Path | None = None
    video_count: int = 0
    total_video_size: int = 0
    materials_cache_size: int = 0


@dataclass(frozen=True, slots=True)
class PruneResult:
    reclaimed_bytes: int
    workspaces_pruned: int
    audio_files_pruned: int
    skipped_locked: int


def get_directory_size(path: Path) -> int:
    total = 0
    try:
        for entry in path.rglob("*"):
            if entry.is_file() and not entry.is_symlink():
                try:
                    total += entry.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _inspect_status(data: dict[str, Any], is_published: bool) -> str:
    if is_published:
        return "published"
    stages = data.get("stages", {})
    if not isinstance(stages, dict):
        return "unknown"
    if any(s.get("status") == "failed" for s in stages.values() if isinstance(s, dict)):
        return "failed"
    if any(s.get("status") == "running" for s in stages.values() if isinstance(s, dict)):
        return "interrupted"
    if stages.get("summary", {}).get("status") == "complete":
        return "summarized"
    if stages.get("transcription", {}).get("status") == "complete":
        return "transcribed"
    if stages.get("audio", {}).get("status") == "complete":
        return "audio_ready"
    if stages.get("download", {}).get("status") == "complete":
        return "downloaded"
    return "incomplete"


def _inspect_cached_lecture(path: Path) -> CachedLecture:
    lecture_id = path.name.removeprefix("lecture-")
    locked = is_workspace_locked(path)

    # Check publication journal
    is_published = False
    pub_file = path / "publication.json"
    if pub_file.is_file():
        try:
            pub_data = json.loads(pub_file.read_text(encoding="utf-8"))
            if isinstance(pub_data, dict) and pub_data.get("status") == "complete":
                is_published = True
        except (OSError, ValueError):
            pass

    # Read run state
    state_file = path / "run.json"
    data: dict[str, Any] = {}
    is_corrupt = False
    if state_file.is_file():
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                is_corrupt = True
                data = {}
        except (OSError, ValueError):
            is_corrupt = True
            data = {}

    url = data.get("url")
    title = data.get("title")
    course = data.get("course")
    lecture_date = data.get("lecture_date")

    # Timestamp extraction
    updated_at: datetime | None = None
    time_str = data.get("updated_at") or data.get("created_at")
    if isinstance(time_str, str):
        try:
            updated_at = datetime.fromisoformat(time_str)
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=UTC)
        except ValueError:
            pass
    if updated_at is None:
        try:
            updated_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        except OSError:
            pass

    status = "corrupt" if is_corrupt else _inspect_status(data, is_published)

    audio_file = path / "audio.wav"
    audio_size = 0
    if audio_file.is_file():
        try:
            audio_size = audio_file.stat().st_size
        except OSError:
            pass

    total_size = get_directory_size(path)

    return CachedLecture(
        path=path,
        lecture_id=lecture_id,
        url=str(url) if url is not None else None,
        title=str(title) if title is not None else None,
        course=str(course) if course is not None else None,
        lecture_date=str(lecture_date) if lecture_date is not None else None,
        updated_at=updated_at,
        status=status,
        audio_size=audio_size,
        total_size=total_size,
        is_published=is_published,
        is_locked=locked,
    )


def scan_cached_lectures(cache_root: Path | None = None) -> list[CachedLecture]:
    root = (cache_root or default_cache_root()).expanduser().resolve()
    if not root.is_dir():
        return []
    lectures: list[CachedLecture] = []
    for entry in root.iterdir():
        if not entry.is_dir() or not entry.name.startswith("lecture-"):
            continue
        lectures.append(_inspect_cached_lecture(entry))
    lectures.sort(
        key=lambda item: item.updated_at or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )
    return lectures


def get_storage_summary(
    cache_root: Path | None = None,
    video_root: Path | None = None,
) -> StorageSummary:
    root = (cache_root or default_cache_root()).expanduser().resolve()
    lectures = scan_cached_lectures(root)
    total_lectures = len(lectures)
    published_lectures = sum(1 for item in lectures if item.is_published)
    materials_cache_size = get_directory_size(root / "materials")
    total_cache_size = sum(item.total_size for item in lectures) + materials_cache_size
    total_audio_size = sum(item.audio_size for item in lectures)

    video_count = 0
    total_video_size = 0
    resolved_video_root: Path | None = None
    if video_root is not None:
        v_root = video_root.expanduser().resolve()
        resolved_video_root = v_root
        if v_root.is_dir():
            for f in v_root.rglob("*.mp4"):
                if f.is_file() and not f.is_symlink():
                    try:
                        video_count += 1
                        total_video_size += f.stat().st_size
                    except OSError:
                        pass

    return StorageSummary(
        cache_root=root,
        total_lectures=total_lectures,
        published_lectures=published_lectures,
        total_cache_size=total_cache_size,
        total_audio_size=total_audio_size,
        video_root=resolved_video_root,
        video_count=video_count,
        total_video_size=total_video_size,
        materials_cache_size=materials_cache_size,
    )


def filter_prunable_lectures(
    lectures: list[CachedLecture],
    *,
    lecture_ids: list[str] | None = None,
    all_workspaces: bool = False,
    audio_only: bool = False,
    days: int | None = None,
    now: datetime | None = None,
) -> list[CachedLecture]:
    targets: list[CachedLecture] = []

    if lecture_ids:
        requested = {item.strip().removeprefix("lecture-") for item in lecture_ids if item.strip()}
        known = {lec.lecture_id for lec in lectures}
        missing = requested - known
        if missing:
            missing_str = ", ".join(sorted(missing))
            raise LectureUtilError(f"Cached lecture(s) not found: {missing_str}")
        for lecture in lectures:
            if lecture.lecture_id in requested:
                if lecture.is_locked:
                    continue
                if audio_only and lecture.audio_size <= 0:
                    continue
                targets.append(lecture)
        return targets

    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=days) if days is not None else None

    for lecture in lectures:
        if lecture.is_locked:
            continue
        if not all_workspaces and not lecture.is_published:
            continue
        if audio_only and lecture.audio_size <= 0:
            continue
        if cutoff is not None:
            if lecture.updated_at is None or lecture.updated_at > cutoff:
                continue
        targets.append(lecture)

    return targets


def execute_prune(
    targets: list[CachedLecture],
    *,
    audio_only: bool = False,
    dry_run: bool = False,
) -> PruneResult:
    reclaimed = 0
    workspaces_pruned = 0
    audio_pruned = 0
    skipped_locked = 0

    for target in targets:
        if target.is_locked or is_workspace_locked(target.path):
            skipped_locked += 1
            continue

        if dry_run:
            if audio_only:
                reclaimed += target.audio_size
                audio_pruned += 1
            else:
                reclaimed += target.total_size
                workspaces_pruned += 1
            continue

        try:
            if audio_only:
                freed = clear_workspace_audio(target.path)
                reclaimed += freed
                audio_pruned += 1
            else:
                freed = target.total_size
                clear_workspace(target.path)
                reclaimed += freed
                workspaces_pruned += 1
        except LectureUtilError:
            skipped_locked += 1
        except OSError:
            pass

    return PruneResult(
        reclaimed_bytes=reclaimed,
        workspaces_pruned=workspaces_pruned,
        audio_files_pruned=audio_pruned,
        skipped_locked=skipped_locked,
    )
