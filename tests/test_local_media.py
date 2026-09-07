from pathlib import Path
import json
import shutil
import subprocess
from unittest.mock import patch

import pytest

from lecture_util.errors import LectureUtilError
from lecture_util.media import resolve_source
from lecture_util.models import Segment, Transcript
from lecture_util.pipeline import prepare_lecture
from lecture_util.state import create_workspace, lecture_id


def probe_result(streams):
    return subprocess.CompletedProcess([], 0, json.dumps({'streams': streams}), '')


@pytest.mark.parametrize(('streams', 'kind'), [
    ([{'codec_type': 'audio'}], 'audio'),
    ([{'codec_type': 'audio'}, {'codec_type': 'video'}], 'video'),
    ([{'codec_type': 'audio'}, {'codec_type': 'video',
                              'disposition': {'attached_pic': 1}}], 'audio'),
])
def test_detect_streams_and_content_cache(tmp_path, streams, kind):
    media = tmp_path / '강의 녹음.data'
    media.write_bytes(b'original')
    with patch('lecture_util.media.require_executable', return_value='tool'), patch(
        'lecture_util.media.subprocess.run', return_value=probe_result(streams)
    ):
        source = resolve_source(str(media))
        assert source.kind == kind
        paths, state = create_workspace(source.location, tmp_path / 'cache', source=source)
        assert state.data['source']['content_hash'] == source.content_hash
        same = resolve_source(str(media))
        assert same.cache_key == source.cache_key
        media.write_bytes(b'replacement')
        changed = resolve_source(str(media))
        assert changed.cache_key != source.cache_key
        assert paths.root.name == f'lecture-{lecture_id(source.cache_key)}'


@pytest.mark.parametrize('result', [probe_result([{'codec_type': 'video'}]),
    subprocess.CompletedProcess([], 1, '', 'broken'),
    subprocess.CompletedProcess([], 0, 'invalid json', '')])
def test_invalid_media(tmp_path, result):
    media = tmp_path / 'file'
    media.write_bytes(b'x')
    with patch('lecture_util.media.require_executable', return_value='tool'), patch(
        'lecture_util.media.subprocess.run', return_value=result
    ), pytest.raises(LectureUtilError):
        resolve_source(str(media))


def test_missing_file(tmp_path):
    with pytest.raises(LectureUtilError, match='does not exist'):
        resolve_source(str(tmp_path / 'missing.mp3'))


