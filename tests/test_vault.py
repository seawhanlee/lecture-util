from __future__ import annotations

import unittest
from pathlib import Path

import pytest

from lecture_util.errors import LectureUtilError
from lecture_util.vault import (
    COURSES_DIRECTORY,
    discover_courses,
    ensure_paths_available,
    publish_lecture_notes,
    published_lecture_paths,
    resolve_course,
    validate_lecture_date,
    validate_title,
)


def create_course(vault: Path, name: str, lecture_dir: str = "Lectures") -> Path:
    path = vault / COURSES_DIRECTORY / name / lecture_dir
    path.mkdir(parents=True)
    return path


class VaultTests(unittest.TestCase):
    def test_validates_date_and_title(self) -> None:
        self.assertEqual(validate_lecture_date("2026-09-04"), "2026-09-04")
        self.assertEqual(validate_title("  압축성 유동  "), "압축성 유동")
        for invalid_date in ("", "2026-9-4", "2026-02-30"):
            with self.subTest(invalid_date=invalid_date), self.assertRaises(LectureUtilError):
                validate_lecture_date(invalid_date)
        for invalid_title in ("", ".", "a/b", "a\nb"):
            with self.subTest(invalid_title=invalid_title), self.assertRaises(LectureUtilError):
                validate_title(invalid_title)


def test_discovers_and_resolves_courses(tmp_path: Path) -> None:
    plural = create_course(tmp_path, "공기역학특론", "Lectures")
    singular = create_course(tmp_path, "문제해결을 위한 글쓰기", "Lecture")
    create_course(tmp_path, "둘 다 있는 과목", "Lecture")
    create_course(tmp_path, "둘 다 있는 과목", "Lectures")
    (tmp_path / COURSES_DIRECTORY / "폴더 없는 과목").mkdir()

    courses = discover_courses(tmp_path)

    assert [course.name for course in courses] == ["공기역학특론", "문제해결을 위한 글쓰기"]
    assert courses[0].lectures == plural
    assert courses[1].lectures == singular
    assert resolve_course("공기역학특론", tmp_path).lectures == plural
    with pytest.raises(LectureUtilError, match="both Lecture and Lectures"):
        resolve_course("둘 다 있는 과목", tmp_path)
    with pytest.raises(LectureUtilError, match="no Lecture or Lectures"):
        resolve_course("폴더 없는 과목", tmp_path)


def test_publishes_linked_obsidian_notes_and_refuses_conflicts(tmp_path: Path) -> None:
    create_course(tmp_path, "공기역학특론")
    course = resolve_course("공기역학특론", tmp_path)
    paths = published_lecture_paths(course, "2026-09-04", "압축성 유동")

    publish_lecture_notes(
        paths,
        course=course,
        lecture_date="2026-09-04",
        title="압축성 유동",
        url="https://example.com/index.m3u8",
        summary="### 핵심\n\n- 마하수",
        transcript="# Transcript\n\n[00:00:00.000–00:00:01.000] 안녕하세요",
    )

    summary = paths.summary.read_text(encoding="utf-8")
    transcript = paths.transcript.read_text(encoding="utf-8")
    assert "type: lecture\n" in summary
    assert 'course: "공기역학특론"' in summary
    assert "[[공기역학특론 MOC|공기역학특론]]" in summary
    assert "[[2026-09-04 압축성 유동 전사]]" in summary
    assert "### 핵심" in summary
    assert "type: lecture-transcript\n" in transcript
    assert "[[2026-09-04 압축성 유동]]" in transcript
    assert transcript.count("# Transcript") == 1

    with pytest.raises(LectureUtilError, match="already exists"):
        ensure_paths_available(paths)
    with pytest.raises(LectureUtilError, match="already exists"):
        publish_lecture_notes(
            paths,
            course=course,
            lecture_date="2026-09-04",
            title="압축성 유동",
            url="https://example.com/index.m3u8",
            summary="replacement",
            transcript="replacement",
        )
    assert paths.summary.read_text(encoding="utf-8") == summary
