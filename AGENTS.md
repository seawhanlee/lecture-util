# Repository Guidelines

## Project Structure & Module Organization

Application code lives in `src/lecture_util/`. `cli.py` defines Typer commands, while `pipeline.py` coordinates HLS downloading, local media preparation, audio extraction, transcription, and summarization. Keep focused logic in the existing modules (`media.py`, `transcription.py`, `summary.py`, `state.py`, and related helpers) rather than expanding the CLI layer. `models.py` defines shared data models, including source identity and run options; `media.py` validates sources and inspects local media streams with `ffprobe`.

`tui.py` implements the Textual lecture form, `onboarding.py` handles initial setup, and `form_ui.py` holds shared form components. `configuration.py` owns persisted settings and validation, while `vault.py` discovers courses, resolves publication paths, and publishes notes. Keep summarizer backend integration in `summarizers.py`, progress events in `progress.py`, and Rich terminal rendering in `console_progress.py`.

`interactive.py` handles Rich prompts when a URL or local path is passed directly. `codex_models.py` discovers Codex models and supported reasoning efforts with a local cache fallback; keep model selection behavior in sync across onboarding and the lecture form.

`recovery.py` persists complete run requests and validates resume inputs. `state.py` owns atomic state writes, fingerprints, and workspace locks; publication recovery records belong in `vault.py`. `benchmark.py` is an opt-in local transcription comparison tool, separate from normal lecture processing.

Tests live in `tests/` and generally mirror module names, for example `tests/test_summary.py`. Directories such as `output/` or `lecture-util-test.*/` contain generated media, transcripts, summaries, and run state; treat them as artifacts, not source fixtures. Project metadata and dependencies are defined in `pyproject.toml`, with exact resolutions in `uv.lock`.

## Build, Test, and Development Commands

- `uv sync --dev`: create/update `.venv` and install runtime plus test dependencies.
- `uv run lecture-util doctor`: verify external tools and platform-specific transcription support.
- `uv run lecture-util`: launch the interactive CLI.
- `uv run lecture-util onboard`: configure or update the Vault, video storage, semester, and model defaults in an interactive terminal.
- `uv run lecture-util config`: edit saved settings; this is an alias for the onboarding settings flow.
- `uv run lecture-util run URL --course 'Course name' --date 2026-09-07 --title 'Lecture title'`: exercise the complete non-interactive pipeline after onboarding, using an existing course.
- `uv run lecture-util run './lecture.m4a' --course 'Course name' --date 2026-09-07 --title 'Lecture title'`: transcribe and summarize a local recording or video without moving the original.
- Add `--video-only` to `run URL ...` or `download URL ...` to save only the HLS video. Without this flag, `download` also extracts audio; standalone `transcribe` and `summarize` commands operate on a cache directory without publishing notes.
- `uv run lecture-util resume LECTURE_DIR`: resume a recorded full run with its saved settings, paths, and prompt. Requires `request.json`; current global defaults are not reapplied.
- `uv run python -m lecture_util.benchmark --help`: inspect the opt-in benchmark options. Actual benchmarks run real inference on explicitly supplied local audio and should not run as routine checks.
- `uv run pytest -q`: run the full test suite, including the local HLS/FFmpeg integration test.
- `uv build`: create distributable wheel and source archives.

Tests involving real transcription or summarization should mock costly model and Codex calls. Reserve actual `yt-dlp`/FFmpeg execution for dedicated integration tests. `tests/test_media_integration.py` serves generated HLS media over a local HTTP server and skips when either external tool is unavailable. `tests/test_local_media.py` also includes generated local audio/video integration coverage using FFmpeg and ffprobe.

## Coding Style & Naming Conventions

Use Python 3.12 syntax, four-space indentation, type hints, and small single-purpose functions. Follow standard Python naming: `snake_case` for functions and variables, `PascalCase` for classes, and `UPPER_SNAKE_CASE` for constants. Prefer `pathlib.Path`, explicit return types, and domain-specific exceptions from `errors.py`. Keep user-facing terminal output consistent with the existing Rich-based progress UI. No formatter or linter is currently configured; keep changes PEP 8-compatible and imports organized.

## Testing Guidelines

