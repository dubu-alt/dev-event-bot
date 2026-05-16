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


if __name__ == "__main__":
    unittest.main()
