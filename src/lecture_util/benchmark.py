"""Opt-in local ASR comparison: python -m lecture_util.benchmark --help."""
from __future__ import annotations

import argparse
import csv
import itertools
import json
import resource
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path
from time import monotonic
from typing import Any

from lecture_util.models import TranscriptionOptions
from lecture_util.progress import ProgressEvent
from lecture_util.state import atomic_write_json


def normalize_text(text: str) -> str:
    """NFC + case folding, ignoring whitespace and punctuation for CER."""
    return ''.join(character for character in unicodedata.normalize('NFC', text).casefold()
                   if not character.isspace() and not unicodedata.category(character).startswith('P'))


def edit_distance(reference: str, hypothesis: str) -> int:
    """Myers bit-vector Levenshtein distance, with arbitrary-width Python ints."""
    if not reference:
        return len(hypothesis)
    masks: dict[str, int] = {}
    for index, character in enumerate(reference):
        masks[character] = masks.get(character, 0) | (1 << index)
    positive, negative, score = ~0, 0, len(reference)
    high = 1 << (len(reference) - 1)
    for character in hypothesis:
        equal = masks.get(character, 0)
        vertical = equal | negative
        horizontal = (((equal & positive) + positive) ^ positive) | equal
        plus = negative | ~(horizontal | positive)
        minus = positive & horizontal
        score += bool(plus & high) - bool(minus & high)
        plus = (plus << 1) | 1
        minus <<= 1
        positive = minus | ~(vertical | plus)
        negative = plus & vertical
    return score


def quality_metrics(text: str, reference: str | None, terms: list[str]) -> dict[str, Any]:
    normalized = normalize_text(text)
    expected = normalize_text(reference) if reference is not None else ''
    missing = [term for term in terms if normalize_text(term) not in normalized]
    return {
        'cer': edit_distance(expected, normalized) / len(expected) if expected else None,
        'missing_terms': missing,
        'term_missing_rate': len(missing) / len(terms) if terms else None,
    }


def measure(audio: Path, options: TranscriptionOptions) -> dict[str, Any]:
    from lecture_util.transcription import preflight_transcription, transcribe_audio
    started = monotonic()
    phase = 'preparation'
    changed = started
    times = {'preparation': 0.0, 'inference': 0.0}

    def progress(event: ProgressEvent) -> None:
        nonlocal phase, changed
        selected = 'inference' if event.phase == 'inference' else 'preparation'
        if event.phase is None or selected == phase:
            return
        now = monotonic()
        times[phase] += now - changed
        phase, changed = selected, now

    preflight_transcription(options)
    transcript = transcribe_audio(audio, **options.to_dict(), progress=progress)
    finished = monotonic()
    times[phase] += finished - changed
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform != 'darwin':
        rss *= 1024
    is_mlx = transcript.effective_options.get('device') == 'mlx'
    return {
        'requested': options.to_dict(), 'effective': transcript.effective_options,
        'fallback_reason': transcript.fallback_reason,
        'duration_seconds': transcript.duration, 'wall_seconds': finished - started,
        'preparation_seconds': None if is_mlx else times['preparation'],
        'inference_seconds': None if is_mlx else times['inference'],
        'combined_transcription_seconds': times['inference'] if is_mlx else None,
        'real_time_factor': ((finished - started) / transcript.duration if transcript.duration else None),
        'peak_process_rss_bytes': rss, 'peak_gpu_bytes': None,
        'memory_method': 'OS process peak RSS; dedicated GPU memory is not measured',
        'timing_note': ('MLX preparation/inference are combined by its API' if options.device == 'mlx'
                        or transcript.effective_options.get('device') == 'mlx' else
                        'Preparation includes model download/loading; inference includes audio decoding/VAD'),
        'text': ' '.join(segment.text for segment in transcript.segments),
    }


def write_results(output: Path, rows: list[dict[str, Any]]) -> None:
    atomic_write_json(output, rows)
    with output.with_suffix('.csv').open('w', encoding='utf-8', newline='') as stream:
        keys = list(dict.fromkeys(key for row in rows for key in row))
        writer = csv.DictWriter(stream, fieldnames=keys)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list))
                             else value for key, value in row.items()})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audio', type=Path, required=True, help='Local WAV; never downloads lectures')
    parser.add_argument('--device', default='auto', choices=['auto', 'cuda', 'cpu', 'mlx'])
    parser.add_argument('--language', default='auto', help='Whisper language code or auto')
    parser.add_argument('--models', nargs='+', default=['large-v3', 'turbo'])
    parser.add_argument('--compute-types', nargs='+', default=['auto'])
    parser.add_argument('--batch-sizes', nargs='+', type=int, default=[0])
    parser.add_argument('--beam-sizes', nargs='+', default=['default'], help='Integers or default')
    parser.add_argument('--reference', type=Path, help='Plain text reference for normalized CER')
    parser.add_argument('--terms', type=Path, help='One expected technical term per line')
    parser.add_argument('--output', type=Path, default=Path('benchmark.json'))
    parser.add_argument('--worker-options', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not args.audio.is_file():
        parser.error('audio file does not exist')
    if args.output.suffix != '.json':
        parser.error('output must have a .json extension; a sibling .csv is also written')
    if args.worker_options:
        options = TranscriptionOptions(**json.loads(args.worker_options))
        atomic_write_json(args.output, measure(args.audio, options))
        return
    reference = args.reference.read_text(encoding='utf-8') if args.reference else None
    terms = list(dict.fromkeys(line.strip() for line in args.terms.read_text(encoding='utf-8').splitlines()
                              if line.strip())) if args.terms else []
    rows = []
    from lecture_util.configuration import validate_transcription_options
    combinations = []
    for model, compute, batch, beam in itertools.product(
        args.models, args.compute_types, args.batch_sizes, args.beam_sizes,
    ):
        try:
            options = TranscriptionOptions(model=model, language=args.language, device=args.device, compute_type=compute,
                                           batch_size=batch, beam_size=None if beam == 'default' else int(beam))
            validate_transcription_options(options)
        except (ValueError, RuntimeError) as error:
            parser.error(str(error))
        combinations.append(options)
    with tempfile.TemporaryDirectory(prefix='lecture-util-benchmark-') as directory:
        result_path = Path(directory) / 'result.json'
        for options in combinations:
            result_path.unlink(missing_ok=True)
            print(f'Benchmark: {options}', flush=True)
            result = subprocess.run([
                sys.executable, '-m', 'lecture_util.benchmark', '--audio', str(args.audio.resolve()),
                '--worker-options', json.dumps(options.to_dict()), '--output', str(result_path),
            ], check=False, capture_output=True, text=True)
            if result.returncode:
                row = {'requested': options.to_dict(), 'error': result.stderr[-2000:]}
            else:
                row = json.loads(result_path.read_text(encoding='utf-8'))
                text = row.pop('text')
                row.update(quality_metrics(text, reference, terms))
            rows.append(row)
            write_results(args.output, rows)
    if any('error' in row for row in rows):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
