import json
import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

from dev_event_bot import (
    BOARD_SECTIONS,
    MAX_BOARD_CHARS,
    DevEventBot,
    DiscordSender,
    EventCache,
    build_board_payload,
    board_webhook_names,
    is_board_enabled,
)

NOW = datetime(2026, 10, 22, 9, 0)


def make_event(title, deadline_text, url=None, kind="접수"):
    return {
        "title": title,
        "url": url or f"https://example.com/{title}",
        "month": "26년 10월",
        "metadata": [
            "분류: `온라인`, `무료`, `대회`",
            f"{kind}: {deadline_text}",
        ],
    }


def payload_chars(payload):
    total = len(payload.get("content", ""))
    for embed in payload["embeds"]:
        total += len(embed.get("title", "")) + len(embed.get("description", ""))
        total += len((embed.get("footer") or {}).get("text", ""))
    return total


class BuildBoardPayloadTest(unittest.TestCase):
    def test_sorts_by_deadline_and_groups_by_dday(self):
        events = [
            make_event("나중", "10. 01(목) ~ 11. 20(금)"),
            make_event("오늘", "10. 01(목) ~ 10. 22(목) 18:00"),
            make_event("이틀뒤", "10. 01(목) ~ 10. 24(토)"),
            make_event("닷새뒤", "10. 01(목) ~ 10. 27(화)"),
        ]
        payload = build_board_payload(events, NOW)
        titles = [embed["title"] for embed in payload["embeds"]]
        self.assertEqual(titles, [f"{name} (1건)" for name, _, _ in BOARD_SECTIONS])
        self.assertIn("`D-DAY`", payload["embeds"][0]["description"])
        self.assertIn("`D-2`", payload["embeds"][1]["description"])
        self.assertIn("`D-5`", payload["embeds"][2]["description"])
        self.assertIn("`D-29`", payload["embeds"][3]["description"])
        self.assertIn("4건", payload["content"])

    def test_excludes_expired_and_unknown_deadline(self):
        events = [
            make_event("어제마감", "10. 01(목) ~ 10. 21(수)"),
            {"title": "날짜없음", "url": "https://example.com/x", "month": "", "metadata": []},
            make_event("진행중", "10. 01(목) ~ 10. 30(금)"),
        ]
        payload = build_board_payload(events, NOW)
        text = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("어제마감", text)
        self.assertIn("진행중", text)
        self.assertIn("마감일을 알 수 없는 1건 제외", payload["embeds"][-1]["footer"]["text"])

    def test_labels_deadline_kind_and_weekday(self):
        events = [
            make_event("접수형", "10. 01(목) ~ 10. 25(일)"),
            make_event("일시형", "10. 24(토) 14:00 - 18:00", kind="일시"),
        ]
        description = build_board_payload(events, NOW)["embeds"][0]["description"]
        self.assertIn("10/25(일) 마감", description)
        self.assertIn("10/24(토) 행사", description)

    def test_skips_duplicate_urls_and_untitled_entries(self):
        events = [
            make_event("같은행사", "10. 01(목) ~ 10. 30(금)", url="https://example.com/a"),
            make_event("같은행사", "10. 01(목) ~ 10. 30(금)", url="https://example.com/a/"),
            {"title": "", "url": "https://example.com/b", "month": "", "metadata": []},
        ]
        self.assertIn("1건", build_board_payload(events, NOW)["content"])

    def test_stays_within_discord_limit_and_reports_omitted(self):
        events = [
            make_event(f"아주 긴 행사 이름 {i:03d} " + "가" * 40, "10. 01(목) ~ 11. 20(금)")
            for i in range(200)
        ]
        payload = build_board_payload(events, NOW)
        self.assertLessEqual(payload_chars(payload), MAX_BOARD_CHARS + 200)
        self.assertLess(payload_chars(payload), 6000)
        self.assertIn("생략", payload["embeds"][-1]["footer"]["text"])

    def test_each_embed_stays_under_4096_when_one_section_is_crowded(self):
        # 월초처럼 거의 모든 행사가 '그 이후' 한 구간에 몰리는 경우
        events = [
            make_event(f"행사 {i:03d} " + "나" * 50, "10. 01(목) ~ 11. 20(금)")
            for i in range(120)
        ]
        payload = build_board_payload(events, datetime(2026, 10, 1))
        for embed in payload["embeds"]:
            self.assertLessEqual(len(embed["description"]), 4096)
        self.assertIn("생략", payload["embeds"][-1]["footer"]["text"])

    def test_crowded_section_does_not_block_later_sections(self):
        near = [
            make_event(f"임박 {i:03d} " + "다" * 50, "10. 01(목) ~ 10. 23(금)")
            for i in range(80)
        ]
        far = [make_event("먼 행사", "10. 01(목) ~ 11. 30(월)")]
        payload = build_board_payload(near + far, NOW)
        self.assertIn("먼 행사", json.dumps(payload, ensure_ascii=False))

    def test_empty_board_has_message(self):
        payload = build_board_payload([], NOW)
        self.assertIn("없어요", payload["embeds"][0]["description"])
        self.assertIn("0건", payload["content"])

    def test_titles_never_break_markdown_links(self):
        events = [make_event("[온라인] 해커톤", "10. 01(목) ~ 10. 30(금)")]
        description = build_board_payload(events, NOW)["embeds"][-1]["description"]
        self.assertIn("[［온라인］ 해커톤](", description)


