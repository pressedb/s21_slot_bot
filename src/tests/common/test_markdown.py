import pytest

from s21_slot_bot.common import markdown
from s21_slot_bot.common.markdown import MarkdownV2Escaper


class TestMarkdown:
    @pytest.fixture
    def escaper(self) -> MarkdownV2Escaper:
        return MarkdownV2Escaper()

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("plain text", "plain text"),
            ("hello!", r"hello\!"),
            ("a+b=c", r"a\+b\=c"),
            ("file.py", r"file\.py"),
            ("#tag", r"\#tag"),
            ("", ""),
        ],
    )
    def test_escape_plain_text(self, escaper: MarkdownV2Escaper, text: str, expected: str) -> None:
        assert escaper.escape(text) == expected

    @pytest.mark.parametrize(
        "text",
        [
            "*bold*",
            "_italic_",
            "__underline__",
            "~strikethrough~",
            "||spoiler||",
            "[link](https://example.com)",
            "`code`",
            "![time](tg://time?unix=1647531900)",
        ],
    )
    def test_escape_preserves_supported_markdown_entities(
        self,
        escaper: MarkdownV2Escaper,
        text: str,
    ) -> None:
        assert escaper.escape(text) == text

    def test_escape_mixed_plain_text_and_supported_entity(self, escaper: MarkdownV2Escaper) -> None:
        assert escaper.escape("Result: *done*!") == r"Result: *done*\!"

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("hello", "`hello`"),
            ("``already ``contains `backticks```", "`already contains backticks`"),
        ],
    )
    def test_backtick_wrap(self, value: str, expected: str) -> None:
        assert markdown.backtick_wrap(value) == expected
