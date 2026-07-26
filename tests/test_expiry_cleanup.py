import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

from dev_event_bot import (
    DEFAULT_DIGEST_STYLE,
    DIGEST_STYLE_COMPACT,
    DevEventBot,
    DiscordSender,
    is_expired,
    parse_deadline,
)

NOW = datetime(2026, 7, 26)


def make_event(**overrides):
    event = {
        "title": "테스트 행사",
        "url": "https://example.com/event/1",
        "month": "26년 07월",
        "metadata": [
            "분류: `온라인`, `무료`, `대회`",
            "주최: 테스트 주최사",
            "접수: 07. 01(수) ~ 07. 20(월)",
        ],
    }
    event.update(overrides)
    return event


class ParseDeadlineTest(unittest.TestCase):
    def test_uses_end_of_application_range(self):
        self.assertEqual(
            parse_deadline(make_event()), datetime(2026, 7, 20)
        )

    def test_ignores_trailing_time(self):
        event = make_event(metadata=["접수: 05. 12(화) ~ 06. 14(일) 23:59"], month="26년 06월")
        self.assertEqual(parse_deadline(event), datetime(2026, 6, 14))

    def test_single_date(self):
        event = make_event(metadata=["일시: 08. 29(토)"], month="26년 08월")
        self.assertEqual(parse_deadline(event), datetime(2026, 8, 29))

    def test_prefers_application_over_held_at(self):
        event = make_event(metadata=[
            "일시: 08. 29(토)",
            "접수: 07. 01(수) ~ 07. 20(월)",
        ])
        self.assertEqual(parse_deadline(event), datetime(2026, 7, 20))

    def test_year_rollover(self):
        event = make_event(metadata=["접수: 12. 01(화) ~ 01. 15(금)"], month="26년 12월")
        self.assertEqual(parse_deadline(event), datetime(2027, 1, 15))

    def test_returns_none_without_date(self):
        self.assertIsNone(parse_deadline(make_event(metadata=["주최: 어딘가"])))
        self.assertIsNone(parse_deadline(make_event(metadata=[])))

    def test_returns_none_for_invalid_date(self):
        event = make_event(metadata=["접수: 13. 45(월)"])
        self.assertIsNone(parse_deadline(event))


class IsExpiredTest(unittest.TestCase):
    def test_not_expired_before_deadline(self):
        event = make_event(metadata=["접수: 07. 01(수) ~ 07. 30(목)"])
        self.assertFalse(is_expired(event, NOW))

    def test_not_expired_on_deadline_day(self):
        event = make_event(metadata=["접수: 07. 01(수) ~ 07. 26(일)"])
        self.assertFalse(is_expired(event, NOW))

    def test_expired_day_after_deadline(self):
        event = make_event(metadata=["접수: 07. 01(수) ~ 07. 25(토)"])
        self.assertTrue(is_expired(event, NOW))

    def test_unknown_deadline_is_never_expired(self):
        self.assertFalse(is_expired(make_event(metadata=[]), NOW))


class CleanupExpiredMessagesTest(unittest.TestCase):
    def _bot(self, cached_events):
        bot = DevEventBot.__new__(DevEventBot)
        bot.dry_run = False
        bot.style = DIGEST_STYLE_COMPACT
        bot.cache = MagicMock()
        bot.cache.events = cached_events
        bot.cache.now = NOW
        sender = DiscordSender("https://discord.test/wh", "테스트", style=DIGEST_STYLE_COMPACT)
        sender.edit_message = MagicMock(return_value=True)
        sender.delete_message = MagicMock(return_value=True)
        bot.senders = [sender]
        return bot, sender

    @staticmethod
    def _cached(title, deadline_text, message_id="100"):
        return make_event(
            title=title,
            url=f"https://example.com/{title}",
            metadata=[f"접수: 07. 01(수) ~ {deadline_text}"],
            messages=[{"webhook": "테스트", "id": message_id, "style": DIGEST_STYLE_COMPACT}],
        )

    def test_edits_message_when_some_expired(self):
        events = [
            self._cached("만료행사", "07. 20(월)"),
            self._cached("진행행사", "08. 20(목)"),
        ]
        bot, sender = self._bot(events)

        edited, deleted = bot.cleanup_expired_messages()

        self.assertEqual((edited, deleted), (1, 0))
        sender.delete_message.assert_not_called()
        payload = sender.edit_message.call_args[0][1]
        self.assertIn("진행행사", payload["embeds"][0]["description"])
        self.assertNotIn("만료행사", payload["embeds"][0]["description"])
        self.assertEqual(payload["content"], "📅 새 개발자 행사 1건")
        # 만료 항목만 메시지 참조가 제거되고, 중복 방지용 캐시 기록은 남는다
        self.assertEqual(events[0]["messages"], [])
        self.assertEqual(len(events[1]["messages"]), 1)

    def test_deletes_message_when_all_expired(self):
        events = [
            self._cached("만료1", "07. 20(월)"),
            self._cached("만료2", "07. 24(금)"),
        ]
        bot, sender = self._bot(events)

        edited, deleted = bot.cleanup_expired_messages()

        self.assertEqual((edited, deleted), (0, 1))
        sender.edit_message.assert_not_called()
        sender.delete_message.assert_called_once_with("100")
        self.assertEqual(events[0]["messages"], [])
        self.assertEqual(events[1]["messages"], [])

    def test_no_action_when_nothing_expired(self):
        events = [self._cached("진행행사", "08. 20(목)")]
        bot, sender = self._bot(events)

        self.assertEqual(bot.cleanup_expired_messages(), (0, 0))
        sender.edit_message.assert_not_called()
        sender.delete_message.assert_not_called()

    def test_handles_multiple_messages_independently(self):
        events = [
            self._cached("만료1", "07. 20(월)", message_id="100"),
            self._cached("진행1", "08. 20(목)", message_id="100"),
            self._cached("만료2", "07. 10(금)", message_id="200"),
        ]
        bot, sender = self._bot(events)

        edited, deleted = bot.cleanup_expired_messages()

        self.assertEqual((edited, deleted), (1, 1))
        sender.delete_message.assert_called_once_with("200")

    def test_keeps_ref_when_api_call_fails(self):
        events = [
            self._cached("만료행사", "07. 20(월)"),
            self._cached("진행행사", "08. 20(목)"),
        ]
        bot, sender = self._bot(events)
        sender.edit_message = MagicMock(return_value=False)

        edited, deleted = bot.cleanup_expired_messages()

        self.assertEqual((edited, deleted), (0, 0))
        self.assertEqual(len(events[0]["messages"]), 1)


