# Repository Guidelines

## Project Structure & Module Organization

Application code lives in `src/lecture_util/`. `cli.py` defines Typer commands, while `pipeline.py` coordinates downloading, audio extraction, transcription, and summarization. Keep focused logic in the existing modules (`media.py`, `transcription.py`, `summary.py`, `state.py`, and related helpers) rather than expanding the CLI layer.

Tests live in `tests/` and generally mirror module names, for example `tests/test_summary.py`. Directories such as `output/` or `lecture-util-test.*/` contain generated media, transcripts, summaries, and run state; treat them as artifacts, not source fixtures. Project metadata and dependencies are defined in `pyproject.toml`, with exact resolutions in `uv.lock`.

## Build, Test, and Development Commands

- `uv sync --dev`: create/update `.venv` and install runtime plus test dependencies.
- `uv run lecture-util doctor`: verify external tools and platform-specific transcription support.
- `uv run lecture-util`: launch the interactive CLI.
- `uv run lecture-util run URL --tag example`: exercise the complete non-interactive pipeline.
- `uv run pytest -q`: run the full test suite, including the local HLS/FFmpeg integration test.
- `uv build`: create distributable wheel and source archives.

Tests involving real transcription or summarization should mock costly model and Codex calls. Reserve actual `yt-dlp`/FFmpeg execution for explicitly marked integration behavior.

## Coding Style & Naming Conventions

Use Python 3.12 syntax, four-space indentation, type hints, and small single-purpose functions. Follow standard Python naming: `snake_case` for functions and variables, `PascalCase` for classes, and `UPPER_SNAKE_CASE` for constants. Prefer `pathlib.Path`, explicit return types, and domain-specific exceptions from `errors.py`. Keep user-facing terminal output consistent with the existing Rich-based progress UI. No formatter or linter is currently configured; keep changes PEP 8-compatible and imports organized.

## Testing Guidelines

Use pytest. Name files `test_<module>.py` and tests `test_<behavior>()`. Add regression coverage for failures, cache/resume behavior, state serialization, and CLI exit paths. Use pytest fixtures such as `tmp_path` so tests never write generated files into the repository.

## Commit & Pull Request Guidelines

Recent history uses concise Conventional Commit prefixes such as `feat:`, `fix:`, `refactor:`, and `docs:`. Write imperative, narrowly scoped subjects. Pull requests should explain the motivation, summarize behavior changes, list verification commands, and note platform assumptions (macOS/MLX or Linux/CUDA). Link relevant issues and include terminal output or screenshots when CLI presentation changes.

Create a commit after every major edit once the relevant tests or checks pass. Keep each commit focused on one coherent change, use a Conventional Commit subject, and do not include unrelated user changes or generated artifacts. Minor follow-up edits may be grouped with the major edit they complete.

## Security & Configuration

Only process lecture URLs the user is authorized to download. Do not commit credentials, private course URLs, downloaded media, transcripts, model caches, or generated output directories.
