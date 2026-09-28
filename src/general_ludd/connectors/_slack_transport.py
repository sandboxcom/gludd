"""Transport adapters and URL validation for the Slack connector."""

from __future__ import annotations

import datetime as dt
from typing import Protocol, cast, runtime_checkable
from urllib.parse import urlsplit

from general_ludd.connectors._errors import SSRFError
from general_ludd.connectors._protocols import HttpResponse
from general_ludd.security.ssrf import is_url_blocked


def invoke_transport(
    transport: object,
    method: str,
    url: str,
    **kwargs: object,
) -> HttpResponse:
    """Support object-, request-, and callable-style injected transports."""
    fn = getattr(transport, method.lower(), None)
    if callable(fn):
        result = fn(url, **kwargs)
    else:
        request = getattr(transport, "request", None)
        if callable(request):
            result = request(method, url, **kwargs)
        else:
            get = getattr(transport, "get", None)
            if method.lower() != "get" and callable(get):
                result = get(url, **kwargs)
            elif callable(transport):
                result = transport(method, url, **kwargs)
            else:
                raise TypeError("transport must expose get/post/request or be callable")
    if isinstance(result, tuple) and len(result) == 2:

        class _TupleResponse:
            status_code = int(result[0]) if isinstance(result[0], int) else 0

            def json(self) -> object:
                return result[1]

        return cast(HttpResponse, _TupleResponse())
    return cast(HttpResponse, result)


@runtime_checkable
class HttpTransport(Protocol):
    """Injected response-object transport used by Slack operations."""

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, object] | None = ...,
        timeout: float = ...,
    ) -> HttpResponse: ...

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        data: dict[str, object] | None = ...,
        json: dict[str, object] | None = ...,
        timeout: float = ...,
    ) -> HttpResponse: ...


class CallableHttpTransport(Protocol):
    """Compact method-and-URL callback used by generated workflows."""

    def __call__(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, object] | None = ...,
        data: dict[str, object] | None = ...,
        json: dict[str, object] | None = ...,
        timeout: float = ...,
    ) -> tuple[int, object]: ...


class _CallbackResponse:
    def __init__(self, status_code: int, body: object) -> None:
        self.status_code = int(status_code)
        self._body = body

    @property
    def text(self) -> str:
        return self._body if isinstance(self._body, str) else str(self._body)

    def json(self) -> object:
        return self._body


class CallableTransportAdapter:
    """Expose a compact callback through Slack's response-object transport."""

    def __init__(self, callback: CallableHttpTransport) -> None:
        self._callback = callback

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, object] | None = None,
        timeout: float = 30.0,
    ) -> HttpResponse:
        status, body = self._callback(
            "GET",
            url,
            headers=headers,
            params=params,
            timeout=timeout,
        )
        return _CallbackResponse(status, body)

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        data: dict[str, object] | None = None,
        json: dict[str, object] | None = None,
        timeout: float = 30.0,
    ) -> HttpResponse:
        status, body = self._callback(
            "POST",
            url,
            headers=headers,
            data=data,
            json=json,
            timeout=timeout,
        )
        return _CallbackResponse(status, body)


def assert_safe_url(url: str, label: str = "url") -> str:
    """Validate an HTTP URL against SSRF policy and remove its trailing slash."""
    if is_url_blocked(url, scheme_allowlist=("http", "https")):
        host = urlsplit(url).hostname or ""
        raise SSRFError(f"forbidden {label} host or address: {host!r}")
    return url.rstrip("/")


def parse_slack_ts(ts: str) -> str | None:
    """Convert a Slack timestamp to ISO-8601 UTC."""
    try:
        seconds = float(ts)
    except (ValueError, TypeError):
        return None
    return dt.datetime.fromtimestamp(seconds, tz=dt.UTC).isoformat()


__all__ = (
    "CallableHttpTransport",
    "CallableTransportAdapter",
    "HttpTransport",
    "assert_safe_url",
    "invoke_transport",
    "parse_slack_ts",
)
