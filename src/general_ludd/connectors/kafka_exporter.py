"""Self-contained kafka_exporter (Prometheus-text) observability connector.

Scrapes the ``/metrics`` endpoint of a `kafka_exporter
<https://github.com/danielqsj/kafka_exporter>`_ instance, parses the Prometheus
*text exposition* format, and normalizes the kafka-relevant series (consumer
group lag, under-replicated partitions, broker throughput) into flat records.

Design constraints honoured here:

* No imports from sibling connectors, the package ``__init__``, or any
  connector base class — this module stands alone.
* SSRF protection blocks loopback / private / link-local / cloud-metadata
  *literal* hosts on ``base_url``. We deliberately do **not** resolve DNS
  (no DNS-rebinding surface, no network at construction time) and **allow**
  plain ``http`` because exporters are frequently exposed on internal HTTP.
* The HTTP transport is *injectable* so the connector is driven entirely from
  mocked responses in tests (no real network, no DNS, no shell, no subprocess).
* ``health()`` never raises (reports failure in ``{"ok", "detail"}``).
  ``query()`` never raises: transport / parse failures become a single
  normalized error record.
* Every backend call is time-bound (``timeout`` passed to the transport).

Record shape (one dict per metric sample)::

    {
        "ts": float,                 # scrape time (unix seconds)
        "source": str,               # connector name
        "kind": "metrics",
        "level_or_status": str,      # "" for data, "error" for failures
        "message": str,              # "<metric>{label="v", ...}"
        "value": float,              # numeric sample value
        "labels": dict[str, str],    # {topic, consumergroup, partition, ...}
        "raw": str,                  # original exposition line
    }
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from urllib.parse import urlencode, urlsplit

import httpx

from general_ludd.security.ssrf import is_url_blocked

logger = logging.getLogger(__name__)

# Injectable transport signature: (url, params, headers, timeout) -> (status, text)
HttpGet = Callable[..., "tuple[int, str]"]

KIND = "metrics"

# kafka_exporter metric names we care about. Anything else in the exposition is
# ignored (kafka_exporter emits a lot of go_/process_ noise).
_WANTED_METRICS = frozenset(
    {
        "kafka_consumergroup_lag",
        "kafka_consumergroup_lag_sum",
        "kafka_topic_partition_under_replicated_partition",
        "kafka_topic_partition_in_sync_replica",
        "kafka_brokers",
        "kafka_topic_partition_current_offset",
        "kafka_topic_partitions",
    }
)

_DEFAULT_TIMEOUT = 10.0
_METRICS_PATH = "/metrics"


def _default_http_get(
    url: str,
    params: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> tuple[int, str]:
    """Real, time-bound stdlib transport used when none is injected.

    Matches the ``(url, params, headers, timeout) -> (status, text)`` contract
    (the Prometheus text-exposition body is returned as a ``str``, not JSON).
    Only ``http``/``https`` are allowed; the request is bounded by an explicit
    timeout. The mocked tests never reach this path.
    """
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"unsupported url scheme: {parsed.scheme!r}")
    if params:
        sep = "&" if parsed.query else "?"
        url = f"{url}{sep}{urlencode(params, doseq=True)}"
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        resp = client.get(url, headers=headers or {})
    status = resp.status_code
    body = resp.content
    text = body.decode("utf-8", errors="replace") if body else ""
    return status, text


def _validate_base_url(base_url: str) -> str:
    """Reject SSRF-prone literal hosts; return a normalized base_url.

    Allows http and https only. Performs NO DNS resolution. The
    private/metadata decision is delegated to the canonical shared guard
    :func:`general_ludd.security.ssrf.is_url_blocked`.
    """
    if not base_url or not isinstance(base_url, str):
        raise ValueError("base_url is required")

    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https"):
        raise ValueError(f"unsupported scheme: {parts.scheme!r} (only http/https)")

    host = (parts.hostname or "").strip().lower()
    if not host:
        raise ValueError("base_url has no host")

    if is_url_blocked(base_url, scheme_allowlist=("http", "https")):
        raise ValueError(f"blocked internal/metadata address: {host!r}")

    return base_url.rstrip("/")


def _parse_label_block(block: str) -> dict[str, str]:
    """Parse ``a="x",b="y"`` (the inside of ``{...}``) into a dict.

    Tolerant of escaped quotes/backslashes in values per the Prometheus text
    format, and of spaces around separators.
    """
    labels: dict[str, str] = {}
    i = 0
    n = len(block)
    while i < n:
        # skip leading whitespace / commas
        while i < n and block[i] in ", \t":
            i += 1
        if i >= n:
            break
        # read key
        key_start = i
        while i < n and block[i] not in "= \t":
            i += 1
        key = block[key_start:i]
        # skip to '='
        while i < n and block[i] in " \t":
            i += 1
        if i >= n or block[i] != "=":
            break
        i += 1  # consume '='
        while i < n and block[i] in " \t":
            i += 1
        if i >= n or block[i] != '"':
            break
        i += 1  # consume opening quote
        # read value honouring \" and \\ escapes
        val_chars: list[str] = []
        while i < n:
            ch = block[i]
            if ch == "\\" and i + 1 < n:
                nxt = block[i + 1]
                if nxt == "n":
                    val_chars.append("\n")
                else:
                    val_chars.append(nxt)
                i += 2
                continue
            if ch == '"':
                i += 1  # consume closing quote
                break
            val_chars.append(ch)
            i += 1
        if key:
            labels[key] = "".join(val_chars)
    return labels


def _parse_metric_line(line: str) -> tuple[str, dict[str, str], float] | None:
    """Parse one ``metric{labels} value [ts]`` exposition line.

    Returns ``(name, labels, value)`` or ``None`` for comments / blanks /
    unparseable / non-finite samples.
    """
    s = line.strip()
    if not s or s.startswith("#"):
        return None

    if "{" in s:
        name_end = s.index("{")
        name = s[:name_end].strip()
        close = s.rfind("}")
        if close < 0:
            return None
        label_block = s[name_end + 1 : close]
        rest = s[close + 1 :].strip()
        labels = _parse_label_block(label_block)
    else:
        parts = s.split()
        if len(parts) < 2:
            return None
        name = parts[0]
        labels = {}
        rest = " ".join(parts[1:])

    if not name:
        return None

    value_token = rest.split()[0] if rest.split() else ""
    try:
        value = float(value_token)
    except (TypeError, ValueError):
        # Prometheus permits NaN/+Inf; we drop those rather than crash.
        return None

    return name, labels, value


def _fmt_message(name: str, labels: dict[str, str]) -> str:
    if not labels:
        return name
    inner = ", ".join(f'{k}="{labels[k]}"' for k in sorted(labels))
    return f"{name}{{{inner}}}"


class KafkaExporterSource:
    """A metrics source backed by a kafka_exporter ``/metrics`` endpoint."""

    KIND = KIND

    def __init__(
        self,
        config: dict[str, object],
        http_get: HttpGet | None = None,
        *,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        base_url = str(config.get("base_url", ""))
        self._base_url = _validate_base_url(base_url)
        self._token_env = config.get("token_env")
        self._http_get = http_get or _default_http_get
        self._timeout = float(timeout)
        self.kind = KIND
        host = urlsplit(self._base_url).netloc
        self.name = config.get("name") or f"kafka_exporter:{host}"

    # -- helpers ----------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "text/plain"}
        if self._token_env:
            token = os.environ.get(str(self._token_env))
            if token:
                headers["Authorization"] = f"Bearer {token}"
        return headers

    def _error_record(self, message: str, raw: object) -> dict[str, object]:
        return {
            "ts": time.time(),
            "source": self.name,
            "kind": KIND,
            "level_or_status": "error",
            "message": message,
            "value": 0.0,
            "labels": {},
            "raw": raw,
        }

    def _sample_record(
        self, name: str, labels: dict[str, str], value: float, ts: float, raw: str
    ) -> dict[str, object]:
        return {
            "ts": ts,
            "source": self.name,
            "kind": KIND,
            "level_or_status": "",
            "message": _fmt_message(name, labels),
            "value": value,
            "labels": labels,
            "raw": raw,
        }

    # -- public API -------------------------------------------------------

    def query(self, spec: dict[str, object]) -> list[dict[str, object]]:
        """Scrape ``/metrics`` and normalize wanted kafka series.

        ``spec`` may carry ``metrics`` (an explicit allowlist of metric names)
        to override the default kafka allowlist. Never raises: failures become a
        single error record.
        """
        url = f"{self._base_url}{_METRICS_PATH}"
        try:
            status, payload = self._http_get(
                url, params=None, headers=self._headers(), timeout=self._timeout
            )
        except Exception as exc:  # surfaced as a record, never raised
            return [self._error_record(f"transport error: {exc}", {"url": url})]

        if not (200 <= int(status) < 300):
            return [self._error_record(f"http status {status}", {"status": status})]

        if not isinstance(payload, str):
            return [self._error_record("non-text /metrics payload", payload)]

        wanted = spec.get("metrics")
        if isinstance(wanted, (list, tuple, set, frozenset)) and wanted:
            wanted_set = frozenset(wanted)
        else:
            wanted_set = _WANTED_METRICS

        ts = time.time()
        records: list[dict[str, object]] = []
        for line in payload.splitlines():
            parsed = _parse_metric_line(line)
            if parsed is None:
                continue
            name, labels, value = parsed
            if name not in wanted_set:
                continue
            records.append(self._sample_record(name, labels, value, ts, line.strip()))
        return records

    def health(self) -> dict[str, object]:
        """Return ``{"ok": bool, "detail": str}``. Never raises.

        Scrapes ``/metrics`` and treats a 2xx text response as healthy.
        """
        url = f"{self._base_url}{_METRICS_PATH}"
        try:
            status, payload = self._http_get(
                url, params=None, headers=self._headers(), timeout=self._timeout
            )
        except Exception:  # health must never raise
            logger.warning("health check failed", exc_info=True)
            return {"ok": False, "detail": "health check failed"}

        ok = 200 <= int(status) < 300 and isinstance(payload, str)
        if ok:
            return {"ok": True, "detail": f"scrape ok (status {status})"}
        return {"ok": False, "detail": f"unhealthy (status {status})"}
