from io import StringIO
from unittest.mock import patch

import pytest
from rich.console import Console

from lecture_util.console_progress import ConsoleProgressReporter
from lecture_util.progress import ProgressEvent


def test_redirected_progress_preserves_plain_logs():
    output = StringIO()
    with ConsoleProgressReporter(("download",), console=Console(file=output)) as reporter:
        reporter(ProgressEvent("download", "start", "Fetch [lecture]"))
        reporter(ProgressEvent("download", "complete", "Downloaded"))
    assert "[1/1] Fetch [lecture]" in output.getvalue()
    assert "Downloaded" in output.getvalue()
    assert "\x1b" not in output.getvalue()


def test_board_keeps_warning_and_cached_state():
    output = StringIO()
    console = Console(file=output, width=80)
    reporter = ConsoleProgressReporter(("download", "audio", "summary"), console=console)
    reporter(ProgressEvent("download", "cached", "Already downloaded"))
    reporter(ProgressEvent("audio", "start", "Extracting"))
    reporter(ProgressEvent("audio", "warning", "Fallback [cpu]"))
    reporter(ProgressEvent("audio", "complete", "Extracted"))
    output.truncate(0)
    output.seek(0)
    console.print(reporter.render())
    text = output.getvalue()
    assert "Cached" in text and "Complete" in text and "Waiting" in text
    assert "Fallback [cpu]" in text


@pytest.mark.parametrize("error", [RuntimeError("broken"), KeyboardInterrupt()])
def test_live_board_stops_and_retains_warning_on_failure(error):
    output = StringIO()
    console = Console(file=output, force_terminal=True)
    with (
        patch.dict("os.environ", {"TERM": "xterm-256color"}),
        patch("lecture_util.console_progress.Live") as live,
    ):
        with pytest.raises(type(error)):
            with ConsoleProgressReporter(("summary",), console=console) as reporter:
                reporter(ProgressEvent("summary", "start", "Summarizing"))
                reporter(ProgressEvent("summary", "warning", "Keep this warning"))
                raise error
        live.return_value.stop.assert_called_once()
    assert "Keep this warning" in output.getvalue()
    assert ("Interrupted" if isinstance(error, KeyboardInterrupt) else "Failed") in output.getvalue()
