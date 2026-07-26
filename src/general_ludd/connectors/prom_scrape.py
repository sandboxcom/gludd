"""Generic Prometheus-exposition (`/metrics`) scrape connector.

`PromScrapeSource` GETs ``{base_url}/metrics`` and parses the Prometheus text
exposition format into normalized records. It is a *generic* scraper: the
target (node_exporter, windows_exporter, cAdvisor, kube-state-metrics, or any
``*/metrics`` exporter) is supplied entirely via ``base_url`` config — there is
no exporter-specific logic here.

Design constraints (see connector contract):
  * Self-contained — no base class, no sibling/package imports.
  * Injectable HTTP transport (anything with a ``get(url, headers=, timeout=)``
    returning an object exposing ``.status_code`` / ``.text``). Defaults to a
    small ``httpx``-backed transport (``follow_redirects=False``) so production
    needs no extra configuration.
  * Literal-host SSRF guard on ``base_url`` (no DNS resolution): loopback,
    RFC-1918 / link-local / unique-local, and the cloud metadata IP are
    rejected unless ``allow_private`` is opted in (exporters are usually
    internal, so opt-in is expected and cheap).
  * ``health()`` never raises. ``query()`` returns normalized dicts and never
    shells out (``shell=True`` is never used — there is no subprocess at all).

"""

from __future__ import annotations

import math
import os
import time
from typing import Protocol, cast, runtime_checkable
from urllib.parse import urlsplit

import httpx

from general_ludd.security.ssrf import is_url_blocked

__all__ = ["PromScrapeSource"]

_DEFAULT_TIMEOUT = 10.0


@runtime_checkable
class _Transport(Protocol):
    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = ...,
        timeout: float | None = ...,
    ) -> object: ...


class _HttpxTransport:
    """Minimal httpx GET transport (redirects not followed — SSRF defense)."""

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        with httpx.Client(
            timeout=timeout if timeout is not None else _DEFAULT_TIMEOUT,
            follow_redirects=False,
        ) as client:
            return client.get(url, headers=headers or {})


def _guard_base_url(base_url: str, allow_private: bool) -> str:
    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https"):
        raise ValueError(f"base_url scheme must be http/https, got {parts.scheme!r}")
    host = parts.hostname or ""
    if not host:
        raise ValueError("base_url has no host")
    # Delegate the private/loopback + cloud-metadata NAME decision (localhost,
    # metadata.google.internal, ...) to the canonical shared guard so this
    # connector can never drift weaker than the single source of truth.
    # Exporters are usually internal, so private hosts are opt-in via
    # ``allow_private``.
    if not allow_private and is_url_blocked(base_url):
        raise ValueError(
            f"refusing private/loopback/metadata host {host!r} "
            "(set allow_private=True to opt in for internal exporters)"
        )
    return base_url.rstrip("/")


def _parse_labels(blob: str) -> dict[str, str]:
    """Parse ``{a="1",b="2"}`` -> dict. Tolerant of quotes/escapes/spaces."""
    labels: dict[str, str] = {}
    inner = blob.strip()
    if inner.startswith("{"):
        inner = inner[1:]
    if inner.endswith("}"):
        inner = inner[:-1]
    i = 0
    n = len(inner)
    while i < n:
        # key
        while i < n and inner[i] in ", ":
            i += 1
        start = i
        while i < n and inner[i] not in "=, ":
            i += 1
        key = inner[start:i].strip()
        while i < n and inner[i] in " =":
            i += 1
        if i >= n or inner[i] != '"':
            break  # malformed label section
        i += 1  # opening quote
        buf: list[str] = []
        while i < n:
            ch = inner[i]
            if ch == "\\" and i + 1 < n:
                nxt = inner[i + 1]
                buf.append({"n": "\n", "t": "\t", '"': '"', "\\": "\\"}.get(nxt, nxt))
                i += 2
                continue
            if ch == '"':
                i += 1
                break
            buf.append(ch)
            i += 1
        if key:
            labels[key] = "".join(buf)
    return labels


def _split_metric_line(line: str) -> tuple[str, dict[str, str], str] | None:
    """Split ``name{labels} value [ts]`` -> (name, labels, value_and_ts).

    Returns ``None`` for lines that are not parseable as a sample.
    """
    brace = line.find("{")
    if brace != -1:
        name = line[:brace].strip()
        close = line.find("}", brace)
        if close == -1:
            return None
        labels = _parse_labels(line[brace : close + 1])
        rest = line[close + 1 :].strip()
    else:
        # name value [ts] — split on first whitespace
        parts = line.split(None, 1)
        if len(parts) != 2:
            return None
        name, rest = parts[0], parts[1].strip()
        labels = {}
    if not name or not rest:
        return None
    return name, labels, rest


