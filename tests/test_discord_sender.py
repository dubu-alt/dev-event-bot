import unittest
from unittest.mock import patch, MagicMock

from dev_event_bot import (
    COLOR_INFO,
    DiscordSender,
    MAX_EMBEDS_PER_MESSAGE,
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


class SendDigestTest(unittest.TestCase):
    def _sender(self):
        return DiscordSender("https://discord.test/webhook", "테스트")

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
        sender = DiscordSender("", "빈웹훅")
        self.assertEqual(sender.send_digest([make_event()]), [False])


if __name__ == "__main__":
    unittest.main()
