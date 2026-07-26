# DEV EVENT Discord Bot

[brave-people/Dev-Event](https://github.com/brave-people/Dev-Event)의 `README.md`에 등록된 개발자 행사 정보를 파싱해 Discord 채널로 알려주는 GitHub Actions 기반 자동화 봇입니다.

## 주요 기능

### 수집·파싱

- Dev-Event README 자동 다운로드 (jsDelivr CDN → GitHub Raw → 로컬 파일 3단계 폴백)
- 월별 섹션(`## 26년 07월`)에서 행사 제목/링크/분류/주최/접수기간 추출
- 인라인·멀티라인 Markdown 행사 형식 모두 지원

### 중복 방지

- `events_cache.json` v2: 전송한 행사를 객체(제목/URL/월/메타데이터/전송일시)로 저장
- URL 정규화 판정: 추적 파라미터(utm 등)·fragment·끝 슬래시·www 차이를 무시하고 동일 행사로 인식
- 제목+월 병행 판정: 같은 행사가 URL만 바꿔 재등록돼도 중복 차단
- 구버전(v1) URL 목록 캐시 자동 마이그레이션 및 제목 정보 자동 백필

### 캐시 관리

- 현재 월 기준 3개월 지난 행사 자동 정리 (`RETENTION_MONTHS`로 조정 가능)
- 손상된 캐시 파일 자동 복구
- GitHub Actions Artifact가 아닌 Git 추적 파일로 캐시 유지 (실행 후 자동 커밋)

### 전송

- 컴팩트 다이제스트(기본): 새 행사 여러 건을 임베드 1개 안의 목록으로 압축해 전송 (메시지당 최대 20건)
- 리치 다이제스트(`DIGEST_STYLE=rich`): 행사 1건당 임베드 1개인 구버전 표현 (메시지당 최대 10개)
- 분류별 색상/말머리: 대회·해커톤=빨강 🔴, 세미나·컨퍼런스=초록 🟢, 교육·부트캠프=주황 🟠, 모임·동아리=파랑 🔵, 기타=⚪
- 행사별 한 줄 요약: 일시(없으면 접수 기간, 그것도 없으면 시기) · 분류 태그 · 주최
- 웹훅 여러 개 동시 지원 (`DISCORD_WEBHOOK_URL`, `DISCORD_SUMOKJANG_WEBHOOK`)
- 서버·네트워크 오류 시 최대 3회 재시도, 전송 실패한 묶음은 캐시 미기록으로 재전송 보장

### 마감 행사 자동 정리

- 접수 마감일이 지난 행사를 이미 보낸 메시지에서 자동 제거 (마감일 다음 날부터)
- 메시지에 유효한 행사가 남아 있으면 그 행사들만으로 메시지 수정, 전부 마감이면 메시지 삭제
- 봇 계정 없이 웹훅 API(`PATCH`/`DELETE /webhooks/{id}/{token}/messages/{message_id}`)만 사용
- 마감일을 판별할 수 없는 행사는 안전하게 그대로 유지

### 운영·테스트

- GitHub Actions 매일 09:00 KST 자동 실행 (수동 실행 지원)
- `DRY_RUN=1` 모드: 전송·캐시 변경 없이 로컬 검증
- 단위 테스트 51개 (Markdown 파서 / 캐시·정규화·정리 / Discord 전송·다이제스트 표현)

## 알림 예시

새 행사가 있으면 `📅 새 개발자 행사 N건` 메시지 1개에 전체 목록이 임베드 1개로 묶여 전송됩니다. 제목을 클릭하면 행사 페이지로 이동합니다.
<p align="center">
  <img width="600" alt="알림 예시 스크린샷" src="https://github.com/user-attachments/assets/b629c745-d6e6-4a46-a5b8-106c2a442bda">
</p>

```text
📅 새 개발자 행사 3건
┌ (분류 대표 색상)
│ 🔵 7월 바이브 코드 러시
│ 　 7. 25(토) 14:00 - 18:00 · 온라인 · 무료 · 모임 · AI · 바이브 코딩 클럽
│ 🔵 2026 AI SPARK in Yonsei
│ 　 8. 01(토) · 오프라인(서울 강남구) · 무료 · 모임 · AI · CREAI+IT
│ 🔴 [Google Cloud X Solana] AI 에이전틱 해커톤
│ 　 접수 7. 17(금) ~ 08. 03(월) · 온라인 · 오프라인(서울 강남구) · 무료 · 대회 · AI · 슈퍼팀 코리아
└ Dev-Event Bot
```
행사 1건당 임베드 1개였던 기존 표현이 필요하면 `DIGEST_STYLE=rich`로 되돌릴 수 있습니다.

## 프로젝트 구조

```text
dev-event-bot/
├── .github/
│   └── workflows/
│       └── dev-event-bot.yml   # GitHub Actions 자동 실행 워크플로
├── tests/
│   ├── test_markdown_parser.py # MarkdownParser 단위 테스트
│   ├── test_event_cache.py     # EventCache/정규화/정리 단위 테스트
│   ├── test_discord_sender.py  # 임베드 생성/색상/다이제스트 단위 테스트
│   └── test_expiry_cleanup.py  # 마감일 파싱/만료 판정/메시지 수정·삭제 테스트
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
3. `events_cache.json`에 없는 신규 행사만 다이제스트로 묶어 Discord Webhook으로 전송합니다.
   - 중복 판정: 정규화된 URL(추적 파라미터·fragment·끝 슬래시 제거) 또는 정규화된 제목+월이 일치하면 중복으로 처리합니다. 같은 행사가 URL만 바꿔 재등록돼도 다시 알리지 않습니다.
   - 다이제스트: 기본(compact)은 메시지 1개당 행사 최대 20건을 임베드 1개 목록으로, `rich`는 메시지 1개당 임베드 최대 10개로 묶습니다. 한도를 넘으면 여러 메시지로 자동 분할합니다.
4. 전송 성공한 행사를 객체(제목/URL/월/메타데이터/전송일시/마감일/메시지 ID)로 캐시에 저장합니다.
   - 전송 시 `?wait=true`로 메시지 ID를 받아두어야 이후 수정·삭제가 가능합니다.
5. 접수 마감일이 지난 행사를 기존 메시지에서 제거합니다. 남은 행사가 있으면 메시지 수정(PATCH), 전부 마감이면 메시지 삭제(DELETE)입니다.
6. 현재 월 기준 3개월 이전 행사는 캐시에서 자동 정리합니다. (마감 정리 후에도 중복 방지를 위해 캐시 기록 자체는 유지됩니다)
7. GitHub Actions가 변경된 캐시 파일을 현재 브랜치에 커밋/푸시합니다.

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

macOS·Linux에서는 `python`이 아니라 `python3`로 가상환경을 만듭니다. macOS에는 `python` 명령이 없어 `command not found: python`이 납니다.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

> 가상환경을 활성화하면 그 셸에서는 `python`이 `.venv/bin/python`을 가리키므로, 이후 명령은 `python`으로 실행합니다. 활성화하지 않은 채 시스템 `python3`로 실행하면 `requests`가 없어 `ModuleNotFoundError`가 납니다.

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

알림이 너무 길게 느껴지지 않도록 기본 표현은 컴팩트 다이제스트입니다. 행사 1건당 임베드 1개였던 기존 표현으로 되돌리려면 `DIGEST_STYLE=rich`를 설정합니다.

```bash
DIGEST_STYLE=rich python dev_event_bot.py
```

## 테스트

Markdown 파서·캐시(마이그레이션/중복 판정/정리)·Discord 전송 단위 테스트를 실행합니다. **가상환경을 활성화한 뒤** 실행하세요.

```bash
source .venv/bin/activate && python -m unittest discover -s tests
```

활성화가 번거로우면 가상환경 인터프리터를 직접 지정해도 됩니다.

```bash
.venv/bin/python -m unittest discover -s tests
```

`-s tests`에 `-t .`를 함께 주면 `Start directory is not importable` 오류가 납니다. `tests/`에 `__init__.py`가 없으므로 위 형태 그대로 쓰세요.

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
- `DiscordSender`: 컴팩트 목록 임베드(기본) 또는 분류별 색상·구조화 필드 임베드(`rich`) 생성 및 전송
- `chunk_events` / `get_digest_style`: 다이제스트 스타일 결정과 메시지 단위 분할
- `ReadmeDownloader`: README 다운로드 및 로컬 폴백 처리
- `DevEventBot`: 전체 실행 흐름 조합

### `events_cache.json`

이미 Discord로 전송한 행사 목록입니다. GitHub Actions가 이 파일을 커밋해 다음 실행에서 중복 알림을 막습니다.

v3 형식 (현재):

```json
{
  "version": 3,
  "updated_at": "2026-07-26T09:00:00",
  "events": [
    {
      "title": "행사명",
      "url": "https://example.com/event",
      "month": "26년 07월",
      "metadata": ["분류: `온라인`, `무료`", "접수: 07. 01(수) ~ 07. 20(월)"],
      "sent_at": "2026-07-26T09:00:00",
      "deadline": "2026-07-20",
      "messages": [
        { "webhook": "DISCORD_WEBHOOK_URL", "id": "1398...", "style": "compact" }
      ]
    }
  ]
}
```

- `deadline`: 접수 마감일(없으면 행사 종료일). 판별 불가 시 `null`이며 자동 정리 대상에서 제외됩니다.
- `messages`: 이 행사가 실린 Discord 메시지 참조. 마감 정리 후에는 비워지지만, 중복 방지를 위해 행사 기록 자체는 남습니다.

구버전(v1 URL 배열, v2 객체) 형식도 로드 시 자동 마이그레이션됩니다. v1 마이그레이션 항목은 제목이 없으므로, 이후 실행에서 URL이 일치하면 제목/월을 자동 백필합니다. v2에서 넘어온 항목은 `messages`가 없어 기존 메시지를 정리할 수 없고, 새로 보내는 행사부터 정리 대상이 됩니다.

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
