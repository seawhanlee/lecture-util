from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pypdfium2 as pdfium
import pytest
from PIL import Image
from typer.testing import CliRunner

from lecture_util.cli import app
from lecture_util.configuration import AppConfig
from lecture_util.errors import LectureUtilError
from lecture_util.materials import (
    BATCH_SIZE, discover_materials, prepare_material_context, register_directory,
    registered_directories, resolve_run_materials, validate_snapshot,
)
from lecture_util.models import LecturePaths, RunOptions, Transcript
from lecture_util.pdf_tools import page_count, read_pages
from lecture_util.recovery import load_request, save_request
from lecture_util.state import RunState, lecture_id
from lecture_util.summary import summary_stage
from lecture_util.vault import COURSES_DIRECTORY


def make_pdf(path: Path, pages: int = 1) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with pdfium.PdfDocument.new() as document:
        for _ in range(pages):
            page = document.new_page(200, 100)
            page.close()
        document.save(path)
    return path


@pytest.fixture
def setup(tmp_path):
    vault = tmp_path / "vault"
    (vault / COURSES_DIRECTORY / "Course" / "Lectures").mkdir(parents=True)
    directory = tmp_path / "자료 디렉토리"
    make_pdf(directory / "교재.pdf", BATCH_SIZE + 1)
    options = RunOptions("https://example.com/lecture.m3u8", "Course", "2026-09-07",
                         "Lecture", "vision-model", None, "turbo", "ko", "cpu", "Prompt", False,
                         semester_start="2026-08-31")
    return vault, directory, options


class FakeCodex:
    name = "codex"
    model = "vision-model"
    reasoning_effort = "high"

    def __init__(self):
        self.calls = []
        self.summaries = []
        self.fail_at = None
        self.relevant = True

    def generate_json(self, prompt, schema):
        self.calls.append((prompt, schema))
        if self.fail_at == len(self.calls):
            raise KeyboardInterrupt()
        if "pages" in schema["properties"]:
            properties = schema["properties"]["pages"]["items"]["properties"]
            first, last = properties["page"]["minimum"], properties["page"]["maximum"]
            return {"pages": [{"page": p, "content": "Diagram: mass conserved; equation m=ρV.",
                               "uncertainty": ""} for p in range(first, last + 1)]}
        ids = schema["properties"]["selected"]["items"]["properties"]["id"]["enum"]
        return {"selected": [{"id": i, "pages": [1] if self.relevant else []} for i in ids]}

    def generate(self, developer, user, transcript, *, materials=None):
        self.summaries.append((developer, user, materials))
        return "### Conservation\nMass is conserved. (교재.pdf, PDF p.1)"


def workspace(tmp_path):
    paths = LecturePaths(tmp_path / "lecture")
    paths.root.mkdir()
    paths.transcript_markdown.write_text("Mass conservation lecture", encoding="utf-8")
    return paths, RunState(paths)


def test_register_reuse_and_unregister_without_deleting_originals(setup):
    vault, directory, options = setup
    original = (directory / "교재.pdf").read_bytes()
    register_directory(vault, "Course", directory)
    register_directory(vault, "Course", directory)
    assert registered_directories(vault, "Course") == [str(directory)]
    resolved = resolve_run_materials(options, vault)
    assert resolved.materials_files[0]["relative_path"] == "교재.pdf"
    assert resolved.materials_files[0]["pages"] == BATCH_SIZE + 1
    register_directory(vault, "Course", directory, remove=True)
    assert registered_directories(vault, "Course") == []
    assert (directory / "교재.pdf").read_bytes() == original


def test_discovery_deduplicates_overlap_and_skips_external_links(setup, tmp_path):
    _, directory, _ = setup
    nested = directory / "slides"
    make_pdf(nested / "교재.PDF")
    outside = make_pdf(tmp_path / "private.pdf")
    (directory / "outside.pdf").symlink_to(outside)
    files = discover_materials([str(directory), str(nested), str(directory)])
    assert len(files) == 2
    assert len({f["id"] for f in files}) == 2
    assert all(f["path"] != str(outside) for f in files)


