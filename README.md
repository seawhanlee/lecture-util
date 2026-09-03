# lecture-util

공개 `.m3u8` 강의를 다운로드하고, 로컬 Whisper로 전사한 다음 LLM으로 학습 노트를 만드는 CLI입니다.

- `yt-dlp`로 HLS 영상을 MP4로 다운로드
- `ffmpeg`로 16 kHz mono WAV 추출
- Apple Silicon에서는 `mlx-whisper`, NVIDIA Linux에서는 `faster-whisper`
- Codex CLI를 사용한 요약
- 원본 영상, 오디오, 타임스탬프 전사, SRT, 요약과 실행 상태 보존
- 실패한 단계부터 재개

본인이 다운로드할 권한이 있는 강의에만 사용하세요. 현재 버전은 로그인이나 쿠키가 필요한 LMS URL을 지원하지 않습니다.

## 설치

Python 3.12 이상, [uv](https://docs.astral.sh/uv/), 로그인된 Codex CLI가 필요합니다.

### Apple Silicon macOS

```bash
brew install ffmpeg yt-dlp uv
uv sync --dev
```

`uv sync`가 `.venv`를 만들고 Apple Silicon용 `mlx-whisper`를 설치합니다. MLX Whisper는 처음 실행할 때 선택한 모델을 Hugging Face에서 내려받습니다.

### NVIDIA GPU Linux

먼저 `ffmpeg`, `yt-dlp`, NVIDIA 드라이버를 설치합니다. `uv sync`가 faster-whisper와 함께 Linux용 CUDA 12 및 cuDNN 9 런타임 패키지를 설치합니다.

```bash
uv sync --dev
```

설치를 확인합니다.

```bash
uv run lecture-util doctor
```

GPU를 사용할 수 없는 환경에서는 자동으로 CPU로 전환하지 않습니다. CPU 전사를 의도한 경우에만 `--device cpu`를 명시하세요.

## 인터랙티브 실행

터미널에서 인자 없이 실행하면 입력 URL, 태그, Whisper 모델, 장치, 언어, Codex 모델과 프롬프트를 차례로 선택합니다.

```bash
uv run lecture-util
```

선택한 값은 현재 실행에만 사용됩니다. 파이프나 CI처럼 비대화형 환경에서 인자 없이 호출하면 대기하지 않고 종료합니다.

## 비대화형 실행

### Codex 기본 실행

```bash
uv run lecture-util run \
  'https://example.com/lecture/index.m3u8' \
  --tag operating-systems \
  --tag midterm
```

요약에는 로그인된 Codex CLI를 사용합니다. Codex에 설정된 기본 모델 대신 특정 모델을 쓰려면 `--llm-model`을 지정합니다.

```bash
uv run lecture-util run URL --llm-model MODEL
```

Codex는 비대화형 `codex exec`로 임시 읽기 전용 작업공간에서 실행되며, 전사문 요약 외의 도구 사용이나 파일 변경을 지시하지 않습니다.

## 여러 강의와 사용자 프롬프트

URL 목록은 한 줄에 하나씩 작성하며 빈 줄과 `#` 주석을 사용할 수 있습니다. 강의는 GPU 메모리 충돌을 피하도록 순차 처리됩니다.

```text
# week 1
https://example.com/lecture-1/index.m3u8
https://example.com/lecture-2/index.m3u8
```

```bash
uv run lecture-util run --input lectures.txt \
  --tag week-1
```

기본 요약은 `이 강의를 요약해`라는 요청 뒤에 녹취록을 Markdown 코드 펜스로 감싼 plain text 사용자 프롬프트를 전달합니다. 다음 옵션 중 하나로 요청 문구를 교체할 수 있습니다.

```bash
uv run lecture-util run URL \
  --prompt '한국어로 시험 대비 요약과 예상 문제를 작성하라.'

uv run lecture-util run URL \
  --prompt-file prompts/exam-notes.md
```

긴 전사문은 기본 12,000자 단위로 나누고 각 부분을 ` ``` ` 코드 펜스로 감싸 요약한 뒤, 부분 요약도 같은 방식으로 재귀적으로 병합합니다. `--chunk-chars`로 크기를 조정할 수 있습니다.

## 단계별 명령과 결과

```bash
uv run lecture-util download URL --tag week-1
uv run lecture-util transcribe output/lecture-0123456789
uv run lecture-util summarize output/lecture-0123456789
```

강의마다 다음 파일을 만듭니다.

```text
output/lecture-<URL 해시>/
├── source.mp4
├── audio.wav
├── transcript.json
├── transcript.md
├── transcript.srt
├── summary.md
├── run.json
└── work/summary-chunks/
```

태그는 `run.json`의 `tags` 배열에만 저장됩니다. 같은 URL을 다시 실행하면 완료된 단계와 요약 청크를 재사용합니다. 다른 Whisper 모델·언어·장치를 지정하면 전사를 다시 수행하며, `--force`는 모든 단계를 다시 실행합니다.

기본 전사 모델은 `large-v3`입니다. 실제 메모리 부족 오류가 발생한 경우에만 메모리를 해제하고 `turbo`로 한 번 다시 시도하며, 요청 모델·실제 모델·전환 이유는 `run.json`과 `transcript.json`에 남습니다.

## 개발

```bash
uv sync --dev
uv run pytest -q
```

테스트에는 작은 HLS 스트림을 로컬에서 생성해 실제 `yt-dlp` 다운로드와 `ffmpeg` 오디오 추출을 확인하는 통합 테스트가 포함됩니다.

관련 문서:

- [MLX Whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper)
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
- [Codex 비대화형 실행](https://learn.chatgpt.com/docs/non-interactive-mode)
