"""``GluddObserve`` — the cross-source debugging facade.

This is the workflow layer the 6 ``observe_*`` Ansible roles and any operator
drive to debug across heterogeneous backends. It composes:

- the ``Source`` structural contract (``name`` / ``KIND`` / ``health()`` /
  ``query(spec)``) from :mod:`general_ludd.connectors.base`, and
- the cross-source normalization in :mod:`general_ludd.connectors.normalize`
  (``normalize_join_keys`` / ``correlate``),

into four debugging primitives:

``query_sources(kinds, spec)``
    Fan ``spec`` across every configured source whose ``KIND`` is in ``kinds``,
    time-bounded via optional ``start`` / ``end`` (injected into the spec). The
    merged result is time-ordered. **A single source failing never aborts the
    fan-out** — its exception becomes an ``"error"`` normalized record and is
    also collected on :attr:`errors`.

``correlate_incident(seed, kinds, by)``
    Pull related records around an incident ``seed`` (the seed itself + a
    ``query_sources`` over ``kinds``) and group them by a canonical join key
    (``trace_id`` by default) via :func:`normalize.correlate`.

``timeline(window, start, end)``
    A merged, time-ordered record stream across the KINDs in ``window``,
    optionally bounded to ``[start, end]``.

``topology(kinds)``
    A service/host adjacency map derived from each record's canonical join
    keys (service <-> host).

Design constraints honoured here
--------------------------------
- **No import of** :mod:`connectors.registry`. The source provider is injected
  (dict / callable / registry-like) so this facade composes with
  ``ConnectorRegistry`` later without a hard dependency.
- **KIND is an open string set.** Concrete connectors use KINDs beyond the four
  canonical ones (e.g. PagerDuty ``"incidents"``, Okta ``"events"``); this
  facade treats KIND as an opaque string and never validates it against
  ``base.VALID_KINDS``.
- **Best-effort, never raises on a single source.** Only an unusable *provider*
  raises (at construction time).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable
from typing import Any, Protocol, runtime_checkable

from general_ludd.connectors.base import normalized_record
from general_ludd.connectors.normalize import correlate, normalize_join_keys

logger = logging.getLogger(__name__)

__all__ = ["GluddObserve", "SourceProvider"]


@runtime_checkable
class _SourceLike(Protocol):
    """Minimal structural shape the facade needs from a source."""

    name: str
    KIND: str

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]: ...


# A provider is one of:
#   * a {name: source} mapping,
#   * a zero-arg callable returning an iterable of sources,
#   * a registry-like object exposing ``by_kind(kind)`` / ``list_sources()``.
SourceProvider = (
    "dict[str, Any] | Callable[[], Iterable[Any]] | Any"  # documented union
)


class GluddObserve:
    """Cross-source debugging facade over a set of configured connectors.

    Parameters
    ----------
    provider:
        How the facade discovers its sources. One of:

        * a ``{name: source}`` dict,
        * a zero-arg callable returning an iterable of sources,
        * a registry-like object exposing ``by_kind(kind)`` (preferred, used for
          KIND-filtered fan-out) and/or ``list_sources()``.

        This is deliberately structural so the facade composes with
        :class:`connectors.registry.ConnectorRegistry` (which exposes both
        methods) WITHOUT importing it.

    Raises
    ------
    TypeError
        If ``provider`` is none of the supported shapes.
    """

    def __init__(
        self,
        provider: dict[str, Any] | Callable[[], Iterable[Any]] | Any,
    ) -> None:
        if not (
            isinstance(provider, dict)
            or callable(provider)
            or hasattr(provider, "by_kind")
            or hasattr(provider, "list_sources")
        ):
            raise TypeError(
                "provider must be a {name: source} dict, a zero-arg callable "
                "returning sources, or a registry-like object exposing "
                "by_kind()/list_sources()"
            )
        self._provider = provider
        # Errors collected on the most recent fan-out (one record per failed
        # source). Reset at the start of every fan-out so callers always see
        # the failures of the operation they just ran.
        self.errors: list[dict[str, Any]] = []

    # -- source discovery -------------------------------------------------- #
    def _all_sources(self) -> list[Any]:
        """Return every configured source, regardless of KIND."""
        provider = self._provider
        if isinstance(provider, dict):
            return list(provider.values())
        # Registry-like: prefer an explicit enumeration method.
        list_sources = getattr(provider, "list_sources", None)
        if callable(list_sources) and not isinstance(provider, type):
            try:
                return list(list_sources())
            except TypeError:
                pass
        if callable(provider):
            return list(provider())
        return []

    def _sources_for_kinds(self, kinds: Iterable[str]) -> list[Any]:
        """Return sources whose ``KIND`` is in ``kinds`` (de-duplicated, ordered).

        Uses a registry-like ``by_kind`` when available (lets a ConnectorRegistry
        do the indexing); otherwise filters the full source list. Sources with a
        falsy/absent ``KIND`` are never matched.
        """
        wanted = {k for k in kinds if k}
        by_kind = getattr(self._provider, "by_kind", None)
        if callable(by_kind) and not isinstance(self._provider, dict):
            collected: list[Any] = []
            seen: set[int] = set()
            for kind in wanted:
                try:
                    matches = by_kind(kind)
                except Exception:
                    matches = []
                for src in matches or []:
                    if id(src) not in seen:
                        seen.add(id(src))
                        collected.append(src)
            return collected
        return [s for s in self._all_sources() if getattr(s, "KIND", None) in wanted]

    # -- fan-out ----------------------------------------------------------- #
    def query_sources(
        self,
        kinds: Iterable[str],
        spec: dict[str, Any],
        *,
        start: float | None = None,
        end: float | None = None,
    ) -> list[dict[str, Any]]:
        """Fan ``spec`` across sources of ``kinds``; merge + time-order results.

        ``start`` / ``end`` (epoch seconds) are injected into a per-call copy of
        ``spec`` (only when not already present) so connectors can scope their
        query; the facade also post-filters the merged records to ``[start, end]``
        for sources that ignore the bounds.

        Per-source error isolation: a source whose ``query`` raises contributes a
        single ``"error"``-level normalized record (also appended to
        :attr:`errors`) and the fan-out continues. Never raises.
        """
        self.errors = []
        bounded_spec = dict(spec)
        if start is not None:
            bounded_spec.setdefault("start", start)
        if end is not None:
            bounded_spec.setdefault("end", end)

        merged: list[dict[str, Any]] = []
        for source in self._sources_for_kinds(kinds):
            try:
                records = source.query(bounded_spec)
            except Exception as exc:  # one flaky backend must not abort the rest
                err = self._error_record(source, exc)
                self.errors.append(err)
                merged.append(err)
                continue
            for rec in records or []:
                if isinstance(rec, dict):
                    merged.append(rec)

        if start is not None or end is not None:
            merged = [r for r in merged if self._within(r, start, end)]
        return self._sort_by_ts(merged)

    # -- incident correlation --------------------------------------------- #
    def correlate_incident(
        self,
        seed: dict[str, Any],
        *,
        kinds: Iterable[str] = ("logs", "traces", "metrics", "events"),
        by: str = "trace_id",
        window_s: float = 300.0,
        spec: dict[str, Any] | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Group records related to an incident ``seed`` by a canonical join key.

        Pulls records around the incident — the ``seed`` itself plus a
        :meth:`query_sources` over ``kinds`` time-bounded to ``[ts - window_s,
        ts + window_s]`` around the seed's timestamp (when it has one) — then
        groups everything via :func:`connectors.normalize.correlate` on the
        ``by`` join key (default ``trace_id``).

        Returns ``{join_value: [records...]}``. Records lacking the ``by`` join
        key are dropped from the result (they cannot be correlated on it), per
        ``normalize.correlate`` semantics. Never raises on a source failure
        (failures are isolated by :meth:`query_sources` and recorded on
        :attr:`errors`).
        """
        seed_ts = _coerce_ts(seed.get("ts"))
        start = end = None
        if seed_ts is not None:
            start = seed_ts - window_s
            end = seed_ts + window_s

        related = self.query_sources(
            kinds, dict(spec or {}), start=start, end=end
        )
        # The seed always participates in correlation (it anchors the group).
        records = [seed, *related]
        return correlate(records, by)

    # -- timeline ---------------------------------------------------------- #
    def timeline(
        self,
        window: Iterable[str],
        *,
        spec: dict[str, Any] | None = None,
        start: float | None = None,
        end: float | None = None,
    ) -> list[dict[str, Any]]:
        """Return a merged, time-ordered record stream across ``window`` KINDs.

        Thin debugging-oriented wrapper over :meth:`query_sources`: fan out over
        the KINDs in ``window``, optionally bounded to ``[start, end]``, and
        return the time-ordered merge (untimed records sort last).
        """
        return self.query_sources(window, dict(spec or {}), start=start, end=end)

    # -- topology ---------------------------------------------------------- #
    def topology(
        self,
        *,
        kinds: Iterable[str] = ("logs", "traces", "metrics", "events"),
        spec: dict[str, Any] | None = None,
    ) -> dict[str, dict[str, set[str]]]:
        """Derive a service/host adjacency map from canonical join keys.

        Fans a query across ``kinds``, normalizes each record's join keys
        (:func:`normalize.normalize_join_keys`), and builds two adjacency maps:

        - ``services``: ``{service_name: {host, ...}}`` — every host a service
          was observed on,
        - ``hosts``: ``{host: {service_name, ...}}`` — every service observed on
          a host.

        Hosts are canonicalized by ``normalize`` (``web-01:8080`` -> ``web-01``).
        Records lacking a ``service`` and/or ``host`` join key contribute nothing
        to the corresponding side.
        """
        records = self.query_sources(kinds, dict(spec or {}))
        services: dict[str, set[str]] = {}
        hosts: dict[str, set[str]] = {}
        for rec in records:
            join = normalize_join_keys(rec).get("join", {})
            service = join.get("service")
            host = join.get("host")
            if service is not None and host is not None:
                services.setdefault(str(service), set()).add(str(host))
                hosts.setdefault(str(host), set()).add(str(service))
        return {"services": services, "hosts": hosts}

    # -- helpers ----------------------------------------------------------- #
    @staticmethod
    def _error_record(source: Any, exc: Exception) -> dict[str, Any]:
        # Redacted: the exception detail is LOGGED, not surfaced — str(exc) can
        # carry DSNs/hosts/internal paths, and the raw exception object is not
        # safely serializable, so neither is embedded in the client-facing record.
        logger.warning(
            "observe query failed for source %s",
            getattr(source, "name", "<unknown>"),
            exc_info=True,
        )
        return dict(
            normalized_record(
                source=str(getattr(source, "name", "<unknown>")),
                kind=str(getattr(source, "KIND", "unknown")),
                level_or_status="error",
                message="query failed",
                ts=time.time(),
                raw=None,
            )
        )

    @staticmethod
    def _within(
        rec: dict[str, Any], start: float | None, end: float | None
    ) -> bool:
        """Keep untimed records and error records; bound-check timed data records.

        Untimed records (``ts is None``) and ``"error"`` records always pass —
        dropping a backend-failure record because it has no timestamp would hide
        the failure, which violates the never-silently-swallow contract.
        """
        if rec.get("level_or_status") == "error":
            return True
        ts = _coerce_ts(rec.get("ts"))
        if ts is None:
            return True
        if start is not None and ts < start:
            return False
        return not (end is not None and ts > end)

    @staticmethod
    def _sort_by_ts(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Stable sort by ts ascending; untimed records sort last."""
        return sorted(
            records,
            key=lambda r: (
                _coerce_ts(r.get("ts")) is None,
                _coerce_ts(r.get("ts")) or 0.0,
            ),
        )


def _coerce_ts(value: Any) -> float | None:
    """Best-effort float timestamp, or ``None`` (unparseable/missing)."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
