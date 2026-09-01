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
            )
            state.start_stage("download")
            state.complete_stage("download", output="source.mp4")

            reloaded = RunState(LecturePaths(paths.root))
            self.assertTrue(reloaded.stage_complete("download"))
            self.assertEqual(reloaded.data["title"], "Lecture")

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


if __name__ == "__main__":
    unittest.main()
