# lecture-util

공개 `.m3u8` 강의를 다운로드하거나 로컬 영상·녹음 파일을 불러와 로컬 Whisper로 전사한 뒤, 전사문과 학습 노트를 개인 Obsidian Vault에 발행하는 도구입니다.

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
3. 인자 없이 실행하여 최초 온보딩을 완료한 뒤 과목, 날짜, 제목과 HLS URL 또는 로컬 파일 경로를 입력합니다.

```bash
uv sync --dev
uv run lecture-util doctor
uv run lecture-util
```

URL을 이미 알고 있다면 명령 뒤에 바로 넣을 수 있습니다. 쿼리 문자열의 `&` 등이 셸에 해석되지 않도록 URL을 따옴표로 감싸세요.

```bash
uv run lecture-util 'https://example.com/lecture/index.m3u8'
```

Rich 대화형 프롬프트에서 과목 번호, 주차, 날짜, 제목을 입력합니다. 날짜 기본값은 설정된 학기 시작일을 기준으로 선택 주차의 첫날이며, 다른 날짜를 입력할 때도 같은 주차에 속해야 합니다. 노트·영상 저장 경로와 저장된 모델·thinking effort 등 처리 설정을 확인한 뒤 실행합니다. 마지막 질문에 `n`을 입력하거나 입력 중 `Ctrl+C`를 누르면 취소됩니다. 설정이 없으면 먼저 온보딩을 진행합니다. 이 방식은 대화형 터미널에서 사용하며, 자동화에는 기존 `run URL --course ... --date ... --title ...` 명령을 사용하세요.

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
- `ffmpeg`와 `ffprobe`
- HLS 다운로드에 필요한 `yt-dlp`
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
| Compute type | `auto` | faster-whisper 정밀도 |
| Batch size | `0` | `0`은 비배치 처리, 양수는 배치 크기 |
| Beam size | 빈 값 | 백엔드 기본값 사용 또는 양수 지정 |
| Codex model | Codex 기본 설정 사용 | 설치된 Codex에서 조회한 모델을 드롭다운으로 선택 |
| Thinking effort | Codex 기본 설정 사용 | 선택 모델의 추론 강도를 드롭다운으로 선택 |

설정은 `$XDG_CONFIG_HOME/lecture-util/config.json`에 저장됩니다. `XDG_CONFIG_HOME`이 없으면 `~/.config/lecture-util/config.json`을 사용합니다. 설정을 바꾸려면 대화형 터미널에서 다음 명령을 실행합니다.

```bash
uv run lecture-util config
```

`config`는 저장된 설정을 채운 편집 화면을 엽니다. Codex 모델 드롭다운을 포함한 기본값을 수정한 뒤 `Ctrl+S`로 저장하고, `Esc`로 취소하면 기존 설정을 유지합니다. 설정이 없으면 최초 기본값으로 시작합니다. 기존 `uv run lecture-util onboard` 명령도 동일한 설정 편집을 지원합니다.

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

강의 입력과 초기 설정 화면은 터미널의 기본 글자색·배경색과 ANSI 색상 팔레트를 사용합니다.

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
| HLS URL or local media path | 공개 HLS URL 또는 로컬 영상·녹음 파일 경로 |

