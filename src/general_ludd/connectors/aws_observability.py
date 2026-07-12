"""AWS observability connector — a single SigV4-family source.

``AwsObservabilitySource`` exposes four read modes over the AWS SigV4 service
family, selected by ``spec['mode']``:

* ``logs``    — CloudWatch Logs ``filter_log_events``
* ``metrics`` — CloudWatch ``get_metric_data``
* ``traces``  — X-Ray ``get_trace_summaries``
* ``events``  — CloudTrail ``lookup_events``

Design notes
------------
* **Injectable client factory.** ``__init__`` accepts ``client_factory(service)
  -> client``. Tests inject fakes; production defaults to a lazy boto3-backed
  factory. The ``boto3`` import is *guarded* so this module imports cleanly even
  when boto3 is absent — in that case ``health()`` reports ``boto3 unavailable``.
* **No hardcoded credentials.** Auth uses the standard AWS credential chain via
  boto3; this module neither accepts nor stores secret material. Only a
  ``region`` is read from config.
* **SSRF.** Not applicable — calls go through SDK service endpoints, not
  user-supplied URLs.
* **Never raises from health().** ``health()`` always returns ``{'ok','detail'}``.

This module is self-contained: it defines no base class and imports no sibling
connector (the ``connectors`` package is a namespace package).

"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Protocol, TypedDict, cast, runtime_checkable

__all__ = ["AwsObservabilitySource"]


# --------------------------------------------------------------------------- #
# AWS API response shapes
# --------------------------------------------------------------------------- #
# All response TypedDicts are ``total=False`` because boto3 omits empty/missing
# fields rather than emitting nulls; every key is therefore optional at runtime.


class CloudWatchLogEvent(TypedDict, total=False):
    """One row from ``logs.filter_log_events``."""

    timestamp: int | float
    message: str
    logStreamName: str


class FilterLogEventsResponse(TypedDict, total=False):
    """Response of ``logs.filter_log_events``."""

    events: list[CloudWatchLogEvent]


class MetricDataResult(TypedDict, total=False):
    """One row from ``cloudwatch.get_metric_data`` results."""

    Id: str
    Label: str
    Timestamps: list[datetime]
    Values: list[float]


class GetMetricDataResponse(TypedDict, total=False):
    """Response of ``cloudwatch.get_metric_data``."""

    MetricDataResults: list[MetricDataResult]


class TraceServiceId(TypedDict, total=False):
    """``ServiceIds`` entry inside a TraceSummary."""

    Name: str


class TraceSummary(TypedDict, total=False):
    """One row from ``xray.get_trace_summaries``."""

    Id: str
    Duration: float
    HasError: bool
    ServiceIds: list[TraceServiceId]
    StartTime: datetime


class GetTraceSummariesResponse(TypedDict, total=False):
    """Response of ``xray.get_trace_summaries``."""

    TraceSummaries: list[TraceSummary]


class CloudTrailResource(TypedDict, total=False):
    """``Resources`` entry inside a CloudTrail event."""

    ResourceType: str
    ResourceName: str


class CloudTrailLookupEvent(TypedDict, total=False):
    """One row from ``cloudtrail.lookup_events``."""

    EventName: str
    Username: str
    EventTime: datetime
    EventSource: str
    AwsRegion: str
    Resources: list[CloudTrailResource]


class LookupEventsResponse(TypedDict, total=False):
    """Response of ``cloudtrail.lookup_events``."""

    Events: list[CloudTrailLookupEvent]


class HealthStatus(TypedDict):
    """Return shape of :meth:`AwsObservabilitySource.health`."""

    ok: bool
    detail: str


class NormalizedRecord(TypedDict):
    """A single normalized observability record produced by ``query()``."""

    ts: float | None
    source: str
    kind: str
    level_or_status: str | None
    message: str | None
    value: float | None
    labels: dict[str, object]
    raw: object


# --------------------------------------------------------------------------- #
# Client protocol + factory
# --------------------------------------------------------------------------- #


@runtime_checkable
class _Client(Protocol):
    """Minimal structural type for an AWS service client.

    boto3 generates one client class per service, each exposing a different
    method surface (``filter_log_events`` vs ``get_metric_data`` vs ...). The
    only honest static type for ``client.<arbitrary_method>`` is therefore
    ``Any`` — this is the documented dynamic-dispatch exception from the
    type-safety skill. The TypedDicts above re-assert the known shape the
    moment we bind the response to a name.
    """

    def __getattr__(self, name: str) -> Callable[..., object]: ...  # pragma: no cover - protocol


ClientFactory = Callable[[str], _Client]


def _default_client_factory(region: str | None) -> ClientFactory:
    """Return a factory that lazily builds boto3 clients.

    The ``boto3`` import is performed *inside* the returned factory so that
    importing this module never requires boto3. If boto3 is unavailable the
    factory raises ``ImportError`` with a stable, recognizable message; callers
    (e.g. :meth:`AwsObservabilitySource.health`) translate that into a
    ``boto3 unavailable`` status rather than propagating.
    """

    def factory(service: str) -> _Client:
        try:
            import importlib

            boto3 = importlib.import_module("boto3")  # lazy/guarded import
        except ImportError as exc:  # boto3 not installed
            raise ImportError("boto3 unavailable") from exc
        if region:
            return cast(_Client, boto3.client(service, region_name=region))
        return cast(_Client, boto3.client(service))

    return factory


def _epoch_seconds(value: object) -> float | None:
    """Best-effort conversion of a timestamp-ish value to epoch seconds."""
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, (int, float)):
        return float(value)
    return None


class AwsObservabilitySource:
    """Config-driven AWS observability source over the SigV4 service family."""

    KIND: str = "aws_observability"

    def __init__(
        self,
        config: Mapping[str, object] | None = None,
        *,
        client_factory: ClientFactory | None = None,
    ) -> None:
        config = config or {}
        self.config: dict[str, object] = dict(config)
        name_raw = config.get("name", self.KIND)
        self.name: str = str(name_raw) if not isinstance(name_raw, str) else name_raw
        region_raw = config.get("region")
        self.region: str | None = region_raw if isinstance(region_raw, str) else None
        # Injectable factory; default lazily imports boto3 (guarded).
        self._client_factory: ClientFactory = (
            client_factory
            if client_factory is not None
            else _default_client_factory(self.region)
        )

    # ----------------------------------------------------------------- #
    # client access
    # ----------------------------------------------------------------- #

    def _client(self, service: str) -> _Client:
        return self._client_factory(service)

    # ----------------------------------------------------------------- #
    # health
    # ----------------------------------------------------------------- #

    def health(self) -> HealthStatus:
        """Report connector health. Never raises.

        Probes the client factory with a benign service. If boto3 is missing
        the factory raises ``ImportError('boto3 unavailable')`` which we surface
        as a not-ok status.
        """
        try:
            self._client("cloudwatch")
        except ImportError as exc:
            return {"ok": False, "detail": f"boto3 unavailable: {exc}"}
        except Exception as exc:  # health must never raise
            return {"ok": False, "detail": str(exc)}
        return {"ok": True, "detail": f"client factory ready (region={self.region})"}

    # ----------------------------------------------------------------- #
    # query dispatch
    # ----------------------------------------------------------------- #

    def query(self, spec: Mapping[str, object]) -> list[NormalizedRecord]:
        """Run a query in the mode named by ``spec['mode']`` and normalize."""
        mode = spec.get("mode")
        if mode == "logs":
            return self._query_logs(spec)
        if mode == "metrics":
            return self._query_metrics(spec)
        if mode == "traces":
            return self._query_traces(spec)
        if mode == "events":
            return self._query_events(spec)
        raise ValueError(f"unknown or missing query mode: {mode!r}")

    # ----------------------------------------------------------------- #
    # normalization helper
    # ----------------------------------------------------------------- #

    def _record(
        self,
        *,
        ts: float | None,
        kind: str,
        level_or_status: str | None,
        message: str | None,
        value: float | None,
        labels: dict[str, object],
        raw: object,
    ) -> NormalizedRecord:
        return NormalizedRecord(
            ts=ts,
            source=self.name,
            kind=kind,
            level_or_status=level_or_status,
            message=message,
            value=value,
            labels=labels,
            raw=raw,
        )

    # ----------------------------------------------------------------- #
    # mode: logs (CloudWatch Logs)
    # ----------------------------------------------------------------- #

    def _query_logs(self, spec: Mapping[str, object]) -> list[NormalizedRecord]:
        client = self._client("logs")
        kwargs: dict[str, object] = {"logGroupName": spec.get("logGroupName", "")}
        if "filterPattern" in spec:
            kwargs["filterPattern"] = spec["filterPattern"]
        if "startTime" in spec:
            kwargs["startTime"] = spec["startTime"]
        resp = cast(FilterLogEventsResponse, client.filter_log_events(**kwargs))
        log_group_raw = spec.get("logGroupName", "")
        log_group = log_group_raw if isinstance(log_group_raw, str) else ""
        records: list[NormalizedRecord] = []
        for event in resp.get("events", []):
            ts_ms = event.get("timestamp")
            ts = ts_ms / 1000 if isinstance(ts_ms, (int, float)) else None
            records.append(
                self._record(
                    ts=ts,
                    kind="logs",
                    level_or_status=None,
                    message=event.get("message"),
                    value=None,
                    labels={
                        "logGroup": log_group,
                        "logStream": event.get("logStreamName"),
                    },
                    raw=event,
                )
            )
        return records

    # ----------------------------------------------------------------- #
    # mode: metrics (CloudWatch get_metric_data)
    # ----------------------------------------------------------------- #

    def _query_metrics(self, spec: Mapping[str, object]) -> list[NormalizedRecord]:
        client = self._client("cloudwatch")
        kwargs: dict[str, object] = {}
        for key in ("MetricDataQueries", "StartTime", "EndTime", "ScanBy"):
            if key in spec:
                kwargs[key] = spec[key]
        resp = cast(GetMetricDataResponse, client.get_metric_data(**kwargs))
        namespace_raw = spec.get("namespace")
        namespace = namespace_raw if isinstance(namespace_raw, str) else None
        metric_name_raw = spec.get("metricName")
        metric_name = metric_name_raw if isinstance(metric_name_raw, str) else None
        dimensions = spec.get("dimensions", {})
        records: list[NormalizedRecord] = []
        for result in resp.get("MetricDataResults", []):
            timestamps = result.get("Timestamps", [])
            values = result.get("Values", [])
            label_raw = result.get("Label", metric_name)
            label = label_raw if isinstance(label_raw, str) else metric_name
            for point_ts, point in zip(timestamps, values, strict=False):
                records.append(
                    self._record(
                        ts=_epoch_seconds(point_ts),
                        kind="metrics",
                        level_or_status=None,
                        message=label,
                        value=point,
                        labels={
                            "namespace": namespace,
                            "metricName": metric_name,
                            "dimensions": dimensions,
                        },
                        raw=result,
                    )
                )
        return records

    # ----------------------------------------------------------------- #
    # mode: traces (X-Ray get_trace_summaries)
    # ----------------------------------------------------------------- #

    def _query_traces(self, spec: Mapping[str, object]) -> list[NormalizedRecord]:
        client = self._client("xray")
        kwargs: dict[str, object] = {}
        for key in ("StartTime", "EndTime", "FilterExpression", "TimeRangeType"):
            if key in spec:
                kwargs[key] = spec[key]
        resp = cast(GetTraceSummariesResponse, client.get_trace_summaries(**kwargs))
        records: list[NormalizedRecord] = []
        for summary in resp.get("TraceSummaries", []):
            service_ids = summary.get("ServiceIds") or []
            first_service = service_ids[0] if service_ids else None
            service_name: str | None = (
                first_service.get("Name")
                if first_service is not None
                else None
            )
            has_error = bool(summary.get("HasError"))
            duration_raw = summary.get("Duration")
            duration: float | None = (
                float(duration_raw)
                if isinstance(duration_raw, (int, float))
                else None
            )
            records.append(
                self._record(
                    ts=_epoch_seconds(summary.get("StartTime")),
                    kind="traces",
                    level_or_status="error" if has_error else "ok",
                    message=None,
                    value=duration,
                    labels={
                        "trace_id": summary.get("Id"),
                        "service": service_name,
                    },
                    raw=summary,
                )
            )
        return records

    # ----------------------------------------------------------------- #
    # mode: events (CloudTrail lookup_events) -> kind='logs', audit
    # ----------------------------------------------------------------- #

    def _query_events(self, spec: Mapping[str, object]) -> list[NormalizedRecord]:
        client = self._client("cloudtrail")
        kwargs: dict[str, object] = {}
        for key in ("LookupAttributes", "StartTime", "EndTime"):
            if key in spec:
                kwargs[key] = spec[key]
        resp = cast(LookupEventsResponse, client.lookup_events(**kwargs))
        records: list[NormalizedRecord] = []
        for event in resp.get("Events", []):
            event_name = event.get("EventName", "")
            username = event.get("Username", "")
            message = f"{event_name} {username}".strip()
            resources: list[dict[str, object]] = [
                {
                    "type": r.get("ResourceType"),
                    "name": r.get("ResourceName"),
                }
                for r in event.get("Resources", [])
            ]
            records.append(
                self._record(
                    ts=_epoch_seconds(event.get("EventTime")),
                    kind="logs",
                    level_or_status="audit",
                    message=message,
                    value=None,
                    labels={
                        "EventSource": event.get("EventSource"),
                        "awsRegion": event.get("AwsRegion"),
                        "resources": resources,
                    },
                    raw=event,
                )
            )
        return records
