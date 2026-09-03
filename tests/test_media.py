from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from lecture_util.errors import CommandError
from lecture_util.media import run_command


class MediaCommandTests(unittest.TestCase):
    def test_failed_command_preserves_diagnostic_output(self) -> None:
        failed = subprocess.CompletedProcess(["tool"], 1, stdout="", stderr="useful error")
        with patch("lecture_util.media.subprocess.run", return_value=failed):
            with self.assertRaisesRegex(CommandError, "useful error"):
                run_command(["tool"])


if __name__ == "__main__":
    unittest.main()
