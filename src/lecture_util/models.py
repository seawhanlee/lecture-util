from __future__ import annotations

from dataclasses import InitVar, asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class Segment:
    start: float
    end: float
    text: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Segment:
        return cls(
            start=float(data["start"]),
            end=float(data["end"]),
            text=str(data["text"]).strip(),
        )


@dataclass(slots=True)
class Transcript:
    language: str
    duration: float
    engine: str
    requested_model: str
    effective_model: str
    segments: list[Segment]
    fallback_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Transcript:
        return cls(
            language=str(data.get("language", "unknown")),
            duration=float(data.get("duration", 0)),
            engine=str(data.get("engine", "unknown")),
            requested_model=str(data.get("requested_model", "unknown")),
            effective_model=str(data.get("effective_model", "unknown")),
            fallback_reason=data.get("fallback_reason"),
            segments=[Segment.from_dict(item) for item in data.get("segments", [])],
        )


@dataclass(slots=True)
class LecturePaths:
    root: Path
    video_path: InitVar[Path | None] = None
    video: Path = field(init=False)
    audio: Path = field(init=False)
    transcript_json: Path = field(init=False)
    transcript_markdown: Path = field(init=False)
    transcript_srt: Path = field(init=False)
    summary: Path = field(init=False)
    state: Path = field(init=False)

    def __post_init__(self, video_path: Path | None) -> None:
        self.video = video_path or self.root / "source.mp4"
        self.audio = self.root / "audio.wav"
        self.transcript_json = self.root / "transcript.json"
        self.transcript_markdown = self.root / "transcript.md"
        self.transcript_srt = self.root / "transcript.srt"
        self.summary = self.root / "summary.md"
        self.state = self.root / "run.json"


@dataclass(slots=True)
class RunOptions:
    url: str
    course: str
    lecture_date: str
    title: str
    llm_model: str | None
    tags: list[str] | None
    whisper_model: str
    language: str
    device: str
    prompt: str
    force: bool
    semester_start: str | None = None
    reasoning_effort: str | None = None