class UpsertMessageTest(unittest.TestCase):
    def setUp(self):
        self.sender = DiscordSender("https://discord.test/wh", "테스트")

    @patch("dev_event_bot.requests.patch")
    def test_edits_existing_board(self, mock_patch):
        mock_patch.return_value = MagicMock(status_code=200)
        self.assertEqual(self.sender.upsert_message("42", {"content": "x"}), "42")
        self.assertEqual(mock_patch.call_args[0][0], "https://discord.test/wh/messages/42")

    @patch("dev_event_bot.requests.post")
    @patch("dev_event_bot.requests.patch")
    def test_recreates_when_board_was_deleted(self, mock_patch, mock_post):
        mock_patch.return_value = MagicMock(status_code=404)
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"id": "99"})
        self.assertEqual(self.sender.upsert_message("42", {"content": "x"}), "99")

    @patch("dev_event_bot.requests.post")
    @patch("dev_event_bot.requests.patch")
    def test_keeps_id_on_temporary_failure(self, mock_patch, mock_post):
        mock_patch.return_value = MagicMock(status_code=500)
        self.assertEqual(self.sender.upsert_message("42", {"content": "x"}), "42")
        mock_post.assert_not_called()

    @patch("dev_event_bot.requests.post")
    def test_creates_when_no_board_yet(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"id": "7"})
        self.assertEqual(self.sender.upsert_message(None, {"content": "x"}), "7")


class BoardCacheAndBotTest(unittest.TestCase):
    def test_boards_round_trip_through_cache_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "cache.json")
            cache = EventCache(path, now=NOW)
            cache.boards["테스트"] = {"id": "123"}
            cache.save()
            self.assertEqual(EventCache(path, now=NOW).boards, {"테스트": {"id": "123"}})

    def test_update_boards_stores_message_id_per_webhook(self):
        bot = DevEventBot.__new__(DevEventBot)
        bot.cache = MagicMock()
        bot.cache.events = [make_event("행사", "10. 01(목) ~ 10. 30(금)")]
        bot.cache.now = NOW
        bot.cache.boards = {"A": {"id": "1"}}
        a = DiscordSender("https://discord.test/a", "A")
        b = DiscordSender("https://discord.test/b", "B")
        a.upsert_message = MagicMock(return_value="1")
        b.upsert_message = MagicMock(return_value="2")
        bot.senders = [a, b]
        self.assertEqual(bot.update_boards(), 2)
        a.upsert_message.assert_called_once()
        self.assertEqual(a.upsert_message.call_args[0][0], "1")
        self.assertIsNone(b.upsert_message.call_args[0][0])
        self.assertEqual(bot.cache.boards, {"A": {"id": "1"}, "B": {"id": "2"}})

    def test_board_only_for_selected_webhooks(self):
        bot = DevEventBot.__new__(DevEventBot)
        bot.cache = MagicMock()
        bot.cache.events = []
        bot.cache.now = NOW
        bot.cache.boards = {}
        a = DiscordSender("https://discord.test/a", "A")
        b = DiscordSender("https://discord.test/b", "B")
        a.upsert_message = MagicMock(return_value="1")
        b.upsert_message = MagicMock(return_value="2")
        bot.senders = [a, b]
        with patch.dict(os.environ, {"ACTIVE_BOARD": "B"}):
            self.assertEqual(bot.update_boards(), 1)
        a.upsert_message.assert_not_called()
        self.assertEqual(bot.cache.boards, {"B": {"id": "2"}})

    def test_board_failure_does_not_skip_cache_save(self):
        bot = DevEventBot.__new__(DevEventBot)
        bot.dry_run = False
        bot.style = "compact"
        bot.cache = MagicMock()
        bot.cache.now = NOW
        bot.cache.events = []
        bot.cache.is_sent.return_value = True
        bot.cache.enrich.return_value = False
        bot.senders = [DiscordSender("https://discord.test/a", "A")]
        bot.cleanup_expired_messages = MagicMock(return_value=(0, 0))
        bot.update_boards = MagicMock(side_effect=RuntimeError("boom"))
        with patch.dict(os.environ, {"ACTIVE_BOARD": ""}), \
                patch("dev_event_bot.ReadmeDownloader.fetch", return_value="readme"), \
                patch("dev_event_bot.MarkdownParser.parse_events",
                      return_value=[make_event("행사", "10. 01(목) ~ 10. 30(금)")]):
            bot.run()
        bot.cache.save.assert_called_once()

    def test_board_webhook_names_env(self):
        with patch.dict(os.environ, {"ACTIVE_BOARD": ""}):
            self.assertIsNone(board_webhook_names())
        with patch.dict(os.environ, {"ACTIVE_BOARD": "1"}):
            self.assertIsNone(board_webhook_names())
        with patch.dict(os.environ, {"ACTIVE_BOARD": "A, B"}):
            self.assertEqual(board_webhook_names(), ["A", "B"])
            self.assertTrue(is_board_enabled())

    def test_board_toggle_env(self):
        with patch.dict(os.environ, {"ACTIVE_BOARD": ""}):
            self.assertTrue(is_board_enabled())
        with patch.dict(os.environ, {"ACTIVE_BOARD": "0"}):
            self.assertFalse(is_board_enabled())
        with patch.dict(os.environ, {"ACTIVE_BOARD": "off"}):
            self.assertFalse(is_board_enabled())


if __name__ == "__main__":
    unittest.main()