Use pytest. Name files `test_<module>.py` and tests `test_<behavior>()`. Add regression coverage for failures, cache/resume behavior, state serialization, and CLI exit paths. Use pytest fixtures such as `tmp_path` so tests never write generated files into the repository.

Existing tests also use `unittest.TestCase` and `tempfile.TemporaryDirectory`; follow the surrounding module's style. For form or publication changes, cover validation, preserved input on errors, course discovery, and destination conflicts as applicable. Isolate configuration, cache, video, and Vault paths in tests so they never touch the user's real settings or notes.

For source or pipeline changes, cover local audio/video detection, content-based cache invalidation, preserved originals, and failure recovery. Preserve the video-only path: it rejects local inputs and skips audio extraction, model processing, and note publication, even when Vault notes already exist. For download progress changes, cover throttled speed updates, finalization, and subprocess cleanup on cancellation. Mock model discovery and cache reads in model-picker tests.

For recovery changes, cover request validation, changed or missing local sources, workspace locking, corrupt state, downstream invalidation, missing Markdown/SRT restoration, and partial publication recovery. Only recover a partially published note pair when the recorded content matches existing files; preserve user edits and reject completed-note conflicts. Cover TUI retry/edit/quit input restoration and cancellation exit code 130.

Keep transcription defaults consistent across onboarding, the lecture form, `run`, and standalone `transcribe`; standalone `summarize` also inherits saved model and reasoning effort. Explicit options override saved settings. Cover compute type, batch size, beam reset (`--beam-size default`), MLX restrictions, and preflight checks without downloading models. Mock inference in progress and benchmark tests; benchmark metrics must distinguish measured values from unavailable data.

## Commit & Pull Request Guidelines

Recent history uses concise Conventional Commit prefixes such as `feat:`, `fix:`, `refactor:`, and `docs:`. Write imperative, narrowly scoped subjects. Pull requests should explain the motivation, summarize behavior changes, list verification commands, and note platform assumptions (macOS/MLX or Linux/CUDA). Link relevant issues and include terminal output or screenshots when CLI presentation changes.

Create a commit after every major edit once the relevant tests or checks pass. Keep each commit focused on one coherent change, use a Conventional Commit subject, and do not include unrelated user changes or generated artifacts. Minor follow-up edits may be grouped with the major edit they complete.

## Security & Configuration

Only process lecture URLs the user is authorized to download. Do not commit credentials, private course URLs, downloaded media, transcripts, model caches, or generated output directories.

Supported inputs are public HLS `.m3u8` URLs and local video/audio files readable by FFmpeg; authenticated LMS URLs requiring login or cookies are unsupported. Local inputs require an audio stream, and attached album art must not count as video. Keep local originals in place and unchanged; their cache identity includes the resolved path and content hash. Store downloaded HLS videos under the configured video root.

Settings live in `$XDG_CONFIG_HOME/lecture-util/config.json` (default `~/.config/lecture-util/config.json`), and intermediate artifacts use the user cache outside the Vault. Keep model, reasoning-effort, and prompt changes reflected in summary cache invalidation. Transcription cache identity must include model, language, device, compute type, batch size, beam size, and runtime platform. Treat `request.json` and `publication.json` as private generated artifacts: they contain prompts, paths, and note content. Resume validates the original source identity and does not repeat the saved `force` flag. `--force` reruns cached stages but must not overwrite local originals or existing Vault notes.

Courses live directly under `<Vault>/10 Academics/Courses` and must contain exactly one of `Lecture` or `Lectures`. Preserve this discovery contract: publication may create week folders, but must not silently create course or lecture directories. Keep note destination conflict checks before full lecture processing and avoid overwriting existing user notes. Video-only downloads do not publish notes or check note conflicts; existing videos still require a matching completed download record for reuse or explicit `--force` for replacement.

## Documentation

Always update `README.md` in the same change whenever user-facing usage changes, including added, renamed, or removed flags, new features, changed defaults, configuration changes, or altered workflows. Treat the README update as required for completing the change.

Keep `README.md` in Korean and these contributor instructions in English. When behavior changes, update the relevant usage section instead of appending unrelated features after the development section. Verify examples and defaults against the CLI, configuration, and tests; distinguish HLS downloads, local media processing, video-only mode, standalone cache commands, and full-run resume.
