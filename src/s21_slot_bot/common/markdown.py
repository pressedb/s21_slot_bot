import re

from telegram.helpers import escape_markdown


# KNOWN LIMITATIONS:
# - can't handle nested mixed formats (e.g. https://core.telegram.org/bots/api#markdownv2-style)
# - This_project_name is treated as italics (should be manually escaped)
# taken from https://ithy.com/article/markdownv2-escaping-python-class-8yyfhi3j
class MarkdownV2Escaper:
    """
    A class to escape text for Telegram MarkdownV2 formatting.
    It ensures that only characters outside MarkdownV2 syntax are escaped.
    """

    def __init__(self) -> None:
        # Define special characters that need to be escaped in MarkdownV2
        self.special_chars = r"_*\[\]()~`>#+-=|{}.!"

        # Compile regex patterns for MarkdownV2 elements
        self.markdown_patterns = [
            r"\*[^*]+\*",  # Bold
            r"_[^_]+_",  # Italic
            r"__[^_]+__",  # Underline
            r"~[^~]+~",  # Strikethrough
            r"\|\|[^|]+\|\|",  # Spoiler
            r"\!*\[([^\]]+)\]\(([^)]+)\)",  # Inline URL / user mention / datetime
            r"`[^`]+`",  # Inline code
            r"(?:[^`]*?)```",  # Code blocks
            r"```python\n[\s\S]*?\n```",  # Python code blocks
        ]
        # Combine all patterns into a single regex
        self.combined_pattern = re.compile("|".join(self.markdown_patterns))

    def escape(self, text: str) -> str:
        """
        Escapes special characters in the text that are not part of MarkdownV2 syntax.

        Args:
            text (str): The input string with MarkdownV2 formatting.

        Returns:
            str: The escaped string safe for Telegram API.
        """
        if not text:
            return text

        # Find all MarkdownV2 syntax matches
        matches = list(self.combined_pattern.finditer(text))

        # Initialize variables
        escaped_text = []
        last_end = 0

        for match in matches:
            start, end = match.start(), match.end()
            # Escape non-Markdown text before the current match
            if last_end < start:
                non_markdown_part = text[last_end:start]
                escaped_non_markdown = self._escape_non_markdown(non_markdown_part)
                escaped_text.append(escaped_non_markdown)
            # Append the Markdown syntax without escaping
            escaped_text.append(match.group())
            last_end = end

        # Escape any remaining non-Markdown text after the last match
        if last_end < len(text):
            remaining_text = text[last_end:]
            escaped_remaining = self._escape_non_markdown(remaining_text)
            escaped_text.append(escaped_remaining)

        return "".join(escaped_text)

    def _escape_non_markdown(self, text: str) -> str:
        """
        Escapes special characters in non-Markdown text.

        Args:
            text (str): Text outside of Markdown syntax.

        Returns:
            str: Escaped text.
        """
        escaped = ""
        for char in text:
            if char in self.special_chars:
                escaped += "\\" + char
            else:
                escaped += char
        return escaped


def backtick_wrap(text: str) -> str:
    text = text.replace("`", "")
    return f"`{text}`"


def format_inline_link(text: str, url: str) -> str:
    escaped_text = escape_markdown(text, version=2)
    inline = f"[{escaped_text}]({url})"
    return inline
