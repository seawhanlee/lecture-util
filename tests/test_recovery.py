from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from lecture_util.cli import app
from lecture_util.errors import LectureUtilError
from lecture_util.models import LecturePaths, RunOptions
from lecture_util.pipeline import execute_run
from lecture_util.recovery import load_request, save_request
from lecture_util.state import lecture_id
from lecture_util.vault import COURSES_DIRECTORY


def request() -> RunOptions:
    return RunOptions(url='https://example.com/a.m3u8', course='Course', lecture_date='2026-09-07',
                      title='Title', llm_model='saved-model', tags=None, whisper_model='turbo',
                      language='ko', device='cpu', prompt='Original prompt', force=False,
                      semester_start='2026-08-31', compute_type='int8', batch_size=4, beam_size=1)


def test_resume_uses_snapshot_without_loading_current_config(tmp_path):
    options = request()
    root = tmp_path / f'lecture-{lecture_id(options.url)}'
    save_request(root, replace(options, force=True), tmp_path / 'vault', tmp_path / 'videos')
    with (patch('lecture_util.cli.load_config', side_effect=AssertionError('must not load settings')),
          patch('lecture_util.cli._execute_run') as execute):
        result = CliRunner().invoke(app, ['resume', str(root)])
    assert result.exit_code == 0, result.output
    assert execute.call_args.args[0] == options


def test_legacy_workspace_has_actionable_resume_error(tmp_path):
    with pytest.raises(LectureUtilError, match='original URL'):
        load_request(tmp_path)


def test_partial_publication_resume_does_not_repeat_processing(tmp_path, monkeypatch):
    import lecture_util.vault as vault_module
    options = request()
    vault = tmp_path / 'vault'
    lecture = vault / COURSES_DIRECTORY / options.course / 'Lectures'
    lecture.mkdir(parents=True)
    cache = tmp_path / 'cache'
    root = cache / f'lecture-{lecture_id(options.url)}'
    def process(*args, **kwargs):
        paths = LecturePaths(root)
        paths.summary.write_text('### Generated summary')
        paths.transcript_markdown.write_text('# Transcript\nHello')
        return paths
    create = vault_module._create_note
    count = 0
    def fail_second(path, content):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError('disk full')
        create(path, content)
    monkeypatch.setattr(vault_module, '_create_note', fail_second)
    with patch('lecture_util.pipeline.run_lecture', side_effect=process):
        with pytest.raises(OSError, match='disk full'):
            execute_run(options, vault_root=vault, video_root=tmp_path / 'videos', cache_root=cache)
    assert len(list(lecture.rglob('*.md'))) == 1
    monkeypatch.setattr(vault_module, '_create_note', create)
    with patch('lecture_util.pipeline.run_lecture', side_effect=AssertionError('must not transcribe')):
        restored, saved_vault, videos = load_request(root)
        execute_run(restored, vault_root=saved_vault, video_root=videos, cache_root=cache)
    assert len(list(lecture.rglob('*.md'))) == 2


def test_workspace_collision_does_not_overwrite_request(tmp_path):
    from lecture_util.state import workspace_lock
    options = request()
    root = tmp_path / f'lecture-{lecture_id(options.url)}'
    save_request(root, options, tmp_path / 'vault', tmp_path / 'videos')
    before = (root / 'request.json').read_bytes()
    with workspace_lock(root), pytest.raises(LectureUtilError, match='Another process'):
        execute_run(replace(options, title='Other'), cache_root=tmp_path)
    assert (root / 'request.json').read_bytes() == before
