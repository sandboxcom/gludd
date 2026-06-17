"""Connector contract: Source Protocol, normalized record, registry, and facade.

This module is the *spine* of the pluggable observability/pipeline integration
layer. It is deliberately free of any concrete backend client so that the
correlation/fan-out logic can be tested and reasoned about in isolation.

Layers
------
- ``Source`` (Protocol) + 4 marker subtypes (``PipelineSource``, ``LogSource``,
  ``MetricSource``, ``TraceSource``): the structural contract a connector must
  satisfy. ``health()`` must never raise; ``query(spec)`` returns a list of
  *normalized records*.
- ``NormalizedRecord`` (TypedDict) + ``normalized_record()`` builder: the shared
  shape every record carries, so the facade can merge/sort/correlate across
  heterogeneous backends.
- ``SourceRegistry``: a name -> source map populated at runtime.
- ``Observability``: pure orchestration over a registry — ``find()`` fans a
  query across matching sources (resilient: a failing source becomes an error
  record, never an aborted find), ``associate()`` correlates records.
- ``is_safe_endpoint()``: a literal-host SSRF guard connectors import before
  talking to a configured backend URL.
"""

from __future__ import annotations

import ipaddress
import math
import operator
from typing import Any, Protocol, TypedDict, runtime_checkable
from urllib.parse import urlsplit

# Valid KIND values for the four marker source subtypes.
PIPELINE_KIND = "pipeline"
LOG_KIND = "logs"
METRIC_KIND = "metrics"
TRACE_KIND = "traces"

VALID_KINDS = frozenset({PIPELINE_KIND, LOG_KIND, METRIC_KIND, TRACE_KIND})


# --------------------------------------------------------------------------- #
# Normalized record contract
# --------------------------------------------------------------------------- #
class NormalizedRecord(TypedDict):
    """One unit of telemetry, normalized across every backend.

    Keys
    ----
    ts:
        Epoch seconds (float) the event occurred, or ``None`` if the backend
        does not attach a timestamp. ``None``-ts records sort *after* timed ones.
    source:
        The registered name of the source that produced this record.
    kind:
        One of ``VALID_KINDS`` (``pipeline`` / ``logs`` / ``metrics`` /
        ``traces``).
    level_or_status:
        Log level (``info`` / ``error`` / ...) or pipeline/job status
        (``success`` / ``failed`` / ...). The facade tags its own failure
        records with ``"error"`` here.
    message:
        Human-readable line.
    value:
        Numeric payload for metric records (else ``None``).
    labels:
        Free-form string->Any tags — correlation keys (``trace_id``, ``commit``)
        live here.
    raw:
        The untouched backend payload, for drill-down.
    """

    ts: float | None
    source: str
    kind: str
    level_or_status: str
    message: str
    value: float | None
    labels: dict[str, Any]
    raw: Any


def normalized_record(
    *,
    source: str,
    kind: str,
    message: str = "",
    ts: float | None = None,
    level_or_status: str = "info",
    value: float | None = None,
    labels: dict[str, Any] | None = None,
    raw: Any = None,
) -> NormalizedRecord:
    """Build a :class:`NormalizedRecord` with well-formed defaults.

    Connectors should funnel every backend row through this so the facade can
    rely on all eight keys being present.
    """
    # Guard against NaN/Inf which are not JSON-serializable and cause downstream
    # issues in sorting and aggregation.
    if value is not None and not math.isfinite(value):
        value = None
    if ts is not None:
        try:
            ts_f = float(ts)
            ts = None if not math.isfinite(ts_f) else ts_f
        except (TypeError, ValueError):
            ts = None
    return NormalizedRecord(
        ts=ts,
        source=source,
        kind=kind,
        level_or_status=level_or_status,
        message=message,
        value=value,
        labels=labels if labels is not None else {},
        raw=raw,
    )


