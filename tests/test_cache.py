from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from typer.testing import CliRunner

from lecture_util.cache import (
    CachedLecture,
    execute_prune,
    filter_prunable_lectures,
    get_directory_size,
    get_storage_summary,
    scan_cached_lectures,
)
from lecture_util.cli import app
from lecture_util.errors import LectureUtilError
from lecture_util.state import (
    clear_workspace_audio,
    is_workspace_locked,
    workspace_lock,
)


def make_cached_workspace(
    cache_root: Path,
    lid: str,
    *,
    title: str = "Test Title",
    course: str = "Test Course",
    lecture_date: str = "2026-09-04",
    stages: dict | None = None,
    is_published: bool = False,
    audio_size: int = 1000,
    updated_at: datetime | None = None,
    corrupt_state: bool = False,
) -> Path:
    ws = cache_root / f"lecture-{lid}"
    ws.mkdir(parents=True, exist_ok=True)
    if corrupt_state:
        (ws / "run.json").write_text("{invalid json", encoding="utf-8")
    else:
        dt = updated_at or datetime.now(UTC)
        run_data = {
            "version": 1,
            "url": f"https://example.com/{lid}.m3u8",
            "title": title,
            "course": course,
            "lecture_date": lecture_date,
            "created_at": dt.isoformat(),
            "updated_at": dt.isoformat(),
            "stages": stages or {
                "download": {"status": "complete"},
                "audio": {"status": "complete"},
                "transcription": {"status": "complete"},
                "summary": {"status": "complete"},
            },
        }
        (ws / "run.json").write_text(json.dumps(run_data), encoding="utf-8")

    if is_published:
        pub_data = {"status": "complete", "files": {}}
        (ws / "publication.json").write_text(json.dumps(pub_data), encoding="utf-8")

    if audio_size > 0:
        (ws / "audio.wav").write_bytes(b"A" * audio_size)
    (ws / "transcript.md").write_text("# Transcript\nText", encoding="utf-8")
    (ws / "summary.md").write_text("## Notes\nSummary", encoding="utf-8")
    return ws


