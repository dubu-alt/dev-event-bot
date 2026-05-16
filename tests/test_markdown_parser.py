import unittest

from dev_event_bot import MarkdownParser


class MarkdownParserTest(unittest.TestCase):
    def test_parse_inline_dev_event_format(self):
        content = (
            "## `26년 05월` "
            "- __[CloudBro 1주년 행사](https://ticketa.co/event/dttikon7)__ "
            "- 분류: `오프라인(서울 강남구)`, `유료`, `모임`, `클라우드` "
            "- 주최: CloudBro "
            "- 접수: 04. 24(목) ~ 05. 12(화) "
            "- __[두번째 행사](https://example.com/event)__ "
            "- 분류: `온라인`, `무료` "
            "- 일시: 05. 19(화)\n"
        )

        events = MarkdownParser.parse_events(content)

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["title"], "CloudBro 1주년 행사")
        self.assertEqual(events[0]["url"], "https://ticketa.co/event/dttikon7")
        self.assertEqual(events[0]["month"], "26년 05월")
        self.assertEqual(
            events[0]["metadata"],
            [
                "분류: `오프라인(서울 강남구)`, `유료`, `모임`, `클라우드`",
                "주최: CloudBro",
                "접수: 04. 24(목) ~ 05. 12(화)",
            ],
        )
        self.assertEqual(events[1]["metadata"], ["분류: `온라인`, `무료`", "일시: 05. 19(화)"])

    def test_parse_legacy_multiline_format(self):
        content = """## `26년 06월`
* **[옛날 행사](https://old.example)**
  + 분류: `온라인`, `무료`, `모임`
  + 주최: 기관명
  + 접수: 03. 01(월) ~ 03. 31(일)
"""

        events = MarkdownParser.parse_events(content)

        self.assertEqual(
            events,
            [
                {
                    "title": "옛날 행사",
                    "url": "https://old.example",
                    "month": "26년 06월",
                    "metadata": [
                        "분류: `온라인`, `무료`, `모임`",
                        "주최: 기관명",
                        "접수: 03. 01(월) ~ 03. 31(일)",
                    ],
                }
            ],
        )

    def test_parse_compacted_live_readme_format_with_nested_brackets(self):
        content = (
            "## `26년 05월` "
            "- __[AWSKRUG 보안 #Security 소모임 Security Night](https://www.meetup.com/awskrug/events/314639723/)__ "
            "- 분류: `오프라인(서울 강남구)`, `유료`, `모임`, `보안`, `클라우드` "
            "- 주최: AWSKRUG "
            "- 접수: 05. 09(토) ~ 05.\n17(일) "
            "- __[Seoul iOS Meetup [May 2026]](https://luma.com/ebg1nvg1)__ "
            "- 분류: `오프라인(서울 강남구)`, `무료`, `iOS` "
            "- 주최: Seoul iOS Meetup "
            "- 접수: 05. 06(수) ~ 05.\n20(수)\n"
            "## `26년 06월` "
            "- __[The Turing Test Hackathon 2026 — Phase 2.\n"
            "AI Awakening Hackathon](https://dorahacks.io/hackathon/mantleturingtesthackathon2026/detail)__ "
            "- 분류: `온라인`, `무료`, `대회`, `블록체인`, `AI` "
            "- 주최: Mantle / Bybit / Byreal / BGA "
            "- 접수: 05. 01(목) ~ 06. 15(월)"
        )

        events = MarkdownParser.parse_events(content)

        self.assertEqual(len(events), 3)
        self.assertEqual(events[1]["title"], "Seoul iOS Meetup [May 2026]")
        self.assertEqual(events[1]["metadata"][-1], "접수: 05. 06(수) ~ 05. 20(수)")
        self.assertEqual(
            events[2]["title"],
            "The Turing Test Hackathon 2026 — Phase 2. AI Awakening Hackathon",
        )


if __name__ == "__main__":
    unittest.main()