# --------------------------------------------------------------------------- #
# Source Protocol + marker subtypes
# --------------------------------------------------------------------------- #
@runtime_checkable
class Source(Protocol):
    """Structural contract every connector must satisfy.

    Implementations need not inherit from this class — duck typing is enough,
    which keeps the facade decoupled from concrete connector base classes.
    """

    name: str
    KIND: str

    def health(self) -> dict[str, Any]:
        """Return a status dict. MUST NOT raise — report failure in the dict."""
        ...

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        """Return a list of normalized-record dicts matching ``spec``."""
        ...


@runtime_checkable
class PipelineSource(Source, Protocol):
    """A source of CI/CD pipeline + job records (``KIND == 'pipeline'``)."""


@runtime_checkable
class LogSource(Source, Protocol):
    """A source of log records (``KIND == 'logs'``)."""


@runtime_checkable
class MetricSource(Source, Protocol):
    """A source of metric samples (``KIND == 'metrics'``)."""


@runtime_checkable
class TraceSource(Source, Protocol):
    """A source of distributed-trace spans (``KIND == 'traces'``)."""


# --------------------------------------------------------------------------- #
# SourceRegistry
# --------------------------------------------------------------------------- #
class SourceRegistry:
    """A runtime name -> :class:`Source` map.

    Registering a second source under an existing name overwrites the first —
    callers reconfiguring a backend get last-write-wins rather than a duplicate.
    """

    def __init__(self) -> None:
        self._sources: dict[str, Source] = {}

    def register(self, source: Source) -> None:
        """Add (or replace) a source, keyed by its ``.name``."""
        self._sources[source.name] = source

    def get(self, name: str) -> Source | None:
        """Return the source registered under ``name``, or ``None``."""
        return self._sources.get(name)

    def by_kind(self, kind: str) -> list[Source]:
        """Return every registered source whose ``.KIND`` equals ``kind``."""
        return [s for s in self._sources.values() if kind == s.KIND]

    def all(self) -> list[Source]:
        """Return every registered source."""
        return list(self._sources.values())


