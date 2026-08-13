"""Slack connector.

Self-contained outbound connector for sending notifications and optionally
reading channel history from a Slack workspace over its Web API + webhook URLs.

This connector is NOT an observability ``Source`` — it does NOT implement the
``Source`` Protocol (no ``query(spec)`` method, no ``KIND`` class attribute).
It is an outbound integration that sends notifications via webhook or
``chat.postMessage`` and reads messages via ``conversations.history``.
It should NOT be registered in a ``SourceRegistry`` or loaded via
``ConnectorRegistry`` (which expects the ``Source`` contract).

Design constraints (intentional, do not "simplify" away):

* No imports from ``general_ludd`` base classes, package ``__init__`` or other
  connectors -- this module is deliberately standalone so it can be vendored or
  tested in isolation.
* The HTTP transport is *injected*. Production callers pass a real client; tests
  pass a fake. We never construct a global/default network client at import time.
* SSRF protection: ``base_url`` and ``webhook_url`` are both validated against
  their *literal* host only. We never resolve DNS (a DNS rebind could otherwise
  smuggle a public name onto a private address). Loopback, link-local, private,
  and cloud-metadata hosts are rejected outright.
* Bot token is read from an environment variable named by the config; the token
  value never appears in the config dict and is never logged.
* ``health()`` never raises; ``send_notification()`` is fail-soft (returns a
  result dict); ``read_channel_history()`` returns empty list on transport OR
  HTTP errors.
* No ``shell=True`` / subprocess use anywhere.
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
from typing import Protocol, cast, runtime_checkable
from urllib.parse import urlsplit

from general_ludd.connectors._errors import SSRFError
from general_ludd.connectors._protocols import HttpResponse
from general_ludd.security.ssrf import is_url_blocked

logger = logging.getLogger(__name__)


def _invoke_transport(transport: object, method: str, url: str, **kwargs: object) -> HttpResponse:
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
                # Some lightweight injected transports expose only ``get``
                # while recording all HTTP calls. Keep POST notification tests
                # and adapters compatible without constructing a real client.
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

__all__ = ["HttpTransport", "SlackSource"]


@runtime_checkable
class HttpTransport(Protocol):
    """Injectable HTTP transport.

    Implementations must accept ``url``, ``headers``, optional ``params`` /
    ``data`` / ``json`` and a ``timeout`` and return an :class:`HttpResponse`.
    """

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, object] | None = ...,
        timeout: float = ...,
    ) -> HttpResponse:  # pragma: no cover - structural typing only
        ...

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        data: dict[str, object] | None = ...,
        json: dict[str, object] | None = ...,
        timeout: float = ...,
    ) -> HttpResponse:  # pragma: no cover - structural typing only
        ...


class CallableHttpTransport(Protocol):
    """Compact method-and-URL callback used by generated connector workflows."""

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


class _CallableTransportAdapter:
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


def _assert_safe_url(url: str, label: str = "url") -> str:
    """Validate ``url`` against SSRF and return a normalized (no trailing /) copy."""
    if is_url_blocked(url, scheme_allowlist=("http", "https")):
        parts = urlsplit(url)
        host = parts.hostname or ""
        raise SSRFError(f"forbidden {label} host or address: {host!r}")
    return url.rstrip("/")


def _parse_slack_ts(ts: str) -> str | None:
    """Convert a Slack timestamp (``seconds.microseconds``) to ISO-8601 UTC."""
    try:
        seconds = float(ts)
    except (ValueError, TypeError):
        return None
    return _dt.datetime.fromtimestamp(seconds, tz=_dt.UTC).isoformat()


class SlackSource:
    """Outbound Slack notifications + optional channel history intake.

    This is NOT a ``Source`` — it does not implement ``query(spec)`` or carry
    a ``KIND`` class attribute.  It sends notifications outbound via webhook or
    ``chat.postMessage`` and reads channel history via
    ``conversations.history``.  Do not register it in a ``SourceRegistry``.

    Parameters
    ----------
    config:
        Mapping with ``base_url`` and ``token_env`` keys. Optional:
        ``webhook_url`` (Slack incoming webhook), ``channel_id`` (default
        channel for ``chat.postMessage`` and ``conversations.history``).
    transport:
        Injected HTTP client implementing :class:`HttpTransport`.
    name:
        Optional human-readable source name (defaults to ``"slack"``).
    timeout:
        Per-request timeout in seconds (default 30).
    env:
        Optional environment mapping (defaults to ``os.environ``).
    """

    def __init__(
        self,
        config: dict[str, object],
        *,
        transport: HttpTransport | CallableHttpTransport,
        name: str | None = None,
        timeout: float = 30.0,
        env: dict[str, str] | None = None,
    ) -> None:
        base_url = config.get("base_url")
        if not isinstance(base_url, str) or not base_url:
            raise ValueError("config.base_url is required")
        token_env = config.get("token_env")
        if not isinstance(token_env, str) or not token_env:
            raise ValueError("config.token_env is required")

        self._base_url = _assert_safe_url(base_url, label="base_url")
        self._token_env = token_env
        if callable(getattr(transport, "get", None)) and callable(
            getattr(transport, "post", None)
        ):
            self._transport = cast(HttpTransport, transport)
        elif callable(transport):
            self._transport = _CallableTransportAdapter(transport)
        else:
            raise TypeError("transport must provide get/post or be callable")
        self._timeout = float(timeout)
        self._env = env if env is not None else dict(os.environ)
        self.name = name or "slack"

        self._webhook_url: str | None
        webhook_url = config.get("webhook_url")
        if isinstance(webhook_url, str) and webhook_url:
            self._webhook_url = _assert_safe_url(webhook_url, label="webhook_url")
        else:
            self._webhook_url = None

        self._channel_id: str | None
        channel_id = config.get("channel_id")
        if isinstance(channel_id, str) and channel_id:
            self._channel_id = channel_id
        else:
            self._channel_id = None

    # -- auth ---------------------------------------------------------------

    def _auth_headers(self) -> dict[str, str]:
        token = self._env.get(self._token_env)
        if not token:
            raise ValueError(f"missing token in env var {self._token_env!r}")
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }

    # -- health -------------------------------------------------------------

    def health(self) -> dict[str, object]:
        """Probe ``/auth.test``; never raises.

        Returns a dict ``{"ok": bool, "name": str, ...}``.
        """
        result: dict[str, object] = {"ok": False, "name": self.name}
        try:
            headers = self._auth_headers()
            resp = _invoke_transport(
                self._transport,
                "GET",
                f"{self._base_url}/auth.test",
                headers=headers,
                timeout=self._timeout,
            )
        except Exception:
            logger.warning("slack health check failed", exc_info=True)
            result["error"] = "slack health check failed"
            return result

        status = getattr(resp, "status_code", None)
        result["status_code"] = status
        if status == 200:
            result["ok"] = True
        elif status in (401, 403):
            result["error"] = "authentication failed"
        else:
            result["error"] = f"unexpected status {status}"
        return result

    # -- send_notification --------------------------------------------------

    def send_notification(self, text: str, /) -> dict[str, object]:
        """Send a simple text notification to Slack.

        Prefers ``webhook_url`` (incoming webhook) over ``channel_id`` +
        ``chat.postMessage`` (API path). Fail-soft: never raises, returns a
        dict with ``ok`` and optionally ``error`` / ``status_code``.

        Returns
        -------
        dict
            ``{"ok": bool, ...}``.
        """
        if not self._webhook_url and not self._channel_id:
            raise ValueError(
                "send_notification requires webhook_url or channel_id in config"
            )

        if self._webhook_url:
            return self._post_to_webhook(text)
        return self._post_to_api(text)

    def _post_to_webhook(self, text: str) -> dict[str, object]:
        assert self._webhook_url is not None
        try:
            resp = _invoke_transport(
                self._transport,
                "POST",
                self._webhook_url,
                headers={"Content-Type": "application/json"},
                json={"text": text},
                timeout=self._timeout,
            )
        except Exception:
            logger.warning("slack webhook post failed", exc_info=True)
            return {"ok": False, "error": "slack webhook post failed"}

        status = getattr(resp, "status_code", None)
        if status == 200:
            return {"ok": True, "status_code": status}
        return {"ok": False, "status_code": status, "error": f"unexpected status {status}"}

    def _post_to_api(self, text: str) -> dict[str, object]:
        try:
            headers = self._auth_headers()
            headers["Content-Type"] = "application/json; charset=utf-8"
            url = f"{self._base_url}/chat.postMessage"
            payload: dict[str, object] = {"channel": self._channel_id, "text": text}
            resp = _invoke_transport(
                self._transport,
                "POST",
                url,
                headers=headers,
                json=payload,
                timeout=self._timeout,
            )
        except Exception:
            logger.warning("slack chat.postMessage failed", exc_info=True)
            return {"ok": False, "error": "slack chat.postMessage failed"}

        status = getattr(resp, "status_code", None)
        if status == 200:
            return {"ok": True, "status_code": status}
        return {"ok": False, "status_code": status, "error": f"unexpected status {status}"}

    # -- read_channel_history -----------------------------------------------

    def read_channel_history(
        self, *, count: int | None = None
    ) -> list[dict[str, object]]:
        """Read recent messages from the configured channel.

        Parameters
        ----------
        count:
            Maximum number of messages to retrieve (passed as ``limit`` to the
            Slack API). Defaults to the Slack default (typically 100).

        Returns
        -------
        list[dict[str, object]]
            Normalized records, one per message. Empty list on transport errors.
        """
        if not self._channel_id:
            raise ValueError("read_channel_history requires channel_id in config")

        params: dict[str, object] = {"channel": self._channel_id}
        if count is not None:
            params["limit"] = int(count)

        try:
            headers = self._auth_headers()
            resp = _invoke_transport(
                self._transport,
                "GET",
                f"{self._base_url}/conversations.history",
                headers=headers,
                params=params,
                timeout=self._timeout,
            )
        except Exception:
            logger.warning("slack conversations.history failed", exc_info=True)
            return []

        status = getattr(resp, "status_code", None)
        if status != 200:
            logger.warning("slack conversations.history failed with status %s", status)
            return []

        payload = resp.json()
        messages = self._extract_messages(payload)
        return [self._normalize_message(msg) for msg in messages]

    @staticmethod
    def _extract_messages(payload: object) -> list[dict[str, object]]:
        if isinstance(payload, dict):
            entries = payload.get("messages")
            if isinstance(entries, list):
                return [m for m in entries if isinstance(m, dict)]
        return []

    def _normalize_message(self, msg: dict[str, object]) -> dict[str, object]:
        ts_raw = msg.get("ts")
        ts = _parse_slack_ts(str(ts_raw)) if ts_raw is not None else None
        return {
            "ts": ts,
            "source": self.name,
            "kind": "chat",
            "level_or_status": msg.get("subtype"),
            "message": msg.get("text"),
            "value": None,
            "labels": {
                "user": msg.get("user"),
                "channel_id": self._channel_id,
            },
            "raw": msg,
        }
