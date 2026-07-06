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

from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

__all__ = ["AwsObservabilitySource"]


@runtime_checkable
class _Client(Protocol):
    """Minimal structural type for an AWS service client."""

    def __getattr__(self, name: str) -> Any: ...  # pragma: no cover - protocol


ClientFactory = Callable[[str], Any]


def _default_client_factory(region: str | None) -> ClientFactory:
    """Return a factory that lazily builds boto3 clients.

    The ``boto3`` import is performed *inside* the returned factory so that
    importing this module never requires boto3. If boto3 is unavailable the
    factory raises ``ImportError`` with a stable, recognizable message; callers
    (e.g. :meth:`AwsObservabilitySource.health`) translate that into a
    ``boto3 unavailable`` status rather than propagating.
    """

    def factory(service: str) -> Any:
        try:
            import boto3  # type: ignore[import-not-found]  # lazy/guarded import
        except ImportError as exc:  # boto3 not installed
            raise ImportError("boto3 unavailable") from exc
        if region:
            return boto3.client(service, region_name=region)
        return boto3.client(service)

    return factory


def _epoch_seconds(value: Any) -> float | None:
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
        config: dict[str, Any] | None = None,
        *,
        client_factory: ClientFactory | None = None,
    ) -> None:
        config = config or {}
        self.config: dict[str, Any] = dict(config)
        self.name: str = str(config.get("name", self.KIND))
        self.region: str | None = config.get("region")
        # Injectable factory; default lazily imports boto3 (guarded).
        self._client_factory: ClientFactory = (
            client_factory
            if client_factory is not None
            else _default_client_factory(self.region)
        )

    # ----------------------------------------------------------------- #
    # client access
    # ----------------------------------------------------------------- #

    def _client(self, service: str) -> Any:
        return self._client_factory(service)

    # ----------------------------------------------------------------- #
    # health
    # ----------------------------------------------------------------- #

    def health(self) -> dict[str, Any]:
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

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
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
        value: Any,
        labels: dict[str, Any],
        raw: Any,
    ) -> dict[str, Any]:
        return {
            "ts": ts,
            "source": self.name,
            "kind": kind,
            "level_or_status": level_or_status,
            "message": message,
            "value": value,
            "labels": labels,
            "raw": raw,
        }

    # ----------------------------------------------------------------- #
    # mode: logs (CloudWatch Logs)
    # ----------------------------------------------------------------- #

    def _query_logs(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        client = self._client("logs")
        kwargs: dict[str, Any] = {"logGroupName": spec.get("logGroupName", "")}
        if "filterPattern" in spec:
            kwargs["filterPattern"] = spec["filterPattern"]
        if "startTime" in spec:
            kwargs["startTime"] = spec["startTime"]
        resp = client.filter_log_events(**kwargs)
        records: list[dict[str, Any]] = []
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
                        "logGroup": spec.get("logGroupName", ""),
                        "logStream": event.get("logStreamName"),
                    },
                    raw=event,
                )
            )
        return records

    # ----------------------------------------------------------------- #
    # mode: metrics (CloudWatch get_metric_data)
    # ----------------------------------------------------------------- #

    def _query_metrics(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        client = self._client("cloudwatch")
        kwargs: dict[str, Any] = {}
        for key in ("MetricDataQueries", "StartTime", "EndTime", "ScanBy"):
            if key in spec:
                kwargs[key] = spec[key]
        resp = client.get_metric_data(**kwargs)
        namespace = spec.get("namespace")
        metric_name = spec.get("metricName")
        dimensions = spec.get("dimensions", {})
        records: list[dict[str, Any]] = []
        for result in resp.get("MetricDataResults", []):
            timestamps = result.get("Timestamps", [])
            values = result.get("Values", [])
            label = result.get("Label", metric_name)
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

    def _query_traces(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        client = self._client("xray")
        kwargs: dict[str, Any] = {}
        for key in ("StartTime", "EndTime", "FilterExpression", "TimeRangeType"):
            if key in spec:
                kwargs[key] = spec[key]
        resp = client.get_trace_summaries(**kwargs)
        records: list[dict[str, Any]] = []
        for summary in resp.get("TraceSummaries", []):
            service_ids = summary.get("ServiceIds") or []
            service = service_ids[0].get("Name") if service_ids else None
            has_error = bool(summary.get("HasError"))
            records.append(
                self._record(
                    ts=_epoch_seconds(summary.get("StartTime")),
                    kind="traces",
                    level_or_status="error" if has_error else "ok",
                    message=None,
                    value=summary.get("Duration"),
                    labels={
                        "trace_id": summary.get("Id"),
                        "service": service,
                    },
                    raw=summary,
                )
            )
        return records

    # ----------------------------------------------------------------- #
    # mode: events (CloudTrail lookup_events) -> kind='logs', audit
    # ----------------------------------------------------------------- #

    def _query_events(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        client = self._client("cloudtrail")
        kwargs: dict[str, Any] = {}
        for key in ("LookupAttributes", "StartTime", "EndTime"):
            if key in spec:
                kwargs[key] = spec[key]
        resp = client.lookup_events(**kwargs)
        records: list[dict[str, Any]] = []
        for event in resp.get("Events", []):
            event_name = event.get("EventName", "")
            username = event.get("Username", "")
            message = f"{event_name} {username}".strip()
            resources = [
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
