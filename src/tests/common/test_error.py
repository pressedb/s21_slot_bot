from http import HTTPStatus

import pytest

from s21_slot_bot.common.error import Error


class DefaultHelpError(Error):
    default_help_text = "default help"


class TestError:
    def test_effective_status_returns_own_status(self) -> None:
        error = Error("oops", status=HTTPStatus.BAD_REQUEST)

        assert error.effective_status == HTTPStatus.BAD_REQUEST

    def test_effective_status_inherits_explicit_cause(self) -> None:
        cause = Error("not found", status=HTTPStatus.NOT_FOUND)

        with pytest.raises(Error) as exc_info:
            raise Error("wrapped") from cause

        assert exc_info.value.effective_status == HTTPStatus.NOT_FOUND

    def test_effective_status_inherits_implicit_context(self) -> None:
        with pytest.raises(Error) as exc_info:
            try:
                raise Error("not found", status=HTTPStatus.NOT_FOUND)
            except Error:
                raise Error("wrapped")

        assert exc_info.value.effective_status == HTTPStatus.NOT_FOUND

    def test_effective_status_prefers_own_status_over_cause(self) -> None:
        cause = Error("not found", status=HTTPStatus.NOT_FOUND)

        with pytest.raises(Error) as exc_info:
            raise Error("wrapped", status=HTTPStatus.INTERNAL_SERVER_ERROR) from cause

        assert exc_info.value.effective_status == HTTPStatus.INTERNAL_SERVER_ERROR

    def test_default_help_text_is_taken_from_error_class(self) -> None:
        error = DefaultHelpError("oops")

        assert error.help_text == "default help"

    def test_explicit_help_text_overrides_class_default(self) -> None:
        error = DefaultHelpError("oops", help_text="custom help")

        assert error.help_text == "custom help"

    @pytest.mark.parametrize(
        ("status", "help_text", "expected"),
        [
            (None, None, "❌ oops"),
            (None, "try again", "❌ oops\nℹ️ try again"),
            (
                HTTPStatus.NOT_FOUND,
                None,
                "❌ oops\nстатус: 404 (Not Found)",
            ),
            (
                HTTPStatus.NOT_FOUND,
                "try again",
                "❌ oops\nℹ️ try again\nстатус: 404 (Not Found)",
            ),
            (
                521,
                "cloudflare!",
                "❌ oops\nℹ️ cloudflare!\nстатус: 521 (ошибка сервера)",
            ),
            (
                666,
                "what is this?",
                "❌ oops\nℹ️ what is this?\nстатус: 666 (неизвестная ошибка)",
            ),
        ],
    )
    def test_to_pretty(
        self,
        status: HTTPStatus | int | None,
        help_text: str | None,
        expected: str,
    ) -> None:
        error = Error("oops", status=status, help_text=help_text)

        assert error.to_pretty() == expected

    def test_str_contains_serialized_location(self) -> None:
        error = Error(
            "oops",
            status=HTTPStatus.BAD_REQUEST,
            location={"operation": "getSomething", "input": {"id": 42}},
        )

        assert str(error) == (
            "message=`oops`, status=`400`, help=`None`\n"
            'location={\n  "operation": "getSomething",\n  "input": {\n    "id": 42\n  }\n}'
        )
