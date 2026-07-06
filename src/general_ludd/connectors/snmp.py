"""SNMP metrics connector.

``SnmpSource`` collects device metrics over SNMP in one of two modes selected by
``config["mode"]``:

* ``"snmp"`` (default) — pull OID values through an *injected* SNMP getter. The
  default getter lazily imports :mod:`pysnmp`; if that library is not installed
  the import is **guarded** and ``health()`` reports ``"pysnmp unavailable"``
  instead of raising. The getter signature is
  ``getter(host, port, community, oids, timeout) -> list[(oid, value)]``.
* ``"exporter"`` — scrape an ``snmp_exporter``-style Prometheus ``/metrics``
  endpoint over HTTP using an injected transport. This path is SSRF-guarded and
  refuses private/loopback targets unless ``allow_private`` is set.

Secrets handling:

* The SNMP community string is read **only** from the environment via
  ``community_env`` (never inline) and is **never logged or emitted**. In record
  labels it is replaced with a fixed redaction marker, and the marker is also
  stamped into the raw payload's community field.

``health()`` never raises; ``query()`` returns normalized ``metrics`` records.
"""

from __future__ import annotations

import ipaddress
import os
import socket
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from general_ludd.connectors._errors import SSRFError

__all__ = ["COMMUNITY_REDACTED", "SnmpSource"]

# The token that stands in for a community string anywhere it would otherwise be
# exposed (labels, raw payloads, error detail). Must never be the real value.
COMMUNITY_REDACTED = "***redacted***"

# getter(host, port, community, oids, timeout) -> list[(oid, value)]
SnmpGetter = Callable[
    [str, int, str, list[str], float],
    list[tuple[str, Any]],
]

# transport(url, timeout) -> (status_code, body_text)
HttpTransport = Callable[[str, float], tuple[int, str]]


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


class _PysnmpUnavailable(RuntimeError):
    """Internal sentinel: the default getter could not import pysnmp."""


def _default_snmp_getter(
    host: str,
    port: int,
    community: str,
    oids: list[str],
    timeout: float,
) -> list[tuple[str, Any]]:
    """Lazily-imported pysnmp getter.

    The import is guarded: if pysnmp is absent we raise ``_PysnmpUnavailable``
    so callers (``health``/``query``) can degrade gracefully rather than crash.
    """
    try:  # pragma: no cover - exercised via injection in tests
        import importlib

        _hlapi = importlib.import_module("pysnmp.hlapi")  # pysnmp: optional, guarded by try/except
        CommunityData = _hlapi.CommunityData
        ContextData = _hlapi.ContextData
        ObjectIdentity = _hlapi.ObjectIdentity
        ObjectType = _hlapi.ObjectType
        SnmpEngine = _hlapi.SnmpEngine
        UdpTransportTarget = _hlapi.UdpTransportTarget
        getCmd = _hlapi.getCmd
    except Exception as exc:
        raise _PysnmpUnavailable(str(exc)) from exc

    results: list[tuple[str, Any]] = []
    for oid in oids:  # pragma: no cover - requires a live device
        iterator = getCmd(
            SnmpEngine(),
            CommunityData(community, mpModel=1),
            UdpTransportTarget((host, port), timeout=timeout, retries=0),
            ContextData(),
            ObjectType(ObjectIdentity(oid)),
        )
        error_indication, error_status, _error_index, var_binds = next(iterator)
        if error_indication or error_status:
            continue
        for name, val in var_binds:
            results.append((str(name), val.prettyPrint() if hasattr(val, "prettyPrint") else val))
    return results


