"""Self-contained Grafana Loki observability connector.

``GrafanaLokiSource`` queries Loki's range-query API and normalizes each log
entry to the common record shape shared by every gludd observability source::

    {
        "ts": float,             # event time in epoch seconds (ns -> s)
        "source": str,           # connector name ("grafana_loki")
        "kind": str,             # "logs"
        "level_or_status": str,  # detected level from stream labels, if any
        "message": str,          # the raw log line
        "value": float | None,   # parsed numeric metric, if the entry carries one
        "labels": dict,          # the stream labels
        "raw": Any,              # the original [ts_ns, line] entry
    }

The module is deliberately standalone: it imports nothing from a connector
base class, the package ``__init__``, or any sibling connector. The HTTP
transport is injected so tests run with zero network I/O.

Security posture mirrors the SigNoz connector: literal-host SSRF blocking with
NO DNS resolution, env-var auth (never hard-coded or logged), explicit
per-request timeout, and a caller-bounded start/end window. No ``shell=True``.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit

from general_ludd.security.ssrf import is_url_blocked

__all__ = ["GrafanaLokiSource"]

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 15.0
_DEFAULT_LIMIT = 100

# Common log-level tokens we recognize when the stream carries no explicit
# ``level`` / ``detected_level`` label.
_LEVEL_LABEL_KEYS = ("detected_level", "level", "severity", "lvl")


@runtime_checkable
class _Transport(Protocol):
    """Minimal injectable HTTP transport returning ``(status_code, body)``."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = ...,
        json: Any | None = ...,
        params: dict[str, Any] | None = ...,
        timeout: float | None = ...,
    ) -> tuple[int, Any]: ...


def _validate_base_url(base_url: str) -> str:
    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https"):
        raise ValueError(f"GrafanaLokiSource: unsupported URL scheme: {parts.scheme!r}")
    host = (parts.hostname or "").strip().lower()
    if not host:
        raise ValueError(f"GrafanaLokiSource: refusing internal/blocked host: {host!r}")
    if is_url_blocked(base_url, scheme_allowlist=("http", "https")):
        raise ValueError(f"GrafanaLokiSource: refusing internal/blocked host: {host!r}")
    return base_url.rstrip("/")


class GrafanaLokiSource:
    """Read-only Grafana Loki logs source.

    Args:
        config: mapping with ``base_url`` (public Loki URL) and ``token_env``
            (name of the env var holding the bearer token).
        transport: injected HTTP transport implementing ``_Transport``.
        timeout: per-request timeout in seconds.
    """

    KIND = "logs"

    def __init__(
        self,
        config: dict[str, Any],
        *,
        transport: _Transport,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self.name = "grafana_loki"
        self._base_url = _validate_base_url(str(config["base_url"]))
        self._token_env = str(config.get("token_env", ""))
        self._transport = transport
        self._timeout = float(timeout)

    # -- internals ---------------------------------------------------------

    def _auth_headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        token = os.environ.get(self._token_env, "") if self._token_env else ""
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    @staticmethod
    def _detect_level(stream_labels: dict[str, Any]) -> str:
        for key in _LEVEL_LABEL_KEYS:
            val = stream_labels.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip().lower()
        return ""

    @staticmethod
    def _ns_to_seconds(ts_ns: Any) -> float:
        try:
            return float(int(str(ts_ns))) / 1_000_000_000.0
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _maybe_value(entry: Any) -> float | None:
        """Loki stream entries can carry a third element (a parsed metric)."""
        if isinstance(entry, (list, tuple)) and len(entry) >= 3:
            try:
                return float(entry[2])
            except (TypeError, ValueError):
                return None
        return None

    def _normalize_entry(
        self, entry: Any, stream_labels: dict[str, Any], detected: str
    ) -> dict[str, Any]:
        if isinstance(entry, (list, tuple)) and len(entry) >= 2:
            ts_ns, line = entry[0], entry[1]
        else:
            ts_ns, line = 0, ""
        return {
            "ts": self._ns_to_seconds(ts_ns),
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": detected,
            "message": str(line),
            "value": self._maybe_value(entry),
            "labels": dict(stream_labels),
            "raw": entry,
        }

    def _iter_records(self, payload: Any) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        if not isinstance(payload, dict):
            return records
        data = payload.get("data")
        if not isinstance(data, dict):
            return records
        result = data.get("result")
        if not isinstance(result, list):
            return records
        for stream in result:
            if not isinstance(stream, dict):
                continue
            labels = stream.get("stream")
            stream_labels = labels if isinstance(labels, dict) else {}
            detected = self._detect_level(stream_labels)
            values = stream.get("values")
            if not isinstance(values, list):
                continue
            for entry in values:
                records.append(self._normalize_entry(entry, stream_labels, detected))
        return records

    # -- public API --------------------------------------------------------

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        """Run a Loki ``query_range`` request and return normalized records.

        ``spec`` keys: ``query`` (LogQL), ``start`` / ``end`` (epoch seconds,
        forwarded as nanoseconds — the bounded window), optional ``limit``.
        """
        logql = str(spec.get("query", ""))
        params: dict[str, Any] = {"query": logql}
        start = spec.get("start")
        end = spec.get("end")
        if start is not None:
            params["start"] = int(float(start) * 1_000_000_000)
        if end is not None:
            params["end"] = int(float(end) * 1_000_000_000)
        params["limit"] = int(spec.get("limit", _DEFAULT_LIMIT))
        url = f"{self._base_url}/loki/api/v1/query_range"
        status_code, payload = self._transport.request(
            "GET",
            url,
            headers=self._auth_headers(),
            params=params,
            timeout=self._timeout,
        )
        if status_code >= 400:
            return []
        return self._iter_records(payload)

    def health(self) -> dict[str, Any]:
        """Probe Loki's ``/ready`` endpoint. Never raises."""
        url = f"{self._base_url}/ready"
        try:
            status_code, _payload = self._transport.request(
                "GET",
                url,
                headers=self._auth_headers(),
                timeout=self._timeout,
            )
        except Exception:  # health must never raise
            # Never echo str(exc): it can embed the Loki base URL / auth detail.
            logger.warning("grafana_loki health check failed", exc_info=True)
            return {"ok": False, "source": self.name, "error": "health check failed"}
        ok = 200 <= status_code < 300
        result: dict[str, Any] = {"ok": ok, "source": self.name, "status_code": status_code}
        if not ok:
            result["error"] = f"unhealthy status {status_code}"
        return result