제목은 앞뒤 공백을 제거한 뒤 사용합니다. 빈 제목, `.`과 `..`, `/`, `\`, 개행 또는 NUL 문자가 포함된 제목은 거부됩니다.

### 고급 설정

`Lecture options`, `Transcription`, `Summary`를 펼치면 다음 값을 바꿀 수 있습니다.

| 항목 | 기본값 | 설명 |
| --- | --- | --- |
| Semester start date | 온보딩 설정값 | 주차 계산의 기준일이며 `YYYY-MM-DD` 형식으로 변경 가능 |
| Tags | 없음 | 쉼표로 구분하며 캐시의 `run.json`에만 기록 |
| Force every stage | 꺼짐 | 다운로드부터 요약까지 캐시 단계를 모두 다시 실행 |
| Transcription device | 온보딩 설정값 | `auto`, `mlx`, `cuda`, `cpu` 중 직접 선택 가능 |
| Whisper model | 온보딩 설정값 | Whisper 모델 이름 또는 지원되는 모델 경로 |
| Lecture language | 온보딩 설정값 | 자동 감지 또는 `ko`, `en` 같은 언어 코드 |
| Compute type | 온보딩 설정값 (`auto`) | faster-whisper의 정밀도. CUDA는 FP16, CPU는 INT8이 기본 |
| Batch size | 온보딩 설정값 (`0`) | `0`은 기존 비배치 처리, 양수는 배치 크기 |
| Beam size | 온보딩 설정값 (빈 값) | faster-whisper 기본값 5. 양수로 직접 지정 가능 |
| Codex model | 온보딩 설정값 | 드롭다운에서 이번 강의에 사용할 모델 선택 |
| Thinking effort | 온보딩 설정값 | 이번 강의에 사용할 추론 강도 선택 |
| Summary prompt | 기본 프롬프트 | 직접 입력하거나 Markdown/text 파일에서 읽기 |

폭 100칸 이상에서는 입력 폼 오른쪽에 강의 정보·저장 경로·처리 설정 요약을 표시합니다. 좁은 터미널에서는 요약이 폼 아래로 이동하며, 하단 실행 버튼은 스크롤과 관계없이 표시됩니다. 초기 설정 화면도 같은 배치를 사용합니다.

`Ctrl+R` 또는 `Run lecture`로 실행합니다. 초기 설정은 `Ctrl+S` 또는 `Save settings`로 저장합니다. `Tab`/`Shift+Tab`으로 이동하고, 다운로드 옵션을 비롯한 선택 메뉴는 `Enter` 또는 `Space`로 펼친 뒤 방향키와 `Enter`/`Space`로 선택합니다. 닫힌 선택 메뉴에서 `↑`/`↓`를 누르면 목록을 다시 열지 않고 이전/다음 항목으로 이동합니다. 여러 줄 프롬프트에서 `Enter`는 줄바꿈입니다. `Esc`는 열린 선택 목록을 먼저 닫으며, 기본 화면에서는 취소합니다.

실행 전에 과목 구조, 날짜, 제목과 대상 파일 충돌을 검사합니다. 오류가 있으면 입력값을 보존하고 해당 필드를 펼쳐 포커스하므로 수정 후 다시 실행할 수 있습니다.

검사를 통과하면 TUI가 닫히고 고정 진행판이 다운로드·오디오 추출·전사·요약 상태, 경과 시간과 최근 메시지를 갱신합니다. 캐시 사용은 `Cached`로 표시하며 경고는 유지됩니다. 완료 후에는 결과 경로와 소요 시간이 남습니다. `run`, `download`, `transcribe`, `summarize` 명령도 동일한 진행 표시를 사용합니다.

전사 중 모델 파일을 준비할 때는 Hugging Face의 별도 다운로드 진행 막대를 억제해 Rich 진행판과 겹치지 않게 합니다. 경고와 오류는 그대로 표시합니다. `HF_HUB_DISABLE_PROGRESS_BARS=0`을 명시한 환경에서는 해당 설정이 우선하므로, 진행 막대가 다시 나타나면 변수를 해제하거나 `1`로 설정하세요.

```text
╭────────────────── Lecture processing ──────────────────╮
│ ✓  Download                                  Complete │
│ ↻  Audio                                       Cached │
│ ⠋  Transcription                              Running │
│ ·  Summary                                    Waiting │
│                                                       │
│ Transcribing with Whisper large-v3 on cuda             │
╰──────────────────── Elapsed 2m 12s ────────────────────╯
```

출력을 파일이나 파이프로 전달하면 애니메이션 없이 단계별 로그를 남깁니다.

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

TUI 없이 강의 하나를 처리하려면 `run` 명령을 사용합니다. 먼저 `lecture-util onboard`를 완료해야 하며 입력 소스(HLS URL 또는 로컬 파일 경로), 과목, 날짜와 제목은 모두 필수입니다.

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

`config`, `onboard`와 강의 실행 화면에서 `Thinking effort`를 선택할 수 있습니다. 선택한 모델의 지원 목록을 Codex 또는 로컬 캐시에서 읽어 표시합니다. 모델 변경 시 지원되지 않는 effort는 기본값으로 전환하며 안내를 표시합니다. 모델을 지정하지 않거나 지원 정보를 조회할 수 없으면 일반적인 effort 옵션과 지원 여부 미확인 안내를 표시합니다. `Use Codex configured default`는 effort를 별도로 지정하지 않습니다.

`run`과 `summarize`에서는 `--reasoning-effort high`처럼 지정할 수도 있습니다. `run`은 저장된 effort를 기본값으로 사용하고 `--reasoning-effort ''`로 Codex 기본 설정을 사용할 수 있습니다. `summarize`도 생략 시 저장된 모델과 effort를 사용하며, `--llm-model ''`와 `--reasoning-effort ''`로 각각 Codex 기본 설정을 사용할 수 있습니다. effort가 달라지면 요약 캐시를 다시 생성합니다.

온보딩과 강의 실행 화면에서 Codex 모델을 드롭다운으로 선택할 수 있습니다. `Use Codex configured default`는 Codex 자체 기본 설정을 사용합니다. 모델 목록은 화면을 연 뒤 자동으로 조회하며, 조회 실패 시 로컬 Codex 캐시를 사용하고 상태를 표시합니다. 캐시도 없으면 기본 설정과 기존 저장 모델을 선택할 수 있습니다. 온보딩에서 저장한 모델은 다음 실행의 기본값이고, 강의 실행 화면의 변경은 해당 실행에만 적용됩니다.

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

`--prompt`와 `--prompt-file`은 동시에 사용할 수 없습니다. 기본 프롬프트는 강의의 핵심 개념과 관계를 중심으로 복습하기 좋은 정리 노트를 만듭니다. 정의, 원리, 수식의 조건, 대표 예시와 주의사항은 보존하고 반복과 여담은 압축합니다. 강의의 주언어를 유지하며 요약 본문에는 타임스탬프를 넣지 않습니다. Codex는 캐시 작업공간을 읽기 전용으로 열고 `transcript.md`를 직접 읽으며, 결과는 기존 `## Notes` 아래에 삽입할 수 있는 **Obsidian Flavored Markdown** 본문으로 생성합니다. 도입은 `[!abstract]` 콜아웃으로, 중요한 한계·불확실성은 필요한 경우 `[!warning]` 콜아웃으로 표시하며 수식은 `$...$`와 `$$` 구분자를 사용합니다. 실험의 비교 조건·측정·결과와 수식의 전제는 전사에 있는 범위에서 보존하고, 강의자의 주장·추정·비유를 확정적인 사실로 바꾸지 않습니다. 반복되는 예시와 중복 정리는 줄입니다. 내부 링크는 생성한 본문의 실제 제목을 가리키는 `[[#제목]]`만 필요할 때 사용하며, 존재 여부를 모르는 Vault 노트 링크는 만들지 않습니다. 제목과 frontmatter는 발행 단계에서 추가합니다. 프롬프트가 바뀌면 다음 요약 실행에서 이전 요약 캐시를 재사용하지 않습니다.

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