@pytest.mark.parametrize("kind", ["missing", "empty", "corrupt"])
def test_invalid_input_has_actionable_error(tmp_path, kind):
    directory = tmp_path / "materials"
    if kind != "missing":
        directory.mkdir()
    if kind == "corrupt":
        (directory / "bad.pdf").write_bytes(b"not a PDF")
    with pytest.raises(LectureUtilError):
        discover_materials([str(directory)])


def test_scanned_pdf_is_rendered_only_when_requested(tmp_path):
    source = tmp_path / "scanned.pdf"
    image = Image.new("RGB", (200, 100), "white")
    image.putpixel((50, 50), (255, 0, 0))
    image.save(source, "PDF")
    original = source.read_bytes()
    assert page_count(source) == 1
    records = read_pages(source, 1, 1, tmp_path / "scratch")
    assert records[0]["page"] == 1
    with Image.open(records[0]["image"]) as rendered:
        assert rendered.width > 200 and rendered.height > 100
    assert source.read_bytes() == original
    with pytest.raises(LectureUtilError, match="range"):
        read_pages(source, 0, 2, tmp_path / "scratch")


def test_full_analysis_is_cached_and_relevance_uses_transcript(setup, tmp_path):
    _, directory, _ = setup
    files = discover_materials([str(directory)])
    paths, state = workspace(tmp_path)
    codex = FakeCodex()
    context = prepare_material_context(files, paths.transcript_markdown, state, codex)
    assert len(codex.calls) == 3  # 21 pages: two batches, then relevance selection.
    assert "@" + str(directory) in codex.calls[0][0]
    assert "EVERY returned image" in codex.calls[0][0]
    assert str(paths.transcript_markdown) in codex.calls[-1][0]
    assert context.selected[0]["pages"] == [1]
    assert len(context.indexes[0]["pages"]) == 1
    assert prepare_material_context(files, paths.transcript_markdown, state, codex) == context
    assert len(codex.calls) == 3
    paths.transcript_markdown.write_text("Different lecture")
    changed = prepare_material_context(files, paths.transcript_markdown, state, codex)
    assert len(codex.calls) == 4  # Page analysis survives transcript changes.
    assert changed.fingerprint != context.fingerprint


def test_cancelled_analysis_resumes_completed_batches(setup, tmp_path):
    _, directory, _ = setup
    files = discover_materials([str(directory)])
    paths, state = workspace(tmp_path)
    codex = FakeCodex()
    codex.fail_at = 2
    with pytest.raises(KeyboardInterrupt):
        prepare_material_context(files, paths.transcript_markdown, state, codex)
    assert state.data["stages"]["materials"]["status"] == "failed"
    codex.fail_at = None
    prepare_material_context(files, paths.transcript_markdown, state, codex)
    assert len(codex.calls) == 4
    assert codex.calls[2][1]["properties"]["pages"]["items"]["properties"]["page"]["minimum"] == 21


def test_corrupt_cache_requires_force(setup, tmp_path, monkeypatch):
    _, directory, _ = setup
    files = discover_materials([str(directory)])
    paths, state = workspace(tmp_path)
    cache = tmp_path / "shared-cache"
    codex = FakeCodex()
    prepare_material_context(files, paths.transcript_markdown, state, codex, cache_root=cache)
    batch = next(cache.rglob("pages-1-20.json"))
    batch.write_text("broken JSON")
    with pytest.raises(LectureUtilError, match="--force"):
        prepare_material_context(files, paths.transcript_markdown, state, codex, cache_root=cache)
    prepare_material_context(files, paths.transcript_markdown, state, codex, cache_root=cache, force=True)
    assert json.loads(batch.read_text())["data"]["pages"][0]["page"] == 1


