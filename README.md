# lecture-util

공개 `.m3u8` 강의를 다운로드하고 로컬 Whisper로 전사한 뒤, 전사문과 학습 노트를 개인 Obsidian Vault에 발행하는 도구입니다.

- `yt-dlp`로 HLS 영상을 MP4로 다운로드
- `ffmpeg`로 16 kHz mono WAV 추출
- Apple Silicon에서는 `mlx-whisper`, NVIDIA Linux에서는 `faster-whisper` 사용
- 로그인된 Codex CLI로 전사문 요약
- 과목별 `Lecture` 또는 `Lectures` 폴더에 Obsidian Markdown 발행
- 원본 영상은 설정한 영상 보관소에, 중간 산출물은 Vault 밖의 사용자 캐시에 보관
- 완료된 단계는 재사용하고 실패한 단계부터 재개

본인이 다운로드할 권한이 있는 강의에만 사용하세요. 로그인, 쿠키 또는 별도 인증이 필요한 LMS URL은 지원하지 않습니다.

## 빠른 시작

1. Vault에 과목 폴더와 `Lecture` 또는 `Lectures` 폴더를 미리 만듭니다.
2. 의존성을 설치하고 환경을 점검합니다.
3. 인자 없이 실행하여 최초 온보딩을 완료한 뒤 과목, 날짜, 제목과 URL을 입력합니다.

```bash
uv sync --dev
uv run lecture-util doctor
uv run lecture-util
```

작업이 끝나면 선택한 과목의 강의 폴더 아래에 주차 폴더를 자동으로 만들고 다음 두 노트를 저장합니다.

```text
N주차/
├── YYYY-MM-DD 제목.md
└── YYYY-MM-DD 제목 전사.md
```

## 요구 사항과 설치

공통으로 다음 프로그램이 필요합니다.

