from __future__ import annotations

import unittest

from lecture_util.summarizers import CodexSummarizer


class SummarizerTests(unittest.TestCase):
    def test_codex_is_the_only_summarizer(self) -> None:
        self.assertEqual(CodexSummarizer().name, "codex")
        self.assertEqual(CodexSummarizer(model="custom-model").model, "custom-model")

if __name__ == "__main__":
    unittest.main()