def test_material_change_invalidates_only_summary_inputs(setup, tmp_path):
    _, directory, _ = setup
    files = discover_materials([str(directory)])
    paths, state = workspace(tmp_path)
    state.complete_stage("transcription", fingerprint="preserved")
    codex = FakeCodex()
    transcript = Transcript("en", 10, "test", "test", "test", [])
    summary_stage(transcript, paths.transcript_markdown, paths.summary, state, codex, materials_files=files)
    assert len(codex.summaries) == 1
    assert "physical PDF page" in codex.summaries[0][0]
    summary_stage(transcript, paths.transcript_markdown, paths.summary, state, codex, materials_files=files)
    assert len(codex.summaries) == 1
    make_pdf(directory / "교재.pdf", 2)
    changed = discover_materials([str(directory)])
    summary_stage(transcript, paths.transcript_markdown, paths.summary, state, codex, materials_files=changed)
    assert len(codex.summaries) == 2
    assert state.data["stages"]["transcription"]["fingerprint"] == "preserved"
    summary_stage(transcript, paths.transcript_markdown, paths.summary, state, codex)
    assert codex.summaries[-1][2] is None


def test_model_and_effort_changes_reanalyze_materials(setup, tmp_path):
    _, directory, _ = setup
    files = discover_materials([str(directory)])
    paths, state = workspace(tmp_path)
    codex = FakeCodex()
    prepare_material_context(files, paths.transcript_markdown, state, codex)
    codex.reasoning_effort = "low"
    prepare_material_context(files, paths.transcript_markdown, state, codex)
    assert len(codex.calls) == 6
    codex.model = "another-model"
    prepare_material_context(files, paths.transcript_markdown, state, codex)
    assert len(codex.calls) == 9


def test_no_relevant_pages_warns_without_inventing_context(setup, tmp_path):
    _, directory, _ = setup
    files = discover_materials([str(directory)])
    paths, state = workspace(tmp_path)
    codex = FakeCodex()
    codex.relevant = False
    events = []
    context = prepare_material_context(files, paths.transcript_markdown, state, codex, progress=events.append)
    assert context.indexes[0]["pages"] == []
    assert any(e.status == "warning" for e in events)


def test_snapshot_preserves_registry_and_rejects_changed_source(setup, tmp_path):
    vault, directory, options = setup
    register_directory(vault, "Course", directory)
    options = resolve_run_materials(options, vault)
    root = tmp_path / f"lecture-{lecture_id(options.url)}"
    save_request(root, options, vault, tmp_path / "videos")
    register_directory(vault, "Course", directory, remove=True)
    restored, _, _ = load_request(root)
    assert restored.materials_files == options.materials_files
    assert resolve_run_materials(restored, vault).materials_files == options.materials_files
    make_pdf(directory / "교재.pdf", 2)
    with pytest.raises(LectureUtilError, match="changed or missing"):
        load_request(root)


def test_legacy_request_does_not_adopt_registered_materials(setup, tmp_path):
    vault, directory, options = setup
    register_directory(vault, "Course", directory)
    root = tmp_path / f"lecture-{lecture_id(options.url)}"
    save_request(root, options, vault, tmp_path / "videos")
    data = json.loads((root / "request.json").read_text())
    data["version"] = 1
    for field in ("materials_files", "materials_dirs", "no_materials"):
        del data["options"][field]
    (root / "request.json").write_text(json.dumps(data))
    restored, _, _ = load_request(root)
    assert resolve_run_materials(restored, vault).materials_files == []
    data["options"]["course"] = None
    (root / "request.json").write_text(json.dumps(data))
    with pytest.raises(LectureUtilError, match="course"):
        load_request(root)


def test_material_cli_register_list_remove(setup, tmp_path):
    vault, directory, _ = setup
    config = AppConfig(vault, tmp_path / "videos", "2026-08-31")
    runner = CliRunner()
    with patch("lecture_util.cli.load_config", return_value=config):
        for command in ("add", "list", "remove"):
            args = ["materials", command, "--course", "Course"]
            if command != "list":
                args.append(str(directory))
            result = runner.invoke(app, args)
            assert result.exit_code == 0, result.output
            assert str(directory) in result.output
    assert (directory / "교재.pdf").is_file()