## 로컬 영상·녹음 처리

기존 파일도 과목·주차별 전사문과 요약 노트로 발행할 수 있습니다.

```bash
uv run lecture-util './강의 녹음.m4a'
uv run lecture-util run './강의 영상.mp4' \
  --course '공기역학특론' --date 2026-09-07 --title '첫 강의'
```

인자 없이 실행한 폼에서도 URL 대신 파일 경로를 입력할 수 있습니다.
상대 경로, `~`, 공백 및 한글 경로를 지원합니다. 셸에서는 공백이 있는 경로를
따옴표로 감싸세요. 실제 미디어 스트림을 `ffprobe`로 검사하므로 확장자와 무관하게
설치된 FFmpeg가 읽을 수 있는 영상·녹음을 지원합니다. 앨범 표지는 영상으로
분류하지 않으며, 오디오 스트림이 없는 영상은 오류로 처리합니다.

로컬 영상은 다운로드 없이 오디오 추출부터, 녹음은 16 kHz 모노 WAV 정규화부터
시작하여 전사·요약·Vault 발행을 수행합니다. `ffmpeg`와 `ffprobe`가 필요하며
둘 다 일반적인 FFmpeg 설치에 포함됩니다. 로컬 처리에는 `yt-dlp`가 필요하지 않습니다.

