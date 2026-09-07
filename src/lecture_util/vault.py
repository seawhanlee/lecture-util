from __future__ import annotations

import json
import hashlib
import os
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from lecture_util.errors import LectureUtilError
from lecture_util.state import atomic_write_json, file_digest, directory_lock


DEFAULT_VAULT_ROOT = Path.home() / "Documents" / "학부연구생"
COURSES_DIRECTORY = Path("10 Academics/Courses")
LECTURE_DIRECTORY_NAMES = ("Lecture", "Lectures")
DEFAULT_SEMESTER_START_MONTH = 8
DEFAULT_SEMESTER_START_DAY = 31


@dataclass(frozen=True, slots=True)
class Course:
    name: str
    root: Path
    lectures: Path


@dataclass(frozen=True, slots=True)
class PublishedLecturePaths:
    summary: Path
    transcript: Path


def default_cache_root() -> Path:
    return Path.home() / ".cache" / "lecture-util"


def _courses_root(vault_root: Path) -> Path:
    courses_root = vault_root / COURSES_DIRECTORY
    if not courses_root.is_dir():
        raise LectureUtilError(f"Courses directory does not exist: {courses_root}")
    return courses_root


def _lecture_directories(course_root: Path) -> list[Path]:
    return [
        course_root / name
        for name in LECTURE_DIRECTORY_NAMES
        if (course_root / name).is_dir()
    ]


def discover_courses(vault_root: Path = DEFAULT_VAULT_ROOT) -> list[Course]:
    courses_root = _courses_root(vault_root)
    courses = [
        Course(child.name, child, lecture_dirs[0])
        for child in courses_root.iterdir()
        if child.is_dir()
        and len(lecture_dirs := _lecture_directories(child)) == 1
    ]
    courses.sort(key=lambda course: course.name.casefold())
    if not courses:
        raise LectureUtilError(
            f"No course has exactly one Lecture or Lectures directory in {courses_root}."
        )
    return courses


def resolve_course(course_name: str, vault_root: Path = DEFAULT_VAULT_ROOT) -> Course:
    normalized = course_name.strip()
    if not normalized or Path(normalized).name != normalized or normalized in {".", ".."}:
        raise LectureUtilError(f"Invalid course name: {course_name!r}")
    course_root = _courses_root(vault_root) / normalized
    if not course_root.is_dir():
        raise LectureUtilError(f"Course directory does not exist: {course_root}")
    lecture_dirs = _lecture_directories(course_root)
    if not lecture_dirs:
        raise LectureUtilError(
            f"Course {normalized!r} has no Lecture or Lectures directory."
        )
    if len(lecture_dirs) > 1:
        raise LectureUtilError(
            f"Course {normalized!r} has both Lecture and Lectures directories."
        )
    return Course(normalized, course_root, lecture_dirs[0])


def validate_lecture_date(value: str) -> str:
    return _validate_iso_date(value, "Lecture date")


def validate_semester_start(value: str) -> str:
    return _validate_iso_date(value, "Semester start date")


def _validate_iso_date(value: str, label: str) -> str:
    normalized = value.strip()
    try:
        parsed = date.fromisoformat(normalized)
    except ValueError as error:
        raise LectureUtilError(f"{label} must use YYYY-MM-DD format.") from error
    if parsed.isoformat() != normalized:
        raise LectureUtilError(f"{label} must use YYYY-MM-DD format.")
    return normalized


def default_semester_start(reference: date | None = None) -> str:
    current = reference or date.today()
    candidate = date(
        current.year,
        DEFAULT_SEMESTER_START_MONTH,
        DEFAULT_SEMESTER_START_DAY,
    )
    if current < candidate:
        candidate = candidate.replace(year=current.year - 1)
    return candidate.isoformat()


def lecture_week(lecture_date: str, semester_start: str) -> int:
    lecture = date.fromisoformat(validate_lecture_date(lecture_date))
    start = date.fromisoformat(validate_semester_start(semester_start))
    elapsed_days = (lecture - start).days
    if elapsed_days < 0:
        raise LectureUtilError("Lecture date cannot be before the semester start date.")
    return elapsed_days // 7 + 1


