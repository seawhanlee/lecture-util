from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from lecture_util.cli import app
from lecture_util.configuration import AppConfig
from lecture_util.vault import COURSES_DIRECTORY

URL = "https://example.com/lecture.m3u8?part=1&quality=high"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    for course in ("Course A", "Course B"):
        (vault / COURSES_DIRECTORY / course / "Lectures").mkdir(parents=True)
    config = AppConfig(
        vault_root=vault, video_root=tmp_path / "videos",
        semester_start="2026-09-01", llm_model="test-model", reasoning_effort="high",
    )
    monkeypatch.setattr("lecture_util.cli._interactive_terminal", lambda: True)
    monkeypatch.setattr("lecture_util.cli.load_config", lambda: config)
    execute = Mock()
    monkeypatch.setattr("lecture_util.cli._execute_run", execute)
    return config, execute


def test_url_shortcut_prompts_and_preserves_query_string(setup):
    config, execute = setup
    result = CliRunner().invoke(app, [URL], input="\n2\n2\n\nLecture [intro]\ny\n")
    assert result.exit_code == 0, result.output
    selected = execute.call_args.args[0]
    assert selected.url == URL
    assert selected.course == "Course B"
    assert selected.lecture_date == "2026-09-08"
    assert selected.semester_start == "2026-09-01"
    assert selected.title == "Lecture [intro]"
    assert selected.llm_model == "test-model"
    assert selected.reasoning_effort == "high"
    assert selected.force is False
    assert execute.call_args.kwargs["video_root"] == config.video_root
    assert "Thinking effort" in result.output
    assert "Start processing?" in result.output


def test_invalid_metadata_and_conflicting_note_allow_correction(setup):
    config, execute = setup
    existing = (config.vault_root / COURSES_DIRECTORY / "Course A" / "Lectures"
                / "2주차" / "2026-09-08 Existing.md")
    existing.parent.mkdir()
    existing.write_text("Original")
    result = CliRunner().invoke(
        app, [URL],
        input="\n99\n1\n0\n2\nwrong\n2026-09-01\n\n../bad\nExisting\nNew\ny\n",
    )
    assert result.exit_code == 0, result.output
    assert execute.call_args.args[0].title == "New"
    assert execute.call_args.args[0].lecture_date == "2026-09-08"
    assert "already exists" in result.output
    assert "Choose a lecture date in week 2" in result.output
    assert existing.read_text() == "Original"


@pytest.mark.parametrize("input_text", ["\n1\n2\n\nLecture\nn\n", ""])
def test_cancel_or_eof_never_starts_processing(setup, input_text):
    _, execute = setup
    result = CliRunner().invoke(app, [URL], input=input_text)
    assert result.exit_code == 0, result.output
    execute.assert_not_called()
    assert "Cancelled" in result.output


def test_url_requires_terminal(setup, monkeypatch):
    _, execute = setup
    monkeypatch.setattr("lecture_util.cli._interactive_terminal", lambda: False)
    result = CliRunner().invoke(app, [URL])
    assert result.exit_code == 2
    assert "interactive terminal" in result.output
    execute.assert_not_called()


@pytest.mark.parametrize("args", [["https://example.com/page"], [URL, "extra"], ["rnu"]])
def test_invalid_url_extra_argument_and_unknown_command_fail(setup, args):
    _, execute = setup
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 2, result.output
    execute.assert_not_called()


def test_first_run_onboards_before_rich_prompts(setup, monkeypatch):
    config, execute = setup
    monkeypatch.setattr("lecture_util.cli.load_config", lambda: None)
    onboard = Mock(return_value=config)
    save = Mock(return_value=config)
    monkeypatch.setattr("lecture_util.cli.run_onboarding", onboard)
    monkeypatch.setattr("lecture_util.cli.save_config", save)
    result = CliRunner().invoke(app, [URL], input="\n1\n1\n\nLecture\ny\n")
    assert result.exit_code == 0, result.output
    onboard.assert_called_once()
    save.assert_called_once_with(config)
    execute.assert_called_once()


def test_video_mode_skips_note_preview(setup):
    _, execute = setup
    result = CliRunner().invoke(app, [URL], input="video\n1\n1\n\nLecture\ny\n")
    assert result.exit_code == 0, result.output
    assert execute.call_args.args[0].video_only is True
    assert "Thinking effort" not in result.output
    assert "Video" in result.output
