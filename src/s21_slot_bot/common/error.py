import json
from http import HTTPStatus
from typing import Any

from s21_slot_bot.common.http_status import get_http_status_description


class Error(Exception):
    default_help_text: str | None = None

    def __init__(
        self,
        message: str,
        status: HTTPStatus | int | None = None,
        location: dict[str, Any] | None = None,
        help_text: str | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.status = status
        self.location = location
        self.location_dump = json.dumps(location, ensure_ascii=False, indent=2)
        self.help_text = help_text or self.default_help_text

    def __str__(self) -> str:
        return f"message=`{self.message}`, status=`{self.effective_status}`, help=`{self.help_text}`\nlocation={self.location_dump}"

    @property
    def effective_status(self) -> HTTPStatus | int | None:
        if self.status is not None:
            return self.status
        cause = self.__cause__
        if isinstance(cause, Error):
            return cause.effective_status
        context = self.__context__
        if isinstance(context, Error):
            return context.effective_status
        return None

    def to_pretty(self) -> str:
        text = f"❌ {self.message}"
        if self.help_text:
            text += f"\nℹ️ {self.help_text}"
        if status := self.effective_status:
            status_description = get_http_status_description(status)
            text += f"\nстатус: {status} ({status_description})"
        return text


def get_error_description(exc: Exception) -> str:
    match exc:
        case Error():
            return exc.to_pretty()
        case _:
            error_type, error_message = type(exc).__name__, str(exc)
            description = f"{error_type}: {error_message}" if error_message else error_type
            return description
