"""Graylog observability connector (logs source).

Self-contained, dependency-light connector that runs a Graylog *universal*
search via the REST API and normalizes each returned message into a flat,
uniform record. It is deliberately standalone — its only ``general_ludd``
dependency is the canonical shared SSRF guard
(``general_ludd.security.ssrf``), the single source of truth every guard must
funnel through; it imports no base class, no package ``__init__`` and no sibling
connectors, so it can be vendored or audited in near-isolation.

CONTRACT
========
- class ``GraylogSource``
- ``KIND = "logs"`` (class attribute) and per-instance ``kind`` on records
- ``__init__(config)`` where ``config`` is a mapping with:
    - ``base_url``   — Graylog REST base, e.g. ``https://graylog.example.com``
    - ``token_env``  — name of the env var holding the API token
  Optional: ``name`` (defaults to ``"graylog"``), ``timeout`` seconds.
- ``name`` instance attribute (human label for this source)
- ``health() -> dict`` — NEVER raises; returns ``{"ok": bool, ...}``
- ``query(spec) -> list[dict]`` — normalized records with keys:
    ``ts, source, kind, level_or_status, message, value, labels, raw``

SECURITY
========
- SSRF defense is a *literal-host* block performed on the configured
  ``base_url`` host BEFORE any request: loopback, link-local, private,
  unique-local, and the cloud metadata IP (169.254.169.254) are rejected.
  No DNS resolution is performed (defense is purely on the literal host as
  written — a hostname that is not an IP literal is allowed through, since
  resolving it would itself be an SSRF vector and is out of scope here).
- HTTP transport is INJECTABLE (``transport=`` constructor arg). The default
  transport uses ``httpx`` with a bounded timeout. No ``shell=True``, no
  subprocess, no ``eval``.
- Auth is HTTP Basic with username = the API token and password = the
  literal string ``"token"`` (Graylog's token-auth convention), read from
  the environment variable named by ``token_env``.
"""

from __future__ import annotations

import base64
import logging
import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Protocol, TypedDict, cast
from urllib.parse import urlsplit

from general_ludd.connectors._protocols import HttpResponse
from general_ludd.security.ssrf import is_url_blocked

logger = logging.getLogger(__name__)

# Default request timeout (seconds) — every outbound call is time-bounded so a
# slow/hung Graylog can never block a caller indefinitely.
DEFAULT_TIMEOUT: float = 15.0

# Syslog severity level (RFC 5424) integer -> canonical name. Graylog stores the
# numeric level in ``message.fields.level``; we map it to a stable lowercase name.
SYSLOG_LEVEL_NAMES: dict[int, str] = {
    0: "emergency",
    1: "alert",
    2: "critical",
    3: "error",
    4: "warning",
    5: "notice",
    6: "info",
    7: "debug",
}


class GraylogConfig(TypedDict, total=False):
    """Configuration mapping accepted by :class:`GraylogSource`.

    ``base_url`` and ``token_env`` are morally required (the constructor raises
    if either is missing); they are declared optional here only so callers may
    construct the dict incrementally. ``name`` and ``timeout`` are genuine
    optional overrides with safe defaults.
    """

    base_url: str
    token_env: str
    name: str
    timeout: float | int | str


# ``GraylogQuerySpec`` is declared via the functional form because ``from`` and
# ``to`` are Python keywords and cannot appear as attribute names in the class
# body of a class-based TypedDict. All fields are optional (total=False);
# defaults are applied in :meth:`GraylogSource._build_search_request`.
# ``range`` / ``limit`` accept int or str because operator-supplied YAML/JSON
# config frequently carries them as strings; they are coerced to int at use time.
GraylogQuerySpec = TypedDict(
    "GraylogQuerySpec",
    {
        "query": str,
        "range": int | str,
        "limit": int | str,
        "from": str,
        "to": str,
    },
    total=False,
)


class GraylogRecord(TypedDict):
    """One normalized Graylog log record (the connector's 8-key shape).

    Note: ``ts`` is the ISO-8601 string emitted by Graylog (not epoch float),
    and ``source`` / ``message`` are ``object`` because the message-field map
    is heterogeneous and only isinstance-narrowed at the boundary. ``value``
    is always ``None`` for log records — the field exists to satisfy the
    shared 8-key normalized-record shape consumed by the Observability facade.
    """

    ts: str | None
    source: object
    kind: str
    level_or_status: str | None
    message: object
    value: None
    labels: dict[str, object]
    raw: dict[str, object]


class _Transport(Protocol):
    """Injectable HTTP transport.

    A transport is any callable that performs one GET request and returns a
    ``HttpResponse``. The default implementation wraps ``httpx``; tests pass a
    canned callable so no socket is ever opened.
    """

    def __call__(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        params: Mapping[str, str | int] | None,
        timeout: float,
    ) -> HttpResponse: ...