class CacheInspectionTests(unittest.TestCase):
    def test_scan_empty_or_nonexistent_cache(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            empty = Path(td) / "empty"
            self.assertEqual(scan_cached_lectures(empty), [])
            empty.mkdir()
            self.assertEqual(scan_cached_lectures(empty), [])

    def test_scan_cached_lectures_various_statuses(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "cache"
            # 1. Published
            make_cached_workspace(root, "001", title="Lecture 1", is_published=True, audio_size=2048)
            # 2. Failed stage
            make_cached_workspace(
                root,
                "002",
                title="Lecture 2",
                stages={"download": {"status": "complete"}, "audio": {"status": "failed"}},
                audio_size=0,
            )
            # 3. Transcribed (no summary yet)
            make_cached_workspace(
                root,
                "003",
                title="Lecture 3",
                stages={
                    "download": {"status": "complete"},
                    "audio": {"status": "complete"},
                    "transcription": {"status": "complete"},
                },
                audio_size=1024,
            )
            # 4. Corrupt state
            make_cached_workspace(root, "004", corrupt_state=True)

            lectures = scan_cached_lectures(root)
            self.assertEqual(len(lectures), 4)

            by_id = {lec.lecture_id: lec for lec in lectures}
            self.assertTrue(by_id["001"].is_published)
            self.assertEqual(by_id["001"].status, "published")
            self.assertEqual(by_id["001"].audio_size, 2048)
            self.assertIn("Published", by_id["001"].display_status)

            self.assertFalse(by_id["002"].is_published)
            self.assertEqual(by_id["002"].status, "failed")
            self.assertEqual(by_id["002"].audio_size, 0)
            self.assertIn("Failed", by_id["002"].display_status)

            self.assertFalse(by_id["003"].is_published)
            self.assertEqual(by_id["003"].status, "transcribed")
            self.assertIn("Transcribed", by_id["003"].display_status)

            self.assertEqual(by_id["004"].status, "corrupt")
            self.assertIn("Corrupt", by_id["004"].display_status)

    def test_is_workspace_locked_detection(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td) / "lecture-locktest"
            ws.mkdir()
            self.assertFalse(is_workspace_locked(ws))
            with workspace_lock(ws):
                self.assertTrue(is_workspace_locked(ws))
            self.assertFalse(is_workspace_locked(ws))

    def test_get_storage_summary(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cache_root = Path(td) / "cache"
            video_root = Path(td) / "videos"
            video_root.mkdir()
            (video_root / "test.mp4").write_bytes(b"V" * 5000)

            make_cached_workspace(cache_root, "001", is_published=True, audio_size=1000)
            make_cached_workspace(cache_root, "002", is_published=False, audio_size=2000)

            summary = get_storage_summary(cache_root, video_root)
            self.assertEqual(summary.total_lectures, 2)
            self.assertEqual(summary.published_lectures, 1)
            self.assertEqual(summary.total_audio_size, 3000)
            self.assertGreater(summary.total_cache_size, 3000)
            self.assertEqual(summary.video_count, 1)
            self.assertEqual(summary.total_video_size, 5000)


class CachePruningTests(unittest.TestCase):
    def test_filter_prunable_default_published_only(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "cache"
            make_cached_workspace(root, "pub", is_published=True, audio_size=1000)
            make_cached_workspace(root, "unpub", is_published=False, audio_size=1000)

            lectures = scan_cached_lectures(root)
            targets = filter_prunable_lectures(lectures, all_workspaces=False)
            self.assertEqual(len(targets), 1)
            self.assertEqual(targets[0].lecture_id, "pub")

    def test_filter_prunable_all_workspaces(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "cache"
            make_cached_workspace(root, "pub", is_published=True, audio_size=1000)
            make_cached_workspace(root, "unpub", is_published=False, audio_size=1000)

            lectures = scan_cached_lectures(root)
            targets = filter_prunable_lectures(lectures, all_workspaces=True)
            self.assertEqual(len(targets), 2)

    def test_filter_prunable_audio_only(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "cache"
            make_cached_workspace(root, "withaudio", is_published=True, audio_size=1000)
            make_cached_workspace(root, "noaudio", is_published=True, audio_size=0)

            lectures = scan_cached_lectures(root)
            targets = filter_prunable_lectures(lectures, audio_only=True)
            self.assertEqual(len(targets), 1)
            self.assertEqual(targets[0].lecture_id, "withaudio")

    def test_filter_prunable_days_cutoff(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "cache"
            now = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
            old_time = now - timedelta(days=10)
            recent_time = now - timedelta(days=2)

            make_cached_workspace(root, "old", is_published=True, updated_at=old_time)
            make_cached_workspace(root, "recent", is_published=True, updated_at=recent_time)

            lectures = scan_cached_lectures(root)
            targets_5d = filter_prunable_lectures(lectures, days=5, now=now)
            self.assertEqual(len(targets_5d), 1)
            self.assertEqual(targets_5d[0].lecture_id, "old")

            targets_1d = filter_prunable_lectures(lectures, days=1, now=now)
            self.assertEqual(len(targets_1d), 2)

    def test_filter_prunable_specific_ids(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "cache"
            make_cached_workspace(root, "abc", is_published=False)
            make_cached_workspace(root, "def", is_published=True)

            lectures = scan_cached_lectures(root)
            targets = filter_prunable_lectures(lectures, lecture_ids=["lecture-abc"])
            self.assertEqual(len(targets), 1)
            self.assertEqual(targets[0].lecture_id, "abc")

            with self.assertRaises(LectureUtilError):
                filter_prunable_lectures(lectures, lecture_ids=["nonexistent"])

    def test_filter_prunable_skips_locked_workspaces(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "cache"
            ws = make_cached_workspace(root, "locked", is_published=True)
            with workspace_lock(ws):
                lectures = scan_cached_lectures(root)
                targets = filter_prunable_lectures(lectures, all_workspaces=True)
                self.assertEqual(len(targets), 0)

    def test_clear_workspace_audio(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "cache"
            ws = make_cached_workspace(root, "001", audio_size=1500)
            (ws / ".audio.wav.tmp").write_bytes(b"T" * 500)

            freed = clear_workspace_audio(ws)
            self.assertEqual(freed, 2000)
            self.assertFalse((ws / "audio.wav").exists())
            self.assertFalse((ws / ".audio.wav.tmp").exists())
            self.assertTrue((ws / "summary.md").exists())
            self.assertTrue((ws / "run.json").exists())

    def test_execute_prune_audio_only(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "cache"
            ws = make_cached_workspace(root, "001", is_published=True, audio_size=3000)
            lectures = scan_cached_lectures(root)
            result = execute_prune(lectures, audio_only=True)
            self.assertEqual(result.audio_files_pruned, 1)
            self.assertEqual(result.workspaces_pruned, 0)
            self.assertEqual(result.reclaimed_bytes, 3000)
            self.assertFalse((ws / "audio.wav").exists())
            self.assertTrue((ws / "run.json").exists())

    def test_execute_prune_full_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "cache"
            ws = make_cached_workspace(root, "001", is_published=True, audio_size=3000)
            lectures = scan_cached_lectures(root)
            result = execute_prune(lectures, audio_only=False)
            self.assertEqual(result.workspaces_pruned, 1)
            self.assertGreaterEqual(result.reclaimed_bytes, 3000)
            self.assertFalse(ws.exists())

    def test_execute_prune_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "cache"
            ws = make_cached_workspace(root, "001", is_published=True, audio_size=3000)
            lectures = scan_cached_lectures(root)
            result = execute_prune(lectures, audio_only=False, dry_run=True)
            self.assertEqual(result.workspaces_pruned, 1)
            self.assertTrue(ws.exists())
            self.assertTrue((ws / "audio.wav").exists())


class CacheCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def test_cli_cache_list_empty(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cache_root = Path(td) / "cache"
            res = self.runner.invoke(app, ["cache", "list", "--cache-root", str(cache_root)])
            self.assertEqual(res.exit_code, 0)
            self.assertIn("No cached lectures found", res.stdout)

    def test_cli_cache_list_populated(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cache_root = Path(td) / "cache"
            make_cached_workspace(
                cache_root,
                "0123456789",
                title="Aerodynamics Lecture",
                course="Aerodynamics",
                is_published=True,
                audio_size=5000,
            )
            res = self.runner.invoke(app, ["cache", "--cache-root", str(cache_root)], env={"COLUMNS": "120"})
            self.assertEqual(res.exit_code, 0)
            self.assertIn("0123456789", res.stdout)
            self.assertIn("Aerodynamics", res.stdout)
            self.assertIn("Published", res.stdout)

    def test_cli_cache_prune_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cache_root = Path(td) / "cache"
            ws = make_cached_workspace(cache_root, "001", is_published=True, audio_size=4000)
            res = self.runner.invoke(
                app,
                ["cache", "prune", "--cache-root", str(cache_root), "--dry-run"],
            )
            self.assertEqual(res.exit_code, 0)
            self.assertIn("Dry run", res.stdout)
            self.assertIn("No files were deleted", res.stdout)
            self.assertTrue((ws / "audio.wav").exists())

    def test_cli_cache_prune_requires_yes_in_non_interactive(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cache_root = Path(td) / "cache"
            make_cached_workspace(cache_root, "001", is_published=True, audio_size=4000)
            res = self.runner.invoke(
                app,
                ["cache", "prune", "--cache-root", str(cache_root)],
            )
            self.assertNotEqual(res.exit_code, 0)
            self.assertIn("requires --yes", res.stdout)

    def test_cli_cache_prune_with_yes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cache_root = Path(td) / "cache"
            ws = make_cached_workspace(cache_root, "001", is_published=True, audio_size=4000)
            res = self.runner.invoke(
                app,
                ["cache", "prune", "--cache-root", str(cache_root), "--yes"],
            )
            self.assertEqual(res.exit_code, 0)
            self.assertIn("Reclaimed", res.stdout)
            self.assertFalse(ws.exists())

    def test_cli_cache_prune_audio_only_with_yes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cache_root = Path(td) / "cache"
            ws = make_cached_workspace(cache_root, "001", is_published=True, audio_size=4000)
            res = self.runner.invoke(
                app,
                ["cache", "prune", "--cache-root", str(cache_root), "--audio-only", "--yes"],
            )
            self.assertEqual(res.exit_code, 0)
            self.assertIn("Reclaimed", res.stdout)
            self.assertTrue(ws.exists())
            self.assertFalse((ws / "audio.wav").exists())
            self.assertTrue((ws / "run.json").exists())

    def test_cli_cache_prune_no_matches(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cache_root = Path(td) / "cache"
            res = self.runner.invoke(
                app,
                ["cache", "prune", "--cache-root", str(cache_root), "--yes"],
            )
            self.assertEqual(res.exit_code, 0)
            self.assertIn("No cached lectures matched", res.stdout)

    def test_cli_cache_prune_specific_id(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cache_root = Path(td) / "cache"
            ws1 = make_cached_workspace(cache_root, "target1", is_published=False, audio_size=1000)
            ws2 = make_cached_workspace(cache_root, "keep2", is_published=False, audio_size=1000)
            res = self.runner.invoke(
                app,
                ["cache", "prune", "target1", "--cache-root", str(cache_root), "--yes"],
            )
            self.assertEqual(res.exit_code, 0)
            self.assertFalse(ws1.exists())
            self.assertTrue(ws2.exists())
