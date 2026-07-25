import unittest
from unittest.mock import patch, MagicMock

from dev_event_bot import (
    COLOR_INFO,
    DEFAULT_DIGEST_STYLE,
    DIGEST_STYLE_COMPACT,
    DIGEST_STYLE_RICH,
    DiscordSender,
    MAX_EMBEDS_PER_MESSAGE,
    MAX_EVENTS_PER_COMPACT_MESSAGE,
    chunk_events,
    get_digest_style,
)


def make_event(**overrides):
    event = {
        "title": "테스트 행사",
        "url": "https://example.com/event/1",
        "month": "26년 07월",
        "metadata": [
            "분류: `오프라인(서울)`, `무료`, `대회`, `AI`",
            "주최: 테스트 주최사",
            "접수: 07. 01(수) ~ 07. 20(월)",
        ],
    }
    event.update(overrides)
    return event


class CategoryColorTest(unittest.TestCase):
    def test_competition_is_red(self):
        self.assertEqual(DiscordSender._category_color(make_event()), 15158332)

    def test_seminar_is_green(self):
        event = make_event(metadata=["분류: `온라인`, `세미나`"])
        self.assertEqual(DiscordSender._category_color(event), 3066993)

    def test_meetup_is_blue(self):
        event = make_event(metadata=["분류: `온라인`, `모임`"])
        self.assertEqual(DiscordSender._category_color(event), 3447003)

    def test_priority_competition_over_meetup(self):
        event = make_event(metadata=["분류: `모임`, `대회`"])
        self.assertEqual(DiscordSender._category_color(event), 15158332)

    def test_unknown_category_falls_back_to_info(self):
        event = make_event(metadata=["분류: `온라인`, `기술일반`"])
        self.assertEqual(DiscordSender._category_color(event), COLOR_INFO)

    def test_no_metadata_falls_back_to_info(self):
        event = make_event(metadata=[])
        self.assertEqual(DiscordSender._category_color(event), COLOR_INFO)


class CreateEmbedTest(unittest.TestCase):
    def test_structured_fields(self):
        embed = DiscordSender._create_embed(make_event())
        field_names = [f["name"] for f in embed["fields"]]
        self.assertEqual(field_names, ["분류", "주최", "접수", "시기"])

        by_name = {f["name"]: f for f in embed["fields"]}
        self.assertEqual(by_name["주최"]["value"], "테스트 주최사")
        self.assertFalse(by_name["분류"]["inline"])
        self.assertTrue(by_name["주최"]["inline"])
        self.assertNotIn("description", embed)

    def test_unknown_metadata_goes_to_description(self):
        event = make_event(metadata=["분류: `온라인`", "비고 없는 텍스트"])
        embed = DiscordSender._create_embed(event)
        self.assertEqual(embed["description"], "비고 없는 텍스트")

    def test_title_url_and_month(self):
        embed = DiscordSender._create_embed(make_event())
        self.assertEqual(embed["title"], "테스트 행사")
        self.assertEqual(embed["url"], "https://example.com/event/1")
        self.assertEqual(embed["fields"][-1], {
            "name": "시기", "value": "26년 07월", "inline": True,
        })


