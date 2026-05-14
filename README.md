# DEV EVENT Discord Bot
행사 정보를 자동으로 수집하여 Discord 채널로 전송하는 GitHub Actions 기반 자동화 봇입니다.

## 기능

- DEV EVENT 행사 자동 크롤링
- Discord Webhook 자동 전송
- GitHub Actions 기반 서버리스 자동 실행
- 중복 행사 자동 필터링
- 5분 단위 자동 모니터링
- 무료 운영 가능

## 봇 알림 미리보기

```
새로운 개발 행사

AI Conference 2026
https://dev-event.vercel.app/events/xxx
```

## 전체적 구조

```
discord-dev-event-bot/
├── bot.py
├── requirements.txt
├── events_cache.json
└── .github/
    └── workflows/
        └── bot.yml
```

## Installation

### 1. Clone Repository

```bash
git clone https://github.com/dubu-alt/discord-dev-event-bot
cd discord-dev-event-bot
```

### 2. 패키지 설치

```bash
pip install -r requirements.txt
```

## Requirements 생성

**requirements.txt**

```
requests
beautifulsoup4
```

## Initial Setup

### events_cache.json

처음에는 빈 배열로 생성합니다.

```json
[]
```

### Discord Webhook Setup

1. Discord 채널 설정 진입
2. 연동 → 웹후크(Webhooks)
3. 새 웹후크 생성
4. 웹후크 URL 복사

**예시:**
```
https://discord.com/api/webhooks/xxxxx/xxxxx
```

**주의:** 웹훅 URL을 절대 코드에 직접 넣지 마세요. GitHub Secrets에 저장해야 합니다.

### GitHub Secrets Setup

GitHub Repository에서:

```
Settings → Secrets and variables → Actions → New repository secret
```

**생성할 시크릿:**

| Name | Value |
|------|-------|
| DISCORD_WEBHOOK_URL | 디스코드 웹훅 URL |

### GitHub Actions Permission Setup

GitHub Repository에서:

```
Settings → Actions → General
```

다음 설정 활성화:

```
Workflow permissions
☑ Read and write permissions
```

## GitHub Actions Workflow

파일 위치: `.github/workflows/bot.yml`

```yaml
name: DEV EVENT BOT

on:
  schedule:
    - cron: '*/5 * * * *'
  workflow_dispatch:

permissions:
  contents: write

concurrency:
  group: dev-event-bot
  cancel-in-progress: true

jobs:
  run-bot:
    runs-on: ubuntu-latest

    env:
      FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: true

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Python Setup
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install Packages
        run: |
          pip install -r requirements.txt

      - name: Run Bot
        env:
          DISCORD_WEBHOOK_URL: ${{ secrets.DISCORD_WEBHOOK_URL }}
        run: |
          python bot.py

      - name: Commit cache
        run: |
          git config --global user.name "github-actions"
          git config --global user.email "github-actions@github.com"
          git add events_cache.json
          git diff --cached --quiet && exit 0
          git commit -m "update cache"
          git push --force-with-lease
```

## Bot Code (bot.py)

```python
import requests
from bs4 import BeautifulSoup
import os
import json
from datetime import datetime

WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]
BASE_URL = "https://dev-event.vercel.app/events"
CACHE_FILE = "events_cache.json"

if os.path.exists(CACHE_FILE):
    with open(CACHE_FILE, "r", encoding="utf-8") as f:
        sent_events = json.load(f)
else:
    sent_events = []

headers = {
    "User-Agent": "Mozilla/5.0"
}

response = requests.get(BASE_URL, headers=headers)

if response.status_code != 200:
    print("사이트 요청 실패")
    exit()

soup = BeautifulSoup(response.text, "html.parser")
cards = soup.select("a")

for card in cards:
    text = card.get_text(strip=True)
    href = card.get("href")

    if not href:
        continue

    if "/events/" not in href:
        continue

    full_link = f"https://dev-event.vercel.app{href}"

    if full_link in sent_events:
        continue

    embed = {
        "title": text[:256],
        "url": full_link,
        "description": "새로운 개발 행사",
        "color": 5814783,
        "footer": {
            "text": "DEV EVENT BOT"
        },
        "timestamp": datetime.utcnow().isoformat()
    }

    payload = {
        "embeds": [embed]
    }

    requests.post(WEBHOOK_URL, json=payload)
    sent_events.append(full_link)

with open(CACHE_FILE, "w", encoding="utf-8") as f:
    json.dump(sent_events, f, ensure_ascii=False, indent=2)

print("완료")
```

## 로컬에서 실행

```bash
python bot.py
```

## Git 연동 방법

```bash
git init
git add .
git commit -m "init"
git branch -M main
git remote add origin https://github.com/dubu-alt/discord-dev-event-bot
git push -u origin main
```

## 문제점 해결 방법

### GitHub Actions Push Error (403)

403 오류 발생 시:

```
Settings → Actions → General → Workflow permissions → Read and write permissions 활성화
```

### Webhook Error

**확인 사항:**

- GitHub Secret 이름이 정확히 `DISCORD_WEBHOOK_URL`인지 확인
- 코드에 웹훅 URL 직접 입력하지 않았는지 확인

**정상 코드:**

```python
WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]
```

## Future Improvements

- AI 행사만 필터링
- 날짜 파싱
- 마감 임박 알림
- 행사 이미지 임베드
- 카테고리별 채널 분리
- SQLite 저장
- Discord Slash Commands
- Redis 캐시
- Docker 배포
