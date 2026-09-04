from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from lecture_util.errors import LectureUtilError
from lecture_util.state import atomic_write_text


DEFAULT_VAULT_ROOT = Path("/home/seawhan/Documents/학부연구생")
COURSES_DIRECTORY = Path("10 Academics/Courses")
LECTURE_DIRECTORY_NAMES = ("Lecture", "Lectures")


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
    normalized = value.strip()
    try:
        parsed = date.fromisoformat(normalized)
    except ValueError as error:
        raise LectureUtilError("Lecture date must use YYYY-MM-DD format.") from error
    if parsed.isoformat() != normalized:
        raise LectureUtilError("Lecture date must use YYYY-MM-DD format.")
    return normalized


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
) -> PublishedLecturePaths:
    normalized_date = validate_lecture_date(lecture_date)
    normalized_title = validate_title(title)
    stem = f"{normalized_date} {normalized_title}"
    return PublishedLecturePaths(
        summary=course.lectures / f"{stem}.md",
        transcript=course.lectures / f"{stem} 전사.md",
    )


def ensure_paths_available(paths: PublishedLecturePaths) -> None:
    conflicts = [path for path in (paths.summary, paths.transcript) if path.exists()]
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
    url: str,
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
source_url: {_yaml_string(url)}
transcript: {_yaml_string(f"[[{transcript_stem}]]")}
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
source_url: {_yaml_string(url)}
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
) -> None:
    ensure_paths_available(paths)
    summary_note = _summary_note(
        course=course,
        lecture_date=lecture_date,
        title=title,
        url=url,
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
    atomic_write_text(paths.summary, summary_note)
    atomic_write_text(paths.transcript, transcript_note)
