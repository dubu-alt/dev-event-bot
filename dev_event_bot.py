"""
brave-people/Dev-Event 레포 파싱 Discord 봇
GitHub README.md에서 개발자 행사 정보를 추출하고 Discord로 전송
"""

import requests
import json
import os
import re
from datetime import datetime, timedelta, timezone
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
CACHE_VERSION = 3  # v3: 마감일(deadline) + 전송 메시지 참조(messages) 추가
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

# 분류별 스타일 (분류 텍스트에 키워드가 포함되면 적용, 위에서부터 우선)
# (키워드, 임베드 색상, 컴팩트 모드 말머리)
CATEGORY_STYLES = [
    (("대회", "해커톤"), 15158332, "🔴"),   # 빨강
    (("세미나", "컨퍼런스"), 3066993, "🟢"),  # 초록
    (("교육", "부트캠프"), 15105570, "🟠"),  # 주황
    (("모임", "동아리"), 3447003, "🔵"),     # 파랑
]
CATEGORY_COLORS = [(keywords, color) for keywords, color, _ in CATEGORY_STYLES]
DEFAULT_CATEGORY_EMOJI = "⚪"

# 다이제스트 스타일
DIGEST_STYLE_COMPACT = "compact"  # 전체를 임베드 1개 목록으로 압축 (기본)
DIGEST_STYLE_RICH = "rich"        # 행사 1건당 임베드 1개 (구버전)
DEFAULT_DIGEST_STYLE = DIGEST_STYLE_COMPACT

# rich 모드: 메시지 1개당 임베드 최대 개수 (Discord 제한 10)
MAX_EMBEDS_PER_MESSAGE = 10

# compact 모드: 메시지 1개당 행사 최대 개수 / description 문자 예산 (Discord 제한 4096)
MAX_EVENTS_PER_COMPACT_MESSAGE = 20
MAX_COMPACT_DESCRIPTION_CHARS = 3800
MAX_COMPACT_SUMMARY_CHARS = 180
# 행사 블록 사이 구분 (빈 줄을 넣어야 목록이 뭉쳐 보이지 않는다)
COMPACT_BLOCK_SEPARATOR = "\n\n"


def get_digest_style() -> str:
    """DIGEST_STYLE 환경변수로 다이제스트 표현 방식 결정"""
    style = os.environ.get("DIGEST_STYLE", "").strip().lower()
    if style in (DIGEST_STYLE_COMPACT, DIGEST_STYLE_RICH):
        return style
    if style:
        logger.warning(f"알 수 없는 DIGEST_STYLE '{style}', 기본값({DEFAULT_DIGEST_STYLE}) 사용")
    return DEFAULT_DIGEST_STYLE


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


def parse_deadline(event: Dict) -> Optional[datetime]:
    """행사의 마감일(접수 마감일, 없으면 행사 종료일)을 추출한다.

    Dev-Event 표기 예시:
      접수: 06. 06(토) ~ 06. 08(월)     → 06. 08
      접수: 05. 12(화) ~ 06. 14(일) 23:59 → 06. 14
      일시: 08. 29(토)                  → 08. 29
    연도는 표기에 없으므로 'month'(예: '26년 07월')의 연도를 기준으로 추론하되,
    12월 → 1월처럼 연말을 넘기는 구간이면 다음 해로 보정한다.
    판별할 수 없으면 None.
    """
    # '접수'가 있으면 그것을, 없으면 '일시'를 마감 기준으로 삼는다.
    # (접수 마감이 지난 행사는 더 이상 신청할 수 없으므로 알림 가치가 없다)
    text = ""
    for name in ('접수', '일시'):
        for part in event.get('metadata', []):
            key, sep, value = part.partition(':')
            if sep and key.strip() == name and value.strip():
                text = value.strip()
                break
        if text:
            break
    if not text:
        return None  # 날짜 정보 자체가 없음

    # 'MM. DD' 형태를 모두 찾는다. 구분자는 '.', '-', '/' 모두 허용.
    # '06. 06(토) ~ 06. 08(월)' → [('06','06'), ('06','08')]
    # 뒤에 붙는 시각('23:59')은 ':' 구분이라 이 패턴에 걸리지 않는다.
    matches = re.findall(r'(\d{1,2})\s*[.\-/]\s*(\d{1,2})', text)
    if not matches:
        return None
    # 마지막 날짜 = 기간의 끝 = 마감일 (단일 날짜면 그 날짜가 곧 마감일)
    month_str, day_str = matches[-1]
    month_num, day_num = int(month_str), int(day_str)
    if not (1 <= month_num <= 12 and 1 <= day_num <= 31):
        return None  # '13. 45' 같은 오탐 방어

    # 원문에 연도가 없으므로 섹션 제목('26년 07월')의 연도를 빌려온다.
    parsed_month = parse_month(event.get('month', ''))
    year = parsed_month[0] if parsed_month else datetime.now().year
    # 시작 월(12월)보다 마감 월(1월)이 작으면 해를 넘긴 구간이므로 +1년
    if len(matches) > 1 and int(matches[0][0]) > month_num:
        year += 1

    try:
        return datetime(year, month_num, day_num)
    except ValueError:
        return None  # 2월 30일처럼 달력에 없는 날짜


