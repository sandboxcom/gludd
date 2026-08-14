"""Kubernetes observability connector — pod logs + cluster events via the REST API.

This is a *self-contained* connector: it deliberately does not import the
``connectors.base`` contract or any sibling module, so it can be reasoned about,
tested, and shipped in isolation. It nonetheless honors the same normalized-record
shape every other source produces (the eight keys ``ts, source, kind,
level_or_status, message, value, labels, raw``) so the facade can merge its output
with other backends.

Design
------
- **No kubectl, no shell.** Everything goes through the Kubernetes REST API over
  an *injectable* HTTP transport (a callable), so production can wire in
  ``requests``/``httpx`` while tests inject a canned transport. There is no
  ``subprocess`` and no ``shell=True`` anywhere.
- **ServiceAccount Bearer auth.** The token is read from the environment variable
  named by ``token_env`` at *request* time (not construction), and sent as
  ``Authorization: Bearer <token>``.
- **SSRF policy (literal-host, no DNS).** Kubernetes API servers are usually on
  internal/private addresses, so the default policy *rejects* a private/internal
  ``api_server`` to avoid blind SSRF, and only permits it when the operator
  explicitly sets ``allow_private=True`` in config. Loopback, link-local and
  cloud-metadata targets are rejected even with ``allow_private=True`` — opting
  into "private" means RFC-1918 cluster networks, never the metadata service or
  localhost. The check is purely literal and never resolves DNS.
- **Resilience.** ``health()`` never raises (it reports failure in its dict) and
  ``query()`` converts every failure — SSRF block, missing token, transport
  exception, non-2xx response, bad mode — into a single ``"error"``-level
  normalized record rather than propagating.

Config keys
-----------
``api_server`` (base URL, required), ``namespace`` (default ``"default"``),
``token_env`` (env var holding the SA token, default ``"K8S_TOKEN"``),
``allow_private`` (bool, default ``False``), ``verify`` / ``ca`` (TLS hints,
forwarded to the transport via ``verify`` kwarg if the transport accepts it),
``transport`` (injectable callable; defaults to an httpx-backed transport),
``timeout_s`` (float request deadline, default ``10.0``), ``name`` (source name).
"""

from __future__ import annotations

import ipaddress
import os
import re
from datetime import UTC, datetime
from typing import Protocol, cast
from urllib.parse import quote, urlencode, urlsplit

import httpx

from general_ludd.connectors._protocols import HttpResponse
from general_ludd.connectors.exc_sanitizer import (
    sanitize_exc_for_health,
    sanitize_exc_for_query,
)
from general_ludd.security.ssrf import BLOCKED_HOST_NAMES, BLOCKED_METADATA_IPS, host_is_blocked

__all__ = ["KubernetesSource"]


# --------------------------------------------------------------------------- #
# Transport protocol + record builder (inlined; no base import)
# --------------------------------------------------------------------------- #
class _Transport(Protocol):
    """Injectable HTTP transport callable.

    A call returns an object satisfying :class:`HttpResponse`. Production transports
    may accept ``verify`` for TLS; the connector passes it only when configured.
    """

    def __call__(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = ...,
        timeout: float | None = ...,
    ) -> HttpResponse: ...


def _record(
    *,
    source: str,
    ts: float | None = None,
    level_or_status: str = "info",
    message: str = "",
    value: float | None = None,
    labels: dict[str, object] | None = None,
    raw: object = None,
) -> dict[str, object]:
    """Build a normalized record dict with the canonical eight keys.

    ``kind`` is always ``"logs"`` for this connector (it is a log/event source).
    """
    return {
        "ts": ts,
        "source": source,
        "kind": KubernetesSource.KIND,
        "level_or_status": level_or_status,
        "message": message,
        "value": value,
        "labels": labels if labels is not None else {},
        "raw": raw,
    }


# --------------------------------------------------------------------------- #
# SSRF guard (literal-host, allow_private opt-in)
# --------------------------------------------------------------------------- #
_ALLOWED_SCHEMES = frozenset({"http", "https"})


