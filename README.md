# DEV EVENT Discord Bot

[brave-people/Dev-Event](https://github.com/brave-people/Dev-Event)의 `README.md`에 등록된 개발자 행사 정보를 파싱해 Discord 채널로 알려주는 GitHub Actions 기반 자동화 봇입니다.

## 주요 기능

- Dev-Event README의 월별 행사 목록 자동 수집
- 인라인/멀티라인 Markdown 행사 형식 파싱
- Discord Webhook Embed 메시지 전송
- `events_cache.json` 기반 중복 알림 방지 (URL 정규화 + 제목/월 병행 판정)
- 오래된 캐시 항목 자동 정리 (기본 3개월 보관)
- `DRY_RUN=1` 모드로 전송/캐시 변경 없이 로컬 검증
- GitHub Actions 스케줄 실행 및 캐시 자동 커밋
- GitHub Actions Artifact가 아닌 Git 추적 파일로 캐시 유지
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
│   ├── test_markdown_parser.py # MarkdownParser 단위 테스트
│   └── test_event_cache.py     # EventCache/정규화/정리 단위 테스트
├── dev_event_bot.py            # 봇 메인 코드
├── events_cache.json           # 이미 전송한 행사 캐시 (v2 객체 형식)
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
   - 중복 판정: 정규화된 URL(추적 파라미터·fragment·끝 슬래시 제거) 또는 정규화된 제목+월이 일치하면 중복으로 처리합니다. 같은 행사가 URL만 바꿔 재등록돼도 다시 알리지 않습니다.
4. 전송 성공한 행사를 객체(제목/URL/월/메타데이터/전송일시)로 캐시에 저장합니다.
5. 현재 월 기준 3개월 이전 행사는 캐시에서 자동 정리합니다.
6. GitHub Actions가 변경된 캐시 파일을 현재 브랜치에 커밋/푸시합니다.

> 캐시는 `actions/download-artifact`로 내려받지 않습니다. Artifact는 실행 간 영속 저장소가 아니므로, 첫 실행이나 업로드가 생략된 실행에서 `Artifact not found for name: events_cache` 오류가 날 수 있습니다.

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

저장소에 커밋된 `events_cache.json`을 그대로 사용하면 됩니다. 완전히 새로 시작하려면 빈 배열로 초기화합니다 (첫 실행 시 자동으로 v2 형식으로 변환).

```json
[]
```

> 캐시를 비운 상태로 실행하면 README의 모든 행사가 신규로 판정되어 한꺼번에 전송됩니다. 알림 없이 현재 행사를 캐시에 기록해두려면 아래 시딩 스크립트를 사용하세요.

```bash
python - <<'PY'
from dev_event_bot import EventCache, MarkdownParser, ReadmeDownloader
cache = EventCache()
for e in MarkdownParser.parse_events(ReadmeDownloader.fetch()):
    cache.enrich(e) if cache.is_sent(e) else cache.mark_sent(e)
cache.prune()
cache.save()
PY
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

Webhook 없이 파싱/중복 판정만 검증하려면 DRY RUN 모드를 사용합니다. 전송과 캐시 파일 변경이 모두 생략됩니다.

```bash
DRY_RUN=1 python dev_event_bot.py
```

## 테스트

Markdown 파서와 캐시(마이그레이션/중복 판정/정리) 단위 테스트를 실행합니다.

```bash
python -m unittest discover -s tests
```

실제 README를 대상으로 전송 없이 동작을 확인하려면 DRY RUN을 사용합니다.

```bash
DRY_RUN=1 python dev_event_bot.py
```

## GitHub Actions 설정

이 저장소는 `.github/workflows/dev-event-bot.yml` 워크플로를 사용합니다. 캐시는 `events_cache.json`을 Git에 커밋하는 방식으로 유지하므로, `events_cache` Artifact 다운로드 단계가 필요하지 않습니다.

```yaml
name: Dev-Event Bot (Git-backed Cache)

on:
  schedule:
    # 매일 09:00 UTC (18:00 KST)
    - cron: '0 0 * * *'
  workflow_dispatch:

permissions:
  contents: write

concurrency:
  group: dev-event-bot-${{ github.ref }}
  cancel-in-progress: false
```

> 참고: 위 주석에는 `09:00 UTC`라고 적혀 있지만, cron 값 `0 0 * * *`는 실제로 매일 **00:00 UTC / 09:00 KST**에 실행됩니다.


### `Artifact not found for name: events_cache` 오류 해결

이 오류는 워크플로에서 `actions/download-artifact`로 `events_cache`를 내려받으려 할 때, 해당 실행에 업로드된 Artifact가 없어서 발생합니다. 이 프로젝트는 실행 간 캐시를 Artifact가 아니라 Git에 커밋된 `events_cache.json`으로 유지합니다.

해결 방법:

1. `.github/workflows/dev-event-bot.yml`에 `actions/download-artifact` 또는 `actions/upload-artifact` 기반 캐시 단계가 남아 있다면 제거합니다.
2. `events_cache.json` 파일을 저장소에 커밋된 상태로 유지합니다.
3. 워크플로의 `Initialize git-backed cache` 단계가 파일 누락 또는 JSON 손상을 자동으로 `[]`로 복구하도록 둡니다.
4. `contents: write` 권한을 켜서 `Commit and push git-backed cache` 단계가 갱신된 캐시를 푸시할 수 있게 합니다.

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

- `EventCache`: 전송된 행사 객체 로드/저장, v1→v2 마이그레이션, 중복 판정, 오래된 항목 정리
- `normalize_url` / `normalize_title`: 중복 판정용 URL·제목 정규화
- `MarkdownParser`: Dev-Event README Markdown에서 행사 정보 추출
- `DiscordSender`: Discord Webhook Embed 생성 및 전송
- `ReadmeDownloader`: README 다운로드 및 로컬 폴백 처리
- `DevEventBot`: 전체 실행 흐름 조합

### `events_cache.json`

이미 Discord로 전송한 행사 목록입니다. GitHub Actions가 이 파일을 커밋해 다음 실행에서 중복 알림을 막습니다.

v2 형식 (현재):

```json
{
  "version": 2,
  "updated_at": "2026-07-19T09:00:00",
  "events": [
    {
      "title": "행사명",
      "url": "https://example.com/event",
      "month": "26년 07월",
      "metadata": ["분류: `온라인`, `무료`"],
      "sent_at": "2026-07-19T09:00:00"
    }
  ]
}
```

구버전(v1) URL 문자열 배열 형식도 로드 시 자동으로 v2로 마이그레이션됩니다. 마이그레이션된 항목은 제목 정보가 없으므로, 이후 실행에서 README와 URL이 일치하면 제목/월을 자동 백필합니다.

## 운영 팁

- Webhook URL이 없으면 봇은 Discord 전송에 실패하며 캐시도 신규 행사로 갱신되지 않습니다.
- Discord API 또는 네트워크 일시 오류에 대비해 서버 오류와 요청 예외는 최대 3회 재시도합니다.
- Dev-Event README 형식이 크게 바뀌면 `MarkdownParser`와 `tests/test_markdown_parser.py`를 함께 업데이트하세요.
- 스케줄을 바꾸려면 `.github/workflows/dev-event-bot.yml`의 `cron` 값을 수정하세요.
- 캐시 보관 기간을 바꾸려면 `dev_event_bot.py`의 `RETENTION_MONTHS`(기본 3개월)를 수정하세요.
- Actions 실행 로그의 `Commit and push git-backed cache` 단계가 실패하면 캐시가 갱신되지 않아 다음 실행에서 같은 행사가 다시 전송될 수 있습니다. 주기적으로 확인하세요.

## 문제 해결

### `DISCORD_WEBHOOK_URL이 설정되지 않았습니다`

환경 변수 또는 GitHub Secret이 누락된 상태입니다. 로컬에서는 `export`/`$env:`로 설정하고, Actions에서는 Repository Secret을 확인하세요.

### 파싱된 이벤트가 없습니다

Dev-Event README의 Markdown 형식이 변경되었을 수 있습니다. `tests/test_markdown_parser.py`에 새 형식의 샘플을 추가한 뒤 `MarkdownParser` 정규식을 조정하세요.

### GitHub Actions가 캐시를 커밋하지 못합니다

`Workflow permissions`가 `Read and write permissions`인지 확인하세요. 보호 브랜치 정책이 있다면 Actions의 직접 push가 차단될 수 있습니다.
