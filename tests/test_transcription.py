from __future__ import annotations

import ctypes
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from lecture_util.errors import DependencyError
from lecture_util.models import Segment, Transcript
from lecture_util.transcription import (
    _prepare_cuda_libraries,
    _transcribe_faster,
    detect_device,
    format_timestamp,
    is_out_of_memory,
    transcript_srt,
    transcribe_audio,
)


class TranscriptionTests(unittest.TestCase):
    def test_timestamp_and_srt_rendering(self) -> None:
        self.assertEqual(format_timestamp(3661.234), "01:01:01:234")
        transcript = Transcript(
            language="ko",
            duration=2,
            engine="test",
            requested_model="large-v3",
            effective_model="large-v3",
            segments=[Segment(0, 1.25, "hello")],
        )
        self.assertIn("00:00:00,000 --> 00:00:01,250", transcript_srt(transcript))

    def test_oom_detection_is_specific(self) -> None:
        self.assertTrue(is_out_of_memory(RuntimeError("CUDA out of memory")))
        self.assertTrue(is_out_of_memory(MemoryError()))
        self.assertFalse(is_out_of_memory(RuntimeError("model download failed")))

    def test_large_v3_retries_turbo_only_after_oom(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "audio.wav"
            audio.touch()
            calls: list[str] = []

            def fake_transcribe(
                _audio: Path, model: str, _language: str, _device: str
            ) -> tuple[list[Segment], str, float]:
                calls.append(model)
                if model == "large-v3":
                    raise RuntimeError("CUDA out of memory")
                return [Segment(0, 1, "ok")], "ko", 1

            with (
                patch("lecture_util.transcription.detect_device", return_value="cuda"),
                patch("lecture_util.transcription._transcribe_faster", side_effect=fake_transcribe),
            ):
                result = transcribe_audio(audio)

            self.assertEqual(calls, ["large-v3", "turbo"])
            self.assertEqual(result.effective_model, "turbo")
            self.assertIn("out of memory", result.fallback_reason or "")

    def test_non_oom_error_is_not_retried(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "audio.wav"
            audio.touch()
            with (
                patch("lecture_util.transcription.detect_device", return_value="cuda"),
                patch(
                    "lecture_util.transcription._transcribe_faster",
                    side_effect=RuntimeError("bad model"),
                ) as transcribe,
            ):
                with self.assertRaisesRegex(RuntimeError, "bad model"):
                    transcribe_audio(audio)
            self.assertEqual(transcribe.call_count, 1)

    def test_cuda_wheel_libraries_are_preloaded_in_dependency_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            packages = {
                "nvidia.cublas": root / "cublas",
                "nvidia.cudnn": root / "cudnn",
            }
            for package_path in packages.values():
                (package_path / "lib").mkdir(parents=True)
            filenames = ("libcublasLt.so.12", "libcublas.so.12", "libcudnn.so.9")
            for filename in filenames[:2]:
                (packages["nvidia.cublas"] / "lib" / filename).touch()
            (packages["nvidia.cudnn"] / "lib" / filenames[2]).touch()

            def fake_module(name: str, _hint: str) -> SimpleNamespace:
                return SimpleNamespace(__path__=[str(packages[name])])

            with (
                patch("lecture_util.transcription._CUDA_LIBRARY_HANDLES", []),
                patch("lecture_util.transcription._module", side_effect=fake_module),
                patch(
                    "lecture_util.transcription.ctypes.CDLL",
                    side_effect=lambda *_args, **_kwargs: object(),
                ) as load,
            ):
                _prepare_cuda_libraries()
                _prepare_cuda_libraries()

            self.assertEqual(
                [Path(call.args[0]).name for call in load.call_args_list],
                list(filenames),
            )
            self.assertTrue(
                all(
                    call.kwargs["mode"] == ctypes.RTLD_GLOBAL
                    for call in load.call_args_list
                )
            )

    def test_missing_cuda_wheel_library_has_installation_hint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            package = SimpleNamespace(__path__=[directory])
            with (
                patch("lecture_util.transcription._CUDA_LIBRARY_HANDLES", []),
                patch("lecture_util.transcription._module", return_value=package),
            ):
                with self.assertRaisesRegex(DependencyError, "uv sync"):
                    _prepare_cuda_libraries()

    def test_cuda_transcription_prepares_wheel_libraries(self) -> None:
        segment = SimpleNamespace(start=0, end=1, text="hello")
        info = SimpleNamespace(language="en", duration=1)
        model = MagicMock()
        model.transcribe.return_value = ([segment], info)
        faster_whisper = SimpleNamespace(WhisperModel=MagicMock(return_value=model))

        with (
            patch("lecture_util.transcription._prepare_cuda_libraries") as prepare,
            patch("lecture_util.transcription._module", return_value=faster_whisper),
        ):
            segments, language, duration = _transcribe_faster(
                Path("audio.wav"), "large-v3", "auto", "cuda"
            )

        prepare.assert_called_once_with()
        self.assertEqual(segments, [Segment(0, 1, "hello")])
        self.assertEqual((language, duration), ("en", 1))

    def test_explicit_cpu_is_always_allowed(self) -> None:
        with patch("lecture_util.transcription.platform.system", return_value="Plan9"):
            self.assertEqual(detect_device("cpu"), "cpu")

    def test_mlx_is_rejected_outside_apple_silicon(self) -> None:
        with (
            patch("lecture_util.transcription.platform.system", return_value="Linux"),
            patch("lecture_util.transcription.platform.machine", return_value="x86_64"),
        ):
            with self.assertRaisesRegex(Exception, "Apple Silicon"):
                detect_device("mlx")


if __name__ == "__main__":
    unittest.main()
