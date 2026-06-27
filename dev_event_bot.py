"""
brave-people/Dev-Event 레포 파싱 Discord 봇
GitHub README.md에서 개발자 행사 정보를 추출하고 Discord로 전송
"""

import requests
import json
import os
import re
from datetime import datetime
from typing import List, Dict, Tuple, Optional
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
MAX_RETRIES = 3
DISCORD_SUCCESS_CODE = 204

# README 다운로드 옵션
README_SOURCES = [
    "https://cdn.jsdelivr.net/gh/brave-people/Dev-Event@master/README.md",
    "https://raw.githubusercontent.com/brave-people/Dev-Event/master/README.md",
]

# 색상
COLOR_INFO = 3447003
COLOR_SUCCESS = 3066993
COLOR_WARNING = 15158332


def get_webhooks() -> List[Tuple[str, str]]:
    """환경 변수에서 설정된 Discord Webhook 목록을 가져온다."""
    webhooks = []
    for env_name in WEBHOOK_ENV_NAMES:
        webhook_url = os.environ.get(env_name, "").strip()
        if webhook_url:
            webhooks.append((env_name, webhook_url))
    return webhooks


class EventCache:
    """이벤트 캐시 관리"""
    
    def __init__(self, cache_file: str = CACHE_FILE):
        self.cache_file = cache_file
        self.sent_urls = self._load()
    
    def _load(self) -> List[str]:
        """캐시 파일 로드"""
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    logger.info(f"캐시 로드 완료: {len(data)}개 이벤트")
                    return data
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"캐시 파일 손상: {e}, 초기화")
                return []
        return []
    
    def save(self) -> None:
        """캐시 파일 저장"""
        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(self.sent_urls, f, ensure_ascii=False, indent=2)
            logger.info(f"캐시 저장 완료: {len(self.sent_urls)}개 이벤트")
        except IOError as e:
            logger.error(f"캐시 저장 실패: {e}")
    
    def is_sent(self, url: str) -> bool:
        """이벤트가 이미 전송되었는지 확인"""
        return url in self.sent_urls
    
    def mark_sent(self, url: str) -> None:
        """이벤트를 전송됨으로 표시"""
        if url not in self.sent_urls:
            self.sent_urls.append(url)


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
        """이벤트 정보를 Discord로 전송"""
        if not self.webhook_url:
            logger.error(f"{self.webhook_name}이 설정되지 않았습니다")
            return False
        
        embed = self._create_embed(event)
        return self._post_webhook(embed)
    
    @staticmethod
    def _create_embed(event: Dict) -> Dict:
        """Discord Embed 생성"""
        description = ""
        if event.get('metadata'):
            description = ' | '.join(event['metadata'])[:4096]
        
        return {
            "title": event['title'][:256],
            "url": event['url'],
            "description": description if description else "개발자 행사",
            "color": COLOR_INFO,
            "fields": [
                {
                    "name": "시기",
                    "value": event.get('month', '미정'),
                    "inline": True
                }
            ],
            "footer": {
                "text": "Dev-Event Bot"
            },
            "timestamp": datetime.utcnow().isoformat()
        }
    
    def _post_webhook(self, embed: Dict, retry_count: int = 0) -> bool:
        """웹훅 POST 요청 (재시도 로직 포함)"""
        payload = {"embeds": [embed]}
        
        try:
            response = requests.post(
                self.webhook_url,
                json=payload,
                timeout=10
            )
            
            if response.status_code == DISCORD_SUCCESS_CODE:
                logger.info(f"✓ Discord 전송 성공 ({self.webhook_name}): {embed['title'][:50]}")
                return True
            
            if response.status_code >= 500 and retry_count < self.max_retries:
                logger.warning(f"서버 오류 ({response.status_code}), 재시도 {retry_count + 1}/{self.max_retries}")
                return self._post_webhook(embed, retry_count + 1)
            
            logger.error(f"Discord 오류 {response.status_code} ({self.webhook_name})")
            return False
        
        except requests.RequestException as e:
            if retry_count < self.max_retries:
                logger.warning(f"네트워크 오류, 재시도 {retry_count + 1}/{self.max_retries}")
                return self._post_webhook(embed, retry_count + 1)
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
        self.senders = [
            DiscordSender(webhook_url, webhook_name)
            for webhook_name, webhook_url in get_webhooks()
        ]
    
    def run(self) -> Tuple[int, int]:
        """봇 실행"""
        logger.info("=" * 60)
        logger.info("Dev-Event 봇 실행 시작")
        logger.info("=" * 60)
        
        try:
            if not self.senders:
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
            
            # 신규 이벤트 필터링 및 전송
            new_count = 0
            for event in events:
                if self.cache.is_sent(event['url']):
                    logger.debug(f"중복 이벤트 건너뜀: {event['title'][:40]}")
                    continue
                
                logger.info(f"새 행사 발견: {event['title']}")
                
                send_results = [sender.send_event(event) for sender in self.senders]
                if all(send_results):
                    self.cache.mark_sent(event['url'])
                    new_count += 1
                else:
                    logger.warning(f"일부 Webhook 전송 실패로 캐시에 기록하지 않음: {event['title']}")
            
            # 캐시 저장
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
