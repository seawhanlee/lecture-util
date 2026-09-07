from __future__ import annotations

import json
import random

from lecture_util.benchmark import edit_distance, quality_metrics, write_results


def test_bitvector_distance_matches_reference_algorithm():
    rng = random.Random(42)
    for _ in range(200):
        a = ''.join(rng.choices('가나다abc', k=rng.randrange(15)))
        b = ''.join(rng.choices('가나다abc', k=rng.randrange(15)))
        previous = list(range(len(b) + 1))
        for i, x in enumerate(a, 1):
            current = [i]
            for j, y in enumerate(b, 1):
                current.append(min(previous[j] + 1, current[-1] + 1, previous[j-1] + (x != y)))
            previous = current
        assert edit_distance(a, b) == previous[-1]


def test_quality_normalizes_and_reports_missing_terms():
    result = quality_metrics('마하 수, MACH!', '마하수 mach', ['마하수', '압력'])
    assert result == {'cer': 0, 'missing_terms': ['압력'], 'term_missing_rate': 0.5}
    assert quality_metrics('', None, [])['cer'] is None


def test_results_include_failed_combinations(tmp_path):
    output = tmp_path / 'benchmark.json'
    rows = [{'requested': {'model': 'turbo'}, 'wall_seconds': 1},
            {'requested': {'model': 'large-v3'}, 'error': 'OOM'}]
    write_results(output, rows)
    assert json.loads(output.read_text()) == rows
    assert 'OOM' in output.with_suffix('.csv').read_text()


def test_measure_separates_preparation_and_inference(tmp_path, monkeypatch):
    from lecture_util.benchmark import measure
    from lecture_util.models import Transcript, TranscriptionOptions, Segment
    from lecture_util.progress import ProgressEvent
    monkeypatch.setattr('lecture_util.transcription.preflight_transcription', lambda _: 'cpu')
    monkeypatch.setattr('lecture_util.benchmark.monotonic', iter([0, 2, 6]).__next__)
    def transcribe(audio, **kwargs):
        kwargs['progress'](ProgressEvent('transcription', 'update', 'Infer', phase='inference'))
        return Transcript('ko', 8, 'test', 'turbo', 'turbo', [Segment(0, 8, 'hello')],
                          effective_options={'device': 'cpu'})
    monkeypatch.setattr('lecture_util.transcription.transcribe_audio', transcribe)
    result = measure(tmp_path / 'audio.wav', TranscriptionOptions(model='turbo', device='cpu'))
    assert result['preparation_seconds'] == 2
    assert result['inference_seconds'] == 4
    assert result['real_time_factor'] == 0.75
    assert result['peak_gpu_bytes'] is None