class DigestStyleTest(unittest.TestCase):
    def test_default_is_compact(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(get_digest_style(), DIGEST_STYLE_COMPACT)
        self.assertEqual(DEFAULT_DIGEST_STYLE, DIGEST_STYLE_COMPACT)

    def test_env_can_select_rich(self):
        with patch.dict("os.environ", {"DIGEST_STYLE": "RICH"}, clear=True):
            self.assertEqual(get_digest_style(), DIGEST_STYLE_RICH)

    def test_unknown_env_falls_back_to_default(self):
        with patch.dict("os.environ", {"DIGEST_STYLE": "fancy"}, clear=True):
            self.assertEqual(get_digest_style(), DEFAULT_DIGEST_STYLE)


class CompactEmbedTest(unittest.TestCase):
    def test_summary_prefers_held_at_over_apply(self):
        event = make_event(metadata=[
            "분류: `온라인`, `무료`, `모임`",
            "주최: 바이브 코딩 클럽",
            "일시: 7. 25(토) 14:00 - 18:00",
            "접수: 07. 01(수) ~ 07. 20(월)",
        ])
        self.assertEqual(
            DiscordSender._compact_summary(event),
            "7. 25(토) 14:00 - 18:00 · 온라인 · 무료 · 모임 · 바이브 코딩 클럽",
        )

    def test_summary_falls_back_to_apply_then_month(self):
        applying = make_event(metadata=["접수: 07. 17(금) ~ 08. 03(월)"])
        self.assertEqual(
            DiscordSender._compact_summary(applying),
            "접수 07. 17(금) ~ 08. 03(월)",
        )

        month_only = make_event(metadata=[], month="26년 07월")
        self.assertEqual(DiscordSender._compact_summary(month_only), "26년 07월")

    def test_line_escapes_brackets_in_title(self):
        event = make_event(title="[온라인] 7월 바이브 코드 러시")
        line = DiscordSender._compact_line(event)
        self.assertIn(r"**[\[온라인\] 7월 바이브 코드 러시](https://example.com/event/1)**", line)

    def test_line_has_emoji_by_category(self):
        self.assertTrue(DiscordSender._compact_line(make_event()).startswith("🔴"))
        seminar = make_event(metadata=["분류: `온라인`, `세미나`"])
        self.assertTrue(DiscordSender._compact_line(seminar).startswith("🟢"))
        unknown = make_event(metadata=["분류: `온라인`, `기술일반`"])
        self.assertTrue(DiscordSender._compact_line(unknown).startswith("⚪"))

    def test_embed_is_single_description_list(self):
        events = [make_event(title=f"행사{i}") for i in range(3)]
        embed = DiscordSender._create_compact_embed(events)

        self.assertNotIn("fields", embed)
        self.assertEqual(embed["footer"], {"text": "Dev-Event Bot"})
        for i in range(3):
            self.assertIn(f"행사{i}", embed["description"])

    def test_embed_color_uses_highest_priority_category(self):
        events = [
            make_event(metadata=["분류: `온라인`, `모임`"]),
            make_event(metadata=["분류: `온라인`, `대회`"]),
        ]
        self.assertEqual(DiscordSender._create_compact_embed(events)["color"], 15158332)


class ChunkEventsTest(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(chunk_events([], DIGEST_STYLE_COMPACT), [])
        self.assertEqual(chunk_events([], DIGEST_STYLE_RICH), [])

    def test_rich_splits_by_embed_limit(self):
        events = [make_event() for _ in range(25)]
        chunks = chunk_events(events, DIGEST_STYLE_RICH)
        self.assertEqual([len(c) for c in chunks], [10, 10, 5])

    def test_compact_splits_by_event_count(self):
        events = [make_event(title=f"행사{i}") for i in range(45)]
        chunks = chunk_events(events, DIGEST_STYLE_COMPACT)
        self.assertEqual([len(c) for c in chunks], [20, 20, 5])

    def test_compact_splits_by_description_budget(self):
        # 건수 한도(20)에는 못 미치지만 description 예산을 넘기는 긴 행사들
        events = [
            make_event(
                title=f"긴 제목 행사 {i} " + "가" * 240,
                url="https://example.com/" + "e" * 200 + f"/{i}",
                metadata=[f"주최: {'나' * 150}"],
            )
            for i in range(10)
        ]
        chunks = chunk_events(events, DIGEST_STYLE_COMPACT)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            description = DiscordSender._create_compact_embed(chunk)["description"]
            self.assertLessEqual(len(description), 4096)


class SendCompactDigestTest(unittest.TestCase):
    def _sender(self):
        return DiscordSender(
            "https://discord.test/webhook", "테스트", style=DIGEST_STYLE_COMPACT
        )

    @patch("dev_event_bot.requests.post")
    def test_many_events_become_one_embed(self, mock_post):
        mock_post.return_value = MagicMock(status_code=204)
        events = [make_event(title=f"행사{i}") for i in range(8)]

        results = self._sender().send_digest(events)

        self.assertEqual(results, [True] * 8)
        self.assertEqual(mock_post.call_count, 1)
        payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(len(payload["embeds"]), 1)
        self.assertEqual(payload["content"], "📅 새 개발자 행사 8건")

    @patch("dev_event_bot.requests.post")
    def test_splits_past_event_limit(self, mock_post):
        mock_post.return_value = MagicMock(status_code=204)
        events = [make_event(title=f"행사{i}") for i in range(MAX_EVENTS_PER_COMPACT_MESSAGE + 3)]

        results = self._sender().send_digest(events)

        self.assertEqual(results, [True] * (MAX_EVENTS_PER_COMPACT_MESSAGE + 3))
        self.assertEqual(mock_post.call_count, 2)
        self.assertIn("(1/2)", mock_post.call_args_list[0].kwargs["json"]["content"])

    @patch("dev_event_bot.requests.post")
    def test_failure_marks_only_its_chunk(self, mock_post):
        mock_post.side_effect = [
            MagicMock(status_code=204),
            MagicMock(status_code=400),
        ]
        events = [make_event(title=f"행사{i}") for i in range(MAX_EVENTS_PER_COMPACT_MESSAGE + 3)]

        results = self._sender().send_digest(events)

        self.assertEqual(results[:MAX_EVENTS_PER_COMPACT_MESSAGE], [True] * MAX_EVENTS_PER_COMPACT_MESSAGE)
        self.assertEqual(results[MAX_EVENTS_PER_COMPACT_MESSAGE:], [False] * 3)


class SendDigestTest(unittest.TestCase):
    def _sender(self):
        return DiscordSender(
            "https://discord.test/webhook", "테스트", style=DIGEST_STYLE_RICH
        )

    @patch("dev_event_bot.requests.post")
    def test_single_message_under_limit(self, mock_post):
        mock_post.return_value = MagicMock(status_code=204)
        events = [make_event(title=f"행사{i}") for i in range(4)]

        results = self._sender().send_digest(events)

        self.assertEqual(results, [True] * 4)
        self.assertEqual(mock_post.call_count, 1)
        payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(len(payload["embeds"]), 4)
        self.assertEqual(payload["content"], "📅 새 개발자 행사 4건")

    @patch("dev_event_bot.requests.post")
    def test_splits_into_multiple_messages(self, mock_post):
        mock_post.return_value = MagicMock(status_code=204)
        events = [make_event(title=f"행사{i}") for i in range(25)]

        results = self._sender().send_digest(events)

        self.assertEqual(results, [True] * 25)
        self.assertEqual(mock_post.call_count, 3)
        first_payload = mock_post.call_args_list[0].kwargs["json"]
        last_payload = mock_post.call_args_list[-1].kwargs["json"]
        self.assertEqual(len(first_payload["embeds"]), MAX_EMBEDS_PER_MESSAGE)
        self.assertEqual(len(last_payload["embeds"]), 5)
        self.assertIn("(1/3)", first_payload["content"])
        self.assertIn("(3/3)", last_payload["content"])

    @patch("dev_event_bot.requests.post")
    def test_failed_chunk_marks_only_its_events(self, mock_post):
        mock_post.side_effect = [
            MagicMock(status_code=204),
            MagicMock(status_code=400),
        ]
        events = [make_event(title=f"행사{i}") for i in range(15)]

        results = self._sender().send_digest(events)

        self.assertEqual(results[:10], [True] * 10)
        self.assertEqual(results[10:], [False] * 5)

    def test_no_webhook_returns_all_false(self):
        sender = DiscordSender("", "빈웹훅", style=DIGEST_STYLE_RICH)
        self.assertEqual(sender.send_digest([make_event()]), [False])


if __name__ == "__main__":
    unittest.main()
