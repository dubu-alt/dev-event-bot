import json
import os
import tempfile
import unittest
from datetime import datetime

from dev_event_bot import (
    EventCache,
    normalize_title,
    normalize_url,
    parse_month,
)

NOW = datetime(2026, 7, 19)


def make_event(**overrides):
    event = {
        "title": "테스트 행사",
        "url": "https://example.com/event/1",
        "month": "26년 07월",
        "metadata": ["분류: `온라인`"],
    }
    event.update(overrides)
    return event


class TempCacheMixin:
    def make_cache_file(self, data) -> str:
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        return path


class NormalizeUrlTest(unittest.TestCase):
    def test_strips_tracking_params_fragment_and_trailing_slash(self):
        self.assertEqual(
            normalize_url("https://WWW.Example.com/event/?utm_source=x&fbclid=y#top"),
            normalize_url("https://example.com/event"),
        )

    def test_keeps_meaningful_query_params(self):
        a = normalize_url("https://example.com/e?id=1")
        b = normalize_url("https://example.com/e?id=2")
        self.assertNotEqual(a, b)


class ParseMonthTest(unittest.TestCase):
    def test_parses_two_digit_year(self):
        self.assertEqual(parse_month("26년 05월"), (2026, 5))

    def test_returns_none_for_invalid(self):
        self.assertIsNone(parse_month(""))
        self.assertIsNone(parse_month("미정"))


class EventCacheMigrationTest(TempCacheMixin, unittest.TestCase):
    def test_migrates_v1_url_list(self):
        path = self.make_cache_file(["https://example.com/old-event"])
        cache = EventCache(cache_file=path, now=NOW)

        self.assertEqual(len(cache.events), 1)
        self.assertTrue(cache.events[0]["migrated"])
        self.assertTrue(cache.is_sent(make_event(url="https://example.com/old-event")))

    def test_loads_v2_format(self):
        path = self.make_cache_file({
            "version": 2,
            "events": [make_event(sent_at=NOW.isoformat())],
        })
        cache = EventCache(cache_file=path, now=NOW)
        self.assertTrue(cache.is_sent(make_event()))

    def test_corrupt_file_resets(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as f:
            f.write("{broken json")
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        cache = EventCache(cache_file=path, now=NOW)
        self.assertEqual(cache.events, [])

    def test_save_writes_v2_format(self):
        path = self.make_cache_file([])
        cache = EventCache(cache_file=path, now=NOW)
        cache.mark_sent(make_event())
        cache.save()

        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["version"], 2)
        self.assertEqual(len(data["events"]), 1)
        self.assertEqual(data["events"][0]["title"], "테스트 행사")


class EventCacheDedupTest(TempCacheMixin, unittest.TestCase):
    def setUp(self):
        path = self.make_cache_file([])
        self.cache = EventCache(cache_file=path, now=NOW)
        self.cache.mark_sent(make_event())

    def test_same_url_with_tracking_params_is_duplicate(self):
        self.assertTrue(self.cache.is_sent(
            make_event(url="https://www.example.com/event/1/?utm_source=discord")
        ))

    def test_same_title_and_month_with_new_url_is_duplicate(self):
        self.assertTrue(self.cache.is_sent(
            make_event(url="https://changed.example.com/new")
        ))

    def test_same_title_different_month_is_not_duplicate(self):
        self.assertFalse(self.cache.is_sent(
            make_event(url="https://changed.example.com/new", month="26년 08월")
        ))

    def test_different_event_is_not_duplicate(self):
        self.assertFalse(self.cache.is_sent(
            make_event(title="다른 행사", url="https://example.com/event/2")
        ))

    def test_mark_sent_does_not_duplicate_entries(self):
        self.cache.mark_sent(make_event())
        self.assertEqual(len(self.cache.events), 1)


class EventCacheEnrichTest(TempCacheMixin, unittest.TestCase):
    def test_enrich_backfills_migrated_entry_and_enables_title_dedup(self):
        path = self.make_cache_file(["https://example.com/event/1"])  # v1
        cache = EventCache(cache_file=path, now=NOW)

        event = make_event()
        self.assertTrue(cache.is_sent(event))          # URL로 중복 판정
        self.assertTrue(cache.enrich(event))           # 제목/월 백필
        self.assertEqual(cache.events[0]["title"], "테스트 행사")

        # 이후 URL이 바뀌어도 제목+월로 중복 판정
        self.assertTrue(cache.is_sent(
            make_event(url="https://changed.example.com/new")
        ))

    def test_enrich_skips_entries_that_already_have_title(self):
        path = self.make_cache_file([])
        cache = EventCache(cache_file=path, now=NOW)
        cache.mark_sent(make_event())
        self.assertFalse(cache.enrich(make_event(title="다른 제목")))
        self.assertEqual(cache.events[0]["title"], "테스트 행사")


class EventCachePruneTest(TempCacheMixin, unittest.TestCase):
    def test_prunes_events_older_than_retention(self):
        path = self.make_cache_file({
            "version": 2,
            "events": [
                make_event(month="26년 03월", url="https://example.com/mar"),  # 4개월 전 → 제거
                make_event(month="26년 04월", url="https://example.com/apr"),  # 3개월 전 → 보관
                make_event(month="26년 07월", url="https://example.com/jul"),  # 현재 → 보관
            ],
        })
        cache = EventCache(cache_file=path, now=NOW)
        removed = cache.prune()

        self.assertEqual(removed, 1)
        urls = [e["url"] for e in cache.events]
        self.assertNotIn("https://example.com/mar", urls)
        self.assertIn("https://example.com/apr", urls)

    def test_prunes_migrated_entries_by_sent_at(self):
        path = self.make_cache_file({
            "version": 2,
            "events": [
                {"title": "", "url": "https://example.com/old", "month": "",
                 "metadata": [], "sent_at": "2025-12-01T00:00:00"},  # 180일 초과 → 제거
                {"title": "", "url": "https://example.com/recent", "month": "",
                 "metadata": [], "sent_at": "2026-07-01T00:00:00"},  # 보관
            ],
        })
        cache = EventCache(cache_file=path, now=NOW)
        removed = cache.prune()

        self.assertEqual(removed, 1)
        self.assertEqual(cache.events[0]["url"], "https://example.com/recent")

    def test_keeps_entries_without_month_or_sent_at(self):
        path = self.make_cache_file({
            "version": 2,
            "events": [{"title": "t", "url": "https://example.com/x",
                        "month": "미정", "metadata": [], "sent_at": ""}],
        })
        cache = EventCache(cache_file=path, now=NOW)
        self.assertEqual(cache.prune(), 0)


class NormalizeTitleTest(unittest.TestCase):
    def test_collapses_whitespace_and_case(self):
        self.assertEqual(
            normalize_title("  Seoul  iOS\nMeetup "),
            "seoul ios meetup",
        )


if __name__ == "__main__":
    unittest.main()
