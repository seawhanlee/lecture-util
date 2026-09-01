class LectureUtilError(RuntimeError):
    """A user-actionable lecture-util failure."""


class DependencyError(LectureUtilError):
    """A required executable, library, or accelerator is unavailable."""


class CommandError(LectureUtilError):
    """An external command failed."""
