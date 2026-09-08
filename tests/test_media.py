from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lecture_util.errors import CommandError
from lecture_util.media import download_hls, run_command


class MediaCommandTests(unittest.TestCase):
    def test_failed_command_preserves_diagnostic_output(self) -> None:
        failed = subprocess.CompletedProcess(["tool"], 1, stdout="", stderr="useful error")
        with patch("lecture_util.media.subprocess.run", return_value=failed):
            with self.assertRaisesRegex(CommandError, "useful error"):
                run_command(["tool"])

    def test_interrupted_download_removes_partial_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "cache" / "source.mp4"
            temporary = destination.with_name(".source.download.mp4")

            def interrupt_download(argv: list[str], **kwargs) -> None:
                output = Path(argv[argv.index("--output") + 1])
                output.write_bytes(b"incomplete")
                raise KeyboardInterrupt

            with (
                patch(
                    "lecture_util.media.require_executable",
                    side_effect=lambda name: name,
                ),
                patch("lecture_util.media.run_download_command", side_effect=interrupt_download),
                self.assertRaises(KeyboardInterrupt),
            ):
                download_hls("https://example.com/lecture.m3u8", destination)

            self.assertFalse(temporary.exists())
            self.assertFalse(destination.exists())

    def test_interrupted_redownload_preserves_completed_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "cache" / "source.mp4"
            destination.parent.mkdir(parents=True)
            destination.write_bytes(b"complete")
            temporary = destination.with_name(".source.download.mp4")

            def interrupt_download(argv: list[str], **kwargs) -> None:
                output = Path(argv[argv.index("--output") + 1])
                output.write_bytes(b"incomplete")
                raise KeyboardInterrupt

            with (
                patch(
                    "lecture_util.media.require_executable",
                    side_effect=lambda name: name,
                ),
                patch("lecture_util.media.run_download_command", side_effect=interrupt_download),
                self.assertRaises(KeyboardInterrupt),
            ):
                download_hls("https://example.com/lecture.m3u8", destination)

            self.assertFalse(temporary.exists())
            self.assertEqual(destination.read_bytes(), b"complete")


if __name__ == "__main__":
    unittest.main()
