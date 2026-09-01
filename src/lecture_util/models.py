from __future__ import annotations

from dataclasses import asdict, dataclass, field
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
    video: Path = field(init=False)
    audio: Path = field(init=False)
    transcript_json: Path = field(init=False)
    transcript_markdown: Path = field(init=False)
    transcript_srt: Path = field(init=False)
    summary: Path = field(init=False)
    state: Path = field(init=False)
    summary_work: Path = field(init=False)

    def __post_init__(self) -> None:
        self.video = self.root / "source.mp4"
        self.audio = self.root / "audio.wav"
        self.transcript_json = self.root / "transcript.json"
        self.transcript_markdown = self.root / "transcript.md"
        self.transcript_srt = self.root / "transcript.srt"
        self.summary = self.root / "summary.md"
        self.state = self.root / "run.json"
        self.summary_work = self.root / "work" / "summary-chunks"
