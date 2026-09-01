from __future__ import annotations

import json
import os
import unittest
from unittest.mock import Mock, patch

from lecture_util.errors import LectureUtilError
from lecture_util.summarizers import (
    OllamaSummarizer,
    create_summarizer,
    parse_opencode_jsonl,
)


class SummarizerTests(unittest.TestCase):
    def test_backend_model_requirements(self) -> None:
        with self.assertRaisesRegex(LectureUtilError, "--llm-model"):
            create_summarizer("openai", model=None)
        with self.assertRaisesRegex(LectureUtilError, "--llm-model"):
            create_summarizer("ollama", model=None)
        self.assertEqual(create_summarizer("codex", model=None).name, "codex")

    def test_openai_backend_validates_key_before_pipeline(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(LectureUtilError, "OPENAI_API_KEY"):
                create_summarizer("openai", model="test-model")

    def test_ollama_native_chat_request(self) -> None:
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"message": {"content": "summary"}}
        with patch("lecture_util.summarizers.httpx.post", return_value=response) as post:
            result = OllamaSummarizer("qwen", "http://localhost:11434/api").generate("dev", "user")
        self.assertEqual(result, "summary")
        self.assertEqual(post.call_args.args[0], "http://localhost:11434/api/chat")
        self.assertFalse(post.call_args.kwargs["json"]["stream"])

    def test_opencode_jsonl_parser_ignores_non_text_events(self) -> None:
        output = "\n".join(
            [
                json.dumps({"type": "step_start", "part": {"text": "ignore"}}),
                json.dumps({"type": "text", "part": {"text": "Hello "}}),
                "not json",
                json.dumps({"type": "text", "part": {"text": "world"}}),
            ]
        )
        self.assertEqual(parse_opencode_jsonl(output), "Hello world")


if __name__ == "__main__":
    unittest.main()
