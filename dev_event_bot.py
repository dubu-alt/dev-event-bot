"""
brave-people/Dev-Event 레포 파싱 Discord 봇
GitHub README.md에서 개발자 행사 정보를 추출하고 Discord로 전송
"""

import requests
import json
import os
import re
from datetime import datetime, timedelta
from typing import List, Dict, Tuple, Optional
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
import logging

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 설정
WEBHOOK_ENV_NAMES = [
    "DISCORD_WEBHOOK_URL",
    "DISCORD_SUMOKJANG_WEBHOOK",
]
CACHE_FILE = "events_cache.json"
CACHE_VERSION = 2
MAX_RETRIES = 3
DISCORD_SUCCESS_CODE = 204

# 캐시 정리 정책
RETENTION_MONTHS = 3          # 현재 월 기준 N개월 이전 행사는 캐시에서 정리
MIGRATED_RETENTION_DAYS = 180  # 월 정보가 없는(구버전 마이그레이션) 항목의 보관 일수

# URL 정규화 시 제거할 추적용 쿼리 파라미터
TRACKING_PARAM_PREFIXES = ("utm_",)
TRACKING_PARAM_NAMES = {"fbclid", "gclid", "igshid"}

# README 다운로드 옵션
README_SOURCES = [
    "https://cdn.jsdelivr.net/gh/brave-people/Dev-Event@master/README.md",
    "https://raw.githubusercontent.com/brave-people/Dev-Event/master/README.md",
]

# 색상
COLOR_INFO = 3447003       # 파랑 (기본)
COLOR_SUCCESS = 3066993
COLOR_WARNING = 15158332

# 분류별 임베드 색상 (분류 텍스트에 키워드가 포함되면 적용, 위에서부터 우선)
CATEGORY_COLORS = [
    (("대회", "해커톤"), 15158332),   # 빨강
    (("세미나", "컨퍼런스"), 3066993),  # 초록
    (("교육", "부트캠프"), 15105570),  # 주황
    (("모임", "동아리"), 3447003),     # 파랑
]

# 다이제스트 모드: 메시지 1개당 임베드 최대 개수 (Discord 제한 10)
MAX_EMBEDS_PER_MESSAGE = 10


def get_webhooks() -> List[Tuple[str, str]]:
    """환경 변수에서 설정된 Discord Webhook 목록을 가져온다."""
    webhooks = []
    for env_name in WEBHOOK_ENV_NAMES:
        webhook_url = os.environ.get(env_name, "").strip()
        if webhook_url:
            webhooks.append((env_name, webhook_url))
    return webhooks


def normalize_url(url: str) -> str:
    """중복 판정을 위한 URL 정규화 (스킴/호스트 소문자, 추적 파라미터·fragment·끝 슬래시 제거)"""
    if not url:
        return ""
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return url.strip().lower()

    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]

    query_pairs = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith(TRACKING_PARAM_PREFIXES)
        and key.lower() not in TRACKING_PARAM_NAMES
    ]

    path = parsed.path.rstrip("/")
    return urlunparse((
        parsed.scheme.lower(),
        netloc,
        path,
        parsed.params,
        urlencode(query_pairs),
        "",  # fragment 제거
    ))


def normalize_title(title: str) -> str:
    """중복 판정을 위한 제목 정규화 (소문자화, 공백 정리)"""
    return re.sub(r"\s+", " ", (title or "")).strip().lower()


def parse_month(month: str) -> Optional[Tuple[int, int]]:
    """'26년 05월' → (2026, 5). 파싱 불가 시 None"""
    match = re.search(r"(\d{2,4})년\s*(\d{1,2})월", month or "")
    if not match:
        return None
    year = int(match.group(1))
    if year < 100:
        year += 2000
    return year, int(match.group(2))


