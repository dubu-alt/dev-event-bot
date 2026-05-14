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