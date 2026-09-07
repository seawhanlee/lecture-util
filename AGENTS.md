# Repository Guidelines

## Project Structure & Module Organization

Application code lives in `src/lecture_util/`. `cli.py` defines Typer commands, while `pipeline.py` coordinates downloading, audio extraction, transcription, and summarization. Keep focused logic in the existing modules (`media.py`, `transcription.py`, `summary.py`, `state.py`, and related helpers) rather than expanding the CLI layer.

`tui.py` implements the Textual lecture form, `onboarding.py` handles initial setup, and `form_ui.py` holds shared form components. `configuration.py` owns persisted settings and validation, while `vault.py` discovers courses, resolves publication paths, and publishes notes. Keep summarizer backend integration in `summarizers.py`, progress events in `progress.py`, and Rich terminal rendering in `console_progress.py`.

Tests live in `tests/` and generally mirror module names, for example `tests/test_summary.py`. Directories such as `output/` or `lecture-util-test.*/` contain generated media, transcripts, summaries, and run state; treat them as artifacts, not source fixtures. Project metadata and dependencies are defined in `pyproject.toml`, with exact resolutions in `uv.lock`.

## Build, Test, and Development Commands

- `uv sync --dev`: create/update `.venv` and install runtime plus test dependencies.
- `uv run lecture-util doctor`: verify external tools and platform-specific transcription support.
- `uv run lecture-util`: launch the interactive CLI.
- `uv run lecture-util onboard`: configure or update the Vault, video storage, semester, and model defaults in an interactive terminal.
- `uv run lecture-util run URL --course 'Course name' --date 2026-09-07 --title 'Lecture title'`: exercise the complete non-interactive pipeline after onboarding, using an existing course.
- `uv run pytest -q`: run the full test suite, including the local HLS/FFmpeg integration test.
- `uv build`: create distributable wheel and source archives.

Tests involving real transcription or summarization should mock costly model and Codex calls. Reserve actual `yt-dlp`/FFmpeg execution for dedicated integration tests. `tests/test_media_integration.py` serves generated HLS media over a local HTTP server and skips when either external tool is unavailable.

## Coding Style & Naming Conventions

Use Python 3.12 syntax, four-space indentation, type hints, and small single-purpose functions. Follow standard Python naming: `snake_case` for functions and variables, `PascalCase` for classes, and `UPPER_SNAKE_CASE` for constants. Prefer `pathlib.Path`, explicit return types, and domain-specific exceptions from `errors.py`. Keep user-facing terminal output consistent with the existing Rich-based progress UI. No formatter or linter is currently configured; keep changes PEP 8-compatible and imports organized.

## Testing Guidelines

Use pytest. Name files `test_<module>.py` and tests `test_<behavior>()`. Add regression coverage for failures, cache/resume behavior, state serialization, and CLI exit paths. Use pytest fixtures such as `tmp_path` so tests never write generated files into the repository.

Existing tests also use `unittest.TestCase` and `tempfile.TemporaryDirectory`; follow the surrounding module's style. For form or publication changes, cover validation, preserved input on errors, course discovery, and destination conflicts as applicable. Isolate configuration, cache, video, and Vault paths in tests so they never touch the user's real settings or notes.

## Commit & Pull Request Guidelines

Recent history uses concise Conventional Commit prefixes such as `feat:`, `fix:`, `refactor:`, and `docs:`. Write imperative, narrowly scoped subjects. Pull requests should explain the motivation, summarize behavior changes, list verification commands, and note platform assumptions (macOS/MLX or Linux/CUDA). Link relevant issues and include terminal output or screenshots when CLI presentation changes.

Create a commit after every major edit once the relevant tests or checks pass. Keep each commit focused on one coherent change, use a Conventional Commit subject, and do not include unrelated user changes or generated artifacts. Minor follow-up edits may be grouped with the major edit they complete.

## Security & Configuration

Only process lecture URLs the user is authorized to download. Do not commit credentials, private course URLs, downloaded media, transcripts, model caches, or generated output directories.

The supported input is a public HLS `.m3u8` URL; authenticated LMS URLs requiring login or cookies are unsupported. Settings live in `$XDG_CONFIG_HOME/lecture-util/config.json` (default `~/.config/lecture-util/config.json`), and intermediate artifacts use the user cache outside the Vault. Store source videos under the configured video root.

Courses live directly under `<Vault>/10 Academics/Courses` and must contain exactly one of `Lecture` or `Lectures`. Preserve this discovery contract: publication may create week folders, but must not silently create course or lecture directories. Keep destination conflict checks before processing and avoid overwriting existing user notes.
