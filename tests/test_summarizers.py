from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lecture_util.summarizers import CodexSummarizer, _agent_prompt


class SummarizerTests(unittest.TestCase):
    def test_codex_is_the_only_summarizer(self) -> None:
        self.assertEqual(CodexSummarizer().name, "codex")
        self.assertEqual(CodexSummarizer(model="custom-model").model, "custom-model")

    def test_codex_reads_transcript_from_its_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transcript_path = Path(directory) / "transcript.md"
            transcript_path.write_text("secret transcript body", encoding="utf-8")

            def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
                output_path = Path(argv[argv.index("--output-last-message") + 1])
                output_path.write_text("# Summary\n", encoding="utf-8")
                return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

            with (
                patch("lecture_util.summarizers._require_cli", return_value="/bin/codex"),
                patch("lecture_util.summarizers.subprocess.run", side_effect=fake_run) as run,
            ):
                result = CodexSummarizer(model="test-model", reasoning_effort="high").generate(
                    "developer instructions",
                    "summarize this lecture",
                    transcript_path,
                )

        argv = run.call_args.args[0]
        prompt = run.call_args.kwargs["input"]
        self.assertEqual(result, "# Summary")
        self.assertEqual(argv[argv.index("--model") + 1], "test-model")
        self.assertEqual(argv[argv.index("--config") + 1], 'model_reasoning_effort="high"')
        self.assertEqual(argv[argv.index("--cd") + 1], str(transcript_path.parent.resolve()))
        self.assertIn("`transcript.md`", prompt)
        self.assertNotIn("secret transcript body", prompt)
        self.assertNotIn("```", prompt)

    def test_default_model_omits_model_argument(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transcript = Path(directory) / "transcript.md"
            transcript.write_text("Lecture", encoding="utf-8")

            def fake_run(argv, **kwargs):
                Path(argv[argv.index("--output-last-message") + 1]).write_text("Notes")
                return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

            with (
                patch("lecture_util.summarizers._require_cli", return_value="codex"),
                patch("lecture_util.summarizers.subprocess.run", side_effect=fake_run) as run,
            ):
                CodexSummarizer().generate("Instructions", "Summarize", transcript)
            self.assertNotIn("--model", run.call_args.args[0])
            self.assertNotIn("--config", run.call_args.args[0])

    def test_agent_prompt_allows_reading_but_forbids_file_changes(self) -> None:
        prompt = _agent_prompt("developer", "summarize", "transcript.md")
        self.assertIn("Read that file", prompt)
        self.assertIn("do not modify any files", prompt)
        self.assertNotIn("Do not use tools", prompt)


if __name__ == "__main__":
    unittest.main()
