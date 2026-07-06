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
    raw               dict   the untouched upstream payload
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Protocol, TypedDict, runtime_checkable

__all__ = ["AwsPipelineSource", "NormalizedRecord"]

logger = logging.getLogger(__name__)


class NormalizedRecord(TypedDict):
    """A single normalized observability record."""

    ts: int
    source: str
    kind: str
    level_or_status: str | None
    message: str
    value: None
    labels: dict[str, str | None]
    raw: dict[str, Any]


@runtime_checkable
class _AwsClient(Protocol):
    """Structural type for the slice of a boto3 client we use.

    Both methods are part of the protocol; a given client only needs the one
    relevant to the service it represents (codepipeline vs. logs).
    """

    def list_pipeline_executions(self, **kwargs: Any) -> dict[str, Any]: ...

    def filter_log_events(self, **kwargs: Any) -> dict[str, Any]: ...


# A factory turning a service name ('codepipeline' | 'logs') into a client.
ClientFactory = Callable[[str], Any]

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

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = dict(config)
        self.region: str = str(config.get("region", ""))
        self.pipeline: str | None = config.get("pipeline")
        self.log_group: str | None = config.get("log_group")

        factory = config.get("client_factory")
        self._client_factory: ClientFactory = (
            factory if callable(factory) else self._default_client_factory
        )

        pipeline_part = self.pipeline or "unknown"
        self.name: str = f"aws-pipeline:{self.region or 'unknown'}:{pipeline_part}"

    # -- client acquisition -------------------------------------------------

    def _default_client_factory(self, service: str) -> Any:
        """Lazily import boto3 and build a real client.

        The boto3 import is intentionally local so the module imports even when
        boto3 is not installed. Relies on the standard AWS credential chain;
        no credentials are sourced from config.
        """
        try:
            import boto3  # type: ignore[import-not-found]  # boto3: optional [aws] extra, lazy-imported
        except ImportError as exc:  # boto3 not installed
            raise RuntimeError("boto3 unavailable") from exc
        return boto3.client(service, region_name=self.region or None)

    def _client(self, service: str) -> Any:
        return self._client_factory(service)

    # -- health -------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Return a health dict. Never raises.

        ``ok`` is True only when a client can be constructed. When boto3 is
        unavailable (default factory path) ``detail`` contains
        ``"boto3 unavailable"``.
        """
        base: dict[str, Any] = {
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
            base["detail"] = "boto3 unavailable"
            return base
        except Exception:  # health must never raise
            logger.warning("aws_pipeline client init failed", exc_info=True)
            base["detail"] = "client init failed"
            return base
        base["ok"] = True
        base["detail"] = "ok"
        return base

    # -- pipeline executions ------------------------------------------------

    def query(self, spec: dict[str, Any] | None = None) -> list[NormalizedRecord]:
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
        response = client.list_pipeline_executions(pipelineName=self.pipeline)
        summaries: list[dict[str, Any]] = list(
            response.get("pipelineExecutionSummaries", [])
        )

        status_filter = spec.get("status")
        records: list[NormalizedRecord] = []
        for summary in summaries:
            status = summary.get("status")
            if status_filter is not None and status != status_filter:
                continue
            records.append(self._normalize_execution(summary))

        limit = spec.get("limit")
        if isinstance(limit, int) and limit >= 0:
            records = records[:limit]
        return records

    def _normalize_execution(self, summary: dict[str, Any]) -> NormalizedRecord:
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
        response = client.filter_log_events(logGroupName=group, startTime=since)
        events: list[dict[str, Any]] = list(response.get("events", []))
        return [self._normalize_log_event(event) for event in events]

    def _normalize_log_event(self, event: dict[str, Any]) -> NormalizedRecord:
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
    def _epoch_seconds(value: Any) -> int:
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