- Python 3.12 이상
- [uv](https://docs.astral.sh/uv/)
- `ffmpeg`
- `yt-dlp`
- 로그인된 Codex CLI

Codex 로그인을 포함한 Codex 자체 설정은 먼저 완료되어 있어야 합니다. 모델을 별도로 지정하지 않으면 Codex CLI에 설정된 기본 모델을 사용합니다.

### Apple Silicon macOS

```bash
brew install ffmpeg yt-dlp uv
uv sync --dev
```

`uv sync`가 `.venv`를 만들고 Apple Silicon용 `mlx-whisper`를 설치합니다. Whisper 모델은 처음 사용할 때 Hugging Face에서 내려받으므로 첫 실행은 평소보다 오래 걸릴 수 있습니다.

### NVIDIA GPU Linux

먼저 `ffmpeg`, `yt-dlp`와 NVIDIA 드라이버를 설치한 뒤 다음을 실행합니다.

```bash
uv sync --dev
```

Linux x86_64에서는 `faster-whisper`와 CUDA 12, cuDNN 9 런타임 패키지가 함께 설치됩니다. 설치 후 장치와 외부 명령을 확인합니다.

```bash
uv run lecture-util doctor
```

자동 장치 선택은 Apple Silicon의 MLX 또는 Linux x86_64의 NVIDIA GPU를 기대합니다. GPU를 사용할 수 없는 환경에서 CPU 전사를 의도한다면 `--device cpu`를 명시하거나 TUI의 장치를 `CPU`로 선택하세요.

## 온보딩과 Vault 준비

처음 인자 없이 실행하면 강의 입력 화면보다 먼저 온보딩 화면이 열립니다. 다음 기본값을 설정합니다.

| 항목 | 최초 제안값 | 설명 |
| --- | --- | --- |
| Obsidian Vault path | `~/Documents/학부연구생` | 강의 노트를 발행할 Vault 루트 |
| Video storage path | `~/Videos/lecture-util` | 과목과 주차별 MP4 영상을 보관할 루트 |
| Semester start date | 가장 최근의 8월 31일 | 강의 주차 계산 기준일 |
| Transcription device | `auto` | `auto`, `mlx`, `cuda`, `cpu` 중 선택 |
| Whisper model | `large-v3` | 기본 Whisper 모델 이름 또는 경로 |
| Lecture language | `auto` | 자동 감지 또는 `ko`, `en` 같은 언어 코드 |
| Codex model | 비어 있음 | 비어 있으면 Codex CLI에 설정된 기본 모델 사용 |

설정은 `$XDG_CONFIG_HOME/lecture-util/config.json`에 저장됩니다. `XDG_CONFIG_HOME`이 없으면 `~/.config/lecture-util/config.json`을 사용합니다. 설정을 바꾸려면 대화형 터미널에서 다음 명령을 실행합니다.

```bash
uv run lecture-util onboard
```

온보딩은 Vault가 현재 강의 발행에 사용 가능한지 확인합니다. 따라서 아래 과목 구조를 먼저 만든 뒤 저장해야 합니다.

영상 저장 폴더가 없으면 설정 저장 시 자동으로 만듭니다. Vault 내부 경로를 입력하면 영상이 Obsidian에 의해 인덱싱되거나 동기화될 수 있다는 확인 화면을 한 번 더 표시합니다.

과목은 Vault의 `10 Academics/Courses` 바로 아래에 있어야 합니다. 각 과목에는 정확히 하나의 `Lecture` 또는 `Lectures` 폴더가 필요합니다.

```text
<Obsidian Vault>/
└── 10 Academics/
    └── Courses/
        ├── 공기역학특론/
        │   ├── 공기역학특론 MOC.md
        │   ├── Lectures/
        │   └── Problems/
        └── 문제해결을 위한 글쓰기/
            ├── 문제해결을 위한 글쓰기 MOC.md
            ├── Lecture/
            └── Problems/
```

다음 규칙으로 과목 선택지를 만듭니다.

- `Courses`의 직속 하위 디렉터리만 과목으로 취급합니다.
- `Lecture`와 `Lectures`는 모두 지원합니다.
- 둘 중 하나만 존재하는 과목만 TUI 선택지에 표시합니다.
- 두 폴더가 모두 있거나 둘 다 없으면 해당 과목은 유효하지 않습니다.
- 요약 노트는 `<과목명> MOC.md`를 `[[과목명 MOC|과목명]]` 형식으로 연결합니다.

도구는 과목이나 `Lecture`/`Lectures` 폴더를 자동으로 만들지 않습니다. 먼저 Vault 구조를 준비하세요. `1주차`, `2주차` 같은 하위 폴더는 강의를 발행할 때 자동으로 만듭니다.

## TUI 사용법

가장 일반적인 실행 방법입니다.

```bash
uv run lecture-util
```

기본 화면에서 다음 값을 입력합니다.

| 항목 | 설명 |
| --- | --- |
| Course | Vault에서 자동 탐색한 과목 선택지 |
| Lecture date | 강의 날짜. 기본값은 프로그램을 실행한 주의 월요일이며 `YYYY-MM-DD` 형식으로 수정 가능 |
| Lecture title | 파일명과 노트 제목에 사용할 강의 제목 |
| Public `.m3u8` URL | 인증 없이 접근 가능한 HLS 재생목록 URL |

제목은 앞뒤 공백을 제거한 뒤 사용합니다. 빈 제목, `.`과 `..`, `/`, `\`, 개행 또는 NUL 문자가 포함된 제목은 거부됩니다.

### 고급 설정

`Advanced settings`를 펼치면 다음 값을 바꿀 수 있습니다.

| 항목 | 기본값 | 설명 |
| --- | --- | --- |
| Semester start date | 온보딩 설정값 | 주차 계산의 기준일이며 `YYYY-MM-DD` 형식으로 변경 가능 |
| Tags | 없음 | 쉼표로 구분하며 캐시의 `run.json`에만 기록 |
| Force every stage | 꺼짐 | 다운로드부터 요약까지 캐시 단계를 모두 다시 실행 |
| Transcription device | 온보딩 설정값 | `auto`, `mlx`, `cuda`, `cpu` 중 직접 선택 가능 |
| Whisper model | 온보딩 설정값 | Whisper 모델 이름 또는 지원되는 모델 경로 |
| Lecture language | 온보딩 설정값 | 자동 감지 또는 `ko`, `en` 같은 언어 코드 |
| Codex model | 온보딩 설정값 | 비어 있으면 Codex CLI 기본 모델 사용 |
| Summary prompt | 기본 프롬프트 | 직접 입력하거나 Markdown/text 파일에서 읽기 |

`Enter`를 누르거나 `Run`을 선택하면 다운로드 전에 과목 구조, 날짜, 제목과 대상 파일 충돌을 검사합니다. `Enter`는 포커스된 항목과 관계없이 즉시 실행합니다. 기존 노트가 있으면 TUI를 닫지 않고 오류를 표시하므로 날짜나 제목을 수정해 다시 실행할 수 있습니다. `Esc` 또는 `Cancel`은 아무 작업도 시작하지 않고 종료합니다.

Course는 `Space`로 목록을 펼치고 방향키로 이동한 뒤 `Space`로 선택할 수 있습니다.

검사를 통과하면 TUI가 닫히고 콘솔에서 다운로드, 오디오 추출, 전사, 요약의 진행 상태를 보여줍니다.

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
✓ [4/4] Wrote summary in 1m 58s to /home/seawhan/.cache/lecture-util/lecture-0123456789/summary.md
Complete <Obsidian Vault>/10 Academics/Courses/공기역학특론/Lectures/1주차/2026-09-04 압축성 유동.md (4m 35s)
```

파이프나 CI처럼 stdin/stdout이 터미널이 아닌 환경에서 인자 없이 호출하면 TUI를 기다리지 않고 종료 코드 2로 끝납니다.

## `run` 명령 사용법

TUI 없이 강의 하나를 처리하려면 `run` 명령을 사용합니다. 먼저 `lecture-util onboard`를 완료해야 하며 URL, 과목, 날짜와 제목은 모두 필수입니다.

```bash
uv run lecture-util run \
  'https://example.com/lecture/index.m3u8' \
  --course '공기역학특론' \
  --date 2026-09-04 \
  --title '압축성 유동'
```

과목 이름은 `Courses` 아래의 실제 디렉터리 이름과 정확히 일치해야 합니다. 한 번에 강의 하나만 처리하며 URL 목록을 받는 `--input` 배치 모드는 지원하지 않습니다.

기본 학기 시작일은 온보딩에서 저장한 값입니다. 시작일부터 7일씩 `1주차`, `2주차` 등으로 계산하며, 다른 기준일이 필요한 한 번의 실행에서는 `--semester-start`로 덮어쓸 수 있습니다.

```bash
uv run lecture-util run URL \
  --course '공기역학특론' \
  --date 2026-09-14 \
  --semester-start 2026-09-07 \
  --title '압축성 유동'
```

### 전사 옵션 지정

Whisper 모델, 언어, 장치와 Codex 모델도 온보딩 설정을 기본값으로 사용합니다. 명령행 옵션을 지정하면 해당 실행에서만 저장값을 덮어쓰며 설정 파일은 변경하지 않습니다.

```bash
uv run lecture-util run URL \
  --course '공기역학특론' \
  --date 2026-09-04 \
  --title '압축성 유동' \
  --device cuda \
  --whisper-model large-v3 \
  --language ko
```

`--language auto`는 언어를 자동 감지합니다. `large-v3` 실행 중 실제 메모리 부족 오류가 발생하면 메모리를 정리한 뒤 `turbo` 모델로 한 번 재시도하며, 요청 모델과 실제 모델 및 전환 이유를 `run.json`과 `transcript.json`에 남깁니다.

### Codex 모델과 요약 지시 지정

짧은 지시는 `--prompt`로 직접 전달합니다.

```bash
uv run lecture-util run URL \
  --course '공기역학특론' \
  --date 2026-09-04 \
  --title '압축성 유동' \
  --llm-model MODEL \
  --prompt '한국어로 시험 대비 핵심 개념과 예상 문제를 정리해.'
```

긴 지시는 파일로 관리할 수 있습니다.

```bash
uv run lecture-util run URL \
  --course '공기역학특론' \
  --date 2026-09-04 \
  --title '압축성 유동' \
  --prompt-file prompts/exam-notes.md
```

`--prompt`와 `--prompt-file`은 동시에 사용할 수 없습니다. 기본 프롬프트는 강의의 핵심 개념과 관계를 중심으로 복습하기 좋은 정리 노트를 만듭니다. 정의, 원리, 수식의 조건, 대표 예시와 주의사항은 보존하고 반복과 여담은 압축합니다. 강의의 주언어를 유지하며 요약 본문에는 타임스탬프를 넣지 않습니다. Codex는 캐시 작업공간을 읽기 전용으로 열고 `transcript.md`를 직접 읽으며, 결과는 기존 `## Notes` 아래에 삽입할 수 있는 Markdown 본문으로 생성합니다.

### 태그와 강제 재실행

`--tag`는 여러 번 지정하거나 쉼표로 구분할 수 있습니다.

```bash
uv run lecture-util run URL \
  --course '공기역학특론' \
  --date 2026-09-04 \
  --title '압축성 유동' \
  --tag week-1 \
  --tag 'exam,aerodynamics'
```

태그는 Vault 노트 frontmatter가 아니라 캐시의 `run.json`에만 저장됩니다.

`--force`는 다운로드, 오디오 추출, 전사와 요약을 모두 다시 수행합니다. 기존 Vault 노트를 덮어쓰는 옵션은 아니므로 같은 날짜와 제목의 노트가 존재하면 `--force`를 사용해도 처리 전에 중단합니다.

## 생성되는 파일

### Vault의 요약 노트

선택한 강의 폴더의 `N주차`에 `YYYY-MM-DD 제목.md`를 만듭니다.

```markdown
---
type: lecture
area:
  - academics
course: "공기역학특론"
created: 2026-09-04
source_url: "https://example.com/lecture/index.m3u8"
transcript: "[[2026-09-04 압축성 유동 전사]]"
---

# 2026-09-04 압축성 유동

## Overview

- Topic: 압축성 유동
- Related course: [[공기역학특론 MOC|공기역학특론]]
- Transcript: [[2026-09-04 압축성 유동 전사]]

## Notes

Codex가 생성한 요약...
```

요약 뒤에는 기존 Lecture Template과 같은 `Questions`, `Concepts to Extract`, `Related Problems and Sources`, `Follow-up` 섹션이 이어집니다.

### Vault의 전사 노트

같은 주차 폴더에 `YYYY-MM-DD 제목 전사.md`를 만듭니다. `lecture-transcript` frontmatter, 원본 URL, 요약 노트 링크와 구간별 타임스탬프가 포함됩니다.

```markdown
# 2026-09-04 압축성 유동 전사

## Overview

- Related lecture: [[2026-09-04 압축성 유동]]
- Source: https://example.com/lecture/index.m3u8

## Transcript

[00:00:00.000–00:00:08.240] 첫 번째 발화 내용
```

기본 설정에서는 Vault에 이 두 Markdown 파일만 저장합니다. Vault 내부 영상 경로 사용에 직접 동의하지 않는 한 MP4, WAV, JSON, SRT와 실행 상태는 Vault에 복사하지 않습니다.

### 영상 보관소

MP4 영상은 온보딩에서 설정한 경로 아래에 과목, 학기 주차와 강의 제목으로 저장합니다. 강의일 자체는 파일명에 넣지 않습니다.

```text
~/Videos/lecture-util/
└── 공기역학특론/
    └── 2주차/
        └── 압축성 유동.mp4
```

같은 URL에 대해 다운로드가 완료된 상태이고 해당 영상이 있으면 재사용합니다. 완료 기록이 없는 같은 경로의 파일은 실수로 덮어쓰지 않으며, 명시적으로 `--force`를 지정해야 교체합니다.

### 사용자 캐시

전체 실행의 작업공간은 URL의 SHA-256 해시 앞 10자를 사용합니다.

```text
~/.cache/lecture-util/lecture-<URL 해시>/
├── audio.wav
├── transcript.json
├── transcript.md
├── transcript.srt
├── summary.md
└── run.json
```

`run.json`에는 원본 URL, 과목, 날짜, 제목, 영상 및 Vault 발행 경로, 태그와 각 처리 단계의 상태가 기록됩니다. 캐시와 영상은 자동 삭제하지 않습니다.

### 기존 설정과 영상 옮기기

영상 경로가 없는 설정 v1은 새 실행에 사용할 수 없습니다. 먼저 `lecture-util onboard`를 실행해 기존 Vault, 학기 및 모델 설정을 불러온 뒤 영상 경로를 저장합니다. 기존 영상은 자동으로 이동하지 않습니다.

기존 `~/.cache/lecture-util/lecture-<URL 해시>/source.mp4`를 계속 재사용하려면 직접 다음 위치로 옮깁니다.

```text
<설정한 영상 경로>/<과목>/<N주차>/<제목>.mp4
```

`N주차`는 온보딩의 학기 시작일과 강의일을 기준으로 계산합니다. 기존 `run.json`의 `course`, `lecture_date`, `title` 값을 참고할 수 있습니다.

## 캐시 재사용과 실패 복구

같은 URL을 다시 처리하면 URL 해시가 같으므로 기존 작업공간을 사용합니다.

- 같은 과목·주차·제목 위치에 다운로드된 영상과 오디오가 정상적으로 완료되어 있으면 다시 만들지 않습니다.
- Whisper 모델, 언어와 장치가 이전 실행과 같으면 기존 전사문을 재사용합니다.
- 전사 내용, Codex 모델과 요약 프롬프트가 같으면 기존 요약을 재사용합니다.
- 같은 URL이면 오디오와 전사 캐시는 유지되지만, 과목·주차·제목이 바뀌어 새 영상 경로가 되면 그 위치에는 영상을 다시 다운로드합니다.
- 실패한 단계는 `run.json`에 실패 상태와 메시지를 기록하며 다음 실행에서 다시 시도합니다.

캐시를 재사용한 단계는 진행 화면에서 `↻`로 표시됩니다.

```text
↻ [1/4] Reusing video (96.8 MiB)
↻ [2/4] Reusing extracted audio (61.4 MiB)
↻ [3/4] Reusing 282 transcript segments (ko)
↻ [4/4] Reusing summary from /home/seawhan/.cache/lecture-util/lecture-0123456789/summary.md
```

기존 Vault 노트를 새 내용으로 교체하려면 도구 밖에서 기존 노트를 직접 이동하거나 이름을 바꾼 뒤 다시 실행해야 합니다. 노트가 남아 있는 동안에는 원본 보호를 위해 실행이 시작되지 않습니다.

## 단계별 명령

문제 진단이나 수동 복구가 필요할 때 작업 단계를 따로 실행할 수 있습니다. 단계별 명령은 캐시만 변경하며 Vault 노트를 발행하지 않습니다.

### 다운로드와 오디오 추출

```bash
uv run lecture-util download URL \
  --course '공기역학특론' \
  --title '압축성 유동'
```

영상 경로와 학기 시작일은 온보딩 설정을 사용합니다. `--date`를 생략하면 명령을 실행한 날짜로 주차를 계산하며 다른 강의일을 사용하려면 `--date YYYY-MM-DD`를 지정합니다.

기본 캐시 작업 루트는 `~/.cache/lecture-util`입니다. `--output-dir`은 영상 보관소가 아니라 오디오, 전사 및 실행 상태 캐시의 위치를 변경합니다.
다운로드를 중단하거나 다운로드가 실패하면 영상 대상 폴더의 불완전한 임시 파일을 자동으로 삭제합니다.

```bash
uv run lecture-util download URL \
  --course '공기역학특론' \
  --date 2026-09-04 \
  --title '압축성 유동' \
  --output-dir /tmp/lecture-cache \
  --force
```

명령이 출력한 `Prepared` 경로가 이후 단계의 `LECTURE_DIR`입니다.

### 기존 오디오 전사

```bash
uv run lecture-util transcribe LECTURE_DIR \
  --whisper-model large-v3 \
  --language ko \
  --device cuda
```

`LECTURE_DIR/audio.wav`를 읽어 `transcript.json`, `transcript.md`, `transcript.srt`를 만듭니다.

### 기존 전사문 요약

```bash
uv run lecture-util summarize LECTURE_DIR \
  --prompt-file prompts/exam-notes.md
```

`LECTURE_DIR/transcript.json`과 `transcript.md`를 읽어 `summary.md`를 만듭니다. 이 명령 역시 Obsidian 노트를 발행하지 않으므로 최종 Vault 발행이 필요하면 같은 URL로 전체 `run` 명령을 실행하세요. 완료된 캐시 단계는 재사용됩니다.

## 자주 발생하는 오류

### 과목이 선택지에 나타나지 않음

과목이 `10 Academics/Courses` 바로 아래에 있는지, 그리고 그 안에 `Lecture` 또는 `Lectures` 중 하나만 존재하는지 확인하세요. 두 폴더를 동시에 만들면 유효하지 않은 과목으로 처리됩니다.

### `Lecture note already exists`

같은 날짜와 제목의 요약 또는 전사 노트가 이미 있습니다. TUI에서 날짜나 제목을 바꾸거나 기존 노트를 직접 정리한 뒤 다시 실행하세요. `--force`는 이 보호 장치를 해제하지 않습니다.

### 자동 장치 선택 실패

NVIDIA 드라이버와 `nvidia-smi`를 확인하세요. GPU 없이 실행하려면 TUI에서 `CPU`를 선택하거나 CLI에 `--device cpu`를 전달합니다.

### URL 또는 다운로드 실패

URL이 `http://` 또는 `https://`로 시작하는 공개 `.m3u8` 주소인지 확인하세요. 브라우저 로그인 세션, 쿠키, 사설 헤더가 필요한 주소는 현재 지원하지 않습니다.

### Codex 요약 실패

Codex CLI 로그인 상태를 확인한 뒤 같은 명령을 다시 실행하세요. 다운로드와 전사가 완료되었다면 해당 단계는 캐시에서 재사용되고 요약부터 다시 시작합니다.

## 개발

개발 의존성을 설치하고 전체 테스트와 패키지 빌드를 실행합니다.

```bash
uv sync --dev
uv run pytest -q
uv build
```

테스트에는 임시 Vault를 사용한 과목 탐색·충돌·노트 발행 테스트와 작은 로컬 HLS 스트림을 생성하는 실제 `yt-dlp`/FFmpeg 통합 테스트가 포함됩니다. 테스트는 실제 Vault에 파일을 만들지 않습니다.

관련 문서:

- [MLX Whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper)
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
- [Codex 비대화형 실행](https://learn.chatgpt.com/docs/non-interactive-mode)
