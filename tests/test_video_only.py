from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from lecture_util.cli import _execute_run, app
from lecture_util.configuration import app_config_from_dict
from lecture_util.errors import LectureUtilError
from lecture_util.vault import COURSES_DIRECTORY
from test_cli import options, configured_defaults, URL, COURSE


def test_video_only_skips_notes_models_and_audio(tmp_path):
    vault = tmp_path / 'vault'
    notes = vault / COURSES_DIRECTORY / COURSE / 'Lectures' / '1주차'
    notes.mkdir(parents=True)
    existing = notes / '2026-09-04 압축성 유동.md'
    existing.write_text('Existing note')
    selected = options()
    selected.video_only = True
    with patch('lecture_util.cli.download_stage') as download, patch(
        'lecture_util.cli.audio_stage'
    ) as audio, patch('lecture_util.pipeline.CodexSummarizer') as model, patch(
        'lecture_util.pipeline.run_lecture'
    ) as run, patch('lecture_util.pipeline.publish_lecture_notes') as publish:
        _execute_run(selected, vault_root=vault, video_root=tmp_path / 'videos',
                     cache_root=tmp_path / 'cache')
        download.assert_called_once()
        audio.assert_not_called()
        model.assert_not_called()
        run.assert_not_called()
        publish.assert_not_called()
    assert existing.read_text() == 'Existing note'


@pytest.mark.parametrize('command', ['run', 'download'])
def test_cli_video_only(command):
    with patch('lecture_util.cli.load_config', return_value=configured_defaults()) as load, patch(
        'lecture_util.cli._execute_download'
    ) as download:
        result = CliRunner().invoke(app, [command, URL, '--course', COURSE,
            '--title', 'Lecture', '--date', '2026-09-07', '--video-only'])
        assert result.exit_code == 0, result.output
        download.assert_called_once()
        assert load.call_args.kwargs['video_only'] is True


def test_video_only_rejects_local_input(tmp_path):
    media = tmp_path / 'local.mp4'
    media.write_bytes(b'video')
    with patch('lecture_util.cli.load_config', return_value=configured_defaults()), patch(
        'lecture_util.cli._execute_download'
    ) as download:
        result = CliRunner().invoke(app, ['run', str(media), '--course', COURSE,
            '--title', 'Lecture', '--date', '2026-09-07', '--video-only'])
        assert result.exit_code != 0
        download.assert_not_called()


def test_video_only_ignores_processing_configuration(tmp_path):
    config = configured_defaults(tmp_path / 'vault', tmp_path / 'videos')
    data = config.to_dict()
    data.update(whisper_model='', device='invalid', reasoning_effort='invalid')
    app_config_from_dict(data, validate_vault=False, video_only=True)
    with pytest.raises(LectureUtilError):
        app_config_from_dict(data, validate_vault=False)
