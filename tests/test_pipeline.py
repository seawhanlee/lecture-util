from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lecture_util.errors import LectureUtilError
from lecture_util.models import Segment, Transcript, TranscriptionOptions
from lecture_util.pipeline import backend_platform
from lecture_util.pipeline import audio_stage, download_stage, transcription_stage
from lecture_util.progress import ProgressEvent
from lecture_util.state import create_workspace, file_digest
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
            state.complete_stage("download", output=str(video))
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

            def download(_url: str, destination: Path, **kwargs) -> None:
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
                input_sha256=file_digest(paths.audio),
            )
            state.complete_stage("transcription", options=TranscriptionOptions(device="cpu").to_dict(),
                                 backend_platform=backend_platform())

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


def test_changed_audio_invalidates_transcription(tmp_path):
    from lecture_util.state import file_digest
    paths, state = create_workspace('https://example.com/a.m3u8', tmp_path)
    paths.audio.write_bytes(b'old')
    state.start_stage('transcription', requested_model='large-v3',
                      requested_language='auto', requested_device='cpu',
                      input_sha256=file_digest(paths.audio))
    state.complete_stage('transcription', options=TranscriptionOptions(device='cpu').to_dict(),
                         backend_platform=backend_platform())
    paths.audio.write_bytes(b'new')
    replacement = Transcript('ko', 1, 'test', 'large-v3', 'large-v3', [])
    with patch('lecture_util.pipeline.transcribe_audio', return_value=replacement) as run:
        transcription_stage(paths, state, device='cpu')
    run.assert_called_once()


def test_upstream_failure_invalidates_dependents(tmp_path):
    paths, state = create_workspace('https://example.com/a.m3u8', tmp_path)
    for stage in ('download', 'audio', 'transcription', 'summary'):
        state.complete_stage(stage)
    with patch('lecture_util.pipeline.download_hls', side_effect=RuntimeError('network')):
        import pytest
        with pytest.raises(RuntimeError):
            download_stage(paths, state, force=True)
    assert all(not state.stage_complete(stage) for stage in ('audio', 'transcription', 'summary'))


def test_cached_transcript_restores_only_missing_files(tmp_path):
    from lecture_util.state import file_digest
    paths, state = create_workspace('https://example.com/a.m3u8', tmp_path)
    paths.audio.touch()
    transcript = Transcript('ko', 1, 'test', 'large-v3', 'large-v3', [Segment(0, 1, 'hello')])
    save_transcript(transcript, paths.transcript_json, paths.transcript_markdown, paths.transcript_srt)
    paths.transcript_markdown.write_text('user edit')
    paths.transcript_srt.unlink()
    state.start_stage('transcription', requested_model='large-v3', requested_language='auto',
                      requested_device='cpu', input_sha256=file_digest(paths.audio))
    state.complete_stage('transcription', options=TranscriptionOptions(device='cpu').to_dict(),
                         backend_platform=backend_platform())
    with patch('lecture_util.pipeline.transcribe_audio') as run:
        transcription_stage(paths, state, device='cpu')
    run.assert_not_called()
    assert paths.transcript_markdown.read_text() == 'user edit'
    assert 'hello' in paths.transcript_srt.read_text()


def test_valid_cache_preflight_never_loads_backend(tmp_path):
    from lecture_util.pipeline import preflight_preparation
    paths, state = create_workspace('https://example.com/a.m3u8', tmp_path)
    paths.video.write_bytes(b'video')
    paths.audio.write_bytes(b'audio')
    transcript = Transcript('ko', 1, 'test', 'large-v3', 'large-v3', [])
    save_transcript(transcript, paths.transcript_json, paths.transcript_markdown, paths.transcript_srt)
    state.complete_stage('download', output=str(paths.video), sha256=file_digest(paths.video))
    state.complete_stage('audio', input_sha256=file_digest(paths.video), sha256=file_digest(paths.audio))
    options = TranscriptionOptions(device='cpu')
    state.complete_stage('transcription', options=options.to_dict(), backend_platform=backend_platform(),
                         input_sha256=file_digest(paths.audio))
    with patch('lecture_util.pipeline.preflight_transcription') as preflight:
        preflight_preparation(paths, state, options)
    preflight.assert_not_called()


def test_invalid_language_prevents_download(tmp_path):
    import pytest
    from lecture_util.pipeline import prepare_lecture
    with patch('lecture_util.pipeline.download_hls') as download:
        with pytest.raises(LectureUtilError, match='language'):
            prepare_lecture('https://example.com/a.m3u8', tmp_path, language='invalid')
    download.assert_not_called()


def test_failed_force_download_can_resume_replacement(tmp_path):
    import pytest
    paths, state = create_workspace('https://example.com/a.m3u8', tmp_path)
    paths.video.write_bytes(b'old')
    with patch('lecture_util.pipeline.download_hls', side_effect=RuntimeError('offline')):
        with pytest.raises(RuntimeError):
            download_stage(paths, state, force=True)
    with (patch('lecture_util.pipeline.download_hls', side_effect=lambda url, path, **kwargs: path.write_bytes(b'new')),
          patch('lecture_util.pipeline.tool_version', return_value='test')):
        download_stage(paths, state)
    assert paths.video.read_bytes() == b'new'


def test_execute_run_with_auto_course_classification(tmp_path):
    from unittest.mock import patch, MagicMock
    from rich.console import Console
    from io import StringIO
    from lecture_util.pipeline import _execute_run_locked
    from lecture_util.models import RunOptions, LecturePaths, CourseClassificationResult
    from lecture_util.vault import COURSES_DIRECTORY

    vault = tmp_path / "vault"
    cache = tmp_path / "cache"
    videos = tmp_path / "videos"

    (vault / COURSES_DIRECTORY / "MachineLearning" / "Lectures").mkdir(parents=True)
    (vault / COURSES_DIRECTORY / "OperatingSystems" / "Lectures").mkdir(parents=True)

    cached = LecturePaths(cache / "lecture-test")
    cached.root.mkdir(parents=True)
    cached.summary.write_text("### 요약\n- 가상 메모리와 페이징 기법\n", encoding="utf-8")
    cached.transcript_markdown.write_text("# Transcript\n내용\n", encoding="utf-8")
    cached.video.write_bytes(b"temp-video")

    options = RunOptions(
        url="https://example.com/test.m3u8",
        course=None,
        lecture_date="2026-09-07",
        title="페이징 기법",
        llm_model=None,
        tags=None,
        whisper_model="large-v3",
        language="ko",
        device="auto",
        prompt="",
        force=False,
    )

    mock_classification = CourseClassificationResult(
        selected_course="OperatingSystems",
        confidence=0.94,
        probabilities={"OperatingSystems": 0.94, "MachineLearning": 0.06},
    )

    terminal = StringIO()
    with (
        patch("lecture_util.pipeline.run_lecture", return_value=cached),
        patch("lecture_util.classifier.classify_lecture_course", return_value=mock_classification),
        patch("lecture_util.pipeline.publish_lecture_notes") as mock_publish,
    ):
        _execute_run_locked(
            options,
            vault_root=vault,
            video_root=videos,
            cache_root=cache,
            console=Console(file=terminal, force_terminal=True),
        )

    mock_publish.assert_called_once()
    published_course = mock_publish.call_args.kwargs["course"]
    assert published_course.name == "OperatingSystems"

