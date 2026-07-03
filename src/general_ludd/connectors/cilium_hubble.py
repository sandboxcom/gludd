"""Cilium Hubble network-observability connector.

``CiliumHubbleSource`` pulls network-flow observability from a Cilium Hubble
deployment. Two data paths are supported and may be combined:

* **Prometheus metrics** — an HTTP ``GET {base_url}/metrics`` against the Hubble
  metrics endpoint (conventionally port ``9965``). Counter/gauge samples are
  normalized into ``metrics``-flavored event records.
* **Flow query** — a structured flow lookup performed through an *injected*
  transport callable. This keeps the connector independent of any particular
  Hubble client/gRPC library: the caller supplies a function that returns a list
  of raw flow dicts (verdict, source, destination, L7 http/dns, ...), which are
  normalized into per-flow event records.

Security posture:

* All HTTP targets are validated by an SSRF guard. By default, requests to
  private / loopback / link-local / reserved hosts are **refused** unless the
  operator explicitly sets ``allow_private`` (the expected mode for an
  in-cluster Hubble relay).
* No ``shell=True`` and no subprocess use anywhere.
* Every network call is time-bound by ``timeout``.
* ``health()`` never raises; failures are reported as ``{"ok": False, ...}``.
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
from general_ludd.security.ssrf import is_url_blocked

__all__ = ["CiliumHubbleSource"]

# A transport is any callable that performs an HTTP GET against an absolute URL
# and returns ``(status_code, body_text)``. Injecting it keeps the connector
# free of a hard dependency on requests/httpx and trivially mockable in tests.
HttpTransport = Callable[[str, float], "tuple[int, str]"]

# A flow runner returns a list of raw Hubble flow dicts for a given query spec.
FlowRunner = Callable[[dict[str, Any]], list[dict[str, Any]]]


# Record level mapping for flow verdicts. Drops are notable; forwards are fine.
_VERDICT_LEVEL = {
    "DROPPED": "warning",
    "DROP": "warning",
    "ERROR": "error",
    "FORWARDED": "info",
    "FORWARD": "info",
    "AUDIT": "info",
    "REDIRECTED": "info",
}


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return True for addresses an untrusted target should not reach."""
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


