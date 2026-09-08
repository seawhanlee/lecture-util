import io
import json
import subprocess
from unittest.mock import Mock, patch

import pytest

from lecture_util.errors import CommandError
from lecture_util.media import DOWNLOAD_PROGRESS_PREFIX, run_download_command


def line(status='downloading', speed=3355443):
    return DOWNLOAD_PROGRESS_PREFIX + json.dumps({'status': status, 'speed': speed}) + '\n'


def process_for(output, code=0):
    return Mock(stdout=io.StringIO(output), wait=Mock(return_value=code),
                poll=Mock(return_value=code))


def test_speed_updates_throttled_and_finalization_clears_speed():
    process = process_for(line(speed=None) + line() + line() + line('finished') +
                          'lecture-util-postprocess:started\n')
    events = []
    with patch('lecture_util.media.subprocess.Popen', return_value=process), patch(
        'lecture_util.media.monotonic', side_effect=[0, .2, 1.2]
    ):
        run_download_command(['yt-dlp'], progress=events.append)
    assert [event.message for event in events] == [
        'Downloading · 속도 계산 중', 'Downloading · 3.2 MiB/s',
        'Finalizing downloaded video',
    ]
    assert all(event.stage == 'download' and event.status == 'update' for event in events)


def test_invalid_output_is_ignored_and_diagnostic_retained():
    process = process_for('ordinary output\n' + DOWNLOAD_PROGRESS_PREFIX + '{broken\n' +
                          line(speed='nonsense') + 'useful failure\n', code=1)
    events = []
    with patch('lecture_util.media.subprocess.Popen', return_value=process):
        with pytest.raises(CommandError, match='useful failure'):
            run_download_command(['yt-dlp'], progress=events.append)
    assert events == []


def test_cancel_terminates_and_waits():
    process = process_for(line())
    process.poll.return_value = None
    events = Mock(side_effect=KeyboardInterrupt)
    with patch('lecture_util.media.subprocess.Popen', return_value=process):
        with pytest.raises(KeyboardInterrupt):
            run_download_command(['yt-dlp'], progress=events)
    process.terminate.assert_called_once()
    process.wait.assert_called_once_with(timeout=5)
    assert process.stdout.closed


def test_cancel_kills_unresponsive_process():
    process = process_for(line())
    process.poll.return_value = None
    process.wait.side_effect = [subprocess.TimeoutExpired('yt-dlp', 5), 1]
    with patch('lecture_util.media.subprocess.Popen', return_value=process):
        with pytest.raises(KeyboardInterrupt):
            run_download_command(['yt-dlp'], progress=Mock(side_effect=KeyboardInterrupt))
    process.kill.assert_called_once()
    assert process.wait.call_count == 2


def test_pipeline_forwards_events_to_console(tmp_path):
    from rich.console import Console
    from lecture_util.console_progress import ConsoleProgressReporter
    from lecture_util.pipeline import download_stage
    from lecture_util.progress import report
    from lecture_util.state import create_workspace

    paths, state = create_workspace('https://example.com/index.m3u8', tmp_path)
    output = io.StringIO()

    def download(url, destination, *, progress):
        report(progress, 'download', 'update', 'Downloading · 3.2 MiB/s')
        destination.write_bytes(b'video')

    with patch('lecture_util.pipeline.download_hls', side_effect=download), patch(
        'lecture_util.pipeline.tool_version', return_value='test'
    ), ConsoleProgressReporter(('download',), console=Console(file=output)) as reporter:
        download_stage(paths, state, progress=reporter)
    assert '3.2 MiB/s' in output.getvalue()