class EventCache:
    """이벤트 캐시 관리 (v2: 이벤트 객체 저장, v1 URL 목록 자동 마이그레이션)"""

    def __init__(self, cache_file: str = CACHE_FILE, now: Optional[datetime] = None):
        self.cache_file = cache_file
        self.now = now or datetime.now()
        self.events = self._load()
        self._url_keys = {
            normalize_url(e["url"]) for e in self.events if e.get("url")
        }
        self._title_keys = {
            (normalize_title(e["title"]), e.get("month", ""))
            for e in self.events
            if e.get("title")
        }

    def _load(self) -> List[Dict]:
        """캐시 파일 로드 (v1 목록/v2 객체 형식 모두 지원)"""
        if not os.path.exists(self.cache_file):
            return []
        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"캐시 파일 손상: {e}, 초기화")
            return []

        # v1: URL 문자열 배열 → v2 객체로 마이그레이션
        if isinstance(data, list):
            migrated = [
                {
                    "title": "",
                    "url": url,
                    "month": "",
                    "metadata": [],
                    "sent_at": self.now.isoformat(),
                    "migrated": True,
                }
                for url in data
                if isinstance(url, str)
            ]
            logger.info(f"v1 캐시 마이그레이션: {len(migrated)}개 이벤트")
            return migrated

        # v2: {"version": 2, "events": [...]}
        if isinstance(data, dict) and isinstance(data.get("events"), list):
            events = [e for e in data["events"] if isinstance(e, dict) and e.get("url")]
            logger.info(f"캐시 로드 완료: {len(events)}개 이벤트")
            return events

        logger.warning("알 수 없는 캐시 형식, 초기화")
        return []

    def save(self) -> None:
        """캐시 파일 저장"""
        payload = {
            "version": CACHE_VERSION,
            "updated_at": self.now.isoformat(),
            "events": self.events,
        }
        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            logger.info(f"캐시 저장 완료: {len(self.events)}개 이벤트")
        except IOError as e:
            logger.error(f"캐시 저장 실패: {e}")

    def is_sent(self, event: Dict) -> bool:
        """이미 전송된 이벤트인지 확인 (정규화 URL 또는 제목+월 일치 시 중복)"""
        if normalize_url(event.get("url", "")) in self._url_keys:
            return True
        title_key = (normalize_title(event.get("title", "")), event.get("month", ""))
        return bool(title_key[0]) and title_key in self._title_keys

    def mark_sent(self, event: Dict) -> None:
        """이벤트를 전송됨으로 표시"""
        if self.is_sent(event):
            return
        self.events.append({
            "title": event.get("title", ""),
            "url": event.get("url", ""),
            "month": event.get("month", ""),
            "metadata": event.get("metadata", []),
            "sent_at": self.now.isoformat(),
        })
        self._url_keys.add(normalize_url(event.get("url", "")))
        if event.get("title"):
            self._title_keys.add(
                (normalize_title(event["title"]), event.get("month", ""))
            )

    def enrich(self, event: Dict) -> bool:
        """URL이 일치하는 캐시 항목에 제목/월/메타데이터가 비어 있으면 백필.
        v1 마이그레이션 항목도 이후 제목 기반 중복 판정이 가능해진다."""
        url_key = normalize_url(event.get("url", ""))
        if not url_key or not event.get("title"):
            return False
        for cached in self.events:
            if normalize_url(cached.get("url", "")) == url_key and not cached.get("title"):
                cached["title"] = event["title"]
                cached["month"] = event.get("month", "")
                cached["metadata"] = event.get("metadata", [])
                self._title_keys.add(
                    (normalize_title(event["title"]), event.get("month", ""))
                )
                return True
        return False

    def prune(self) -> int:
        """오래된 캐시 항목 정리. 제거된 개수 반환"""
        cutoff_index = (
            self.now.year * 12 + (self.now.month - 1) - RETENTION_MONTHS
        )
        migrated_cutoff = self.now - timedelta(days=MIGRATED_RETENTION_DAYS)

        kept = []
        for event in self.events:
            month = parse_month(event.get("month", ""))
            if month:
                if month[0] * 12 + (month[1] - 1) >= cutoff_index:
                    kept.append(event)
                continue
            # 월 정보가 없으면 sent_at 기준으로 보관
            try:
                sent_at = datetime.fromisoformat(event.get("sent_at", ""))
                if sent_at >= migrated_cutoff:
                    kept.append(event)
            except ValueError:
                kept.append(event)  # 판단 불가 항목은 안전하게 보관

        removed = len(self.events) - len(kept)
        if removed:
            logger.info(f"오래된 캐시 정리: {removed}개 제거")
            self.events = kept
        return removed