원본 파일은 현재 위치에 그대로 유지하며, 노트에 절대 경로를 기록합니다.
중간 산출물은 캐시에 저장합니다. 경로와 파일 내용으로 캐시를 구분하므로
같은 경로의 내용이 바뀌면 새로 처리합니다. `--force`는 캐시를 다시 처리하지만
원본이나 기존 Vault 노트를 덮어쓰지 않습니다. 기존 노트가 있으면 제목을 바꾸세요.


## 영상만 다운로드

전사·요약 없이 공개 HLS 영상을 MP4로 저장하려면 `--video-only`를 사용합니다.

```bash
uv run lecture-util run 'https://example.com/lecture/index.m3u8' \
  --course '공기역학특론' --date 2026-09-07 --title '첫 강의' --video-only
uv run lecture-util download 'https://example.com/lecture/index.m3u8' \
  --course '공기역학특론' --title '첫 강의' --video-only
```

`download`에서 날짜를 생략하면 오늘 날짜를 사용합니다. `run`은 날짜가 필수입니다.
URL만 전달하는 대화형 실행에서는 처리 모드에 `video`를 선택하고, TUI에서는
`Download video only`를 선택합니다. 기본값은 전사·요약·Vault 저장입니다.
로컬 파일 경로에서는 다운로드 전용 모드를 사용할 수 없습니다.

영상은 설정된 동영상 저장소의 과목·주차 폴더에 저장하며 완료 화면에서 경로를
확인할 수 있습니다. 오디오 추출·전사·요약·노트 발행은 수행하지 않고 다운로드
상태만 캐시에 기록합니다. 기존 Vault 노트가 있어도 다운로드할 수 있습니다.
기존 영상은 완료 기록이 일치하면 재사용하고, 그 외에는 `--force` 없이 덮어쓰지
않습니다. 옵션 없는 `download`는 기존처럼 오디오 추출까지 수행합니다.


다운로드 중에는 기존 진행 화면에 `Downloading · 3.2 MiB/s`처럼 속도가 표시됩니다.
속도는 최대 초당 한 번 갱신되며, 측정값이 아직 없으면 `속도 계산 중`으로 표시됩니다.
병합·후처리 중에는 속도 대신 `Finalizing downloaded video`가 표시됩니다.
전체 강의 처리와 `--video-only`에 자동 적용되며, 캐시 재사용 시에는 기존
`Cached` 표시를 유지합니다. 파일로 리디렉션한 로그에도 같은 갱신 주기를 적용합니다.

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

같은 주차 폴더에 `YYYY-MM-DD 제목 전사.md`를 만듭니다. `lecture-transcript` frontmatter와 본문의 원본 URL, 요약 노트 링크 및 구간별 타임스탬프가 포함됩니다. 생성되는 두 노트의 Properties에는 `source_url`과 `transcript`를 추가하지 않습니다.

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

같은 URL에 대해 다운로드가 완료되고 기록된 영상 경로와 SHA-256이 일치하면 재사용합니다. 완료 기록이 없는 같은 경로의 파일은 실수로 덮어쓰지 않으며, 명시적으로 `--force`를 지정해야 교체합니다.

### 사용자 캐시

전체 실행의 작업공간은 소스 식별자의 SHA-256 해시 앞 10자를 사용합니다. HLS는 URL, 로컬 파일은 해석된 절대 경로와 파일 내용 해시로 식별합니다.

```text
~/.cache/lecture-util/lecture-<소스 해시>/
├── audio.wav
├── transcript.json
├── transcript.md
├── transcript.srt
├── summary.md
├── run.json
├── request.json
└── publication.json
```

`run.json`에는 원본 URL, 과목, 날짜, 제목, 영상 및 Vault 발행 경로, 태그, 입력·출력 지문과 각 단계의 상태가 기록됩니다. `request.json`은 재개에 필요한 입력과 프롬프트 본문을 보관하고, `publication.json`은 게시할 두 노트의 내용·지문·상태를 보관합니다. 이 파일들은 캐시에만 저장되며 저장소에 커밋하지 않습니다. 캐시와 영상은 자동 삭제하지 않습니다.

### 기존 설정과 영상 옮기기

영상 경로가 없는 설정 v1은 새 실행에 사용할 수 없습니다. 먼저 `lecture-util onboard`를 실행해 기존 Vault, 학기 및 모델 설정을 불러온 뒤 영상 경로를 저장합니다. 기존 영상은 자동으로 이동하지 않습니다.

기존 영상은 기록된 다운로드 출력 경로와 현재 경로가 같을 때만 재사용합니다.
영상 파일을 직접 옮겨 출력 경로가 달라졌다면 새 경로의 파일을 자동으로 신뢰하지 않습니다.
원본을 별도로 보관한 뒤 `--force`로 다시 다운로드하거나 기존 저장 위치를 사용하세요.
입력 지문이 없는 이전 캐시는 오디오·전사·요약을 처음 한 번 다시 계산합니다.

## 캐시 재사용과 실패 복구

같은 URL을 다시 처리하면 URL 해시가 같으므로 기존 작업공간을 사용합니다.

- 완료 상태뿐 아니라 입력·출력 파일 지문과 전사 옵션을 비교합니다. 모델·언어·장치·정밀도·배치·beam 또는 실행 플랫폼이 바뀌면 전사를 다시 수행합니다.
- 상위 단계를 시작할 때 하위 완료 상태를 무효화합니다. 따라서 `download --force` 이후 일반 `transcribe`도 새 오디오를 처리합니다.
- 유효한 JSON 전사가 있고 Markdown/SRT만 없으면 누락된 파일을 복원합니다. 기존 Markdown 편집 내용은 보존되며, 요약 캐시는 편집된 내용을 기준으로 판단합니다.
- 손상된 상태와 전사 JSON은 오류로 보고합니다. `run.json`은 정상 백업에서 복구하고, 전사는 `transcribe --force`로 재생성할 수 있습니다.
- 같은 URL의 동시 실행은 작업공간 잠금으로 차단합니다. 게시에는 기존 파일을 교체하지 않는 배타적 생성과 강의 폴더 잠금을 사용합니다.
- 노트 한 개만 게시된 뒤 실패하면, 게시 기록과 기존 파일의 내용이 일치하는 경우에만 나머지를 복구합니다. 사용자가 수정했거나 이미 게시가 완료된 노트는 덮어쓰지 않습니다.

캐시를 재사용한 단계는 진행 화면에서 `↻`로 표시됩니다.

```text
↻ [1/5] Reusing video (96.8 MiB)
↻ [2/5] Reusing extracted audio (61.4 MiB)
↻ [3/5] Reusing 282 transcript segments (ko)
↻ [4/5] Reusing summary from /home/seawhan/.cache/lecture-util/lecture-0123456789/summary.md
```

기존 Vault 노트를 새 내용으로 교체하려면 도구 밖에서 기존 노트를 직접 이동하거나 이름을 바꾼 뒤 다시 실행해야 합니다. 노트가 남아 있는 동안에는 원본 보호를 위해 실행이 시작되지 않습니다.

### 재개 명령과 입력 복원

실행 오류에는 실패 단계, 캐시 위치와 다음 재개 명령이 표시됩니다.

```bash
uv run lecture-util resume "$HOME/.cache/lecture-util/lecture-0123456789"
```

위 경로는 예시이며 오류 화면에 표시된 실제 캐시 경로로 바꾸세요. 로컬 원본이 없어졌거나 내용이 바뀌면 기존 요청을 재개할 수 없으므로 현재 파일로 새 `run`을 실행해야 합니다.

재개는 저장된 모델·경로·강의 정보·프롬프트를 사용하며 현재 전역 설정을 다시 적용하지 않습니다.
완료된 단계는 재사용하고, 강제 재실행 옵션은 반복하지 않습니다. 실패한 강제 다운로드의 파일 교체 권한은 해당 시도의 상태에 기록되어 재시도에 적용됩니다.
기존 작업공간에 `request.json`이 없으면 원래 URL과 메타데이터로 `run`을 다시 실행해야 합니다.

TUI 실행 실패 후에는 `retry`, `edit`, `quit`을 선택합니다. `edit`은 입력과 프롬프트를 복원한 폼을 엽니다.
`Ctrl+C`는 재시도 없이 중단하며 종료 코드는 130입니다. TUI 제출 시 모델을 다운로드하지 않는 사전 검사를 비동기로 수행하고, 오류가 있으면 입력을 보존합니다.

