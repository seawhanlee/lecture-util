import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from typesafe_sdk import Choice, ChoiceAnswer, SystemOneResponse

from lecture_util.classifier import (
    build_course_criteria,
    classify_lecture_course,
    extract_lecture_titles,
    prepare_classification_request,
)
from lecture_util.errors import LectureUtilError
from lecture_util.models import CourseClassificationResult
from lecture_util.vault import Course


class ClassifierTests(unittest.TestCase):
    def test_extract_lecture_titles(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            lectures = root / "Lectures" / "1주차"
            lectures.mkdir(parents=True)
            (lectures / "01. Introduction to ML.md").write_text("# Intro", encoding="utf-8")
            (lectures / "02. Linear Regression.md").write_text("# Reg", encoding="utf-8")
            (lectures / "01. Introduction to ML.transcript.md").write_text("# Tr", encoding="utf-8")

            course = Course("머신러닝", root, root / "Lectures")
            titles = extract_lecture_titles(course)
            self.assertEqual(titles, ["01. Introduction to ML", "02. Linear Regression"])

    def test_build_course_criteria(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            c1_root = root / "ML"
            (c1_root / "Lectures").mkdir(parents=True)
            (c1_root / "Lectures" / "Perceptron.md").write_text("...", encoding="utf-8")

            c2_root = root / "OS"
            (c2_root / "Lectures").mkdir(parents=True)

            courses = [
                Course("ML", c1_root, c1_root / "Lectures"),
                Course("OS", c2_root, c2_root / "Lectures"),
            ]
            criteria = build_course_criteria(courses)
            self.assertIn("Course lectures include: Perceptron", criteria["ML"])
            self.assertEqual(criteria["OS"], "Academic course: OS")

    def test_prepare_classification_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            courses = [
                Course("ML", root / "ML", root / "ML" / "Lectures"),
                Course("OS", root / "OS", root / "OS" / "Lectures"),
            ]
            title = "Virtual Memory & Paging"
            summary = "### 요약\n- 페이지 테이블 구조\n- 가상 메모리 매핑"

            state, questions = prepare_classification_request(title, summary, courses)
            self.assertEqual(state["title"], title)
            self.assertEqual(state["summary"], summary)
            self.assertIn("course", questions)
            self.assertIsInstance(questions["course"], Choice)
            self.assertIn("ML", questions["course"].criteria)
            self.assertIn("OS", questions["course"].criteria)

    def test_prepare_classification_request_empty_courses(self) -> None:
        with self.assertRaises(LectureUtilError):
            prepare_classification_request("Title", "Summary", [])

    def test_classify_single_course_returns_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            course = Course("SingleCourse", root, root / "Lectures")
            result = classify_lecture_course("Title", "Summary", [course])
            self.assertEqual(result.selected_course, "SingleCourse")
            self.assertEqual(result.confidence, 1.0)
            self.assertEqual(result.probabilities, {"SingleCourse": 1.0})

    def test_classify_missing_api_key_raises_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            courses = [
                Course("C1", root / "C1", root / "C1" / "Lectures"),
                Course("C2", root / "C2", root / "C2" / "Lectures"),
            ]
            with patch("lecture_util.classifier.resolve_typesafe_api_key", side_effect=LectureUtilError("Key missing")):
                with self.assertRaises(LectureUtilError):
                    classify_lecture_course("Title", "Summary", courses)

    def test_classify_with_mock_client(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            courses = [
                Course("ML", root / "ML", root / "ML" / "Lectures"),
                Course("OS", root / "OS", root / "OS" / "Lectures"),
            ]
            mock_client = MagicMock()
            mock_response = MagicMock(spec=SystemOneResponse)
            mock_response.answers = {
                "course": ChoiceAnswer(
                    type="choice",
                    choice="OS",
                    confidence=0.92,
                    probabilities={"OS": 0.92, "ML": 0.08},
                )
            }
            mock_client.system_one.return_value = mock_response

            result = classify_lecture_course("Title", "Summary", courses, client=mock_client)
            self.assertEqual(result.selected_course, "OS")
            self.assertEqual(result.confidence, 0.92)
            self.assertEqual(result.probabilities, {"OS": 0.92, "ML": 0.08})
            mock_client.system_one.assert_called_once()
            call_kwargs = mock_client.system_one.call_args.kwargs
            self.assertEqual(call_kwargs["model"], "jev-latest")
            self.assertEqual(call_kwargs["state"]["title"], "Title")