class MarkdownParser:
    """마크다운 형식 README.md 파서"""

    MONTH_PATTERN = re.compile(r'##\s+`?(\d{1,2}년\s+\d{1,2}월)`?')
    EVENT_LINK_PATTERN = re.compile(
        r'(?:^|(?<=\s))[-*]?\s*(?:\*\*|__)?\[(?P<title>.*?)\]'
        r'\((?P<url>https?://.*?)\)(?:\*\*|__)?'
        r'(?=\s*(?:[-+*]\s+(?:분류|주최|접수|일시)\s*:|$))',
        re.MULTILINE | re.DOTALL,
    )
    METADATA_PATTERN = re.compile(r'(?:^|\s)[-+*]\s+(?=(?:분류|주최|접수|일시)\s*:)')

    @classmethod
    def parse_events(cls, content: str) -> List[Dict]:
        """
        README.md에서 이벤트 정보 추출

        지원 형식:
        - 기존 여러 줄 목록 형식
          * **[이벤트명](링크)**
            + 분류: `온라인`, `무료`, `모임`
            + 주최: 기관명
            + 접수: 03. 01(월) ~ 03. 31(일)
        - 현재 Dev-Event README 인라인 형식
          ## `26년 05월` - __[이벤트명](링크)__ - 분류: ... - 주최: ...
        """
        events = []
        month_matches = list(cls.MONTH_PATTERN.finditer(content))

        for index, month_match in enumerate(month_matches):
            current_month = month_match.group(1)
            section_start = month_match.end()
            section_end = (
                month_matches[index + 1].start()
                if index + 1 < len(month_matches)
                else len(content)
            )
            section = content[section_start:section_end]

            # 지난 행사 기록 이후의 연도별 링크는 행사 목록이 아니므로 제외한다.
            if '## 지난 행사 기록' in content[month_match.start():month_match.end() + len(section)]:
                section = section.split('## 지난 행사 기록', 1)[0]

            event_matches = list(cls.EVENT_LINK_PATTERN.finditer(section))
            for event_index, event_match in enumerate(event_matches):
                metadata_start = event_match.end()
                metadata_end = (
                    event_matches[event_index + 1].start()
                    if event_index + 1 < len(event_matches)
                    else len(section)
                )
                metadata_text = section[metadata_start:metadata_end]

                events.append({
                    'title': cls._normalize_text(event_match.group('title')),
                    'url': event_match.group('url').strip(),
                    'month': current_month,
                    'metadata': cls._parse_metadata(metadata_text),
                })

        return events

    @staticmethod
    def _normalize_text(value: str) -> str:
        """줄바꿈으로 분리된 제목/메타데이터 조각을 한 줄 텍스트로 정리"""
        return re.sub(r'\s+', ' ', value).strip()

    @classmethod
    def _parse_metadata(cls, metadata_text: str) -> List[str]:
        """인라인/여러 줄 메타데이터를 Discord에 넣기 좋은 목록으로 정리"""
        normalized = cls._normalize_text(metadata_text)
        normalized = re.sub(r'^(?:[-+*]\s*)+', '', normalized).strip()
        if not normalized:
            return []

        parts = [
            part.strip(' -')
            for part in cls.METADATA_PATTERN.split(normalized)
            if part.strip(' -')
        ]

        return parts


