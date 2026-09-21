from __future__ import annotations

from dataclasses import InitVar, asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal


@dataclass(slots=True)
class Segment:
    start: float | None
    end: float | None
    text: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Segment:
        return cls(
            start=float(data["start"]) if data["start"] is not None else None,
            end=float(data["end"]) if data["end"] is not None else None,
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
    requested_options: dict[str, Any] = field(default_factory=dict)
    effective_options: dict[str, Any] = field(default_factory=dict)

    @property
    def has_timestamps(self) -> bool:
        return self.effective_options.get("timestamps", True) and all(
            segment.start is not None and segment.end is not None for segment in self.segments
        )

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
            requested_options=data.get("requested_options", {}),
            effective_options=data.get("effective_options", {}),
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


@dataclass(frozen=True, slots=True)
class CourseClassificationResult:
    selected_course: str
    confidence: float
    probabilities: dict[str, float]


@dataclass(slots=True)
class RunOptions:
    url: str
    course: str | None
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
    compute_type: str = "auto"
    batch_size: int = 0
    beam_size: int | None = None
    source: LectureSource | None = None
    video_only: bool = False
    transcription_provider: str = "local"
    openai_transcription_model: str = "gpt-4o-transcribe"


@dataclass(frozen=True, slots=True)
class LectureSource:
    kind: Literal["hls", "video", "audio"]
    location: str
    content_hash: str | None = None

    @property
    def cache_key(self) -> str:
        if self.kind == "hls":
            return self.location
        return f"local:{self.location}:{self.content_hash}"


@dataclass(frozen=True, slots=True)
class TranscriptionOptions:
    model: str = "large-v3"
    language: str = "auto"
    device: str = "auto"
    compute_type: str = "auto"
    batch_size: int = 0
    beam_size: int | None = None

    transcription_provider: str = "local"
    openai_transcription_model: str = "gpt-4o-transcribe"

    @property
    def selected_model(self) -> str:
        return self.openai_transcription_model if self.transcription_provider == "openai" else self.model

    def to_dict(self) -> dict[str, Any]:
        if self.transcription_provider == "openai":
            return {"provider": "openai", "model": self.openai_transcription_model,
                    "language": self.language, "chunk_version": 1}
        data = asdict(self)
        data.pop("transcription_provider")
        data.pop("openai_transcription_model")
        return data
