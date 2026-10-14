from __future__ import annotations

import io
import subprocess
import unittest
from unittest.mock import MagicMock, patch

from lecture_util.notifications import (
    _backend_command,
    _bell,
    _format_duration,
    notify,
    run_failed_message,
    run_succeeded_message,
)


class NotificationTests(unittest.TestCase):
    def test_backend_command_macos(self) -> None:
        with (
            patch("sys.platform", "darwin"),
            patch("shutil.which", return_value="/usr/bin/osascript"),
        ):
            cmd = _backend_command("Title", "Message")
        self.assertIsNotNone(cmd)
        self.assertEqual(cmd[0], "osascript")
        # Ensure arguments are passed as separate argv items to prevent script injection
        self.assertIn("on run argv", cmd)
        self.assertEqual(cmd[-2], "Title")
        self.assertEqual(cmd[-1], "Message")

    def test_backend_command_linux(self) -> None:
        with (
            patch("sys.platform", "linux"),
            patch("shutil.which", return_value="/usr/bin/notify-send"),
        ):
            cmd = _backend_command("Title", "Message")
        self.assertEqual(cmd, ["notify-send", "--app-name=lecture-util", "Title", "Message"])

    def test_backend_command_unsupported(self) -> None:
        with (
            patch("sys.platform", "win32"),
            patch("shutil.which", return_value=None),
        ):
            cmd = _backend_command("Title", "Message")
        self.assertIsNone(cmd)

    def test_notify_success(self) -> None:
        with (
            patch("lecture_util.notifications._backend_command", return_value=["fake-notify", "Title", "Message"]),
            patch("subprocess.run") as mock_run,
        ):
            result = notify("Title", "Message")
        self.assertTrue(result)
        mock_run.assert_called_once_with(
            ["fake-notify", "Title", "Message"],
            check=True,
            timeout=5,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def test_notify_subprocess_failure_falls_back_to_bell(self) -> None:
        with (
            patch("lecture_util.notifications._backend_command", return_value=["fake-notify"]),
            patch("subprocess.run", side_effect=subprocess.SubprocessError("Failed")),
            patch("lecture_util.notifications._bell") as mock_bell,
        ):
            result = notify("Title", "Message")
        self.assertFalse(result)
        mock_bell.assert_called_once()

    def test_bell_writes_to_tty(self) -> None:
        fake_stdout = io.StringIO()
        fake_stdout.isatty = MagicMock(return_value=True)  # type: ignore[method-assign]
        with patch("sys.stdout", fake_stdout):
            _bell()
        self.assertEqual(fake_stdout.getvalue(), "\a")

    def test_bell_skips_non_tty(self) -> None:
        fake_stdout = io.StringIO()
        fake_stdout.isatty = MagicMock(return_value=False)  # type: ignore[method-assign]
        with patch("sys.stdout", fake_stdout):
            _bell()
        self.assertEqual(fake_stdout.getvalue(), "")

    def test_format_duration(self) -> None:
        self.assertEqual(_format_duration(45), "45s")
        self.assertEqual(_format_duration(125), "2m 5s")
        self.assertEqual(_format_duration(3665), "1h 1m 5s")

    def test_run_succeeded_message(self) -> None:
        msg = run_succeeded_message("Physics · 2026-09-07 Wave", 75.2)
        self.assertEqual(msg, "✓ Physics · 2026-09-07 Wave (1m 15s)")

    def test_run_failed_message(self) -> None:
        msg = run_failed_message("Physics · 2026-09-07 Wave", "Connection error\nTraceback...")
        self.assertEqual(msg, "✗ Physics · 2026-09-07 Wave failed: Connection error")
