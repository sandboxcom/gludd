"""AWS pipeline observability connector.

Self-contained source for AWS CodePipeline execution history with kind-bridging
into CloudWatch Logs. It deliberately depends on nothing else in this package
(no shared base class, no sibling connectors) so it can be vendored or tested in
isolation.

Design notes
------------
* boto3 is an OPTIONAL dependency. The import is guarded so this module always
  imports cleanly even when boto3 is absent; in that case ``health()`` reports a
  not-ok status with ``detail`` containing ``"boto3 unavailable"`` and ``query``
  raises a clear ``RuntimeError``.
* The boto3 client is obtained through an injectable factory. Tests pass a fake
  ``client_factory(service) -> client`` via config so no network or credentials
  are ever touched. The default factory lazily imports boto3 and relies on the
  standard AWS credential chain — no credentials are read from config or
  hardcoded here.

Normalized record schema (every emitted record is a dict with exactly):
    ts                int    epoch seconds
    source            str    this source's ``name``
    kind              str    'pipeline' for executions, 'logs' for log events
    level_or_status   str|None  execution status, or parsed log level
    message           str
    value             None   (reserved; always None for this source)
    labels            dict[str, str | None]
    raw               object the untouched upstream payload
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any, Protocol, TypedDict, cast, runtime_checkable

__all__ = ["AwsPipelineSource", "NormalizedRecord"]

logger = logging.getLogger(__name__)


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
    eventId: str


class FilterLogEventsResponse(TypedDict, total=False):
    """Response of ``logs.filter_log_events``."""

    events: list[CloudWatchLogEvent]


class PipelineExecutionTrigger(TypedDict, total=False):
    """``trigger`` sub-document inside a PipelineExecutionSummary."""

    triggerType: str
    trigger: str


class PipelineExecutionSummary(TypedDict, total=False):
    """One row from ``codepipeline.list_pipeline_executions``."""

    pipelineExecutionId: str
    status: str
    lastUpdateTime: object  # boto3 returns datetime | epoch int/float
    trigger: PipelineExecutionTrigger


class ListPipelineExecutionsResponse(TypedDict, total=False):
    """Response of ``codepipeline.list_pipeline_executions``."""

    pipelineExecutionSummaries: list[PipelineExecutionSummary]


class HealthStatus(TypedDict):
    """Return shape of :meth:`AwsPipelineSource.health`."""

    ok: bool
    kind: str
    name: str
    region: str
    detail: str


class NormalizedRecord(TypedDict):
    """A single normalized observability record."""

    ts: int
    source: str
    kind: str
    level_or_status: str | None
    message: str
    value: None
    labels: dict[str, str | None]
    raw: object


# --------------------------------------------------------------------------- #
# Client protocol + factory
# --------------------------------------------------------------------------- #


@runtime_checkable
class _AwsClient(Protocol):
    """Structural type for the slice of a boto3 client we use.

    Both methods are part of the protocol; a given client only needs the one
    relevant to the service it represents (codepipeline vs. logs). ``__getattr__``
    is the dynamic-dispatch escape hatch (boto3 generates one client class per
    service) — this is the documented ``Any`` exception per the type-safety
    skill.
    """

    def __getattr__(self, name: str) -> Any: ...  # pragma: no cover - protocol


class AwsClientCallback(Protocol):
    """Compact generated-workflow callback for AWS API methods."""

    def __call__(self, method: str, **kwargs: object) -> tuple[int, object]: ...


class _CallbackAwsClient:
    """Expose a compact callback through the boto3-style method interface."""

    def __init__(self, callback: AwsClientCallback) -> None:
        self._callback = callback

    def __getattr__(self, method: str) -> Any:
        def invoke(**kwargs: object) -> dict[str, object]:
            status, payload = self._callback(method, **kwargs)
            if not (200 <= int(status) < 300):
                raise RuntimeError(f"AWS {method} request failed with HTTP {status}")
            if not isinstance(payload, Mapping):
                raise RuntimeError(f"AWS {method} response was not an object")
            return dict(payload)

        return invoke


# A factory turning a service name ('codepipeline' | 'logs') into a client.
ClientFactory = Callable[[str], _AwsClient]

# Known CloudWatch-Logs / generic log levels, longest-first so 'WARNING' is
# matched before a hypothetical shorter prefix would be.
_LOG_LEVELS: tuple[str, ...] = (
    "CRITICAL",
    "WARNING",
    "ERROR",
    "DEBUG",
    "TRACE",
    "FATAL",
    "INFO",
    "WARN",
)


class AwsPipelineSource:
    """Observability source over AWS CodePipeline executions (+ CloudWatch Logs).

    Parameters
    ----------
    config:
        Mapping with keys:
          * ``region`` (required) — AWS region name.
          * ``pipeline`` (optional) — CodePipeline name; required by ``query``.
          * ``log_group`` (optional) — default CloudWatch log group for
            ``fetch_logs``.
          * ``client_factory`` (optional) — ``factory(service) -> client``. When
            absent, a default factory lazily imports boto3 and constructs a
            real client using the standard AWS credential chain.
    """

    KIND: str = "pipeline"

    def __init__(
        self,
        config: Mapping[str, object],
        *,
        aws_client: AwsClientCallback | None = None,
    ) -> None:
        self._config: dict[str, object] = dict(config)
        self.region: str = str(config.get("region", ""))
        pipeline_raw = config.get("pipeline")
        if not isinstance(pipeline_raw, str):
            pipeline_raw = config.get("name")
        self.pipeline: str | None = pipeline_raw if isinstance(pipeline_raw, str) else None
        log_group_raw = config.get("log_group")
        self.log_group: str | None = log_group_raw if isinstance(log_group_raw, str) else None

        factory = config.get("client_factory")
        if aws_client is not None and callable(factory):
            raise ValueError("client_factory and aws_client are mutually exclusive")
        if aws_client is not None:
            self._client_factory = lambda _service: cast(
                _AwsClient, _CallbackAwsClient(aws_client)
            )
        else:
            self._client_factory = (
                factory if callable(factory) else self._default_client_factory
            )

        pipeline_part = self.pipeline or "unknown"
        self.name: str = f"aws-pipeline:{self.region or 'unknown'}:{pipeline_part}"

    # -- client acquisition -------------------------------------------------

    def _default_client_factory(self, service: str) -> _AwsClient:
        """Lazily import boto3 and build a real client.

        The boto3 import is intentionally local so the module imports even when
        boto3 is not installed. Relies on the standard AWS credential chain;
        no credentials are sourced from config.
        """
        try:
            import importlib

            boto3 = importlib.import_module("boto3")  # boto3: optional [aws] extra, lazy-imported
        except ImportError as exc:  # boto3 not installed
            raise RuntimeError("boto3 unavailable") from exc
        return cast(_AwsClient, boto3.client(service, region_name=self.region or None))

    def _client(self, service: str) -> _AwsClient:
        return self._client_factory(service)

    # -- health -------------------------------------------------------------

    def health(self) -> HealthStatus:
        """Return a health dict. Never raises.

        ``ok`` is True only when a client can be constructed. When boto3 is
        unavailable (default factory path) ``detail`` contains
        ``"boto3 unavailable"``.
        """
        base: HealthStatus = {
            "ok": False,
            "kind": self.KIND,
            "name": self.name,
            "region": self.region,
            "detail": "",
        }
        try:
            self._client("codepipeline")
        except RuntimeError:
            # The default factory's only RuntimeError is the boto3-import
            # sentinel; never echo str(exc) (a custom factory could embed an
            # endpoint/credential). Log the real detail, return the sentinel.
            logger.warning("aws_pipeline client init failed", exc_info=True)
            return {**base, "detail": "boto3 unavailable"}
        except Exception:  # health must never raise
            logger.warning("aws_pipeline client init failed", exc_info=True)
            return {**base, "detail": "client init failed"}
        return {**base, "ok": True, "detail": "ok"}

    # -- pipeline executions ------------------------------------------------

    def query(self, spec: Mapping[str, object] | None = None) -> list[NormalizedRecord]:
        """List recent CodePipeline executions as normalized records.

        ``spec`` filters:
          * ``limit`` (int) — cap on returned records (applied after status
            filtering, preserving upstream order).
          * ``status`` (str) — keep only executions whose status matches.
        """
        spec = spec or {}
        if not self.pipeline:
            raise ValueError("query requires a 'pipeline' name in config")

        client = self._client("codepipeline")
        response: ListPipelineExecutionsResponse = client.list_pipeline_executions(
            pipelineName=self.pipeline
        )
        summaries: list[PipelineExecutionSummary] = list(
            response.get("pipelineExecutionSummaries", [])
        )

        status_filter = spec.get("status")
        records: list[NormalizedRecord] = []
        for summary in summaries:
            status = summary.get("status")
            if status_filter is not None and status != status_filter:
                continue
            records.append(self._normalize_execution(summary))

        limit_raw = spec.get("limit")
        if isinstance(limit_raw, int) and limit_raw >= 0:
            records = records[:limit_raw]
        return records

    def _normalize_execution(self, summary: PipelineExecutionSummary) -> NormalizedRecord:
        execution_id = str(summary.get("pipelineExecutionId", ""))
        status = summary.get("status")
        trigger = summary.get("trigger")
        trigger_type: str | None = (
            trigger.get("triggerType") if isinstance(trigger, dict) else None
        )
        message = f"pipeline={self.pipeline} execution={execution_id} status={status}"
        return NormalizedRecord(
            ts=self._epoch_seconds(summary.get("lastUpdateTime")),
            source=self.name,
            kind=self.KIND,
            level_or_status=status if status is None else str(status),
            message=message,
            value=None,
            labels={"executionId": execution_id, "trigger": trigger_type},
            raw=summary,
        )

    # -- CloudWatch Logs bridge --------------------------------------------

    def fetch_logs(
        self, log_group: str | None, since: int
    ) -> list[NormalizedRecord]:
        """Fetch CloudWatch Logs events as normalized ``kind='logs'`` records.

        ``log_group`` falls back to the config ``log_group`` when falsy.
        ``since`` is a CloudWatch ``startTime`` in epoch milliseconds.
        """
        group = log_group or self.log_group
        if not group:
            raise ValueError("fetch_logs requires a log_group (arg or config)")

        client = self._client("logs")
        response: FilterLogEventsResponse = client.filter_log_events(
            logGroupName=group, startTime=since
        )
        events: list[CloudWatchLogEvent] = list(response.get("events", []))
        return [self._normalize_log_event(event) for event in events]

    def _normalize_log_event(self, event: CloudWatchLogEvent) -> NormalizedRecord:
        message = str(event.get("message", ""))
        timestamp_ms = event.get("timestamp")
        ts = int(timestamp_ms) // 1000 if isinstance(timestamp_ms, (int, float)) else 0
        labels: dict[str, str | None] = {
            "logStreamName": event.get("logStreamName"),
            "eventId": event.get("eventId"),
        }
        return NormalizedRecord(
            ts=ts,
            source=self.name,
            kind="logs",
            level_or_status=self._parse_level(message),
            message=message,
            value=None,
            labels=labels,
            raw=event,
        )

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _parse_level(message: str) -> str | None:
        """Best-effort log-level extraction from the start of a message."""
        head = message.lstrip()
        upper = head.upper()
        for level in _LOG_LEVELS:
            if upper.startswith(level):
                return level
        return None

    @staticmethod
    def _epoch_seconds(value: object) -> int:
        """Coerce a CodePipeline ``lastUpdateTime`` to epoch seconds.

        Accepts an int/float epoch or an object exposing ``timestamp()`` (e.g. a
        ``datetime``, as boto3 returns). Falls back to 0 when unparsable.
        """
        if isinstance(value, bool):  # bool is an int subclass; reject explicitly
            return 0
        if isinstance(value, (int, float)):
            return int(value)
        timestamp = getattr(value, "timestamp", None)
        if callable(timestamp):
            try:
                return int(timestamp())
            except Exception:  # defensive coercion
                return 0
        return 0