class SnmpSource:
    """SNMP metrics source (direct pysnmp pull or snmp_exporter scrape)."""

    KIND = "metrics"

    def __init__(
        self,
        config: dict[str, Any],
        *,
        getter: SnmpGetter | None = None,
        transport: HttpTransport | None = None,
        resolver: Callable[[str], list[str]] | None = None,
    ) -> None:
        cfg = dict(config or {})
        self.name: str = str(cfg.get("name") or "snmp")
        self.kind: str = self.KIND

        self.mode: str = str(cfg.get("mode", "snmp")).lower()
        if self.mode not in ("snmp", "exporter"):
            self.mode = "snmp"

        self.host: str = str(cfg.get("host") or "")
        try:
            self.port: int = int(cfg.get("port", 161))
        except (TypeError, ValueError):
            self.port = 161

        oids = cfg.get("oids") or []
        self.oids: list[str] = [str(o) for o in oids]

        try:
            self.timeout: float = float(cfg.get("timeout", 5.0))
        except (TypeError, ValueError):
            self.timeout = 5.0
        if self.timeout <= 0:
            self.timeout = 5.0

        # Exporter-mode HTTP settings.
        base_url = cfg.get("base_url")
        self.base_url: str | None = (
            str(base_url).rstrip("/") if base_url else None
        )
        self.allow_private: bool = bool(cfg.get("allow_private", False))

        # Community string is resolved from the environment ONLY.
        community_env = cfg.get("community_env")
        self._community: str | None = (
            os.environ.get(str(community_env)) if community_env else None
        )

        self._getter: SnmpGetter = getter or _default_snmp_getter
        self._transport: HttpTransport | None = transport
        self._resolver = resolver or (
            lambda h: [str(ai[4][0]) for ai in socket.getaddrinfo(h, None)]
        )

    # -- SSRF guard (exporter mode) ----------------------------------------

    def _guard_url(self, url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise SSRFError(f"unsupported scheme: {parsed.scheme!r}")
        host = parsed.hostname
        if not host:
            raise SSRFError("missing host in URL")
        if self.allow_private:
            return url

        try:
            literal = ipaddress.ip_address(host)
            addrs = [str(literal)]
        except ValueError:
            try:
                addrs = self._resolver(host)
            except OSError as exc:
                raise SSRFError(f"cannot resolve host {host!r}: {exc}") from exc
            if not addrs:
                raise SSRFError(f"host {host!r} did not resolve") from None

        for addr in addrs:
            try:
                ip = ipaddress.ip_address(addr)
            except ValueError:
                raise SSRFError(
                    f"unparseable resolved address {addr!r}"
                ) from None
            if _is_blocked_ip(ip):
                raise SSRFError(
                    f"target {host!r} resolves to blocked address {addr} "
                    "(set allow_private to permit it)"
                )
        return url

    # -- health -------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Liveness check that never raises and never leaks the community."""
        if self.mode == "exporter":
            if not self.base_url:
                return {"ok": False, "detail": "exporter mode requires base_url"}
            url = f"{self.base_url}/metrics"
            try:
                self._guard_url(url)
            except SSRFError as exc:
                return {"ok": False, "detail": f"ssrf-blocked: {exc}"}
            if self._transport is None:
                return {"ok": False, "detail": "no HTTP transport configured"}
            try:
                status, _body = self._transport(url, self.timeout)
            except Exception as exc:
                return {"ok": False, "detail": f"exporter probe failed: {exc}"}
            if 200 <= status < 300:
                return {"ok": True, "detail": f"snmp_exporter {status}"}
            return {"ok": False, "detail": f"exporter http {status}"}

        # Direct SNMP mode.
        if not self.host:
            return {"ok": False, "detail": "snmp mode requires host"}
        if self._community is None:
            return {
                "ok": False,
                "detail": "no community string resolved (set community_env)",
            }
        try:
            self._getter(self.host, self.port, self._community, [], self.timeout)
        except _PysnmpUnavailable:
            return {"ok": False, "detail": "pysnmp unavailable"}
        except Exception as exc:
            return {"ok": False, "detail": f"snmp probe failed: {_scrub(exc, self._community)}"}
        return {"ok": True, "detail": "snmp getter reachable"}

    # -- query --------------------------------------------------------------

    def query(self, spec: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Return normalized ``metrics`` records (community never present)."""
        spec = dict(spec or {})
        if self.mode == "exporter":
            return self._query_exporter()
        return self._query_snmp(spec)

    def _query_snmp(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        if not self.host or self._community is None:
            return []
        oids = [str(o) for o in (spec.get("oids") or self.oids)]
        try:
            pairs = self._getter(
                self.host, self.port, self._community, oids, self.timeout
            )
        except _PysnmpUnavailable:
            return []
        now = time.time()
        out: list[dict[str, Any]] = []
        for oid, raw_value in pairs or []:
            value, status = _coerce_numeric(raw_value)
            out.append(
                {
                    "ts": now,
                    "source": self.name,
                    "kind": self.KIND,
                    "level_or_status": status,
                    "message": str(oid),
                    "value": value,
                    "labels": {
                        "oid": str(oid),
                        "host": self.host,
                        "community": COMMUNITY_REDACTED,
                    },
                    # Raw payload carries the value but a redacted community so
                    # downstream sinks can never re-expose the secret.
                    "raw": {
                        "oid": str(oid),
                        "value": _stringify(raw_value),
                        "host": self.host,
                        "community": COMMUNITY_REDACTED,
                    },
                }
            )
        return out

    def _query_exporter(self) -> list[dict[str, Any]]:
        if not self.base_url or self._transport is None:
            return []
        url = f"{self.base_url}/metrics"
        self._guard_url(url)
        status_code, body = self._transport(url, self.timeout)
        if not (200 <= status_code < 300):
            return []
        now = time.time()
        out: list[dict[str, Any]] = []
        for line in body.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parsed = _parse_prom_line(line)
            if parsed is None:
                continue
            name, labels, value = parsed
            oid = labels.get("oid") or name
            host = labels.get("instance") or labels.get("host") or self.host
            out.append(
                {
                    "ts": now,
                    "source": self.name,
                    "kind": self.KIND,
                    "level_or_status": "ok",
                    "message": name,
                    "value": value,
                    "labels": {
                        "oid": str(oid),
                        "host": str(host),
                        "community": COMMUNITY_REDACTED,
                        **{k: v for k, v in labels.items() if k not in ("oid",)},
                    },
                    "raw": line,
                }
            )
        return out


# -- helpers ----------------------------------------------------------------


def _coerce_numeric(raw: Any) -> tuple[float | None, str]:
    """Return ``(numeric_value_or_None, status)`` for an SNMP value."""
    if isinstance(raw, bool):
        return (1.0 if raw else 0.0), "ok"
    if isinstance(raw, (int, float)):
        return float(raw), "ok"
    text = str(raw).strip()
    try:
        return float(text), "ok"
    except ValueError:
        # Non-numeric OID value (e.g. sysDescr string): keep status, no value.
        return None, "ok"


def _stringify(raw: Any) -> Any:
    if isinstance(raw, (int, float, bool)):
        return raw
    return str(raw)


def _scrub(obj: Any, secret: str | None) -> str:
    """Render ``obj`` as text with any occurrence of ``secret`` redacted."""
    text = str(obj)
    if secret:
        text = text.replace(secret, COMMUNITY_REDACTED)
    return text


def _parse_prom_line(
    line: str,
) -> tuple[str, dict[str, str], float | None] | None:
    """Parse one Prometheus exposition line into ``(name, labels, value)``."""
    try:
        head, raw_value = line.rsplit(None, 1)
    except ValueError:
        return None
    try:
        value: float | None = float(raw_value)
    except ValueError:
        value = None

    labels: dict[str, str] = {}
    if "{" in head and head.endswith("}"):
        name, _, rest = head.partition("{")
        label_blob = rest[:-1]
        for pair in _split_labels(label_blob):
            key, _, val = pair.partition("=")
            key = key.strip()
            val = val.strip().strip('"')
            if key:
                labels[key] = val
    else:
        name = head
    name = name.strip()
    if not name:
        return None
    return name, labels, value


def _split_labels(blob: str) -> list[str]:
    """Split a Prometheus label blob on commas not inside quotes."""
    parts: list[str] = []
    buf: list[str] = []
    in_quote = False
    for ch in blob:
        if ch == '"':
            in_quote = not in_quote
            buf.append(ch)
        elif ch == "," and not in_quote:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return [p for p in (s.strip() for s in parts) if p]
