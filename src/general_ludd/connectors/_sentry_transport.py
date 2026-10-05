"""Transport contracts and adapters for the Sentry connector."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Protocol, cast, runtime_checkable

import httpx


class SentryResponse:
    """Minimal transport-agnostic HTTP response."""

    __slots__ = ("_body", "status")

    def __init__(self, status: int, body: bytes | str) -> None:
        self.status = status
        self._body = body

    @property
    def text(self) -> str:
        if isinstance(self._body, bytes):
            return self._body.decode("utf-8", errors="replace")
        return self._body

    def json(self) -> object:
        body = self.text.strip()
        return None if not body else json.loads(body)


@runtime_checkable
class Transport(Protocol):
    """Injected Sentry GET transport."""

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        timeout: float,
    ) -> SentryResponse: ...


class UrllibTransport:
    """Default httpx transport with redirect following disabled."""

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        timeout: float,
    ) -> SentryResponse:
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            response = client.get(url, headers=headers)
        return SentryResponse(status=response.status_code, body=response.content)


class CallableTransport:
    """Adapt the connector callback contract to :class:`Transport`."""

    def __init__(self, fn: object) -> None:
        self._fn = cast("Callable[..., object]", fn)

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        timeout: float,
    ) -> SentryResponse:
        result = self._fn("GET", url, headers=headers, timeout=timeout)
        if isinstance(result, tuple) and len(result) == 2:
            status, body = result
            if isinstance(body, (dict, list)):
                body = json.dumps(body)
            return SentryResponse(int(status), str(body))
        return cast(SentryResponse, result)


__all__ = ("CallableTransport", "SentryResponse", "Transport", "UrllibTransport")