class MessageApiTest(unittest.TestCase):
    def setUp(self):
        self.sender = DiscordSender("https://discord.test/wh", "테스트")

    @patch("dev_event_bot.requests.post")
    def test_send_returns_message_id(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200, json=MagicMock(return_value={"id": "999"})
        )
        results = self.sender.send_digest_detailed([make_event()])

        self.assertEqual(results, [(True, "999")])
        self.assertEqual(mock_post.call_args.kwargs["params"], {"wait": "true"})

    @patch("dev_event_bot.requests.post")
    def test_send_failure_returns_none_id(self, mock_post):
        mock_post.return_value = MagicMock(status_code=400)
        self.assertEqual(self.sender.send_digest_detailed([make_event()]), [(False, None)])

    @patch("dev_event_bot.requests.patch")
    def test_edit_message_url_and_success(self, mock_patch):
        mock_patch.return_value = MagicMock(status_code=200)
        self.assertTrue(self.sender.edit_message("777", {"content": "x"}))
        self.assertEqual(
            mock_patch.call_args[0][0], "https://discord.test/wh/messages/777"
        )

    @patch("dev_event_bot.requests.patch")
    def test_edit_treats_missing_message_as_done(self, mock_patch):
        mock_patch.return_value = MagicMock(status_code=404)
        self.assertTrue(self.sender.edit_message("777", {"content": "x"}))

    @patch("dev_event_bot.requests.delete")
    def test_delete_message(self, mock_delete):
        mock_delete.return_value = MagicMock(status_code=204)
        self.assertTrue(self.sender.delete_message("777"))
        self.assertEqual(
            mock_delete.call_args[0][0], "https://discord.test/wh/messages/777"
        )

    @patch("dev_event_bot.requests.delete")
    def test_delete_already_removed_is_success(self, mock_delete):
        mock_delete.return_value = MagicMock(status_code=404)
        self.assertTrue(self.sender.delete_message("777"))

    def test_no_message_id_is_noop(self):
        self.assertFalse(self.sender.delete_message(""))
        self.assertFalse(self.sender.edit_message("", {}))


class CacheStoresDeadlineTest(unittest.TestCase):
    def test_mark_sent_records_deadline_and_messages(self):
        import tempfile, os, json
        from dev_event_bot import EventCache

        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(path)
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))

        cache = EventCache(cache_file=path, now=NOW)
        cache.mark_sent(
            make_event(),
            messages=[{"webhook": "테스트", "id": "1", "style": DEFAULT_DIGEST_STYLE}],
        )
        cache.save()

        with open(path, encoding="utf-8") as f:
            saved = json.load(f)
        entry = saved["events"][0]
        self.assertEqual(entry["deadline"], "2026-07-20")
        self.assertEqual(entry["messages"][0]["id"], "1")

    def test_mark_sent_without_deadline(self):
        import tempfile, os
        from dev_event_bot import EventCache

        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(path)
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))

        cache = EventCache(cache_file=path, now=NOW)
        cache.mark_sent(make_event(metadata=[]))
        self.assertIsNone(cache.events[0]["deadline"])
        self.assertEqual(cache.events[0]["messages"], [])


if __name__ == "__main__":
    unittest.main()
