# lecture-util

공개 `.m3u8` 강의를 다운로드하고, 로컬 Whisper로 전사한 다음 전사문과 학습 노트를 개인 Obsidian Vault에 발행하는 CLI입니다.

- `yt-dlp`로 HLS 영상을 MP4로 다운로드
- `ffmpeg`로 16 kHz mono WAV 추출
- Apple Silicon에서는 `mlx-whisper`, NVIDIA Linux에서는 `faster-whisper`
- Codex CLI를 사용한 요약
- 과목별 `Lecture`/`Lectures` 폴더에 Obsidian용 전사문과 요약 노트 발행
- 원본 영상, 오디오, JSON, SRT와 실행 상태는 사용자 캐시에 보존
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

터미널에서 인자 없이 실행하면 설정 TUI가 열립니다. 다음 위치의 직속 하위 폴더 중 정확히 하나의 `Lecture` 또는 `Lectures` 폴더가 있는 과목을 선택지로 표시합니다.

```text
/home/seawhan/Documents/학부연구생/10 Academics/Courses
```

강의 날짜, 제목과 URL은 필수입니다. 날짜는 `YYYY-MM-DD` 형식으로 직접 입력합니다. 필요하면 접힌 고급 설정에서 태그, Whisper 모델·장치·언어, Codex 모델과 요약 프롬프트를 변경할 수 있습니다.

```bash
uv run lecture-util
```

`Run`을 선택하면 기존 노트와 충돌하는지 먼저 검사합니다. 충돌하면 TUI를 닫지 않고 오류를 표시하므로 날짜나 제목을 바꿔 다시 시도할 수 있습니다. 검사를 통과하면 TUI가 닫힌 뒤 다운로드·전사·요약 진행 상태를 표시합니다. `Cancel`이나 `Esc`로 취소할 수 있으며, 파이프나 CI처럼 비대화형 환경에서 인자 없이 호출하면 대기하지 않고 종료합니다.

## 비대화형 실행

### Codex 기본 실행

```bash
uv run lecture-util run \
  'https://example.com/lecture/index.m3u8' \
  --course '공기역학특론' \
  --date 2026-09-04 \
  --title '압축성 유동' \
  --tag operating-systems \
  --tag midterm
```

요약에는 로그인된 Codex CLI를 사용합니다. Codex에 설정된 기본 모델 대신 특정 모델을 쓰려면 `--llm-model`을 지정합니다.

```bash
uv run lecture-util run URL \
  --course '공기역학특론' \
  --date 2026-09-04 \
  --title '압축성 유동' \
  --llm-model MODEL
```

Codex는 비대화형 `codex exec`로 강의 결과 디렉터리를 읽기 전용으로 열고 `transcript.md`를 직접 읽습니다. 파일 변경은 허용하지 않습니다.

### 진행 상태

실행 중에는 현재 강의와 단계, 캐시 재사용 여부, 파일 크기, 전사 모델·언어·세그먼트 수, Codex 요약 상태와 경과 시간을 표시합니다.

```text
──────────────── 공기역학특론 · 2026-09-04 압축성 유동 ────────────────
https://example.com/lecture/index.m3u8
→ [1/4] Downloading HLS video
✓ [1/4] Downloaded 96.8 MiB in 23.4s
→ [2/4] Extracting 16 kHz mono audio
✓ [2/4] Extracted 61.4 MiB in 0.9s
→ [3/4] Transcribing with Whisper large-v3 on cuda (this may take several minutes)
✓ [3/4] Created 282 segments in 2m 12s (faster-whisper/large-v3, ko)
→ [4/4] Summarizing transcript file with Codex
· [4/4] Summarizing transcript.md with Codex
✓ [4/4] Wrote summary in 1m 58s to /home/seawhan/.cache/lecture-util/lecture-0123456789/summary.md
Complete /home/seawhan/Documents/학부연구생/10 Academics/Courses/공기역학특론/Lectures/2026-09-04 압축성 유동.md (4m 35s)
```

완료된 단계는 다음 실행에서 `↻` 기호와 함께 캐시 재사용으로 표시됩니다. 외부 명령의 장황한 정상 로그는 숨기지만, 실패하면 진단에 필요한 마지막 오류 내용은 출력합니다.

## 사용자 프롬프트

기본 요약은 `이 강의를 요약해`라는 요청과 함께 캐시의 `transcript.md` 파일을 Codex가 직접 읽도록 합니다. 다음 옵션 중 하나로 요청 문구를 교체할 수 있습니다. 각 명령에는 앞의 예시처럼 `--course`, `--date`, `--title`도 지정해야 합니다.

```bash
uv run lecture-util run URL \
  --prompt '한국어로 시험 대비 요약과 예상 문제를 작성하라.'

uv run lecture-util run URL \
  --prompt-file prompts/exam-notes.md
```

## 단계별 명령과 결과

```bash
uv run lecture-util download URL --tag week-1
uv run lecture-util transcribe ~/.cache/lecture-util/lecture-0123456789
uv run lecture-util summarize ~/.cache/lecture-util/lecture-0123456789
```

강의마다 다음 캐시 파일을 만듭니다.

```text
~/.cache/lecture-util/lecture-<URL 해시>/
├── source.mp4
├── audio.wav
├── transcript.json
├── transcript.md
├── transcript.srt
├── summary.md
└── run.json
```

Vault에는 다음 두 파일만 저장합니다. 과목에 이미 존재하는 강의 폴더 이름이 `Lecture`든 `Lectures`든 자동으로 사용합니다.

```text
10 Academics/Courses/<과목>/<Lecture 또는 Lectures>/
├── YYYY-MM-DD 제목.md
└── YYYY-MM-DD 제목 전사.md
```

요약 노트는 기존 강의 템플릿 구조와 과목 MOC 링크를 사용하며 전사 노트와 서로 연결됩니다. 같은 이름의 파일이 하나라도 있으면 기존 노트를 보호하기 위해 다운로드 전에 중단합니다.

태그는 `run.json`의 `tags` 배열에만 저장됩니다. 같은 URL을 다시 실행하면 완료된 단계와 요약을 재사용합니다. 제목이나 날짜만 바꾼 재실행도 같은 URL 캐시를 재사용합니다. 다른 Whisper 모델·언어·장치를 지정하면 전사를 다시 수행하며, `--force`는 캐시의 모든 단계를 다시 실행하지만 기존 Vault 노트를 덮어쓰지는 않습니다.

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
