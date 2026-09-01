from collections.abc import Callable
from typing import Any


def ensure_str(field: Any, getter: Callable[..., str] | None = None, default: str = "-", **kwargs: Any) -> str:
    getter = getter if getter is not None else lambda _: field
    try:
        value = getter(field, **kwargs)
        if value is None:
            return default
        return str(value)
    except Exception:  # noqa: BLE001
        return default