def test_no_materials_bypasses_missing_registry_sources(setup):
    vault, directory, options = setup
    register_directory(vault, "Course", directory)
    (directory / "교재.pdf").unlink()
    assert resolve_run_materials(replace(options, no_materials=True), vault).materials_files == []
    with pytest.raises(LectureUtilError, match="No readable PDFs"):
        resolve_run_materials(options, vault)


@pytest.mark.parametrize("flags", [["--materials-dir", "/missing"], ["--no-materials"]])
def test_video_only_rejects_material_options_before_download(setup, tmp_path, flags):
    vault, _, _ = setup
    with (patch("lecture_util.cli.load_config", return_value=AppConfig(vault, tmp_path / "videos", "2026-08-31")),
          patch("lecture_util.cli._execute_run") as run):
        result = CliRunner().invoke(app, ["run", "https://example.com/lecture.m3u8", "--course", "Course",
                                         "--date", "2026-09-07", "--title", "Title", "--video-only", *flags])
    assert result.exit_code == 1 and "Video-only" in result.output
    run.assert_not_called()


def test_summary_cli_uses_course_and_one_time_materials(setup, tmp_path):
    vault, directory, _ = setup
    register_directory(vault, "Course", directory)
    other = tmp_path / "slides"
    make_pdf(other / "slides.pdf")
    config = AppConfig(vault, tmp_path / "videos", "2026-08-31")
    transcript = Transcript("en", 0, "test", "test", "test", [])
    with (patch("lecture_util.cli.load_config", return_value=config),
          patch("lecture_util.cli.load_transcript", return_value=transcript),
          patch("lecture_util.cli.restore_transcript_files"),
          patch("lecture_util.cli.summary_stage") as summarize):
        result = CliRunner().invoke(app, ["summarize", str(tmp_path), "--course", "Course",
                                         "--materials-dir", str(other)])
    assert result.exit_code == 0, result.output
    assert len(summarize.call_args.kwargs["materials_files"]) == 2
    assert not list(vault.rglob("*.md"))


def test_resume_missing_pdf_and_invalid_snapshot_rejected(setup):
    _, directory, _ = setup
    files = discover_materials([str(directory)])
    for invalid in ({**files[0], "pages": True}, {**files[0], "id": "forged"}):
        with pytest.raises(LectureUtilError, match="identity"):
            validate_snapshot([invalid])
    (directory / "교재.pdf").unlink()
    with pytest.raises(LectureUtilError, match="changed or missing"):
        validate_snapshot(files)


def test_analysis_rejects_missing_pages_in_codex_output(setup, tmp_path):
    _, directory, _ = setup
    paths, state = workspace(tmp_path)
    codex = FakeCodex()
    with patch.object(codex, "generate_json", return_value={"pages": []}):
        with pytest.raises(LectureUtilError, match="coverage"):
            prepare_material_context(discover_materials([str(directory)]), paths.transcript_markdown, state, codex)
    assert state.data["stages"]["materials"]["status"] == "failed"


def test_selection_rejects_pages_outside_pdf(setup, tmp_path):
    _, directory, _ = setup
    paths, state = workspace(tmp_path)
    codex = FakeCodex()
    original = codex.generate_json
    def invalid_selection(prompt, schema):
        if "selected" in schema["properties"]:
            ids = schema["properties"]["selected"]["items"]["properties"]["id"]["enum"]
            return {"selected": [{"id": ids[0], "pages": [999]}]}
        return original(prompt, schema)
    with patch.object(codex, "generate_json", side_effect=invalid_selection):
        with pytest.raises(LectureUtilError, match="selected pages"):
            prepare_material_context(discover_materials([str(directory)]), paths.transcript_markdown, state, codex)