class CiliumHubbleSource:
    """Network-flow observability source backed by Cilium Hubble."""

    KIND = "events"

    def __init__(
        self,
        config: dict[str, Any],
        *,
        transport: HttpTransport | None = None,
        flow_runner: FlowRunner | None = None,
        resolver: Callable[[str], list[str]] | None = None,
    ) -> None:
        cfg = dict(config or {})
        self.name: str = str(cfg.get("name") or "cilium-hubble")
        self.kind: str = self.KIND

        base_url = cfg.get("base_url")
        self.base_url: str | None = (
            str(base_url).rstrip("/") if base_url else None
        )

        # Opt-in to in-cluster private targets (Hubble relay lives on a private
        # cluster IP). Off by default so a misconfig cannot become an SSRF.
        self.allow_private: bool = bool(cfg.get("allow_private", False))

        # Mode toggles: scrape Prometheus /metrics and/or run a flow query.
        self.scrape_metrics: bool = bool(cfg.get("scrape_metrics", True))
        self.scrape_flows: bool = bool(cfg.get("scrape_flows", True))

        try:
            self.timeout: float = float(cfg.get("timeout", 5.0))
        except (TypeError, ValueError):
            self.timeout = 5.0
        if self.timeout <= 0:
            self.timeout = 5.0

        # Optional bearer token, resolved from the environment only — never
        # accepted inline so it cannot leak through serialized config.
        token_env = cfg.get("token_env")
        self._token: str | None = (
            os.environ.get(str(token_env)) if token_env else None
        )

        self._transport: HttpTransport | None = transport
        self._flow_runner: FlowRunner | None = flow_runner
        self._resolver = resolver or (
            lambda h: [str(ai[4][0]) for ai in socket.getaddrinfo(h, None)]
        )

    # -- SSRF guard ---------------------------------------------------------

    def _guard_url(self, url: str) -> str:
        """Validate ``url`` against the SSRF policy; return it or raise."""
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise SSRFError(f"unsupported scheme: {parsed.scheme!r}")
        host = parsed.hostname
        if not host:
            raise SSRFError("missing host in URL")

        if self.allow_private:
            return url

        # Literal-host / cloud-metadata / private-IP decision is delegated to
        # the canonical general_ludd.security.ssrf.is_url_blocked (no DNS) so the
        # loopback-name, ``.localhost``-suffix, cloud-metadata-name and
        # metadata-IP (169.254.169.254 / 100.100.100.200) coverage can never
        # drift from the single source of truth.
        if is_url_blocked(url, scheme_allowlist=("http", "https")):
            raise SSRFError(
                f"target {host!r} is blocked by SSRF policy "
                "(set allow_private to permit in-cluster targets)"
            )

        # Connector-specific extra the no-DNS canonical guard cannot provide:
        # for a *named* host, resolve and re-check every address so a DNS name
        # that points (or rebinds) to a private/blocked IP is still refused at
        # request time. A literal IP was already fully vetted above.
        try:
            ipaddress.ip_address(host)
            return url
        except ValueError:
            pass
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
                    "(set allow_private to permit in-cluster targets)"
                )
        return url

    # -- transport ----------------------------------------------------------

    def _http_get(self, url: str) -> tuple[int, str]:
        self._guard_url(url)
        if self._transport is None:
            raise RuntimeError("no HTTP transport configured")
        return self._transport(url, self.timeout)

    # -- health -------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Return liveness without ever raising."""
        if not self.base_url and self._flow_runner is None:
            return {
                "ok": False,
                "detail": "no base_url and no flow_runner configured",
            }
        if self.base_url and self.scrape_metrics:
            url = f"{self.base_url}/metrics"
            try:
                status, _body = self._http_get(url)
            except SSRFError as exc:
                return {"ok": False, "detail": f"ssrf-blocked: {exc}"}
            except Exception as exc:
                return {"ok": False, "detail": f"metrics probe failed: {exc}"}
            if 200 <= status < 300:
                return {"ok": True, "detail": f"hubble metrics {status}"}
            return {"ok": False, "detail": f"metrics endpoint http {status}"}
        # Flow-only mode: a configured runner is enough to be healthy.
        if self._flow_runner is not None:
            return {"ok": True, "detail": "flow runner configured"}
        return {"ok": False, "detail": "no usable data path"}

    # -- query --------------------------------------------------------------

    def query(self, spec: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Return normalized event records from metrics and/or flows."""
        spec = dict(spec or {})
        records: list[dict[str, Any]] = []

        if self.base_url and self.scrape_metrics:
            records.extend(self._query_metrics())
        if self.scrape_flows and self._flow_runner is not None:
            records.extend(self._query_flows(spec))
        return records

    def _query_metrics(self) -> list[dict[str, Any]]:
        assert self.base_url is not None
        url = f"{self.base_url}/metrics"
        status, body = self._http_get(url)
        if not (200 <= status < 300):
            return []
        now = time.time()
        out: list[dict[str, Any]] = []
        for line in body.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            metric, value = self._parse_prom_line(line)
            if metric is None or value is None:
                continue
            metric_name, labels = metric
            out.append(
                {
                    "ts": now,
                    "source": self.name,
                    "kind": self.KIND,
                    "level_or_status": "info",
                    "message": metric_name,
                    "value": value,
                    "labels": {**labels, "metric": metric_name},
                    "raw": line,
                }
            )
        return out

    @staticmethod
    def _parse_prom_line(
        line: str,
    ) -> tuple[tuple[str, dict[str, str]] | None, float | None]:
        """Parse a single Prometheus exposition line.

        Returns ``((name, labels), value)`` or ``(None, None)`` if unparseable.
        """
        # Split off the trailing value (last whitespace-separated token).
        try:
            head, raw_value = line.rsplit(None, 1)
        except ValueError:
            return None, None
        try:
            value = float(raw_value)
        except ValueError:
            return None, None

        labels: dict[str, str] = {}
        if "{" in head and head.endswith("}"):
            name, _, rest = head.partition("{")
            label_blob = rest[:-1]  # drop trailing '}'
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
            return None, None
        return (name, labels), value

    def _query_flows(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        assert self._flow_runner is not None
        flows = self._flow_runner(spec) or []
        now = time.time()
        out: list[dict[str, Any]] = []
        for flow in flows:
            out.append(self._normalize_flow(flow, now))
        return out

    @staticmethod
    def _normalize_flow(flow: dict[str, Any], default_ts: float) -> dict[str, Any]:
        verdict = str(flow.get("verdict", "")).upper()
        level = _VERDICT_LEVEL.get(verdict, "info")

        src = flow.get("source") or {}
        dst = flow.get("destination") or {}
        l4 = flow.get("l4") or {}
        l7 = flow.get("l7") or {}

        protocol = ""
        for proto_key in ("TCP", "UDP", "ICMPv4", "ICMPv6", "SCTP"):
            if proto_key in l4:
                protocol = proto_key
                break
        if not protocol and l7:
            protocol = str(l7.get("type") or "L7")

        # Build a human-readable message describing the flow.
        src_id = _endpoint_label(src)
        dst_id = _endpoint_label(dst)
        message = f"{verdict or 'FLOW'} {src_id} -> {dst_id}"
        http = l7.get("http") if isinstance(l7, dict) else None
        dns = l7.get("dns") if isinstance(l7, dict) else None
        if isinstance(http, dict):
            method = http.get("method", "")
            url = http.get("url", "")
            message = f"{message} http {method} {url}".rstrip()
        elif isinstance(dns, dict):
            qname = dns.get("query", "")
            message = f"{message} dns {qname}".rstrip()

        ts_raw = flow.get("time") or flow.get("timestamp")
        ts = _coerce_ts(ts_raw, default_ts)

        labels = {
            "source": src_id,
            "destination": dst_id,
            "verdict": verdict or "UNKNOWN",
            "protocol": protocol or "unknown",
        }
        return {
            "ts": ts,
            "source": flow.get("node_name") or "cilium-hubble",
            "kind": "events",
            "level_or_status": level,
            "message": message,
            "value": None,
            "labels": labels,
            "raw": flow,
        }


def _endpoint_label(ep: dict[str, Any]) -> str:
    """Best-effort short label for a flow endpoint."""
    if not isinstance(ep, dict):
        return "unknown"
    namespace = ep.get("namespace")
    workload = (
        ep.get("workloads", [{}])[0].get("name")
        if isinstance(ep.get("workloads"), list) and ep.get("workloads")
        else None
    )
    name = (
        ep.get("pod_name")
        or workload
        or ep.get("identity_name")
        or ep.get("name")
    )
    ident = name or (ep.get("labels") or [None])[0] or "endpoint"
    if namespace and name:
        return f"{namespace}/{name}"
    ip = ep.get("ip") or ep.get("IP")
    return str(ident or ip or "endpoint")


def _coerce_ts(raw: Any, default: float) -> float:
    """Coerce a Hubble timestamp (epoch float or RFC3339 string) to epoch."""
    if raw is None:
        return default
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw)
    # RFC3339 like '2026-06-16T12:00:00Z'
    try:
        from datetime import datetime

        cleaned = text.replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned).timestamp()
    except ValueError:
        return default


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
