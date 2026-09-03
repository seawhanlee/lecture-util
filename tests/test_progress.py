from __future__ import annotations

import unittest

from lecture_util.progress import ProgressEvent, format_duration, format_size, report


class ProgressTests(unittest.TestCase):
    def test_human_readable_measurements(self) -> None:
        self.assertEqual(format_duration(12.34), "12.3s")
        self.assertEqual(format_duration(132), "2m 12s")
        self.assertEqual(format_size(1024 * 1024), "1.0 MiB")

    def test_report_emits_structured_event(self) -> None:
        events: list[ProgressEvent] = []
        report(events.append, "download", "start", "Downloading HLS video")
        self.assertEqual(
            events,
            [ProgressEvent("download", "start", "Downloading HLS video")],
        )
        report(None, "download", "complete", "ignored")


if __name__ == "__main__":
    unittest.main()
