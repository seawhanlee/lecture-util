from __future__ import annotations

import subprocess
import signal
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

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


def test_material_json_uses_disposable_workspace_and_schema():
    def start(argv, **kwargs):
        output = Path(argv[argv.index("--output-last-message") + 1])
        output.write_text('{"pages": []}', encoding="utf-8")
        schema_path = Path(argv[argv.index("--output-schema") + 1])
        assert schema_path.is_file()
        return Mock(returncode=0, communicate=Mock(return_value=("", "")))

    with (patch("lecture_util.summarizers._require_cli", return_value="codex"),
          patch("lecture_util.summarizers.subprocess.Popen", side_effect=start) as run):
        data = CodexSummarizer().generate_json("Read @/course/pdfs", {"type": "object"})
    argv = run.call_args.args[0]
    assert argv[argv.index("--sandbox") + 1] == "workspace-write"
    assert "sandbox_workspace_write.exclude_slash_tmp=true" in argv
    assert "sandbox_workspace_write.writable_roots=[]" in argv
    assert not Path(argv[argv.index("--cd") + 1]).exists()
    assert data == {"pages": []}


def test_material_cancellation_terminates_codex_and_helper_group():
    process = Mock(pid=1234)
    process.communicate.side_effect = [KeyboardInterrupt(), ("", "")]
    with (patch("lecture_util.summarizers._require_cli", return_value="codex"),
          patch("lecture_util.summarizers.subprocess.Popen", return_value=process),
          patch("lecture_util.summarizers.os.killpg") as kill):
        with pytest.raises(KeyboardInterrupt):
            CodexSummarizer().generate_json("Read PDFs", {})
    kill.assert_called_once_with(1234, signal.SIGTERM)
    assert process.communicate.call_count == 2


def test_material_json_rejects_non_object_response():
    from lecture_util.errors import LectureUtilError
    with patch.object(CodexSummarizer, "_request", return_value="[]"):
        with pytest.raises(LectureUtilError, match="invalid material JSON"):
            CodexSummarizer().generate_json("Read PDFs", {})
