"""Exercise real Hub progress rendering without network or model execution."""
from __future__ import annotations

import importlib
import sys
import warnings
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from huggingface_hub.utils import are_progress_bars_disabled, tqdm
from rich.console import Console

from lecture_util.console_progress import ConsoleProgressReporter
from lecture_util.progress import ProgressEvent
from lecture_util.transcription import _transcribe_mlx, transcribe_audio


@pytest.fixture
def hub_progress(monkeypatch: pytest.MonkeyPatch):
    # Isolate Hub's process-wide state from the developer's environment and tests.
    module = importlib.import_module("huggingface_hub.utils.tqdm")
    monkeypatch.setattr(module, "HF_HUB_DISABLE_PROGRESS_BARS", None)
    monkeypatch.setattr(module, "progress_bar_states", {"_global": True})
    return module


def emit_download_bars() -> None:
    with (
        tqdm(total=1, desc="Downloading bytes", file=sys.stderr) as transfer,
        tqdm(total=1, desc="Reconstructing", position=1, file=sys.stderr) as rebuild,
        tqdm(total=4, desc="Fetching 4 files", position=2, file=sys.stderr) as files,
    ):
        transfer.update(1)
        rebuild.update(1)
        files.update(4)


@pytest.mark.parametrize("device", ["mlx", "cuda", "cpu"])
@pytest.mark.parametrize("terminal", [True, False])
def test_hub_bars_do_not_leak_above_rich(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hub_progress, device: str,
    terminal: bool,
) -> None:
    baseline = StringIO()
    with redirect_stderr(baseline):
        emit_download_bars()
    assert "Fetching 4 files" in baseline.getvalue()
    assert "\x1b[A" in baseline.getvalue()

    audio = tmp_path / "audio.wav"
    audio.touch()
    output = StringIO()
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setattr("lecture_util.transcription.detect_device", lambda _: device)

    def backend(*args):
        assert are_progress_bars_disabled()
        emit_download_bars()
        print("Model diagnostic [preserved]", file=sys.stderr)
        warnings.warn("Model warning preserved", UserWarning)
        return [], "ko", 0.0

    target = "_transcribe_mlx" if device == "mlx" else "_transcribe_faster"
    monkeypatch.setattr(f"lecture_util.transcription.{target}", backend)
    console = Console(file=output, force_terminal=terminal, width=100)
    with pytest.warns(UserWarning, match="Model warning preserved"):
        with ConsoleProgressReporter(("transcription",), console=console) as reporter:
            reporter(ProgressEvent("transcription", "start", "Preparing model"))
            transcribe_audio(audio)
            reporter(ProgressEvent("transcription", "complete", "Transcribed"))
    text = output.getvalue()
    for description in ("Downloading bytes", "Reconstructing", "Fetching 4 files"):
        assert description not in text
    assert "Transcribed" in text
    if terminal:
        assert "Lecture processing" in text
        assert "Model diagnostic" in text
    assert not are_progress_bars_disabled()


@pytest.mark.parametrize("disabled_before", [False, True])
@pytest.mark.parametrize("error", [None, RuntimeError("download failed"), KeyboardInterrupt()])
def test_hub_setting_restored_after_success_failure_or_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hub_progress,
    disabled_before: bool, error: BaseException | None,
) -> None:
    hub_progress.progress_bar_states["_global"] = not disabled_before
    audio = tmp_path / "audio.wav"
    audio.touch()
    monkeypatch.setattr("lecture_util.transcription.detect_device", lambda _: "mlx")

    def backend(*args):
        assert are_progress_bars_disabled()
        if error is not None:
            raise error
        return [], "ko", 0.0

    monkeypatch.setattr("lecture_util.transcription._transcribe_mlx", backend)
    if error is None:
        transcribe_audio(audio)
    else:
        with pytest.raises(type(error)) as raised:
            transcribe_audio(audio)
        assert raised.value is error
    assert are_progress_bars_disabled() == disabled_before


def test_oom_retry_remains_quiet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hub_progress,
) -> None:
    audio = tmp_path / "audio.wav"
    audio.touch()
    monkeypatch.setattr("lecture_util.transcription.detect_device", lambda _: "mlx")
    monkeypatch.setattr("lecture_util.transcription._release_memory", lambda _: None)
    models = []

    def backend(_audio, model, _language):
        assert are_progress_bars_disabled()
        models.append(model)
        if model == "large-v3":
            raise MemoryError("out of memory")
        return [], "ko", 0.0

    monkeypatch.setattr("lecture_util.transcription._transcribe_mlx", backend)
    transcript = transcribe_audio(audio)
    assert models == ["large-v3", "turbo"]
    assert transcript.effective_model == "turbo"
    assert not are_progress_bars_disabled()


@pytest.mark.parametrize("setting", [True, False])
def test_explicit_hub_environment_setting_is_respected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hub_progress, setting: bool,
) -> None:
    # Hub reads the environment once at import time; exercise the parsed value.
    monkeypatch.setattr(hub_progress, "HF_HUB_DISABLE_PROGRESS_BARS", setting)
    audio = tmp_path / "audio.wav"
    audio.touch()
    monkeypatch.setattr("lecture_util.transcription.detect_device", lambda _: "mlx")

    def backend(*args):
        assert are_progress_bars_disabled() == setting
        return [], "ko", 0.0

    monkeypatch.setattr("lecture_util.transcription._transcribe_mlx", backend)
    if setting:
        transcribe_audio(audio)
    else:
        with pytest.warns(UserWarning, match="Cannot disable progress bars"):
            transcribe_audio(audio)
    assert are_progress_bars_disabled() == setting


def test_mlx_does_not_enable_its_own_transcription_bar(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = Mock(return_value={"segments": [], "language": "ko"})
    monkeypatch.setattr(
        "lecture_util.transcription._module",
        lambda *_: SimpleNamespace(transcribe=backend),
    )
    _transcribe_mlx(Path("audio.wav"), "large-v3", "auto")
    assert "verbose" in backend.call_args.kwargs
    assert backend.call_args.kwargs["verbose"] is None