def is_expired(event: Dict, now: Optional[datetime] = None) -> bool:
    """마감일 다음 날부터 만료로 본다. 마감일을 알 수 없으면 만료로 보지 않는다."""
    deadline = parse_deadline(event)
    if deadline is None:
        # 날짜를 못 읽은 행사를 지워버리면 정보 손실이 크므로 보수적으로 유지
        return False
    # 시각을 0시로 맞춰 '날짜' 단위로만 비교한다.
    # 마감일 당일(reference == deadline)은 아직 신청 가능하므로 만료가 아니다.
    reference = (now or datetime.now()).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return reference > deadline


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

    def mark_sent(self, event: Dict, messages: Optional[List[Dict]] = None) -> None:
        """이벤트를 전송됨으로 표시

        messages: [{"webhook": 이름, "id": 메시지ID, "style": 다이제스트 스타일}]
        나중에 마감된 행사를 메시지에서 지우거나 메시지를 삭제할 때 사용한다.
        """
        if self.is_sent(event):
            return
        deadline = parse_deadline(event)
        self.events.append({
            "title": event.get("title", ""),
            "url": event.get("url", ""),
            "month": event.get("month", ""),
            "metadata": event.get("metadata", []),
            "sent_at": self.now.isoformat(),
            "deadline": deadline.date().isoformat() if deadline else None,
            "messages": messages or [],
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
        """줄바꿈으로 분리된 제목/메타데이터 조각을 한 줄 텍스트로 정리

        Dev-Event README에는 '접수: 05. 11(월) ~ 08. 31(월) <br />'처럼
        HTML 태그가 섞여 있는 경우가 있어 함께 제거한다.
        """
        without_tags = re.sub(r'<[^<>]{0,80}?>', ' ', value)
        return re.sub(r'\s+', ' ', without_tags).strip()

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
    
    def __init__(
        self,
        webhook_url: str,
        webhook_name: str,
        max_retries: int = MAX_RETRIES,
        style: Optional[str] = None,
    ):
        self.webhook_url = webhook_url
        self.webhook_name = webhook_name
        self.max_retries = max_retries
        self.style = style or get_digest_style()

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

    def build_payload(
        self,
        events: List[Dict],
        style: Optional[str] = None,
        total: Optional[int] = None,
        index: Optional[int] = None,
        count: Optional[int] = None,
    ) -> Dict:
        """다이제스트 메시지 payload 생성 (전송·수정 공용)"""
        style = style or self.style
        content = f"📅 새 개발자 행사 {total if total is not None else len(events)}건"
        if count and count > 1 and index is not None:
            content += f" ({index + 1}/{count})"
        if style == DIGEST_STYLE_RICH:
            embeds = [self._create_embed(e) for e in events]
        else:
            embeds = [self._create_compact_embed(events)]
        return {"content": content, "embeds": embeds}

    def send_digest(self, events: List[Dict]) -> List[bool]:
        """이벤트 여러 건을 다이제스트로 전송.
        이벤트별 성공 여부 리스트를 반환한다."""
        return [success for success, _ in self.send_digest_detailed(events)]

    def send_digest_detailed(
        self, events: List[Dict]
    ) -> List[Tuple[bool, Optional[str]]]:
        """send_digest와 동일하되 이벤트별 (성공 여부, 메시지 ID)를 반환한다.

        메시지 ID는 나중에 마감된 행사를 지우거나 메시지를 삭제할 때 쓴다.
        """
        if not self.webhook_url:
            logger.error(f"{self.webhook_name}이 설정되지 않았습니다")
            return [(False, None)] * len(events)

        results: List[Tuple[bool, Optional[str]]] = []
        chunks = chunk_events(events, self.style)
        for index, chunk in enumerate(chunks):
            payload = self.build_payload(
                chunk, total=len(events), index=index, count=len(chunks)
            )
            message = self._post_webhook_message(payload)
            success = message is not None
            message_id = str(message.get("id")) if message and message.get("id") else None
            if success:
                logger.info(
                    f"✓ 다이제스트 전송 성공 ({self.webhook_name}, {self.style}): "
                    f"{len(chunk)}건 ({index + 1}/{len(chunks)})"
                )
            results.extend([(success, message_id)] * len(chunk))
        return results

    @staticmethod
    def _category_text(event: Dict) -> str:
        """'분류' 메타데이터 원문 조각 반환"""
        for part in event.get('metadata', []):
            if part.startswith('분류'):
                return part
        return ""

    @classmethod
    def _category_color(cls, event: Dict) -> int:
        """분류 메타데이터 키워드로 임베드 색상 결정"""
        category_text = cls._category_text(event)
        for keywords, color in CATEGORY_COLORS:
            if any(keyword in category_text for keyword in keywords):
                return color
        return COLOR_INFO

    @classmethod
    def _category_emoji(cls, event: Dict) -> str:
        """분류 메타데이터 키워드로 컴팩트 목록 말머리 결정"""
        category_text = cls._category_text(event)
        for keywords, _, emoji in CATEGORY_STYLES:
            if any(keyword in category_text for keyword in keywords):
                return emoji
        return DEFAULT_CATEGORY_EMOJI

    @staticmethod
    def _metadata_value(event: Dict, name: str) -> str:
        """'주최: 값' 형태 메타데이터에서 값만 추출"""
        for part in event.get('metadata', []):
            key, sep, value = part.partition(':')
            if sep and key.strip() == name:
                return value.strip()
        return ""

    @staticmethod
    def _escape_link_text(text: str) -> str:
        """Markdown 링크 라벨을 깨뜨리는 대괄호를 전각 괄호로 치환

        Dev-Event 제목에는 '[온라인] 7월 …'처럼 대괄호가 자주 들어간다.
        백슬래시 이스케이프(`\\[`)는 Discord가 링크 라벨 안에서 해석하지 않고
        백슬래시를 그대로 보여주므로, 겉보기가 거의 같은 전각 괄호로 바꾼다.
        """
        return text.replace('[', '［').replace(']', '］')

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
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if extra_parts:
            embed["description"] = ' | '.join(extra_parts)[:4096]
        return embed

    @classmethod
    def _compact_summary(cls, event: Dict) -> str:
        """행사 1건을 '날짜 · 분류 · 주최' 한 줄로 요약"""
        segments: List[str] = []

        held_at = cls._metadata_value(event, '일시')
        apply_at = cls._metadata_value(event, '접수')
        if held_at:
            segments.append(held_at)
        elif apply_at:
            segments.append(f"접수 {apply_at}")
        elif event.get('month'):
            segments.append(event['month'])

        category = cls._metadata_value(event, '분류')
        if category:
            segments.extend(
                tag.strip(' `') for tag in category.split(',') if tag.strip(' `')
            )

        host = cls._metadata_value(event, '주최')
        if host:
            segments.append(host)

        return ' · '.join(segments)[:MAX_COMPACT_SUMMARY_CHARS]

    @classmethod
    def _compact_line(cls, event: Dict) -> str:
        """컴팩트 목록의 행사 1건 블록 (제목 링크 + 요약)"""
        title = cls._escape_link_text(event['title'][:256])
        line = f"{cls._category_emoji(event)} **[{title}]({event['url']})**"
        summary = cls._compact_summary(event)
        if summary:
            # '-# '는 Discord의 subtext 문법. 요약을 작고 흐리게 표시해
            # 제목과 시각적으로 구분한다. 반드시 줄 맨 앞에 와야 한다.
            line += f"\n-# {summary}"
        return line

    @classmethod
    def _create_compact_embed(cls, events: List[Dict]) -> Dict:
        """여러 행사를 임베드 1개의 목록으로 압축"""
        description = COMPACT_BLOCK_SEPARATOR.join(
            cls._compact_line(e) for e in events
        )
        return {
            "description": description[:4096],
            "color": cls._digest_color(events),
            "footer": {"text": "Dev-Event Bot"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def _digest_color(events: List[Dict]) -> int:
        """묶음 전체를 대표하는 색상 (분류 우선순위가 가장 높은 행사 기준)"""
        category_texts = [DiscordSender._category_text(e) for e in events]
        for keywords, color in CATEGORY_COLORS:
            for text in category_texts:
                if any(keyword in text for keyword in keywords):
                    return color
        return COLOR_INFO

    def _post_webhook(self, payload: Dict, retry_count: int = 0) -> bool:
        """웹훅 POST 요청 (재시도 로직 포함)"""
        return self._post_webhook_message(payload, retry_count) is not None

    def _post_webhook_message(
        self, payload: Dict, retry_count: int = 0
    ) -> Optional[Dict]:
        """웹훅 POST 요청. 성공 시 생성된 메시지 정보(Dict)를 반환한다.

        ?wait=true를 붙이면 Discord가 메시지 객체를 돌려주므로 message id를
        확보할 수 있다. 나중에 이 id로 메시지를 수정·삭제한다.
        """
        try:
            response = requests.post(
                self.webhook_url,
                params={"wait": "true"},  # 응답 본문으로 메시지 객체를 받기 위해 필수
                json=payload,
                timeout=10,
            )

            if response.status_code in (DISCORD_SUCCESS_CODE, 200):
                try:
                    return response.json() or {}
                except ValueError:
                    # wait=true인데도 본문이 비어 오는 경우.
                    # 전송 자체는 성공이므로 빈 dict로 성공을 알리되 ID는 없다.
                    return {}

            if response.status_code >= 500 and retry_count < self.max_retries:
                logger.warning(f"서버 오류 ({response.status_code}), 재시도 {retry_count + 1}/{self.max_retries}")
                return self._post_webhook_message(payload, retry_count + 1)

            logger.error(f"Discord 오류 {response.status_code} ({self.webhook_name})")
            return None

        except requests.RequestException as e:
            if retry_count < self.max_retries:
                logger.warning(f"네트워크 오류, 재시도 {retry_count + 1}/{self.max_retries}")
                return self._post_webhook_message(payload, retry_count + 1)
            logger.error(f"전송 실패 (최대 재시도, {self.webhook_name}): {e}")
            return None

    def edit_message(self, message_id: str, payload: Dict) -> bool:
        """이미 보낸 웹훅 메시지를 수정 (PATCH)"""
        if not (self.webhook_url and message_id):
            return False
        # PATCH /webhooks/{id}/{token}/messages/{message_id}
        # 웹훅 URL 자체가 '/webhooks/{id}/{token}' 이므로 뒤에 경로만 붙이면 된다.
        url = f"{self.webhook_url.rstrip('/')}/messages/{message_id}"
        try:
            response = requests.patch(url, json=payload, timeout=10)
            if response.status_code in (200, DISCORD_SUCCESS_CODE):
                return True
            if response.status_code == 404:
                # 사람이 이미 지운 메시지. 재시도해도 의미 없으므로 성공으로 처리해
                # 캐시의 메시지 참조를 정리하고 넘어간다.
                logger.info(f"메시지 없음(이미 삭제됨): {message_id}")
                return True
            logger.warning(f"메시지 수정 실패 {response.status_code} ({self.webhook_name})")
            return False
        except requests.RequestException as e:
            # 정리는 부가 기능이므로 실패해도 봇 전체를 멈추지 않는다.
            # 참조를 남겨두면 다음 실행에서 다시 시도한다.
            logger.warning(f"메시지 수정 오류 ({self.webhook_name}): {e}")
            return False

    def delete_message(self, message_id: str) -> bool:
        """이미 보낸 웹훅 메시지를 삭제 (DELETE)"""
        if not (self.webhook_url and message_id):
            return False
        # DELETE /webhooks/{id}/{token}/messages/{message_id}
        url = f"{self.webhook_url.rstrip('/')}/messages/{message_id}"
        try:
            response = requests.delete(url, timeout=10)
            # 404(이미 없음)도 '지워진 상태'라는 목적은 달성했으므로 성공 취급
            if response.status_code in (DISCORD_SUCCESS_CODE, 200, 404):
                return True
            logger.warning(f"메시지 삭제 실패 {response.status_code} ({self.webhook_name})")
            return False
        except requests.RequestException as e:
            logger.warning(f"메시지 삭제 오류 ({self.webhook_name}): {e}")
            return False


def chunk_events(events: List[Dict], style: str) -> List[List[Dict]]:
    """다이제스트 스타일에 맞춰 이벤트를 메시지 단위로 분할"""
    if not events:
        return []

    if style == DIGEST_STYLE_RICH:
        return [
            events[i:i + MAX_EMBEDS_PER_MESSAGE]
            for i in range(0, len(events), MAX_EMBEDS_PER_MESSAGE)
        ]

    chunks: List[List[Dict]] = []
    current: List[Dict] = []
    used = 0
    for event in events:
        cost = len(DiscordSender._compact_line(event)) + len(COMPACT_BLOCK_SEPARATOR)
        exceeds_count = len(current) >= MAX_EVENTS_PER_COMPACT_MESSAGE
        exceeds_chars = used + cost > MAX_COMPACT_DESCRIPTION_CHARS
        if current and (exceeds_count or exceeds_chars):
            chunks.append(current)
            current = []
            used = 0
        current.append(event)
        used += cost
    if current:
        chunks.append(current)
    return chunks


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
        self.style = get_digest_style()
        self.senders = [
            DiscordSender(webhook_url, webhook_name, style=self.style)
            for webhook_name, webhook_url in get_webhooks()
        ]

    def _grouped_messages(self) -> Dict[Tuple[str, str], Dict]:
        """캐시된 이벤트를 (웹훅, 메시지 ID) 단위로 묶는다

        정리는 '메시지' 단위로 이뤄지는데 캐시는 '행사' 단위로 저장돼 있어
        역방향 인덱스가 필요하다. 웹훅이 여러 개면 같은 행사가 여러 메시지에
        들어가므로 웹훅 이름까지 키에 포함한다.
        """
        groups: Dict[Tuple[str, str], Dict] = {}
        for cached in self.cache.events:
            for ref in cached.get('messages') or []:
                webhook_name = ref.get('webhook')
                message_id = ref.get('id')
                if not (webhook_name and message_id):
                    continue
                group = groups.setdefault(
                    (webhook_name, message_id),
                    {"style": ref.get('style') or DEFAULT_DIGEST_STYLE, "events": []},
                )
                group["events"].append(cached)
        return groups

    @staticmethod
    def _drop_message_ref(event: Dict, webhook_name: str, message_id: str) -> None:
        """캐시 이벤트에서 특정 메시지 참조만 제거 (중복 방지용 기록은 유지)

        주의: 이벤트 자체를 캐시에서 지우면 다음 실행에서 신규 행사로 오인해
        다시 알림을 보내게 된다. 그래서 'messages'만 비운다.
        """
        event['messages'] = [
            ref for ref in event.get('messages') or []
            if not (ref.get('webhook') == webhook_name and ref.get('id') == message_id)
        ]

    def cleanup_expired_messages(self) -> Tuple[int, int]:
        """접수 마감된 행사를 기존 메시지에서 제거한다.

        메시지에 남은 행사가 있으면 그 행사들만으로 메시지를 수정(PATCH)하고,
        전부 마감되었으면 메시지를 삭제(DELETE)한다.
        반환값은 (수정한 메시지 수, 삭제한 메시지 수).
        """
        senders_by_name = {sender.webhook_name: sender for sender in self.senders}
        edited = deleted = 0

        for (webhook_name, message_id), group in self._grouped_messages().items():
            # 지금 설정에 없는 웹훅(환경변수 제거 등)의 메시지는 건드릴 수 없다
            sender = senders_by_name.get(webhook_name)
            if not sender:
                continue

            expired = [e for e in group["events"] if is_expired(e, self.cache.now)]
            if not expired:
                continue  # 마감된 게 없으면 API 호출 자체를 하지 않는다
            remaining = [e for e in group["events"] if e not in expired]

            if remaining:
                # 살아 있는 행사만으로 메시지를 다시 만들어 덮어쓴다.
                # 캐시에 제목·URL·메타데이터가 그대로 있으므로 재구성이 가능하다.
                payload = sender.build_payload(remaining, style=group["style"])
                if sender.edit_message(message_id, payload):
                    edited += 1
                    # 성공했을 때만 참조를 지운다. 실패하면 참조가 남아
                    # 다음 실행에서 자동으로 재시도된다.
                    for event in expired:
                        self._drop_message_ref(event, webhook_name, message_id)
                    logger.info(
                        f"메시지 수정 ({webhook_name}): 마감 {len(expired)}건 제거, "
                        f"{len(remaining)}건 유지"
                    )
            else:
                # 남는 행사가 없으면 메시지를 통째로 지운다
                if sender.delete_message(message_id):
                    deleted += 1
                    for event in group["events"]:
                        self._drop_message_ref(event, webhook_name, message_id)
                    logger.info(
                        f"메시지 삭제 ({webhook_name}): {len(expired)}건 전부 마감"
                    )

        if edited or deleted:
            logger.info(f"마감 정리 완료 | 수정 {edited}건, 삭제 {deleted}건")
        return edited, deleted

    def _log_dry_run_cleanup(self) -> None:
        """DRY RUN에서 마감 정리 대상만 로그로 출력"""
        for (webhook_name, message_id), group in self._grouped_messages().items():
            expired = [e for e in group["events"] if is_expired(e, self.cache.now)]
            if not expired:
                continue
            remaining = len(group["events"]) - len(expired)
            action = "수정" if remaining else "삭제"
            logger.info(
                f"[DRY RUN] 마감 정리 {action} 대상 ({webhook_name}/{message_id}): "
                f"마감 {len(expired)}건, 유지 {remaining}건"
            )
            for event in expired:
                logger.info(f"[DRY RUN]   - 마감: {event.get('title', '')[:60]}")

    def run(self) -> Tuple[int, int]:
        """봇 실행"""
        logger.info("=" * 60)
        logger.info("Dev-Event 봇 실행 시작" + (" [DRY RUN]" if self.dry_run else ""))
        logger.info("=" * 60)

        try:
            if not self.senders and not self.dry_run:
                logger.error("설정된 Discord Webhook이 없습니다")
                return 0, 0

            logger.info(f"Discord Webhook {len(self.senders)}개 설정됨 (다이제스트: {self.style})")

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

            # 다이제스트 전송
            new_count = 0
            if new_events and self.dry_run:
                chunks = chunk_events(new_events, self.style)
                logger.info(
                    f"[DRY RUN] 다이제스트 전송 생략: "
                    f"{len(new_events)}건 → 메시지 {len(chunks)}개 ({self.style})"
                )
                for event in new_events:
                    logger.info(f"[DRY RUN]   - {event['title'][:60]} | {event['url']}")
                new_count = len(new_events)
            elif new_events:
                all_results = [
                    (sender, sender.send_digest_detailed(new_events))
                    for sender in self.senders
                ]
                for index, event in enumerate(new_events):
                    if all(results[index][0] for _, results in all_results):
                        messages = [
                            {
                                "webhook": sender.webhook_name,
                                "id": results[index][1],
                                "style": sender.style,
                            }
                            for sender, results in all_results
                            if results[index][1]
                        ]
                        self.cache.mark_sent(event, messages=messages)
                        new_count += 1
                    else:
                        logger.warning(f"일부 Webhook 전송 실패로 캐시에 기록하지 않음: {event['title']}")

            # 접수 마감된 행사를 기존 메시지에서 정리
            if self.dry_run:
                self._log_dry_run_cleanup()
            else:
                self.cleanup_expired_messages()

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
