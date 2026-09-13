"""Small validated seams for proposal-callback compatibility and deadlines."""

from __future__ import annotations

import inspect
import math
from collections.abc import Callable, Mapping
from typing import TypeVar

_Result = TypeVar("_Result")


def accepts_keyword(callback: Callable[..., object], keyword: str) -> bool:
    """Return whether one trusted callback can receive one named keyword."""
    if not isinstance(keyword, str) or not keyword.isidentifier():
        return False
    try:
        parameters = inspect.signature(callback).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        or (
            parameter.name == keyword
            and parameter.kind
            in {inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY}
        )
        for parameter in parameters
    )


def accepts_timeout_keyword(callback: Callable[..., object]) -> bool:
    """Return whether one trusted callback can receive ``timeout_seconds``."""
    return accepts_keyword(callback, "timeout_seconds")


def validated_timeout_seconds(value: object) -> float:
    """Return one exact finite candidate timeout in the supported interval."""
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(result := float(value))
        or not 0.0 < result <= 3_600.0
    ):
        raise ValueError("timeout_seconds must be finite and in (0, 3600]")
    return result


def invoke_with_optional_timeout(
    callback: Callable[..., _Result],
    arguments: tuple[object, ...],
    *,
    timeout_seconds: float,
) -> _Result:
    """Invoke a trusted callback without breaking its legacy call shape."""
    if accepts_timeout_keyword(callback):
        return callback(*arguments, timeout_seconds=timeout_seconds)
    return callback(*arguments)


def invoke_with_supported_keywords(
    callback: Callable[..., _Result],
    arguments: tuple[object, ...],
    optional_keywords: Mapping[str, object],
) -> _Result:
    """Invoke with only optional keywords declared by the callback contract."""
    supported = {
        name: value
        for name, value in optional_keywords.items()
        if accepts_keyword(callback, name)
    }
    return callback(*arguments, **supported)


__all__ = [
    "accepts_keyword",
    "accepts_timeout_keyword",
    "invoke_with_optional_timeout",
    "invoke_with_supported_keywords",
    "validated_timeout_seconds",
]
