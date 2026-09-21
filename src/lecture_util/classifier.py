"""Course classification using Typesafe AI Jev."""
from __future__ import annotations

from typing import Any
from pathlib import Path

from typesafe_sdk import Choice, TypeSafeClient

from lecture_util.credentials import resolve_typesafe_api_key
from lecture_util.errors import LectureUtilError
from lecture_util.models import CourseClassificationResult
from lecture_util.vault import Course


def extract_lecture_titles(course: Course, limit: int = 5) -> list[str]:
    """Scan existing markdown notes in the course to extract representative lecture titles."""
    titles: list[str] = []
    if not course.lectures.is_dir():
        return titles
    for path in sorted(course.lectures.rglob("*.md")):
        name = path.stem
        # Exclude transcript files if named .transcript
        if name.endswith(".transcript") or name.startswith("."):
            continue
        titles.append(name)
        if len(titles) >= limit:
            break
    return titles


def build_course_criteria(courses: list[Course]) -> dict[str, str]:
    """Construct Choice criteria descriptions for each course candidate."""
    criteria: dict[str, str] = {}
    for course in courses:
        past_titles = extract_lecture_titles(course)
        if past_titles:
            criteria[course.name] = f"Course lectures include: {', '.join(past_titles)}"
        else:
            criteria[course.name] = f"Academic course: {course.name}"
    return criteria


def prepare_classification_request(
    title: str,
    summary: str,
    courses: list[Course],
) -> tuple[dict[str, Any], dict[str, Choice]]:
    """Prepare the state and Choice question before making the System One API call."""
    if not courses:
        raise LectureUtilError("No courses available in Vault to classify against.")
    state = {
        "title": title,
        "summary": summary,
    }
    criteria = build_course_criteria(courses)
    questions = {
        "course": Choice(
            instructions=(
                "Which academic course does this lecture belong to based on the title and summary? "
                "Select the single most relevant course."
            ),
            criteria=criteria,
        ),
    }
    return state, questions


def classify_lecture_course(
    title: str,
    summary: str,
    courses: list[Course],
    *,
    api_key: str | None = None,
    client: TypeSafeClient | None = None,
) -> CourseClassificationResult:
    """Classify lecture into one of the vault courses using Typesafe AI Jev."""
    if len(courses) == 1:
        # Trivial single-course vault: only one option exists.
        return CourseClassificationResult(
            selected_course=courses[0].name,
            confidence=1.0,
            probabilities={courses[0].name: 1.0},
        )

    state, questions = prepare_classification_request(title, summary, courses)

    def _call(active_client: TypeSafeClient) -> CourseClassificationResult:
        response = active_client.system_one(
            state=state,
            questions=questions,
            model="jev-latest",
        )
        answer = response.answers.get("course")
        if answer is None:
            raise LectureUtilError("Typesafe Jev response did not contain the 'course' answer.")
        return CourseClassificationResult(
            selected_course=answer.choice,
            confidence=float(answer.confidence),
            probabilities={k: float(v) for k, v in answer.probabilities.items()},
        )

    if client is not None:
        return _call(client)

    effective_key = api_key or resolve_typesafe_api_key(required=True)
    with TypeSafeClient(api_key=effective_key) as new_client:
        return _call(new_client)