def _parse_value_ts(rest: str) -> tuple[float, float | None] | None:
    """Parse the ``value [timestamp_ms]`` tail. Drops NaN/Inf-value samples."""
    fields = rest.split()
    if not fields:
        return None
    try:
        value = float(fields[0])
    except ValueError:
        return None
    if math.isnan(value) or math.isinf(value):
        return None
    ts: float | None = None
    if len(fields) >= 2:
        try:
            ts = float(fields[1]) / 1000.0  # exposition timestamps are ms
        except ValueError:
            ts = None
    return value, ts


class PromScrapeSource:
    """Generic Prometheus ``/metrics`` exposition scraper.

    Config keys:
      * ``base_url`` (required) — exporter base, e.g. ``http://host:9100``.
      * ``token_env`` (optional) — env var holding a bearer token.
      * ``allow_private`` (optional, default False) — opt into private hosts.
      * ``name`` (optional) — connector instance name (defaults to base host).
      * ``timeout`` (optional) — per-request timeout seconds.

    ``query(spec)`` spec keys:
      * ``metric_prefix`` — only emit metrics whose name starts with this.
      * ``allow_private`` — (accepted; the base_url guard already ran at init).
    """

    KIND: str = "metrics"

    def __init__(self, config: dict[str, object], *, transport: _Transport | None = None) -> None:
        if "base_url" not in config:
            raise ValueError("config requires 'base_url'")
        self._allow_private = bool(config.get("allow_private", False))
        self._base_url = _guard_base_url(str(config["base_url"]), self._allow_private)
        self._token_env = config.get("token_env")
        self._timeout = float(cast("float | int | str", config.get("timeout", _DEFAULT_TIMEOUT)))
        host = urlsplit(self._base_url).hostname or self._base_url
        self.name: str = str(config.get("name") or f"prom:{host}")
        self._transport: _Transport = transport if transport is not None else _HttpxTransport()

    # -- internals -----------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "text/plain"}
        if self._token_env:
            token = os.environ.get(str(self._token_env))
            if token:
                headers["Authorization"] = f"Bearer {token}"
        return headers

    def _fetch(self) -> object:
        url = f"{self._base_url}/metrics"
        # Use the named argument so injected transports can expose ``url`` as
        # a keyword-only parameter while remaining compatible with httpx.
        return self._transport.get(url=url, headers=self._headers(), timeout=self._timeout)

    @staticmethod
    def _status_ok(resp: object) -> bool:
        code = getattr(resp, "status_code", None)
        return isinstance(code, int) and 200 <= code < 300

    # -- public API ----------------------------------------------------------

    def health(self) -> dict[str, object]:
        """Probe the exporter. Never raises; returns ``{'ok', 'detail'}``."""
        try:
            resp = self._fetch()
        except Exception as exc:  # health must never raise
            return {"ok": False, "detail": f"error: {exc}"}
        code = getattr(resp, "status_code", "?")
        if self._status_ok(resp):
            return {"ok": True, "detail": f"HTTP {code}"}
        return {"ok": False, "detail": f"HTTP {code}"}

    def query(self, spec: dict[str, object] | None = None) -> list[dict[str, object]]:
        """Scrape + normalize. Returns ``[]`` on any transport/HTTP failure."""
        spec = spec or {}
        prefix = spec.get("metric_prefix")
        try:
            resp = self._fetch()
        except Exception:  # degrade to empty result set
            return []
        if not self._status_ok(resp):
            return []
        text = getattr(resp, "text", "") or ""
        now = time.time()
        records: list[dict[str, object]] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue  # blank or HELP/TYPE/other comment
            split = _split_metric_line(line)
            if split is None:
                continue
            name, labels, rest = split
            if prefix and not name.startswith(str(prefix)):
                continue
            parsed = _parse_value_ts(rest)
            if parsed is None:
                continue  # malformed / NaN / Inf value -> skip
            value, ts = parsed
            records.append(
                {
                    "ts": ts if ts is not None else now,
                    "source": self.name,
                    "kind": self.KIND,
                    "level_or_status": "",
                    "message": name,
                    "value": value,
                    "labels": labels,
                    "raw": raw_line,
                }
            )
        return records
