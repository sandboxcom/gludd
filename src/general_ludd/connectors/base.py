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

import contextlib
import logging
import math
import operator
import sys
import threading
from collections.abc import Mapping
from typing import Literal, Protocol, TypedDict, cast, runtime_checkable

from general_ludd.security.ssrf import is_url_blocked

logger = logging.getLogger(__name__)

# D-30: per-source and global fan-out caps to prevent OOM from a runaway source.
_GLOBAL_CAP = 50_000
_PER_SOURCE_CAP = 10_000  # per-source cap is tighter than the global cap
_BYTE_BUDGET = 50 * 1024 * 1024  # 50 MB


def _finite_or_none(value: float | None) -> float | None:
    """Return ``value`` if it is finite, else ``None``."""
    if value is None:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value

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
    labels: dict[str, object]
    raw: object


def normalized_record(
    *,
    source: str,
    kind: str,
    message: str = "",
    ts: float | None = None,
    level_or_status: str = "info",
    value: float | None = None,
    labels: Mapping[str, object] | None = None,
    raw: object = None,
) -> NormalizedRecord:
    """Build a :class:`NormalizedRecord` with well-formed defaults.

    Connectors should funnel every backend row through this so the facade can
    rely on all eight keys being present.

    D-31: NaN/Inf guards — non-finite ts or value are set to None so downstream
    consumers never receive a NaN/Inf that could corrupt metrics or sort order.
    """
    # D-31: sanitize ts
    if ts is not None and not math.isfinite(ts):
        logger.warning("non-finite ts %r coerced to None", ts)
        ts = None
    # D-31: sanitize value
    if value is not None and isinstance(value, float) and not math.isfinite(value):
        logger.warning("non-finite value %r coerced to None", value)
        value = None
    return NormalizedRecord(
        ts=ts,
        source=source,
        kind=kind,
        level_or_status=level_or_status,
        message=message,
        value=value,
        labels=dict(labels) if labels is not None else {},
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

    def health(self) -> dict[str, object]:
        """Return a status dict. MUST NOT raise — report failure in the dict."""
        ...

    def query(self, spec: dict[str, object]) -> list[dict[str, object]]:
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
        name = getattr(source, "name", "<unknown>")
        self._sources[str(name)] = source

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

    MAX_FIND_RESULTS: int = _GLOBAL_CAP

    def __init__(self, registry: SourceRegistry) -> None:
        self._registry = registry

    # -- query fan-out ----------------------------------------------------- #
    def find(self, spec: dict[str, object], kinds: list[str] | None = None) -> list[dict[str, object]]:
        """Fan ``spec`` across matching sources; merge + sort the results by ts.

        ``kinds`` restricts the fan-out to sources of those kinds (default: all).

        Resilience: each source is queried independently. If a source's
        ``query()`` raises, the exception is captured as an ``"error"``-level
        normalized record attributed to that source and the fan-out continues —
        one flaky backend never aborts the whole ``find``.

        D-30: per-source cap (_PER_SOURCE_CAP) and global cap (_GLOBAL_CAP /
        _BYTE_BUDGET) prevent a runaway source from causing OOM.
        """
        if kinds is None:
            sources = self._registry.all()
        else:
            wanted = set(kinds)
            sources = [s for s in self._registry.all() if s.KIND in wanted]

        merged: list[dict[str, object]] = []
        _global_count = 0
        _byte_count = 0
        for source in sources:
            if _global_count >= _GLOBAL_CAP or _byte_count >= _BYTE_BUDGET:
                break
            try:
                raw_records = source.query(spec)
            except Exception:
                # Resilience is the whole point: a single source blowing up must
                # never abort the fan-out — capture it as an error record.
                error_rec: dict[str, object] = dict(
                    normalized_record(
                        source=getattr(source, "name", "<unknown>"),
                        kind=getattr(source, "KIND", "unknown"),
                        level_or_status="error",
                        message="query failed",
                        raw=None,
                    )
                )
                merged.append(error_rec)
                _global_count += 1
                continue
            # D-30: per-source cap
            if len(raw_records) > _PER_SOURCE_CAP:
                logger.warning(
                    "find(): source %r returned %d records, truncated to %d (per-source cap)",
                    getattr(source, "name", "<unknown>"),
                    len(raw_records),
                    _PER_SOURCE_CAP,
                )
                raw_records = raw_records[:_PER_SOURCE_CAP]
            for rec in raw_records:
                if _global_count >= _GLOBAL_CAP or _byte_count >= _BYTE_BUDGET:
                    logger.warning(
                        "find(): global cap reached, results truncated to %d",
                        _GLOBAL_CAP,
                    )
                    break
                merged.append(rec)
                _global_count += 1
                with contextlib.suppress(Exception):
                    _byte_count += sys.getsizeof(rec)

        if _global_count >= _GLOBAL_CAP:
            logger.warning(
                "find(): total results truncated to %d (global cap)",
                _GLOBAL_CAP,
            )

        return self._sort_by_ts(merged)

    @staticmethod
    def _is_missing_ts(ts: object) -> bool:
        """``True`` for a ``ts`` that is missing *or* non-finite.

        A connector returning ``float("nan")`` (or ``inf``/``-inf``) poisons
        ordering: NaN comparisons are neither ``<`` nor ``>=`` anything, which
        breaks the total order ``sorted()`` assumes. Treat non-finite the
        same as ``None`` (missing) so it sorts last, deterministically,
        instead of corrupting the comparison.
        """
        if ts is None:
            return True
        try:
            f = float(cast(float, ts))
        except (TypeError, ValueError):
            return True
        return not math.isfinite(f)

    @staticmethod
    def _sort_key_ts(ts: object) -> float:
        """Sanitize a ``ts`` value into a finite sort key (epoch-min for missing/non-finite)."""
        if Observability._is_missing_ts(ts):
            return 0.0
        return float(cast(float, ts))

    @staticmethod
    def _sort_by_ts(records: list[dict[str, object]]) -> list[dict[str, object]]:
        """Stable sort by ts ascending; ``None``/non-finite timestamps sort last."""
        return sorted(
            records,
            key=lambda r: (
                Observability._is_missing_ts(r.get("ts")),
                Observability._sort_key_ts(r.get("ts")),
            ),
        )

    # -- correlation ------------------------------------------------------- #
    @staticmethod
    def associate(
        records: list[dict[str, object]],
        by: str = "trace_id",
        window_s: float = 60.0,
    ) -> list[dict[str, object]]:
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
    def _associate_by_label(records: list[dict[str, object]], label: str) -> list[dict[str, object]]:
        groups: dict[str, list[dict[str, object]]] = {}
        order: list[str] = []
        for rec in records:
            labels = cast(dict[str, object], rec.get("labels")) or {}
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
    def _associate_by_window(records: list[dict[str, object]], window_s: float) -> list[dict[str, object]]:
        timed = [r for r in records if r.get("ts") is not None]
        timed.sort(key=operator.itemgetter("ts"))

        groups: list[dict[str, object]] = []
        current: list[dict[str, object]] = []
        anchor_ts: float | None = None

        for rec in timed:
            ts = float(cast(float, rec["ts"]))
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
# Connectors permit plain http (observability backends are frequently plain http
# on internal-but-allowlisted hosts) in addition to https.
_ALLOWED_SCHEMES = frozenset({"http", "https"})


def is_safe_endpoint(url: str) -> bool:
    """Literal-host SSRF guard for observability backend URLs.

    Returns ``True`` only when ``url`` is an ``http``/``https`` URL whose *literal*
    host is not an obvious internal/metadata target. This mirrors a fetch-URL
    guard but intentionally permits plain ``http`` (observability backends are
    frequently plain http on internal-but-allowlisted hosts).

    The host/scheme decision is delegated to the canonical
    :func:`general_ludd.security.ssrf.is_url_blocked` so this guard can never
    drift weaker than the others — it now blocks the SAME hostile set, including
    ``instance-data`` and the Alibaba metadata IP ``100.100.100.200`` that this
    layer previously missed. The check is purely literal — it NEVER performs DNS
    resolution. A hostname that does not resolve still passes (the connector
    layer, not this guard, owns network egress policy / allowlisting). What is
    rejected:

    - non-http(s) schemes,
    - loopback hosts (``localhost``, ``127.0.0.0/8``, ``::1``),
    - the cloud metadata IPs ``169.254.169.254`` / ``100.100.100.200`` and
      link-local ranges,
    - RFC-1918 private ranges (``10/8``, ``172.16/12``, ``192.168/16``),
    - unique-local IPv6 (``fc00::/7``),
    - named metadata hosts (``metadata.google.internal``, ``metadata``,
      ``instance-data``, ``localhost.localdomain``).
    """
    return not is_url_blocked(url, scheme_allowlist=_ALLOWED_SCHEMES)


# --------------------------------------------------------------------------- #
# Connector healthcheck contract
# --------------------------------------------------------------------------- #
HealthStatus = Literal["healthy", "degraded", "unhealthy"]

_DEFAULT_HEALTH_TIMEOUT = 30.0


class HealthResult(TypedDict):
    status: str
    detail: str
    source: str


def _is_configured(source: Source) -> bool:
    """Heuristic: does the source appear to have working credentials?

    Inspects ``*_env`` attributes on the instance:
    - At least one ``*_env`` attr that names an env var present in
      ``os.environ`` → configured.
    - ``*_env`` attrs exist but none resolve to a set env var → unconfigured.
    - No ``*_env`` attrs at all → indeterminate → assume configured (the
      connector may use non-env-var configuration such as a local file path).
    """
    import os

    has_env_attrs = False
    for attr_name in dir(source):
        if not attr_name.lower().endswith("_env"):
            continue
        value = getattr(source, attr_name, None)
        if not isinstance(value, str) or not value:
            continue
        has_env_attrs = True
        if value in os.environ:
            return True
    # No *_env attrs → this source doesn't use env-var config (e.g. local
    # files, pure-parser connectors).  Only consider it unconfigured if it
    # declares *_env attrs but none are satisfied.
    return not has_env_attrs


def classify_health(result: dict[str, object], source_name: str) -> HealthResult:
    """Normalize a connector's ``health()`` return dict to a :class:`HealthResult`.

    Mapping rules (first-match):
    - ``ok == True``  → ``healthy``
    - ``ok == False`` AND source appears unconfigured → ``degraded``
    - ``ok == False`` (configured but failing probe) → ``unhealthy``
    - missing ``ok`` key → ``unhealthy`` (unrecognised format)
    - exception captured earlier → ``unhealthy``
    """
    ok = result.get("ok")
    if ok is True:
        return HealthResult(
            status="healthy",
            detail=str(result.get("detail") or "ok"),
            source=source_name,
        )
    if ok is False:
        return HealthResult(
            status="unhealthy",
            detail=str(result.get("detail") or result.get("error") or "health check failed"),
            source=source_name,
        )
    return HealthResult(
        status="unhealthy",
        detail="unrecognised health format (missing 'ok' key)",
        source=source_name,
    )


def classify_health_for_source(
    source: Source, result: dict[str, object]
) -> HealthResult:
    """Like :func:`classify_health` but inspects whether *source* is configured.

    An unconfigured source that returns ``ok == False`` gets ``degraded``
    instead of ``unhealthy`` — missing credentials is an operator-configuration
    gap, not a backend failure.
    """
    classification = classify_health(result, getattr(source, "name", "?"))
    if classification["status"] == "unhealthy" and not _is_configured(source):
        classification["status"] = "degraded"
        classification["detail"] = (
            f"unconfigured — {classification['detail']}"
        )
    return classification


def run_healthcheck(
    source: Source,
    *,
    timeout: float = _DEFAULT_HEALTH_TIMEOUT,
) -> HealthResult:
    """Run ``source.health()`` with a timeout guard. Never raises.

    On timeout the result is ``unhealthy`` with a timeout detail. On any other
    exception the result is also ``unhealthy`` with the exception class in the
    detail (never the message string — credential leak prevention).
    """
    result_holder: dict[str, object] = {}
    error_holder: Exception | None = None
    done = threading.Event()

    def _probe() -> None:
        nonlocal error_holder
        try:
            result_holder.update(source.health())
        except Exception as exc:
            error_holder = exc
        finally:
            done.set()

    thread = threading.Thread(target=_probe, daemon=True)
    thread.start()
    finished = done.wait(timeout)

    if not finished:
        return HealthResult(
            status="unhealthy",
            detail=f"healthcheck timeout: timed out after {timeout:.1f}s",
            source=getattr(source, "name", "?"),
        )

    if error_holder is not None:
        return HealthResult(
            status="unhealthy",
            detail=f"exception during healthcheck: {type(error_holder).__name__}",
            source=getattr(source, "name", "?"),
        )

    raw = result_holder
    if not isinstance(raw, dict) or not raw:
        return HealthResult(
            status="unhealthy",
            detail="health() returned empty or non-dict",
            source=getattr(source, "name", "?"),
        )

    return classify_health_for_source(source, raw)