# --------------------------------------------------------------------------- #
# Observability facade
# --------------------------------------------------------------------------- #
class Observability:
    """Pure orchestration over a :class:`SourceRegistry`.

    Depends only on the ``Source`` Protocol and the normalized-record contract —
    never on a concrete connector. The registry is populated at runtime, so this
    facade can fan a query across whatever backends an operator has wired up.
    """

    def __init__(self, registry: SourceRegistry) -> None:
        self._registry = registry

    # -- query fan-out ----------------------------------------------------- #
    def find(self, spec: dict[str, Any], kinds: list[str] | None = None) -> list[dict[str, Any]]:
        """Fan ``spec`` across matching sources; merge + sort the results by ts.

        ``kinds`` restricts the fan-out to sources of those kinds (default: all).

        Resilience: each source is queried independently. If a source's
        ``query()`` raises, the exception is captured as an ``"error"``-level
        normalized record attributed to that source and the fan-out continues —
        one flaky backend never aborts the whole ``find``.
        """
        if kinds is None:
            sources = self._registry.all()
        else:
            wanted = set(kinds)
            sources = [s for s in self._registry.all() if s.KIND in wanted]

        MAX_RECORDS = 50_000
        merged: list[dict[str, Any]] = []
        for source in sources:
            try:
                merged.extend(source.query(spec))
            except Exception as exc:
                # Resilience is the whole point: a single source blowing up must
                # never abort the fan-out — capture it as an error record.
                error_rec: dict[str, Any] = dict(
                    normalized_record(
                        source=getattr(source, "name", "<unknown>"),
                        kind=getattr(source, "KIND", "unknown"),
                        level_or_status="error",
                        message=f"query failed: {exc}",
                        raw=exc,
                    )
                )
                merged.append(error_rec)
            if len(merged) >= MAX_RECORDS:
                merged = merged[:MAX_RECORDS]
                break

        return self._sort_by_ts(merged)

    @staticmethod
    def _sort_by_ts(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Stable sort by ts ascending; ``None`` timestamps sort last."""
        return sorted(
            records,
            key=lambda r: (r.get("ts") is None, r.get("ts") if r.get("ts") is not None else 0.0),
        )

    # -- correlation ------------------------------------------------------- #
    @staticmethod
    def associate(
        records: list[dict[str, Any]],
        by: str = "trace_id",
        window_s: float = 60.0,
    ) -> list[dict[str, Any]]:
        """Correlate ``records`` into groups.

        ``by``:
            - ``"trace_id"`` / ``"commit"`` (or any other label name): group
              records that share that label value. Records missing the label are
              dropped (they cannot be correlated on that key).
            - ``"time_window"``: greedily cluster records whose timestamps fall
              within ``window_s`` seconds of the cluster's first record. Records
              with no ts are skipped.

        Returns a list of group dicts: ``{"key": <group key>, "records": [...]}``.
        """
        if by == "time_window":
            return Observability._associate_by_window(records, window_s)
        return Observability._associate_by_label(records, by)

    @staticmethod
    def _associate_by_label(records: list[dict[str, Any]], label: str) -> list[dict[str, Any]]:
        groups: dict[str, list[dict[str, Any]]] = {}
        order: list[str] = []
        for rec in records:
            labels = rec.get("labels") or {}
            key = labels.get(label)
            if key is None:
                continue
            key_str = str(key)
            if key_str not in groups:
                groups[key_str] = []
                order.append(key_str)
            groups[key_str].append(rec)
        return [{"key": k, "records": groups[k]} for k in order]

    @staticmethod
    def _associate_by_window(records: list[dict[str, Any]], window_s: float) -> list[dict[str, Any]]:
        timed = [r for r in records if r.get("ts") is not None]
        timed.sort(key=operator.itemgetter("ts"))

        groups: list[dict[str, Any]] = []
        current: list[dict[str, Any]] = []
        anchor_ts: float | None = None

        for rec in timed:
            ts = float(rec["ts"])
            if anchor_ts is None or ts - anchor_ts > window_s:
                if current:
                    groups.append({"key": anchor_ts, "records": current})
                current = [rec]
                anchor_ts = ts
            else:
                current.append(rec)
        if current:
            groups.append({"key": anchor_ts, "records": current})
        return groups


# --------------------------------------------------------------------------- #
# SSRF guard
# --------------------------------------------------------------------------- #
# Hostnames that name a cloud/internal metadata endpoint by name (no IP).
_BLOCKED_HOST_NAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "metadata.google.internal",
        "metadata",
    }
)

_ALLOWED_SCHEMES = frozenset({"http", "https"})


def is_safe_endpoint(url: str) -> bool:
    """Literal-host SSRF guard for observability backend URLs.

    Returns ``True`` only when ``url`` is an ``http``/``https`` URL whose *literal*
    host is not an obvious internal/metadata target. This mirrors a fetch-URL
    guard but intentionally permits plain ``http`` (observability backends are
    frequently plain http on internal-but-allowlisted hosts).

    The check is purely literal — it NEVER performs DNS resolution. A hostname
    that does not resolve still passes (the connector layer, not this guard,
    owns network egress policy / allowlisting). What is rejected:

    - non-http(s) schemes,
    - loopback hosts (``localhost``, ``127.0.0.0/8``, ``::1``),
    - the cloud metadata IP ``169.254.169.254`` and link-local ranges,
    - RFC-1918 private ranges (``10/8``, ``172.16/12``, ``192.168/16``),
    - unique-local IPv6 (``fc00::/7``),
    - named metadata hosts (``metadata.google.internal``, ...).
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return False

    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        return False

    host = parts.hostname
    if not host:
        return False

    host_l = host.lower()
    if host_l in _BLOCKED_HOST_NAMES:
        return False

    # If the host is a literal IP, classify it; otherwise it is a DNS name we do
    # NOT resolve — accept it (literal-host policy).
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True

    return not (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )
