# DEV EVENT Discord Bot

[brave-people/Dev-Event](https://github.com/brave-people/Dev-Event)의 `README.md`에 등록된 개발자 행사 정보를 파싱해 Discord 채널로 알려주는 GitHub Actions 기반 자동화 봇입니다.

## 주요 기능

- Dev-Event README의 월별 행사 목록 자동 수집
- 인라인/멀티라인 Markdown 행사 형식 파싱
- Discord Webhook Embed 메시지 전송
- `events_cache.json` 기반 중복 알림 방지
- GitHub Actions 스케줄 실행 및 캐시 자동 커밋
- 네트워크 다운로드 실패 시 로컬 README 폴백 지원

## 알림 예시

Discord에는 아래와 같은 Embed 형태로 전송됩니다.

```text
제목: CloudBro 1주년 행사
URL: https://ticketa.co/event/dttikon7
설명: 분류: `오프라인(서울 강남구)`, `유료`, `모임`, `클라우드` | 주최: CloudBro | 접수: 04. 24(목) ~ 05. 12(화)
필드: 시기 = 26년 05월
푸터: Dev-Event Bot
```

## 프로젝트 구조

```text
dev-event-bot/
├── .github/
│   └── workflows/
│       └── dev-event-bot.yml   # GitHub Actions 자동 실행 워크플로
├── tests/
│   └── test_markdown_parser.py # MarkdownParser 단위 테스트
├── dev_event_bot.py            # 봇 메인 코드
├── events_cache.json           # 이미 전송한 행사 URL 캐시
├── requirements.txt            # Python 의존성
└── README.md
```

## 동작 방식

1. `dev_event_bot.py`가 Dev-Event README를 다운로드합니다.
   - 1차: jsDelivr CDN
   - 2차: GitHub Raw URL
   - 폴백: 로컬 `README.md`
2. `MarkdownParser`가 ``## `26년 05월` `` 같은 월별 섹션에서 행사 링크와 메타데이터를 추출합니다.
3. `events_cache.json`에 없는 신규 행사만 Discord Webhook으로 전송합니다.
4. 전송 성공한 행사 URL을 캐시에 저장합니다.
5. GitHub Actions가 변경된 캐시 파일을 현재 브랜치에 커밋/푸시합니다.

## 요구 사항

- Python 3.11 이상 권장
- Discord Webhook URL
- GitHub Actions 사용 시 `contents: write` 권한

Python 패키지는 `requirements.txt`로 관리합니다.

```text
requests>=2.31.0
```

## 설치 및 로컬 실행

### 1. 저장소 클론

```bash
git clone https://github.com/dubu-alt/dev-event-bot.git
cd dev-event-bot
```

> 저장소 URL이 다르다면 실제 사용 중인 GitHub 저장소 주소로 바꿔 주세요.

### 2. 가상환경 생성 및 의존성 설치

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Windows PowerShell에서는 다음처럼 활성화합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 3. 캐시 파일 준비

처음 실행하는 경우 `events_cache.json`을 빈 배열로 준비합니다.

```json
[]
```

### 4. Discord Webhook 환경 변수 설정

macOS/Linux:

```bash
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/xxxxx/xxxxx"
```

Windows PowerShell:

```powershell
$env:DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/xxxxx/xxxxx"
```

> Webhook URL은 민감 정보입니다. 코드, README, 캐시 파일에 직접 커밋하지 마세요.

### 5. 봇 실행

```bash
python dev_event_bot.py
```

## 테스트

Markdown 파서 단위 테스트를 실행하려면 다음 명령을 사용합니다.

```bash
python -m unittest discover -s tests
```

## GitHub Actions 설정

이 저장소는 `.github/workflows/dev-event-bot.yml` 워크플로를 사용합니다.

```yaml
name: Dev-Event Bot (Git Cache)

on:
  schedule:
    # 매일 09:00 UTC (18:00 KST)
    - cron: '0 0 * * *'
  workflow_dispatch:

permissions:
  contents: write
```

> 참고: 위 주석에는 `09:00 UTC`라고 적혀 있지만, cron 값 `0 0 * * *`는 실제로 매일 **00:00 UTC / 09:00 KST**에 실행됩니다.

### Discord Webhook Secret 등록

GitHub 저장소에서 아래 경로로 이동합니다.

```text
Settings → Secrets and variables → Actions → New repository secret
```

다음 Secret을 생성합니다.

| Name | Value |
| --- | --- |
| `DISCORD_WEBHOOK_URL` | Discord Webhook URL |

### Actions 권한 확인

GitHub 저장소에서 아래 설정을 확인합니다.

```text
Settings → Actions → General → Workflow permissions
```

- `Read and write permissions` 활성화
- 필요 시 `Allow GitHub Actions to create and approve pull requests`는 사용 정책에 맞게 선택

## 주요 파일 설명

### `dev_event_bot.py`

- `EventCache`: 전송된 행사 URL 로드/저장
- `MarkdownParser`: Dev-Event README Markdown에서 행사 정보 추출
- `DiscordSender`: Discord Webhook Embed 생성 및 전송
- `ReadmeDownloader`: README 다운로드 및 로컬 폴백 처리
- `DevEventBot`: 전체 실행 흐름 조합

### `events_cache.json`

이미 Discord로 전송한 행사 URL 목록입니다. GitHub Actions가 이 파일을 커밋해 다음 실행에서 중복 알림을 막습니다.

```json
[
  "https://example.com/event"
]
```

## 운영 팁

- Webhook URL이 없으면 봇은 Discord 전송에 실패하며 캐시도 신규 행사로 갱신되지 않습니다.
- Discord API 또는 네트워크 일시 오류에 대비해 서버 오류와 요청 예외는 최대 3회 재시도합니다.
- Dev-Event README 형식이 크게 바뀌면 `MarkdownParser`와 `tests/test_markdown_parser.py`를 함께 업데이트하세요.
- 스케줄을 바꾸려면 `.github/workflows/dev-event-bot.yml`의 `cron` 값을 수정하세요.

## 문제 해결

### `DISCORD_WEBHOOK_URL이 설정되지 않았습니다`

환경 변수 또는 GitHub Secret이 누락된 상태입니다. 로컬에서는 `export`/`$env:`로 설정하고, Actions에서는 Repository Secret을 확인하세요.

### 파싱된 이벤트가 없습니다

Dev-Event README의 Markdown 형식이 변경되었을 수 있습니다. `tests/test_markdown_parser.py`에 새 형식의 샘플을 추가한 뒤 `MarkdownParser` 정규식을 조정하세요.

### GitHub Actions가 캐시를 커밋하지 못합니다

`Workflow permissions`가 `Read and write permissions`인지 확인하세요. 보호 브랜치 정책이 있다면 Actions의 직접 push가 차단될 수 있습니다.