class DiscordSender:
    """Discord 웹훅 전송"""
    
    def __init__(self, webhook_url: str, webhook_name: str, max_retries: int = MAX_RETRIES):
        self.webhook_url = webhook_url
        self.webhook_name = webhook_name
        self.max_retries = max_retries
    
    def send_event(self, event: Dict) -> bool:
        """이벤트 1건을 Discord로 전송"""
        if not self.webhook_url:
            logger.error(f"{self.webhook_name}이 설정되지 않았습니다")
            return False

        payload = {"embeds": [self._create_embed(event)]}
        success = self._post_webhook(payload)
        if success:
            logger.info(f"✓ Discord 전송 성공 ({self.webhook_name}): {event['title'][:50]}")
        return success

    def send_digest(self, events: List[Dict]) -> List[bool]:
        """이벤트 여러 건을 메시지당 최대 10개 임베드로 묶어 전송.
        이벤트별 성공 여부 리스트를 반환한다."""
        if not self.webhook_url:
            logger.error(f"{self.webhook_name}이 설정되지 않았습니다")
            return [False] * len(events)

        results: List[bool] = []
        chunks = [
            events[i:i + MAX_EMBEDS_PER_MESSAGE]
            for i in range(0, len(events), MAX_EMBEDS_PER_MESSAGE)
        ]
        for index, chunk in enumerate(chunks):
            content = f"📅 새 개발자 행사 {len(events)}건"
            if len(chunks) > 1:
                content += f" ({index + 1}/{len(chunks)})"
            payload = {
                "content": content,
                "embeds": [self._create_embed(e) for e in chunk],
            }
            success = self._post_webhook(payload)
            if success:
                logger.info(
                    f"✓ 다이제스트 전송 성공 ({self.webhook_name}): "
                    f"{len(chunk)}건 ({index + 1}/{len(chunks)})"
                )
            results.extend([success] * len(chunk))
        return results

    @staticmethod
    def _category_color(event: Dict) -> int:
        """분류 메타데이터 키워드로 임베드 색상 결정"""
        category_text = ""
        for part in event.get('metadata', []):
            if part.startswith('분류'):
                category_text = part
                break
        for keywords, color in CATEGORY_COLORS:
            if any(keyword in category_text for keyword in keywords):
                return color
        return COLOR_INFO

    @classmethod
    def _create_embed(cls, event: Dict) -> Dict:
        """Discord Embed 생성 (분류별 색상 + 구조화된 필드)"""
        fields = []
        extra_parts = []
        for part in event.get('metadata', []):
            name, sep, value = part.partition(':')
            name = name.strip()
            value = value.strip()
            if sep and name in ('분류', '주최', '접수', '일시') and value:
                fields.append({
                    "name": name,
                    "value": value[:1024],
                    "inline": name != '분류',
                })
            elif part.strip():
                extra_parts.append(part.strip())

        fields.append({
            "name": "시기",
            "value": event.get('month') or '미정',
            "inline": True,
        })

        embed = {
            "title": event['title'][:256],
            "url": event['url'],
            "color": cls._category_color(event),
            "fields": fields[:25],
            "footer": {"text": "Dev-Event Bot"},
            "timestamp": datetime.utcnow().isoformat(),
        }
        if extra_parts:
            embed["description"] = ' | '.join(extra_parts)[:4096]
        return embed

    def _post_webhook(self, payload: Dict, retry_count: int = 0) -> bool:
        """웹훅 POST 요청 (재시도 로직 포함)"""
        try:
            response = requests.post(
                self.webhook_url,
                json=payload,
                timeout=10
            )
            
            if response.status_code in (DISCORD_SUCCESS_CODE, 200):
                return True

            if response.status_code >= 500 and retry_count < self.max_retries:
                logger.warning(f"서버 오류 ({response.status_code}), 재시도 {retry_count + 1}/{self.max_retries}")
                return self._post_webhook(payload, retry_count + 1)

            logger.error(f"Discord 오류 {response.status_code} ({self.webhook_name})")
            return False

        except requests.RequestException as e:
            if retry_count < self.max_retries:
                logger.warning(f"네트워크 오류, 재시도 {retry_count + 1}/{self.max_retries}")
                return self._post_webhook(payload, retry_count + 1)
            logger.error(f"전송 실패 (최대 재시도, {self.webhook_name}): {e}")
            return False


