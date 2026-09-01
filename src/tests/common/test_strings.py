from typing import Any

import pytest

from s21_slot_bot.common.strings import ensure_str


class TestStrings:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("hello", "hello"),
            (123, "123"),
            (False, "False"),
            (None, "-"),
        ],
    )
    def test_ensure_str_default_getter(self, value: Any, expected: str) -> None:
        assert ensure_str(value) == expected

    def test_ensure_str_uses_custom_default_for_none(self) -> None:
        assert ensure_str(None, default="N/A") == "N/A"

    def test_ensure_str_applies_getter_and_kwargs(self) -> None:
        result = ensure_str(
            "hello",
            getter=lambda value, prefix: f"{prefix}{value.upper()}",
            prefix="> ",
        )

        assert result == "> HELLO"

    def test_ensure_str_returns_default_if_getter_raises(self) -> None:
        def failing_getter(_: object) -> str:
            raise ValueError("boom")

        assert ensure_str("hello", getter=failing_getter) == "-"