@pytest.mark.parametrize('video', [False, True])
@patch("lecture_util.pipeline.preflight_transcription")
def test_local_pipeline_real_ffmpeg(preflight, tmp_path, video):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('FFmpeg tools unavailable')
    media = tmp_path / ('강의 영상.mp4' if video else '강의 녹음.wav')
    command = ['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'sine=duration=0.2']
    if video:
        command += ['-f', 'lavfi', '-i', 'color=size=16x16:duration=0.2',
                    '-c:v', 'mpeg4', '-shortest']
    subprocess.run(command + [str(media)], check=True)
    original = media.read_bytes()
    source = resolve_source(str(media))
    assert source.kind == ('video' if video else 'audio')
    transcript = Transcript('ko', .2, 'test', 'large-v3', 'large-v3',
                            [Segment(0, .2, '강의')])
    with patch('lecture_util.pipeline.download_hls') as download, patch(
        'lecture_util.pipeline.transcribe_audio', return_value=transcript
    ) as transcribe:
        paths, state, _ = prepare_lecture(str(media), tmp_path / 'cache')
        prepare_lecture(str(media), tmp_path / 'cache')
        assert transcribe.call_count == 1
        prepare_lecture(str(media), tmp_path / 'cache', force=True)
        assert transcribe.call_count == 2
        download.assert_not_called()
    assert paths.audio.is_file()
    assert 'download' not in state.data['stages']
    assert media.read_bytes() == original


def test_local_cli_publication_and_conflict(tmp_path):
    from io import StringIO
    from rich.console import Console
    from lecture_util.cli import _execute_run
    from lecture_util.models import LecturePaths, LectureSource
    from test_cli import options, COURSE
    from lecture_util.vault import COURSES_DIRECTORY

    vault = tmp_path / 'vault'
    lecture_dir = vault / COURSES_DIRECTORY / COURSE / 'Lectures'
    lecture_dir.mkdir(parents=True)
    media = tmp_path / '녹음.m4a'
    media.write_bytes(b'original')
    selected = options()
    selected.url = str(media)
    selected.source = LectureSource('audio', str(media), 'hash')
    cached = LecturePaths(tmp_path / 'cache')
    cached.root.mkdir()
    cached.summary.write_text('Summary')
    cached.transcript_markdown.write_text('Transcript')
    output = StringIO()
    with patch('lecture_util.cli.run_lecture', return_value=cached) as run, patch(
        'lecture_util.cli.CodexSummarizer'
    ), patch('lecture_util.cli.console', Console(file=output)):
        _execute_run(selected, vault_root=vault, cache_root=tmp_path / "cache")
        assert run.call_args.kwargs['video_path'] is None
        assert str(media) in next(lecture_dir.rglob('*전사.md')).read_text()
        assert 'Downloading' not in output.getvalue()
        assert 'Video:' not in output.getvalue()
        run.reset_mock()
        with pytest.raises(LectureUtilError, match='already exists'):
            _execute_run(selected, vault_root=vault, cache_root=tmp_path / "cache")
        run.assert_not_called()
    assert media.read_bytes() == b'original'


def test_relative_source_and_tilde(tmp_path, monkeypatch):
    media = tmp_path / '녹음.mp3'
    media.write_bytes(b'audio')
    monkeypatch.chdir(tmp_path)
    with patch('pathlib.Path.home', return_value=tmp_path), patch(
        'lecture_util.media.require_executable', return_value='tool'
    ), patch('lecture_util.media.subprocess.run', return_value=probe_result(
        [{'codec_type': 'audio'}]
    )):
        assert resolve_source('녹음.mp3').location == str(media.resolve())
        with patch('os.path.expanduser', return_value=str(tmp_path)):
            assert resolve_source('~/녹음.mp3').location == str(media.resolve())


def test_cli_run_and_bare_path(tmp_path):
    from typer.testing import CliRunner
    from lecture_util.cli import app
    from lecture_util.models import LectureSource
    from test_cli import configured_defaults
    media = tmp_path / '강의 녹음.m4a'
    media.write_bytes(b'audio')
    source = LectureSource('audio', str(media), 'hash')
    runner = CliRunner()
    with patch('lecture_util.cli.resolve_source', return_value=source), patch(
        'lecture_util.cli.load_config', return_value=configured_defaults()
    ), patch('lecture_util.cli._execute_run') as execute:
        result = runner.invoke(app, ['run', str(media), '--course', 'Course',
                                    '--date', '2026-09-07', '--title', 'Title'])
        assert result.exit_code == 0, result.output
        assert execute.call_args.args[0].source == source
        with patch('lecture_util.cli._interactive_terminal', return_value=True), patch(
            'lecture_util.cli.prompt_lecture', return_value=None
        ) as prompt:
            result = runner.invoke(app, [str(media)])
            assert result.exit_code == 0, result.output
            assert prompt.call_args.args[0] == str(media)


@patch("lecture_util.pipeline.preflight_transcription")
def test_retry_after_audio_failure(preflight, tmp_path):
    from lecture_util.models import LectureSource
    media = tmp_path / 'source.wav'
    media.write_bytes(b'original')
    source = LectureSource('audio', str(media), 'hash')
    with patch('lecture_util.pipeline.extract_audio', side_effect=LectureUtilError('broken')):
        with pytest.raises(LectureUtilError):
            prepare_lecture(str(media), tmp_path / 'cache', source=source)
    paths, state = create_workspace(str(media), tmp_path / 'cache', source=source)
    assert state.data['stages']['audio']['status'] == 'failed'
    with patch('lecture_util.pipeline.extract_audio', side_effect=lambda src, dst: dst.write_bytes(b'wav')) as extract, patch(
        'lecture_util.pipeline.transcription_stage'
    ):
        prepare_lecture(str(media), tmp_path / 'cache', source=source)
        extract.assert_called_once()
    assert media.read_bytes() == b'original'
