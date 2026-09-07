from io import StringIO
from unittest.mock import patch

import pytest
from rich.console import Console
from rich.cells import cell_len

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


@pytest.mark.parametrize("width", [40, 80])
def test_board_contains_literal_lecture_details_and_wraps_url(width):
    output = StringIO()
    console = Console(file=output, width=width)
    label = "브랜드 생성 · 2026-09-07 [2-2]"
    url = "https://example.com/" + "lecture/" * 20 + "index.m3u8"
    reporter = ConsoleProgressReporter(
        ("download",), console=console, lecture_label=label, source_url=url,
    )
    console.print(reporter.render())
    rendered = output.getvalue()
    lines = rendered.splitlines()
    content = "".join(line[1:-1].strip() for line in lines[1:-1])
    assert label in content
    assert url in content
    assert content.index(url) < content.index("Download")
    assert all(cell_len(line) <= width for line in lines)


def test_redirected_lecture_details_print_once():
    output = StringIO()
    with ConsoleProgressReporter(
        ("download",), console=Console(file=output, width=120),
        lecture_label="Course · 2026-09-07 [2-2]",
        source_url="https://example.com/index.m3u8",
    ) as reporter:
        reporter(ProgressEvent("download", "start", "Fetching"))
        reporter(ProgressEvent("download", "complete", "Downloaded"))
    rendered = output.getvalue()
    assert rendered.count("Course · 2026-09-07 [2-2]") == 1
    assert rendered.count("https://example.com/index.m3u8") == 1
    assert rendered.index("index.m3u8") < rendered.index("Fetching")
    assert "\x1b" not in rendered


def test_live_lecture_details_only_render_in_transient_panel():
    output = StringIO()
    console = Console(file=output, force_terminal=True)
    with (
        patch.dict("os.environ", {"TERM": "xterm-256color"}),
        patch("lecture_util.console_progress.Live") as live,
    ):
        with ConsoleProgressReporter(
            ("download",), console=console,
            lecture_label="Course", source_url="https://example.com/index.m3u8",
        ) as reporter:
            reporter(ProgressEvent("download", "complete", "Downloaded"))
        assert live.call_args.kwargs["transient"] is True
        live.return_value.stop.assert_called_once()
    assert output.getvalue() == ""


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
