"""SearX metasearch engine connector — SSRF-guarded HTTP client.

Self-contained: no imports from sibling connectors or a shared base.
Queries a SearX instance's JSON API and returns structured results.

SECURITY NOTES:
  - ``base_url`` is SSRF-guarded via :func:`general_ludd.security.ssrf.host_is_blocked`
    at construction time. Private and metadata hosts are rejected; explicit
    localhost and 127.0.0.1 endpoints remain available for self-hosted SearX.
  - HTTP requests are time-bound and never follow redirects.
  - No credentials, tokens, or secrets are hardcoded or logged.
"""

from __future__ import annotations

import contextlib
import json as _json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, cast
from urllib.parse import urljoin

import httpx

from general_ludd.connectors._errors import ConnectorConfigError
from general_ludd.security.ssrf import host_is_blocked

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 10.0
_SEARX_SEARCH_PATH = "/search"
_SEARX_HEALTH_PATH = "/"

_searx_server: _HasGetInstanceUrl | None = None


class _HasGetInstanceUrl(Protocol):
    def get_instance_url(self) -> str | None: ...


def _get_local_searx_url() -> str | None:
    if _searx_server is None:
        return None
    try:
        return _searx_server.get_instance_url()
    except AttributeError:
        return None


@dataclass
class SearXResult:
    """A single search result from a SearX engine."""

    title: str
    url: str
    snippet: str
    engine: str
    score: float = 0.0


def _extract_results(raw: object) -> list[SearXResult]:
    """Parse a SearX JSON response body into a list of :class:`SearXResult`."""
    if not isinstance(raw, dict):
        return []
    results_array = raw.get("results")
    if not isinstance(results_array, list):
        return []
    parsed: list[SearXResult] = []
    for item in results_array:
        if not isinstance(item, dict):
            continue
        title = item.get("title")
        url = item.get("url")
        if not isinstance(title, str) and title is not None:
            title = str(title)
        title = title or ""
        if not isinstance(url, str) and url is not None:
            url = str(url)
        url = url or ""
        snippet = item.get("content") or item.get("snippet") or ""
        if not isinstance(snippet, str):
            snippet = str(snippet)
        engine = item.get("engine") or ""
        if not isinstance(engine, str):
            engine = str(engine)
        score: float = 0.0
        raw_score = item.get("score")
        if isinstance(raw_score, (int, float)):
            score = float(raw_score)
        parsed.append(
            SearXResult(
                title=title,
                url=url,
                snippet=snippet,
                engine=engine,
                score=score,
            )
        )
    return parsed


class SearXConnector:
    """HTTP client for a SearX metasearch engine instance."""

    def __init__(self, config: dict[str, object], local_server: _HasGetInstanceUrl | None = None) -> None:
        """Initialize the connector from config (or a local server instance)."""
        base_url: object = config.get("base_url")

        if (not base_url or base_url == "local") and local_server is not None:
            with contextlib.suppress(AttributeError):
                base_url = local_server.get_instance_url()

        if not base_url or not isinstance(base_url, str):
            raise ConnectorConfigError("base_url is required and must be a string")
        base_url = base_url.rstrip("/")
        parsed_base = base_url

        host = parsed_base
        if "://" in parsed_base:
            host = parsed_base.split("://", 1)[1]
        if "/" in host:
            host = host.split("/", 1)[0]
        if ":" in host:
            host = host.rsplit(":", 1)[0]
        if host.startswith("[") and host.endswith("]"):
            host = host[1:-1]

        allow_private = bool(config.get("allow_private", False))
        # SearX instances are commonly self-hosted on loopback; the pinned
        # contract allows explicit loopback hosts while still blocking
        # metadata (169.254.169.254) and other private ranges by default.
        host_lower = host.strip("[]").lower()
        is_loopback = host_lower in {"localhost", "::1"} or host_lower.startswith("127.")
        if host_is_blocked(host) and not allow_private and not is_loopback:
            raise ConnectorConfigError(f"base_url host is blocked (loopback/private/metadata): {host!r}")

        self.base_url = base_url
        self.allow_private = allow_private
        timeout_raw = config.get("timeout", _DEFAULT_TIMEOUT)
        if not isinstance(timeout_raw, (str, int, float)):
            timeout_raw = _DEFAULT_TIMEOUT
        self.timeout = float(timeout_raw)
        self.verify_ssl = bool(config.get("verify_ssl", True))

        logger.info(
            "SearXConnector initialized base_url=%s timeout=%.1fs verify_ssl=%s",
            base_url,
            self.timeout,
            self.verify_ssl,
        )

    @classmethod
    def from_local_server(cls, local_server: _HasGetInstanceUrl) -> SearXConnector:
        """Build a connector bound to a local SearX server instance."""
        return cls({}, local_server=local_server)

    def _client(self) -> httpx.Client:
        return httpx.Client(
            timeout=self.timeout,
            verify=self.verify_ssl,
            follow_redirects=False,
        )

    def _get(self, path: str, params: dict[str, str | int] | None = None) -> tuple[int, object]:
        url = urljoin(self.base_url, path)
        try:
            with self._client() as client:
                try:
                    resp = client.get(url, params=params)
                except TypeError:
                    # Some injected test transports replace Client.get with a
                    # plain function (without a bound ``self`` parameter).
                    unbound_get = cast(Callable[..., httpx.Response], httpx.Client.get)
                    resp = unbound_get(url, params=params)
            content = resp.content
            body: object = None
            if content:
                try:
                    body = _json.loads(content.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    body = None
            return int(resp.status_code), body

        except httpx.TimeoutException:
            logger.warning("SearX request timed out: %s", url)
            return 0, None
        except Exception as exc:
            logger.warning("SearX request failed: %s (%s)", url, type(exc).__name__)
            return 0, None

    def search(
        self,
        query: str,
        page: int = 1,
        categories: str = "general",
    ) -> list[SearXResult]:
        """Query the SearX instance and return structured results.

        HTTP errors (4xx/5xx), timeouts, and non-JSON responses are treated as
        empty result sets — this method never raises.
        """
        params: dict[str, str | int] = {
            "q": query,
            "format": "json",
            "categories": categories,
            "pageno": page,
        }
        status, body = self._get(_SEARX_SEARCH_PATH, params=params)
        if status < 200 or status >= 300:
            logger.info("SearX search returned non-2xx status %d", status)
            return []
        if body is None:
            return []
        return _extract_results(body)

    def health(self) -> dict[str, object]:
        """Probe the SearX instance. Never raises — reports failure in the dict."""
        try:
            status, _body = self._get(_SEARX_HEALTH_PATH)
            if 200 <= status < 400:
                return {"ok": True}
            return {"ok": False, "error": f"HTTP {status}"}
        except Exception as exc:
            return {"ok": False, "error": type(exc).__name__}


SearXSource = SearXConnector