def validate_title(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise LectureUtilError("Enter a lecture title.")
    if normalized in {".", ".."} or any(character in normalized for character in "/\\\r\n\0"):
        raise LectureUtilError("Lecture title contains characters that are unsafe in a filename.")
    return normalized


def published_lecture_paths(
    course: Course,
    lecture_date: str,
    title: str,
    *,
    semester_start: str | None = None,
) -> PublishedLecturePaths:
    normalized_date = validate_lecture_date(lecture_date)
    normalized_title = validate_title(title)
    selected_start = semester_start or default_semester_start(
        date.fromisoformat(normalized_date)
    )
    week_directory = course.lectures / f"{lecture_week(normalized_date, selected_start)}주차"
    stem = f"{normalized_date} {normalized_title}"
    return PublishedLecturePaths(
        summary=week_directory / f"{stem}.md",
        transcript=week_directory / f"{stem} 전사.md",
    )


def lecture_video_path(
    video_root: Path,
    course: Course,
    lecture_date: str,
    title: str,
    *,
    semester_start: str,
) -> Path:
    normalized_date = validate_lecture_date(lecture_date)
    normalized_title = validate_title(title)
    week = lecture_week(normalized_date, semester_start)
    return video_root / course.name / f"{week}주차" / f"{normalized_title}.mp4"


def _publication_record(journal: Path | None) -> dict:
    if journal is None or not journal.exists():
        return {}
    try:
        record = json.loads(journal.read_text(encoding="utf-8"))
        if not isinstance(record, dict) or not isinstance(record.get("files"), dict):
            raise ValueError("invalid publication record")
        return record
    except (OSError, ValueError) as error:
        raise LectureUtilError(f"Could not read publication record {journal}: {error}") from error


def ensure_paths_available(
    paths: PublishedLecturePaths, *, journal: Path | None = None,
) -> None:
    record = _publication_record(journal)
    expected = {str(paths.summary), str(paths.transcript)}
    recovering = record.get("status") == "pending" and set(record.get("files", {})) == expected
    conflicts = []
    for path in (paths.summary, paths.transcript):
        if path.exists() or path.is_symlink():
            entry = record.get("files", {}).get(str(path), {})
            if (recovering and path.is_file() and not path.is_symlink()
                    and isinstance(entry, dict) and entry.get("sha256") == file_digest(path)):
                continue
            conflicts.append(path)
    if conflicts:
        joined = ", ".join(str(path) for path in conflicts)
        raise LectureUtilError(
            "Lecture note already exists. Change the lecture date or title and try again: "
            f"{joined}"
        )


def _yaml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _summary_note(
    *,
    course: Course,
    lecture_date: str,
    title: str,
    transcript_stem: str,
    summary: str,
) -> str:
    moc = f"{course.name} MOC"
    body = summary.strip()
    return f"""---
type: lecture
area:
  - academics
course: {_yaml_string(course.name)}
created: {lecture_date}
---

# {lecture_date} {title}

## Overview

- Topic: {title}
- Related course: [[{moc}|{course.name}]]
- Transcript: [[{transcript_stem}]]

## Notes

{body}

## Questions

- [ ]

## Concepts to Extract

- [ ]

## Related Problems and Sources

- Problems:
- Sources:

## Follow-up

- [ ] 과목 MOC에 개념 흐름 반영
"""


def _transcript_note(
    *,
    course: Course,
    lecture_date: str,
    title: str,
    url: str,
    summary_stem: str,
    transcript: str,
) -> str:
    body = transcript.strip()
    if body.startswith("# Transcript"):
        body = body.removeprefix("# Transcript").lstrip()
    return f"""---
type: lecture-transcript
area:
  - academics
course: {_yaml_string(course.name)}
created: {lecture_date}
lecture: {_yaml_string(f"[[{summary_stem}]]")}
---

# {lecture_date} {title} 전사

## Overview

- Related lecture: [[{summary_stem}]]
- Source: {url}

## Transcript

{body}
"""


def publish_lecture_notes(
    paths: PublishedLecturePaths,
    *,
    course: Course,
    lecture_date: str,
    title: str,
    url: str,
    summary: str,
    transcript: str,
    journal: Path | None = None,
) -> None:
    # Lock the existing lecture directory, never recreate a disappeared course.
    if _lecture_directories(course.root) != [course.lectures]:
        raise LectureUtilError(f"Course lecture directory changed: {course.root}")
    with directory_lock(course.lectures):
        _publish_locked(paths, course=course, lecture_date=lecture_date, title=title,
                        url=url, summary=summary, transcript=transcript, journal=journal)


def _publish_locked(
    paths: PublishedLecturePaths, *, course: Course, lecture_date: str,
    title: str, url: str, summary: str, transcript: str, journal: Path | None,
) -> None:
    ensure_paths_available(paths, journal=journal)
    paths.summary.parent.mkdir(exist_ok=True)
    summary_note = _summary_note(
        course=course,
        lecture_date=lecture_date,
        title=title,
        transcript_stem=paths.transcript.stem,
        summary=summary,
    )
    transcript_note = _transcript_note(
        course=course,
        lecture_date=lecture_date,
        title=title,
        url=url,
        summary_stem=paths.summary.stem,
        transcript=transcript,
    )
    contents = {str(paths.summary): summary_note, str(paths.transcript): transcript_note}
    previous = _publication_record(journal)
    if previous.get("status") == "pending":
        if set(previous["files"]) != set(contents):
            raise LectureUtilError("Pending publication destinations changed; resume the original run.")
        contents = {name: entry["content"] for name, entry in previous["files"].items()}
    record = {"status": "pending", "files": {
        name: {"content": content, "sha256": hashlib.sha256(content.encode()).hexdigest()}
        for name, content in contents.items()
    }}
    if journal is not None:
        atomic_write_json(journal, record)
    for name, content in contents.items():
        path = Path(name)
        if path.exists():
            if file_digest(path) != record["files"][name]["sha256"]:
                raise LectureUtilError(f"Publication conflict: {path}")
            continue
        _create_note(path, content)
    if journal is not None:
        atomic_write_json(journal, {**record, "status": "complete"})


def _create_note(path: Path, content: str) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".lecture-util-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    except OSError as error:
        raise LectureUtilError(f"Could not exclusively publish {path}: {error}") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
