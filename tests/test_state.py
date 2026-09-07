from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lecture_util.models import LecturePaths
from lecture_util.state import RunState, create_workspace


class RunStateTests(unittest.TestCase):
    def test_stage_state_survives_reload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths, state = create_workspace(
                "https://example.com/lecture/index.m3u8",
                Path(directory),
                title="Lecture",
                course="Course",
                lecture_date="2026-09-04",
                published_summary=Path("note.md"),
                published_transcript=Path("transcript.md"),
            )
            state.start_stage("download")
            state.complete_stage("download", output="source.mp4")

            reloaded = RunState(LecturePaths(paths.root))
            self.assertTrue(reloaded.stage_complete("download"))
            self.assertEqual(reloaded.data["title"], "Lecture")
            self.assertEqual(reloaded.data["course"], "Course")
            self.assertEqual(reloaded.data["lecture_date"], "2026-09-04")
            self.assertEqual(reloaded.data["published_summary"], "note.md")
            self.assertEqual(reloaded.data["published_transcript"], "transcript.md")

    def test_failure_is_recorded_without_secret_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths, state = create_workspace(
                "https://example.com/index.m3u8",
                Path(directory),
            )
            state.start_stage("transcription", requested_model="large-v3")
            state.fail_stage("transcription", RuntimeError("failed"))
            content = paths.state.read_text(encoding="utf-8")
            self.assertIn('"status": "failed"', content)
            self.assertNotIn("api_key", content)

    def test_tags_replace_only_when_explicitly_supplied(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            url = "https://example.com/index.m3u8"
            paths, _ = create_workspace(url, Path(directory), tags=["first"])
            _, preserved = create_workspace(url, Path(directory), tags=None)
            self.assertEqual(preserved.data["tags"], ["first"])
            _, replaced = create_workspace(url, Path(directory), tags=["second"])
            self.assertEqual(replaced.data["tags"], ["second"])
            self.assertEqual(paths.root, replaced.paths.root)

    def test_workspace_can_keep_video_outside_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "videos" / "Course" / "1주차" / "Title.mp4"

            paths, _ = create_workspace(
                "https://example.com/index.m3u8",
                root / "cache",
                video_path=video,
            )

            self.assertEqual(paths.video, video)
            self.assertTrue(paths.root.is_dir())
            self.assertFalse(video.parent.exists())


if __name__ == "__main__":
    unittest.main()


def test_workspace_lock_is_exclusive_and_released(tmp_path):
    import pytest
    from lecture_util.state import workspace_lock
    from lecture_util.errors import LectureUtilError
    with workspace_lock(tmp_path):
        with pytest.raises(LectureUtilError, match='Another process'):
            with workspace_lock(tmp_path):
                pass
    with workspace_lock(tmp_path):
        pass


def test_corrupt_state_is_preserved(tmp_path):
    import pytest
    from lecture_util.errors import LectureUtilError
    paths = LecturePaths(tmp_path)
    paths.state.write_text('{broken')
    with pytest.raises(LectureUtilError, match='Restore'):
        RunState(paths)
    assert paths.state.read_text() == '{broken'
