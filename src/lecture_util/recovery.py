"""Persist complete run inputs and validate local restart requests."""
from __future__ import annotations

import json
import shlex
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from lecture_util.configuration import validate_transcription_options
from lecture_util.errors import LectureUtilError
from lecture_util.media import resolve_source
from lecture_util.models import RunOptions, TranscriptionOptions
from lecture_util.state import atomic_write_json, lecture_id


def transcription_options(options: RunOptions) -> TranscriptionOptions:
    return TranscriptionOptions(options.whisper_model, options.language, options.device,
                                options.compute_type, options.batch_size, options.beam_size)


def save_request(root: Path, options: RunOptions, vault: Path, videos: Path) -> None:
    atomic_write_json(root / 'request.json', {
        'version': 1, 'options': asdict(replace(options, force=False)),
        'vault_root': str(vault.resolve()), 'video_root': str(videos.resolve()),
    })


def load_request(root: Path) -> tuple[RunOptions, Path, Path]:
    path = root / 'request.json'
    try:
        data: dict[str, Any] = json.loads(path.read_text(encoding='utf-8'))
        if data.get('version') != 1:
            raise ValueError('unsupported request version')
        options = RunOptions(**data['options'])
        for name in ('url', 'course', 'lecture_date', 'title', 'prompt', 'semester_start'):
            if not isinstance(getattr(options, name), str):
                raise ValueError(f'missing or invalid {name}')
        source = resolve_source(options.url)
        options = replace(options, source=source if options.source is not None or source.kind != "hls" else None)
        validate_transcription_options(transcription_options(options))
        if root.name != f'lecture-{lecture_id(source.cache_key)}':
            raise ValueError('workspace does not match the recorded URL')
        vault, videos = Path(data['vault_root']), Path(data['video_root'])
        if not vault.is_absolute() or not videos.is_absolute():
            raise ValueError('restart paths must be absolute')
        return replace(options, force=False), vault, videos
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        raise LectureUtilError(
            f'Could not resume {path}: {error}. '
            'Use lecture-util run with the original URL and lecture metadata.'
        ) from error


def resume_message(root: Path, stage: str) -> str:
    command = shlex.join(['lecture-util', 'resume', str(root)])
    return f'Failed stage: {stage}\nCache: {root}\nResume: {command}'