def test_shared_analysis_lock_prevents_simultaneous_writes(setup, tmp_path):
    from lecture_util.state import workspace_lock
    _, directory, _ = setup
    files = discover_materials([str(directory)])
    paths, state = workspace(tmp_path)
    codex = FakeCodex()
    shared = tmp_path / "shared"
    prepare_material_context(files, paths.transcript_markdown, state, codex, cache_root=shared)
    cache = next((shared / "materials").iterdir())
    with workspace_lock(cache):
        with pytest.raises(LectureUtilError, match="using"):
            prepare_material_context(files, paths.transcript_markdown, state, codex, cache_root=shared)
    assert len(codex.calls) == 3


def test_pdf_cache_is_counted_and_survives_lecture_prune(tmp_path):
    from lecture_util.cache import get_storage_summary, scan_cached_lectures, execute_prune
    cache = tmp_path / "cache"
    shared = cache / "materials" / "hash"
    shared.mkdir(parents=True)
    (shared / "pages-1-20.json").write_bytes(b"analysis")
    paths = LecturePaths(cache / "lecture-example")
    RunState(paths).complete_stage("summary")
    summary = get_storage_summary(cache)
    assert summary.materials_cache_size == len(b"analysis")
    assert summary.total_lectures == 1
    execute_prune(scan_cached_lectures(cache))
    assert (shared / "pages-1-20.json").read_bytes() == b"analysis"


def test_full_run_publishes_material_summary_and_resume_finishes_pair(setup, tmp_path, monkeypatch):
    from lecture_util.pipeline import execute_run
    from lecture_util.transcription import save_transcript
    import lecture_util.vault as vault_module

    vault, directory, options = setup
    register_directory(vault, "Course", directory)
    cache = tmp_path / "cache"
    root = cache / f"lecture-{lecture_id(options.url)}"
    codex = FakeCodex()
    def prepare(*args, **kwargs):
        paths = LecturePaths(root)
        paths.root.mkdir(parents=True, exist_ok=True)
        state = RunState(paths)
        transcript = Transcript("en", 10, "test", "test", "test", [])
        save_transcript(transcript, paths.transcript_json, paths.transcript_markdown, paths.transcript_srt)
        state.complete_stage("transcription", sha256=state.digest(paths.transcript_json))
        return paths, state, transcript
    create = vault_module._create_note
    created = []
    def fail_second(path, content):
        created.append(path)
        if len(created) == 2:
            raise OSError("disk full")
        create(path, content)
    monkeypatch.setattr(vault_module, "_create_note", fail_second)
    with (patch("lecture_util.pipeline.CodexSummarizer", return_value=codex),
          patch("lecture_util.pipeline.prepare_lecture", side_effect=prepare),
          patch("lecture_util.media.require_executable")):
        with pytest.raises(OSError, match="disk full"):
            execute_run(options, vault_root=vault, video_root=tmp_path / "videos", cache_root=cache)
    restored, _, videos = load_request(root)
    assert len(restored.materials_files) == 1
    monkeypatch.setattr(vault_module, "_create_note", create)
    with patch("lecture_util.pipeline.run_lecture", side_effect=AssertionError("must not repeat processing")):
        execute_run(restored, vault_root=vault, video_root=videos, cache_root=cache)
    notes = list(vault.rglob("*.md"))
    assert len(notes) == 2
    assert any("PDF p.1" in note.read_text() for note in notes)
    assert len(codex.summaries) == 1


def test_note_conflict_precedes_material_reads(setup, tmp_path):
    from lecture_util.pipeline import execute_run
    from lecture_util.vault import resolve_course, published_lecture_paths
    vault, directory, options = setup
    register_directory(vault, "Course", directory)
    destination = published_lecture_paths(resolve_course("Course", vault), options.lecture_date,
                                          options.title, semester_start=options.semester_start)
    destination.summary.parent.mkdir(parents=True)
    destination.summary.write_text("User edits")
    (directory / "교재.pdf").unlink()
    with patch("lecture_util.pipeline.run_lecture") as run:
        with pytest.raises(LectureUtilError, match="already exists"):
            execute_run(options, vault_root=vault, video_root=tmp_path / "videos", cache_root=tmp_path / "cache")
        run.assert_not_called()
    assert destination.summary.read_text() == "User edits"