class ReadmeDownloader:
    """README.md 다운로드 (여러 방식 지원)"""
    
    @staticmethod
    def fetch(sources: List[str] = README_SOURCES, local_fallback: Optional[str] = None) -> Optional[str]:
        """다양한 방식으로 README 다운로드"""
        # 온라인 소스 시도
        for url in sources:
            try:
                logger.info(f"시도: {url}")
                response = requests.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
                response.raise_for_status()
                logger.info(f"✓ 다운로드 성공: {len(response.text)} bytes")
                return response.text
            except Exception as e:
                logger.warning(f"실패: {type(e).__name__}")
                continue
        
        # 로컬 파일 폴백
        if local_fallback and os.path.exists(local_fallback):
            logger.info(f"로컬 파일 사용: {local_fallback}")
            try:
                with open(local_fallback, 'r', encoding='utf-8') as f:
                    content = f.read()
                    logger.info(f"✓ 로컬 파일 읽기 성공: {len(content)} bytes")
                    return content
            except Exception as e:
                logger.error(f"로컬 파일 읽기 실패: {e}")
        
        return None


class DevEventBot:
    """메인 봇 클래스"""
    
    def __init__(self):
        self.cache = EventCache()
        self.dry_run = os.environ.get("DRY_RUN", "").strip().lower() in ("1", "true", "yes")
        self.senders = [
            DiscordSender(webhook_url, webhook_name)
            for webhook_name, webhook_url in get_webhooks()
        ]

    def run(self) -> Tuple[int, int]:
        """봇 실행"""
        logger.info("=" * 60)
        logger.info("Dev-Event 봇 실행 시작" + (" [DRY RUN]" if self.dry_run else ""))
        logger.info("=" * 60)

        try:
            if not self.senders and not self.dry_run:
                logger.error("설정된 Discord Webhook이 없습니다")
                return 0, 0

            logger.info(f"Discord Webhook {len(self.senders)}개 설정됨")

            # README.md 다운로드
            readme_content = ReadmeDownloader.fetch(
                sources=README_SOURCES,
                local_fallback="README.md"
            )
            
            if not readme_content:
                logger.error("README.md를 획득할 수 없습니다")
                return 0, 0
            
            # 이벤트 파싱
            logger.info("이벤트 파싱 중...")
            events = MarkdownParser.parse_events(readme_content)
            logger.info(f"총 {len(events)}개 이벤트 파싱 완료")
            
            if not events:
                logger.warning("파싱된 이벤트가 없습니다")
                return 0, 0
            
            # 신규 이벤트 필터링
            new_events = []
            enriched_count = 0
            for event in events:
                if self.cache.is_sent(event):
                    if self.cache.enrich(event):
                        enriched_count += 1
                    logger.debug(f"중복 이벤트 건너뜀: {event['title'][:40]}")
                    continue
                logger.info(f"새 행사 발견: {event['title']}")
                new_events.append(event)

            # 다이제스트 전송 (메시지당 최대 10개 임베드)
            new_count = 0
            if new_events and self.dry_run:
                message_count = -(-len(new_events) // MAX_EMBEDS_PER_MESSAGE)
                logger.info(
                    f"[DRY RUN] 다이제스트 전송 생략: "
                    f"{len(new_events)}건 → 메시지 {message_count}개"
                )
                for event in new_events:
                    logger.info(f"[DRY RUN]   - {event['title'][:60]} | {event['url']}")
                new_count = len(new_events)
            elif new_events:
                all_results = [sender.send_digest(new_events) for sender in self.senders]
                for index, event in enumerate(new_events):
                    if all(results[index] for results in all_results):
                        self.cache.mark_sent(event)
                        new_count += 1
                    else:
                        logger.warning(f"일부 Webhook 전송 실패로 캐시에 기록하지 않음: {event['title']}")

            # 오래된 캐시 정리 후 저장 (DRY RUN에서는 파일 미변경)
            if self.dry_run:
                logger.info("[DRY RUN] 캐시 저장 생략")
            else:
                if enriched_count:
                    logger.info(f"마이그레이션 항목 백필: {enriched_count}개")
                self.cache.prune()
                self.cache.save()
            
            logger.info("=" * 60)
            logger.info(f"봇 실행 완료 | 새 행사: {new_count}개, 총: {len(events)}개")
            logger.info("=" * 60)
            
            return new_count, len(events)
        
        except Exception as e:
            logger.error(f"예상치 못한 오류: {e}", exc_info=True)
            return 0, 0


def main():
    """엔트리포인트"""
    bot = DevEventBot()
    new_count, total_count = bot.run()
    exit(0 if new_count >= 0 else 1)


if __name__ == "__main__":
    main()
