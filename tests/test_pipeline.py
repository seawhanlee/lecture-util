from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lecture_util.errors import LectureUtilError
from lecture_util.models import Segment, Transcript
from lecture_util.pipeline import audio_stage, download_stage, transcription_stage
from lecture_util.progress import ProgressEvent
from lecture_util.state import create_workspace
from lecture_util.transcription import save_transcript


class PipelineTests(unittest.TestCase):
    def test_unknown_existing_video_is_not_overwritten_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "videos" / "Course" / "1주차" / "Title.mp4"
            video.parent.mkdir(parents=True)
            video.write_bytes(b"existing")
            paths, state = create_workspace(
                "https://example.com/index.m3u8",
                root / "cache",
                video_path=video,
            )

            with (
                patch("lecture_util.pipeline.download_hls") as download,
                self.assertRaisesRegex(LectureUtilError, "already exists"),
            ):
                download_stage(paths, state)

            download.assert_not_called()
            self.assertEqual(video.read_bytes(), b"existing")

    def test_completed_external_video_is_reused_for_same_url(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "videos" / "Course" / "1주차" / "Title.mp4"
            video.parent.mkdir(parents=True)
            video.write_bytes(b"complete")
            paths, state = create_workspace(
                "https://example.com/index.m3u8",
                root / "cache",
                video_path=video,
            )
            state.start_stage("download")
            state.complete_stage("download", output="old/cache/source.mp4")
            events: list[ProgressEvent] = []

            with patch("lecture_util.pipeline.download_hls") as download:
                download_stage(paths, state, progress=events.append)

            download.assert_not_called()
            self.assertEqual(events[-1].status, "cached")

    def test_force_replaces_unknown_external_video(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "videos" / "Course" / "1주차" / "Title.mp4"
            video.parent.mkdir(parents=True)
            video.write_bytes(b"existing")
            paths, state = create_workspace(
                "https://example.com/index.m3u8",
                root / "cache",
                video_path=video,
            )

            def download(_url: str, destination: Path) -> None:
                destination.write_bytes(b"replacement")

            with (
                patch("lecture_util.pipeline.download_hls", side_effect=download),
                patch("lecture_util.pipeline.tool_version", return_value="test"),
            ):
                download_stage(paths, state, force=True)

            self.assertEqual(video.read_bytes(), b"replacement")
            self.assertEqual(
                state.data["stages"]["download"]["output"],
                str(video),
            )

    def test_audio_stage_reads_external_video_into_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "videos" / "Course" / "1주차" / "Title.mp4"
            video.parent.mkdir(parents=True)
            video.touch()
            paths, state = create_workspace(
                "https://example.com/index.m3u8",
                root / "cache",
                video_path=video,
            )

            def extract(source: Path, destination: Path) -> None:
                self.assertEqual(source, video)
                destination.touch()

            with patch("lecture_util.pipeline.extract_audio", side_effect=extract):
                audio_stage(paths, state)

            self.assertEqual(paths.audio.parent, paths.root)
            self.assertTrue(paths.audio.is_file())

    def test_transcription_is_reused_only_for_matching_options(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths, state = create_workspace(
                "https://example.com/index.m3u8",
                Path(directory),
            )
            paths.audio.touch()
            existing = Transcript(
                language="ko",
                duration=1,
                engine="test",
                requested_model="large-v3",
                effective_model="large-v3",
                segments=[Segment(0, 1, "existing")],
            )
            save_transcript(
                existing,
                paths.transcript_json,
                paths.transcript_markdown,
                paths.transcript_srt,
            )
            state.start_stage(
                "transcription",
                requested_model="large-v3",
                requested_language="auto",
                requested_device="cpu",
            )
            state.complete_stage("transcription")

            events: list[ProgressEvent] = []
            with patch("lecture_util.pipeline.transcribe_audio") as transcribe:
                reused = transcription_stage(
                    paths,
                    state,
                    model="large-v3",
                    language="auto",
                    device="cpu",
                    progress=events.append,
                )
            transcribe.assert_not_called()
            self.assertEqual(reused.segments[0].text, "existing")
            self.assertEqual(events[-1].status, "cached")

            replacement = Transcript(
                language="en",
                duration=1,
                engine="test",
                requested_model="large-v3",
                effective_model="large-v3",
                segments=[Segment(0, 1, "replacement")],
            )
            events.clear()
            with patch(
                "lecture_util.pipeline.transcribe_audio", return_value=replacement
            ) as transcribe:
                result = transcription_stage(
                    paths,
                    state,
                    model="large-v3",
                    language="en",
                    device="cpu",
                    progress=events.append,
                )
            transcribe.assert_called_once()
            self.assertEqual(result.segments[0].text, "replacement")
            self.assertEqual([event.status for event in events], ["start", "complete"])


if __name__ == "__main__":
    unittest.main()
