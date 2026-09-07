class LectureUtilError(RuntimeError):
    """A user-actionable lecture-util failure."""


class DependencyError(LectureUtilError):
    """A required executable, library, or accelerator is unavailable."""


class CommandError(LectureUtilError):
    """An external command failed."""


class TranscriptionOptionError(LectureUtilError):
    """Invalid transcription input with its corresponding form field."""

    def __init__(self, message: str, field: str) -> None:
        super().__init__(message)
        self.field = field