def _default_transport(
    url: str,
    *,
    headers: Mapping[str, str],
    params: Mapping[str, str | int] | None,
    timeout: float,
) -> HttpResponse:
    """Real GET via httpx with a bounded timeout (imported lazily).

    httpx is imported inside the function so that importing this module (and
    running its tests with a mocked transport) never requires httpx to be
    installed or imported.
    """
    import httpx

    resp = httpx.get(
        url,
        headers=dict(headers),
        params=dict(params or {}),
        timeout=timeout,
        follow_redirects=False,
    )
    return resp


class GraylogSource:
    """Graylog logs connector.

    Example
    -------
    >>> src = GraylogSource({"base_url": "https://graylog.example", "token_env": "GL_TOKEN"})
    >>> src.KIND
    'logs'
    """

    KIND: str = "logs"

    def __init__(
        self,
        config: GraylogConfig,
        *,
        transport: _Transport | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        base_url = str(config.get("base_url", "")).rstrip("/")
        if not base_url:
            raise ValueError("GraylogSource: config['base_url'] is required")
        token_env = str(config.get("token_env", "")).strip()
        if not token_env:
            raise ValueError("GraylogSource: config['token_env'] is required")

        # Fail fast on an internal/SSRF-prone base_url so neither query() nor
        # health() can ever be tricked into hitting a private/metadata host.
        _reject_internal_url(base_url)

        self.base_url: str = base_url
        self.token_env: str = token_env
        self.name: str = str(config.get("name", "graylog"))
        self.kind: str = self.KIND
        try:
            self.timeout: float = float(config.get("timeout", DEFAULT_TIMEOUT))
        except (TypeError, ValueError):
            self.timeout = DEFAULT_TIMEOUT

        self._transport: _Transport = transport or _default_transport
        # Env source is injectable for testing; defaults to the process env.
        self._env: Mapping[str, str] = env if env is not None else os.environ

    # ------------------------------------------------------------------ auth
    def _auth_header(self) -> dict[str, str]:
        """Build the HTTP Basic header from the token env var.

        Graylog token auth: username = <token>, password = the literal "token".
        """
        token = self._env.get(self.token_env, "")
        raw = f"{token}:token".encode()
        encoded = base64.b64encode(raw).decode("ascii")
        return {"Authorization": f"Basic {encoded}", "Accept": "application/json"}

    def _request(self, url: str, *, params: Mapping[str, str | int] | None = None) -> HttpResponse:
        """Invoke URL-first and method-first injectable transport adapters."""
        try:
            return self._transport(url, headers=self._auth_header(), params=params, timeout=self.timeout)
        except TypeError as first_error:
            try:
                method_transport = cast(Callable[..., HttpResponse], self._transport)
                return method_transport("GET", url, headers=self._auth_header(), params=params, timeout=self.timeout)
            except TypeError:
                raise first_error from None

    # ---------------------------------------------------------------- health
    def health(self) -> dict[str, object]:
        """Probe Graylog load-balancer/system status. NEVER raises.

        Returns a dict with at least ``ok`` (bool). On any failure (network
        error, non-200, auth rejection) ``ok`` is False and ``error``/``status``
        explain why.
        """
        url = f"{self.base_url}/api/system/lbstatus"
        try:
            resp = self._request(url)
        except Exception:  # health must never raise
            # Do not leak repr(exc) (can embed the base URL / token env / internal
            # detail) into the health response; log it for operators instead.
            logger.warning("graylog health check failed", exc_info=True)
            return {
                "ok": False,
                "name": self.name,
                "kind": self.kind,
                "error": "graylog health check failed",
            }

        status = int(getattr(resp, "status_code", 0))
        ok = status == 200
        result: dict[str, object] = {
            "ok": ok,
            "name": self.name,
            "kind": self.kind,
            "status": status,
        }
        if status == 401:
            result["error"] = "unauthorized"
        elif not ok:
            result["error"] = f"unexpected status {status}"
        return result

    # ----------------------------------------------------------------- query
    def query(self, spec: GraylogQuerySpec | None = None) -> list[GraylogRecord]:
        """Run a Graylog universal search and return normalized records.

        ``spec`` keys (all optional):
          - ``query``  — Graylog search query string (default ``"*"``)
          - ``range``  — relative window in seconds (default ``300``)
          - ``limit``  — max messages to return (default ``100``)
          - ``absolute`` — if a ``(from, to)`` ISO pair is given under
            ``"from"``/``"to"``, the absolute endpoint is used instead.

        Returns a list of flat records. Returns ``[]`` on any transport/parse
        failure (fail-soft) so a logs source can never crash a caller.
        """
        spec_copy: Mapping[str, object] = dict(spec or {})
        url, params = self._build_search_request(spec_copy)
        try:
            resp = self._request(url, params=params)
            if int(getattr(resp, "status_code", 0)) != 200:
                return []
            payload = resp.json()
        except Exception:  # fail-soft: a logs query never crashes
            return []

        if not isinstance(payload, Mapping):
            return []
        messages = payload.get("messages")
        if not isinstance(messages, list):
            return []

        records: list[GraylogRecord] = []
        for entry in messages:
            record = self._normalize(entry)
            if record is not None:
                records.append(record)
        return records

    # ------------------------------------------------------- request builder
    def _build_search_request(self, spec: Mapping[str, object]) -> tuple[str, dict[str, str | int]]:
        query = str(spec.get("query", "*"))
        limit = _coerce_int(spec.get("limit"), default=100)

        if spec.get("from") is not None and spec.get("to") is not None:
            url = f"{self.base_url}/api/search/universal/absolute"
            params: dict[str, str | int] = {
                "query": query,
                "from": str(spec["from"]),
                "to": str(spec["to"]),
                "limit": limit,
            }
            return url, params

        rng = _coerce_int(spec.get("range"), default=300)
        url = f"{self.base_url}/api/search/universal/relative"
        params = {"query": query, "range": rng, "limit": limit}
        return url, params

    # ---------------------------------------------------------- normalization
    def _normalize(self, entry: object) -> GraylogRecord | None:
        """Normalize one universal-search ``messages[]`` entry into a record.

        A universal-search entry is ``{"message": {...fields...}, ...}``. The
        inner ``message`` mapping holds the actual log fields.
        """
        if not isinstance(entry, Mapping):
            return None
        msg = entry.get("message")
        if not isinstance(msg, Mapping):
            return None

        ts = _parse_timestamp(msg.get("timestamp"))
        level_or_status = _level_name(msg.get("level"))
        text = msg.get("message")
        labels: dict[str, object] = {
            "source": msg.get("source"),
            "facility": msg.get("facility"),
            "stream": _first_stream(msg.get("streams")),
        }
        return {
            "ts": ts,
            "source": msg.get("source"),
            "kind": self.kind,
            "level_or_status": level_or_status,
            "message": text,
            "value": None,
            "labels": labels,
            "raw": dict(msg),
        }


# ============================================================== module helpers


def _coerce_int(value: object, *, default: int) -> int:
    """Best-effort int coercion that never raises."""
    if value is None:
        return default
    try:
        # int() accepts int|float|str|bool at runtime; the cast preserves the
        # original try/except contract without weakening the static type.
        return int(cast(int | float | str | bool, value))
    except (TypeError, ValueError):
        return default


def _level_name(level: object) -> str | None:
    """Map a numeric syslog level to its canonical name.

    Non-numeric or out-of-range values pass through as a string (or None).
    """
    if level is None:
        return None
    try:
        num = int(cast(int | float | str | bool, level))
    except (TypeError, ValueError):
        return str(level)
    return SYSLOG_LEVEL_NAMES.get(num, str(num))


def _first_stream(streams: object) -> object | None:
    """Return the first stream id from a list, else the value/None as-is."""
    if isinstance(streams, list) and streams:
        first: object = streams[0]
        return first
    return None


def _parse_timestamp(value: int | float | str | None) -> str | None:
    """Parse a Graylog ISO-8601 timestamp into a normalized UTC ISO string.

    Graylog emits e.g. ``2026-06-12T18:30:00.000Z``. We normalize to an
    explicit ``+00:00`` UTC offset. On any parse failure the original value is
    returned as a string (lossy-but-never-crash), or None if absent.
    """
    if value is None:
        return None
    text = str(value)
    candidate = text.replace("Z", "+00:00") if text.endswith("Z") else text
    try:
        dt = datetime.fromisoformat(candidate)
    except ValueError:
        return text
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()


# ------------------------------------------------------------------- SSRF guard


def _reject_internal_url(url: str) -> None:
    """Raise ValueError if ``url``'s literal host is internal/SSRF-prone.

    Delegates the deny decision to the canonical shared guard
    :func:`general_ludd.security.ssrf.is_url_blocked` so this connector can never
    drift weaker than the single source of truth. That guard is LITERAL-host
    only (NO DNS resolution) and blocks: the cloud-metadata IPs and metadata
    host NAMES (localhost, metadata, metadata.google.internal, ...), loopback,
    link-local, private/unique-local, reserved, multicast, and unspecified. A
    non-IP-literal, non-metadata hostname is allowed through (resolving it would
    itself be an SSRF vector and is intentionally out of scope).
    """
    host = (urlsplit(url).hostname or "").strip()
    if not host:
        raise ValueError("GraylogSource: base_url has no host")
    if is_url_blocked(url):
        raise ValueError(f"GraylogSource: refusing internal/SSRF host {host!r}")