전사 진행 화면에는 모델 준비와 추론 상태를 표시합니다. faster-whisper는 처리 위치 기반의 추정 진행률과 오디오 시간/실행 시간 배속을 보여줍니다. 무음을 건너뛸 수 있으므로 정확한 남은 시간 예측은 아닙니다. MLX는 공개 API가 제공하지 않는 구간별 진행률을 표시하지 않습니다.

## 단계별 명령

문제 진단이나 수동 복구가 필요할 때 작업 단계를 따로 실행할 수 있습니다. `run`, `transcribe`, `summarize`는 명시적 옵션 → 저장 설정 → 프로그램 기본값 순서로 값을 선택합니다. 단독 전사·요약은 설정이나 Vault가 없어도 사용할 수 있지만 잘못된 설정 파일은 오류로 보고합니다. 단계별 명령은 캐시만 변경하며 Vault 노트를 발행하지 않습니다.

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

`LECTURE_DIR/transcript.json`과 `transcript.md`를 읽어 `summary.md`를 만듭니다. 이 명령 역시 Obsidian 노트를 발행하지 않으므로 최종 Vault 발행이 필요하면 같은 소스와 강의 정보로 전체 `run` 명령을 실행하거나, 기록된 전체 실행이 있다면 `resume`을 사용하세요. 완료된 캐시 단계는 재사용됩니다.

## 전사 최적화와 비교 측정

기본 모델과 품질 설정은 유지됩니다. faster-whisper에서만 다음 옵션을 적용할 수 있습니다.

```bash
uv run lecture-util transcribe LECTURE_DIR \
  --device cuda --whisper-model large-v3 --language ko \
  --compute-type int8_float16 --batch-size 4 --beam-size 5
```

`--beam-size default`는 저장된 beam 설정을 백엔드 기본값으로 되돌립니다.
`--compute-type`은 `auto`, `float16`, `float32`, `int8`, `int8_float16` 중 선택하며 장치 지원 여부를 검사합니다.
배치 처리는 속도를 높일 수 있지만 메모리 사용과 전사 결과도 달라질 수 있습니다.
MLX는 `auto`, 배치 `0`, 기본 beam만 허용합니다. 명시적 장치 선택 화면에서 MLX를 선택하면 튜닝 필드를 기본값으로 되돌리고 비활성화합니다.
`large-v3` 메모리 부족 시 참조와 캐시를 정리한 뒤 `turbo`로 한 번 재시도하며, 다른 오류와 취소는 재시도하지 않습니다.

실제 강의로 기본값을 바꾸기 전에 다음 개발용 도구로 비교할 수 있습니다. 지정한 로컬 WAV만 읽으며 조합별로 별도 프로세스를 실행합니다.

```bash
uv run python -m lecture_util.benchmark \
  --audio /path/to/sample.wav --device cuda --language ko \
  --models large-v3 turbo --compute-types float16 int8_float16 \
  --batch-sizes 0 4 --beam-sizes 1 5 \
  --reference /path/to/reference.txt --terms /path/to/terms.txt \
  --output /tmp/lecture-benchmark.json
```

정답 텍스트와 용어 목록은 선택 사항이며, 용어 파일은 한 줄에 하나씩 작성합니다.
JSON과 CSV에는 준비·추론 시간, 전체 처리 시간/오디오 길이 비율(RTF), 프로세스 최대 RSS, 정규화 CER, 용어 누락률과 실패 이유를 기록합니다.
CER는 NFC 정규화·대소문자 통일 후 공백과 구두점을 제외해 계산합니다. 용어 누락은 같은 정규화 후 문자열 포함 여부로 판정하며 의미적 정확도 평가는 아닙니다.
MLX 준비·추론 시간은 분리할 수 없어 결합 시간만 기록합니다. RSS에는 전용 GPU 메모리가 포함되지 않으며 GPU 메모리 필드는 미측정으로 남습니다.
모델이 캐시에 없는 첫 실행은 다운로드 시간이 포함됩니다. 공정한 비교는 모델 준비 상태를 맞추고 같은 입력과 조합으로 반복 측정하세요.
결과에는 전사 본문을 저장하지 않습니다. 실제 모델 추론은 일반 테스트에서 실행하지 않습니다.

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