def _endpoint_block_reason(url: str, *, allow_private: bool) -> str | None:
    """Return a human reason the endpoint is blocked, or ``None`` if allowed.

    Literal-host only — never resolves DNS. The name/metadata-IP blocklists
    (:data:`general_ludd.security.ssrf.BLOCKED_HOST_NAMES` /
    ``BLOCKED_METADATA_IPS``) are delegated to the canonical module so this
    connector's coverage — previously missing ``instance-data``,
    ``metadata.goog``, the ``ip6-*`` loopback aliases, and the Alibaba
    ``100.100.100.200`` metadata IP — can never drift from the single source
    of truth. With ``allow_private=True``, RFC-1918 / unique-local / other
    non-globally-routable hosts (including TEST-NET/documentation ranges) are
    permitted, but loopback, link-local, reserved, multicast, unspecified and
    named-metadata targets stay blocked regardless of ``allow_private`` — that
    carve-out only ever means "an internal CLUSTER network", never the
    metadata service or loopback.
    Host-level allow/deny decisions delegate to
    :func:`general_ludd.security.ssrf.host_is_blocked` — the canonical
    literal-host guard — so the blocklists and IP-classification logic can
    never drift. The ``allow_private`` carve-out is the only connector-specific
    addition: it permits private/non-globally-routable IPs that
    ``host_is_blocked`` would otherwise deny, keeping loopback, link-local,
    and metadata targets blocked regardless.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return "ssrf: unparseable api_server URL"

    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        return f"ssrf: blocked scheme {parts.scheme!r} (only http/https)"

    host = parts.hostname
    if not host:
        return "ssrf: api_server has no host"

    if not host_is_blocked(host):
        return None  # passes canonical literal-host guard

    # Canonical guard blocked it.  Build a reason and check allow_private.
    lowered = host.lower().rstrip(".")
    if lowered in BLOCKED_HOST_NAMES or lowered in BLOCKED_METADATA_IPS:
        return f"ssrf: blocked metadata/loopback host {host!r}"
    if lowered == "localhost" or lowered.endswith(".localhost"):
        return f"ssrf: blocked loopback host {host!r}"

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return f"ssrf: blocked host {host!r}"

    if ip.is_loopback:
        return f"ssrf: blocked loopback address {host!r}"
    if ip.is_link_local:
        return f"ssrf: blocked link-local/metadata address {host!r}"
    if ip.is_reserved or ip.is_multicast or ip.is_unspecified:
        return f"ssrf: blocked reserved/multicast address {host!r}"

    if allow_private:
        return None
    return (
        f"ssrf: blocked private/internal api_server {host!r} "
        "(set allow_private=True to permit an internal cluster API server)"
    )


# --------------------------------------------------------------------------- #
# Log-line helpers
# --------------------------------------------------------------------------- #
# Leading RFC3339 token the kubelet prepends when timestamps=true, e.g.
# "2026-06-16T10:00:01.500000000Z rest of line".
_RFC3339_LEADING = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2}))\s+"
    r"(?P<rest>.*)$"
)

_LEVEL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("error", re.compile(r"\b(?:error|err|fatal|panic|critical|crit)\b", re.IGNORECASE)),
    ("warn", re.compile(r"\b(?:warn|warning)\b", re.IGNORECASE)),
    ("debug", re.compile(r"\b(?:debug|trace)\b", re.IGNORECASE)),
    ("info", re.compile(r"\b(?:info|notice)\b", re.IGNORECASE)),
)


def _detect_level(line: str) -> str:
    """Best-effort log-level detection; defaults to ``"info"``."""
    for level, pat in _LEVEL_PATTERNS:
        if pat.search(line):
            return level
    return "info"


def _parse_rfc3339(value: str | None) -> float | None:
    """Parse an RFC3339 timestamp to epoch seconds, or ``None`` if unparseable."""
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    # datetime.fromisoformat handles fractional seconds of arbitrary length only
    # up to 6 digits; trim nanosecond precision down to microseconds.
    text = re.sub(r"(\.\d{6})\d+", r"\1", text)
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.timestamp()


# --------------------------------------------------------------------------- #
# Default httpx transport (no shell, redirects never followed)
# --------------------------------------------------------------------------- #
def _default_transport(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float | None = None,
) -> httpx.Response:
    """httpx-backed transport. Used only when no transport is injected.

    No ``subprocess``/shell. Redirects are never followed, so a 3xx cannot
    pivot the request to an internal/metadata address (SSRF). Network errors
    propagate to the caller, which wraps them into error records.
    """
    return httpx.request(
        method,
        url,
        headers=headers or {},
        timeout=timeout,
        follow_redirects=False,
    )


# --------------------------------------------------------------------------- #
# Connector
# --------------------------------------------------------------------------- #
class KubernetesSource:
    """Pull pod logs and cluster events from the Kubernetes REST API.

    ``query(spec)`` dispatches on ``spec['mode']``:

    - ``"logs"``  -> ``GET {api}/api/v1/namespaces/{ns}/pods/{pod}/log`` (plain
      text). One record per non-blank line. ``ts`` is parsed from the leading
      RFC3339 token when ``spec['timestamps']`` is true, else ``None``.
    - ``"events"`` -> ``GET {api}/api/v1/namespaces/{ns}/events`` (or
      ``/api/v1/events`` for all namespaces when ``namespace`` is blank). One
      record per event.

    Never raises from ``query`` or ``health``; failures become error records /
    a not-ok health dict.
    """

    KIND: str = "logs"

    def __init__(self, config: dict[str, object]) -> None:
        """Configure a Kubernetes source with a vetted API endpoint.

        Args:
            config: Connector name, endpoint, namespace, authentication, TLS,
                timeout, and optional transport settings.

        Raises:
            ValueError: If the endpoint is malformed or violates the private-host
                policy, or if the timeout is not numeric.
        """
        self._config = dict(config)
        self.name: str = str(config.get("name") or "kubernetes")

        raw_api = str(config.get("api_server", "")).rstrip("/")
        self._api_server = raw_api
        self._namespace = str(config.get("namespace", "default"))
        self._token_env = str(config.get("token_env", "K8S_TOKEN"))
        self._allow_private = bool(config.get("allow_private", False))
        self._timeout_s = float(str(config.get("timeout_s", 10.0)))

        # TLS hint: prefer explicit verify, else a CA path means "verify", else
        # default True. Forwarded to the transport only if it accepts ``verify``.
        if "verify" in config:
            self._verify: object = config["verify"]
        elif config.get("ca"):
            self._verify = config["ca"]
        else:
            self._verify = True

        transport = config.get("transport")
        self._transport: _Transport = (
            cast(_Transport, transport)
            if transport is not None
            else cast(_Transport, _default_transport)
        )

        reason = _endpoint_block_reason(self._api_server, allow_private=self._allow_private)
        if reason:
            raise ValueError(reason)

    # -- internals --------------------------------------------------------- #
    def _bearer_token(self) -> str | None:
        """Read the ServiceAccount token from ``token_env`` at request time."""
        token = os.environ.get(self._token_env)
        return token or None

    def _headers(self, token: str, *, accept: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}", "Accept": accept}

    def _send(self, url: str, *, accept: str) -> HttpResponse:
        """Issue a time-bound GET with Bearer auth. May raise (caller wraps)."""
        token = self._bearer_token()
        if token is None:
            raise _ConfigError(
                f"no ServiceAccount token in env var {self._token_env!r}"
            )
        headers = self._headers(token, accept=accept)
        return self._transport("GET", url, headers=headers, timeout=self._timeout_s)

    def _guard(self) -> str | None:
        """Return an SSRF block reason for the configured api_server, or None."""
        return _endpoint_block_reason(self._api_server, allow_private=self._allow_private)

    # -- public API -------------------------------------------------------- #
    def health(self) -> dict[str, object]:
        """Probe ``/livez`` (falling back to ``/version``). Never raises."""
        block = self._guard()
        if block is not None:
            return {"ok": False, "detail": block}

        for path in ("/livez", "/version"):
            url = f"{self._api_server}{path}"
            try:
                resp = self._send(url, accept="application/json")
            except Exception as exc:
                return {"ok": False, "detail": sanitize_exc_for_health(exc)}
            if 200 <= resp.status_code < 300:
                return {"ok": True, "detail": f"{path} -> {resp.status_code}"}
        return {
            "ok": False,
            "detail": f"livez/version both unhealthy (last status {resp.status_code})",
        }

    def query(self, spec: dict[str, object]) -> list[dict[str, object]]:
        """Dispatch on ``spec['mode']`` and return normalized records.

        Any failure is returned as a single ``"error"``-level record; this method
        never raises.
        """
        block = self._guard()
        if block is not None:
            return [self._error(block)]

        mode = str(spec.get("mode", "logs"))
        try:
            if mode == "logs":
                return self._query_logs(spec)
            if mode == "events":
                return self._query_events(spec)
            return [self._error(f"unknown mode {mode!r} (expected 'logs' or 'events')")]
        except _ConfigError as exc:
            sanitize_exc_for_query(exc)
            return [self._error("token configuration unavailable")]
        except Exception as exc:
            sanitize_exc_for_query(exc)
            return [self._error("query failed")]

    # -- logs -------------------------------------------------------------- #
    def _query_logs(self, spec: dict[str, object]) -> list[dict[str, object]]:
        namespace = str(spec.get("namespace", self._namespace))
        pod = str(spec.get("pod", ""))
        if not pod:
            return [self._error("logs mode requires spec['pod']")]
        container = spec.get("container")
        timestamps = bool(spec.get("timestamps", False))

        params: dict[str, str] = {"timestamps": "true" if timestamps else "false"}
        if container:
            params["container"] = str(container)
        if spec.get("tailLines") is not None:
            params["tailLines"] = str(spec["tailLines"])
        if spec.get("sinceSeconds") is not None:
            params["sinceSeconds"] = str(spec["sinceSeconds"])

        url = (
            f"{self._api_server}/api/v1/namespaces/{quote(namespace)}"
            f"/pods/{quote(pod)}/log?{urlencode(params)}"
        )
        resp = self._send(url, accept="text/plain")
        if not (200 <= resp.status_code < 300):
            return [
                self._error(
                    f"log fetch returned {resp.status_code}: {resp.text[:200]}"
                )
            ]

        labels = {
            "namespace": namespace,
            "pod": pod,
            "container": str(container) if container else "",
        }
        records: list[dict[str, object]] = []
        for raw_line in resp.text.splitlines():
            line = raw_line.rstrip("\r")
            if not line.strip():
                continue
            ts: float | None = None
            message = line
            if timestamps:
                m = _RFC3339_LEADING.match(line)
                if m:
                    ts = _parse_rfc3339(m.group("ts"))
                    message = m.group("rest")
            records.append(
                _record(
                    source=self.name,
                    ts=ts,
                    level_or_status=_detect_level(message),
                    message=message,
                    labels=dict(labels),
                    raw=line,
                )
            )
        return records

    # -- events ------------------------------------------------------------ #
    def _query_events(self, spec: dict[str, object]) -> list[dict[str, object]]:
        namespace = spec.get("namespace", self._namespace)
        namespace = "" if namespace is None else str(namespace)
        if namespace:
            url = f"{self._api_server}/api/v1/namespaces/{quote(namespace)}/events"
        else:
            url = f"{self._api_server}/api/v1/events"

        resp = self._send(url, accept="application/json")
        if not (200 <= resp.status_code < 300):
            return [
                self._error(
                    f"events fetch returned {resp.status_code}: {resp.text[:200]}"
                )
            ]

        body = resp.json() or {}
        items = body.get("items", []) if isinstance(body, dict) else []
        records: list[dict[str, object]] = []
        for ev in items:
            records.append(self._normalize_event(ev))
        return records

    def _normalize_event(self, ev: dict[str, object]) -> dict[str, object]:
        ev = ev if isinstance(ev, dict) else {}
        ev_type = str(ev.get("type") or "Normal")
        reason = str(ev.get("reason") or "")
        body = str(ev.get("message") or "")
        message = f"{reason}: {body}".strip(": ").strip() if reason else body

        ts = _parse_rfc3339(cast(str | None, ev.get("lastTimestamp"))) or _parse_rfc3339(
            cast(str | None, ev.get("eventTime"))
        )
        if ts is None:
            ts = _parse_rfc3339(cast(str | None, ev.get("firstTimestamp")))

        involved_raw = ev.get("involvedObject") or {}
        involved: dict[str, object] = involved_raw if isinstance(involved_raw, dict) else {}
        labels: dict[str, object] = {
            "involvedObject.kind": str(involved.get("kind") or ""),
            "involvedObject.name": str(involved.get("name") or ""),
            "reason": reason,
            "namespace": str(involved.get("namespace") or self._namespace),
        }
        return _record(
            source=self.name,
            ts=ts,
            level_or_status=ev_type,
            message=message,
            labels=labels,
            raw=ev,
        )

    # -- helpers ----------------------------------------------------------- #
    def _error(self, message: str) -> dict[str, object]:
        return _record(
            source=self.name,
            level_or_status="error",
            message=message,
            labels={"namespace": self._namespace},
            raw=None,
        )


class KubernetesConfigError(RuntimeError):
    """Raised when Kubernetes connector credentials are unavailable or invalid."""


# Compatibility alias retained for internal integrations using the old name.
_ConfigError = KubernetesConfigError
