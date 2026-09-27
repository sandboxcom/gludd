"""AWS Config + CloudTrail infra-state connector.

Self-contained source that reads AWS infra state from two backends and
normalizes each item into the project's standard event row shape::

    {ts, source, kind='infra', level_or_status, message, value, labels, raw}

Modes (selected via ``spec['mode']``):

* ``'config'`` (default) — enumerate resources with
  ``config.list_discovered_resources`` and pull the latest configuration item
  per resource via ``config.get_resource_config_history``. Each item becomes a
  row whose ``level_or_status`` is the configurationItemStatus.
* ``'cloudtrail'`` — read management/audit events via
  ``cloudtrail.lookup_events``; each event becomes an audit row
  (``level_or_status = 'audit'``).

Design constraints honored here:

* ``KIND = 'infra'`` and a stable ``name`` attribute.
* ``__init__(config)`` is config-driven. The boto3 client is created through an
  injectable ``client_factory(service_name, **kw) -> client`` so tests need no
  real boto3 and no AWS credentials. boto3 itself is imported lazily and
  guarded — when unavailable, ``health()`` reports ``'boto3 unavailable'`` and
  ``query`` returns ``[]`` instead of raising.
* No HTTP is issued directly (boto3 owns the transport), so there is no SSRF
  literal-host check here; there is also no hardcoded credential and no
  ``shell=True`` / subprocess use.
* ``health()`` never raises; it returns ``{'ok': bool, 'detail': str}``.
* ``query(spec)`` is time-bound through the factory/client config and returns a
  list of normalized dicts.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import cast

from general_ludd.connectors._aws_config_types import (
    ClientFactory,
    CloudTrailLookupEvent,
    ConfigurationItem,
    GetResourceConfigHistoryResponse,
    HealthStatus,
    ListDiscoveredResourcesResponse,
    LookupEventsResponse,
    NormalizedRecord,
    _Client,
    _default_factory,
    _TupleAwsClient,
)

__all__ = ["AwsConfigTrailSource"]

logger = logging.getLogger(__name__)


class AwsConfigTrailSource:
    """AWS Config / CloudTrail infra-state source."""

    KIND = "infra"
    name = "aws_config_trail"

    def __init__(
        self,
        config: Mapping[str, object],
        *,
        aws_client: object | None = None,
    ) -> None:
        """Build the source from connector config; an injected client wins over the boto3 factory."""
        self._config: dict[str, object] = dict(config)
        region_raw = config.get("region")
        self._region: str | None = region_raw if isinstance(region_raw, str) else None
        timeout_raw = config.get("timeout", 10.0)
        self._timeout = float(timeout_raw) if isinstance(timeout_raw, (int, float)) else 10.0
        max_results_raw = config.get("max_results", 100)
        self._max_results = int(max_results_raw) if isinstance(max_results_raw, (int, float)) else 100
        default_resource_type_raw = config.get("resource_type", "AWS::EC2::Instance")
        self._default_resource_type = (
            str(default_resource_type_raw)
            if not isinstance(default_resource_type_raw, str)
            else default_resource_type_raw
        )
        # Injectable factory wins; otherwise attempt a guarded boto3 factory.
        self._client_factory: ClientFactory | None = None
        if aws_client is not None:
            if callable(aws_client) and not hasattr(aws_client, "lookup_events"):

                def _tuple_factory(service: str) -> _Client | None:
                    return cast(_Client, _TupleAwsClient(aws_client, service))

                self._client_factory = _tuple_factory
            else:
                concrete_client = cast(_Client, aws_client)
                self._client_factory = lambda _service: concrete_client
        elif "client_factory" in config:
            factory_val = config["client_factory"]
            self._client_factory = cast(ClientFactory, factory_val) if callable(factory_val) else None
        else:
            self._client_factory = _default_factory(self._region, self._timeout)

    # -- internals -----------------------------------------------------------

    def _client(self, service_name: str) -> _Client | None:
        if self._client_factory is None:
            return None
        return self._client_factory(service_name)

    @staticmethod
    def _first(d: Mapping[str, object], *keys: str) -> object:
        """Return the first non-None value at ``keys`` in ``d`` (else None)."""
        for k in keys:
            v = d.get(k)
            if v is not None:
                return v
        return None

    def _normalize_config_item(self, item: ConfigurationItem) -> NormalizedRecord:
        resource_id_val = self._first(item, "resourceId")
        resource_id = str(resource_id_val) if resource_id_val is not None else ""
        resource_type_val = self._first(item, "resourceType")
        resource_type = str(resource_type_val) if resource_type_val is not None else ""
        status_val = self._first(item, "configurationItemStatus")
        status = str(status_val) if status_val is not None else "UNKNOWN"
        region_val = self._first(item, "awsRegion")
        region = str(region_val) if region_val is not None else (self._region or "")
        message = " ".join(p for p in (resource_type, resource_id) if p).strip()
        az_val = self._first(item, "availabilityZone")
        labels: dict[str, str] = {
            "awsRegion": region,
            "resourceType": resource_type,
            "availabilityZone": str(az_val) if az_val is not None else "",
        }
        return NormalizedRecord(
            ts=self._first(item, "configurationItemCaptureTime"),
            source=self.name,
            kind="infra",
            level_or_status=status,
            message=message,
            value=self._first(item, "configurationStateId"),
            labels=labels,
            raw=item,
        )

    def _normalize_event(self, event: CloudTrailLookupEvent) -> NormalizedRecord:
        event_name_val = self._first(event, "EventName")
        event_name = str(event_name_val) if event_name_val is not None else ""
        username_val = self._first(event, "Username")
        username = str(username_val) if username_val is not None else ""
        event_source_val = self._first(event, "EventSource")
        event_source = str(event_source_val) if event_source_val is not None else ""
        region_val = self._first(event, "AwsRegion", "awsRegion")
        region = str(region_val) if region_val is not None else (self._region or "")
        message = " ".join(p for p in (event_name, username) if p).strip()
        labels: dict[str, str] = {
            "EventSource": event_source,
            "awsRegion": region,
        }
        return NormalizedRecord(
            ts=self._first(event, "EventTime"),
            source=self.name,
            kind="infra",
            level_or_status="audit",
            message=message,
            value=self._first(event, "EventId"),
            labels=labels,
            raw=event,
        )

    # -- mode handlers -------------------------------------------------------

    def _query_config(self, spec: Mapping[str, object]) -> list[NormalizedRecord]:
        client = self._client("config")
        if client is None:
            return []
        if not hasattr(client, "list_discovered_resources"):
            lookup = getattr(client, "lookup_events", None)
            if callable(lookup):
                response = lookup(LookupAttributes=[])
                events = response.get("Events", []) if isinstance(response, Mapping) else []
                return [self._normalize_event(cast(CloudTrailLookupEvent, e)) for e in events if isinstance(e, Mapping)]
            return []
        resource_type_val = spec.get("resourceType", self._default_resource_type)
        resource_type = resource_type_val if isinstance(resource_type_val, str) else self._default_resource_type
        limit_val = spec.get("limit", self._max_results)
        limit = int(limit_val) if isinstance(limit_val, (int, float)) else self._max_results
        listed: ListDiscoveredResourcesResponse = client.list_discovered_resources(
            resourceType=resource_type, limit=limit
        )
        identifiers_raw = listed.get("resourceIdentifiers", []) if isinstance(listed, dict) else []
        rows: list[NormalizedRecord] = []
        for ident in identifiers_raw:
            resource_id_raw = ident.get("resourceId")
            if not resource_id_raw:
                continue
            resource_id = resource_id_raw if isinstance(resource_id_raw, str) else str(resource_id_raw)
            ident_type_raw = ident.get("resourceType", resource_type)
            ident_type = ident_type_raw if isinstance(ident_type_raw, str) else resource_type
            history: GetResourceConfigHistoryResponse = client.get_resource_config_history(
                resourceType=ident_type,
                resourceId=resource_id,
                limit=1,
            )
            items = history.get("configurationItems", []) if isinstance(history, dict) else []
            for item in items:
                rows.append(self._normalize_config_item(item))
        return rows

    def _query_cloudtrail(self, spec: Mapping[str, object]) -> list[NormalizedRecord]:
        client = self._client("cloudtrail")
        if client is None:
            return []
        limit_val = spec.get("limit", self._max_results)
        limit = int(limit_val) if isinstance(limit_val, (int, float)) else self._max_results
        kwargs: dict[str, object] = {"MaxResults": limit}
        if spec.get("lookup_attributes"):
            kwargs["LookupAttributes"] = spec["lookup_attributes"]
        if spec.get("start_time"):
            kwargs["StartTime"] = spec["start_time"]
        if spec.get("end_time"):
            kwargs["EndTime"] = spec["end_time"]
        result: LookupEventsResponse = client.lookup_events(**kwargs)
        events = result.get("Events", []) if isinstance(result, dict) else []
        rows: list[NormalizedRecord] = []
        for event in events:
            rows.append(self._normalize_event(event))
        return rows

    # -- public API ----------------------------------------------------------

    def health(self) -> HealthStatus:
        """Probe the source. Never raises."""
        try:
            if self._client_factory is None:
                return {"ok": False, "detail": "boto3 unavailable"}
            # Probe the factory without performing any network call.
            try:
                self._client_factory("config")
            except Exception:
                logger.warning("client factory health check failed", exc_info=True)
                return {"ok": False, "detail": "health check failed"}
            return {"ok": True, "detail": f"ready region={self._region or 'default'}"}
        except Exception:  # pragma: no cover - defensive; never raises
            logger.warning("health check failed", exc_info=True)
            return {"ok": False, "detail": "health check failed"}

    def query(self, spec: Mapping[str, object]) -> list[NormalizedRecord]:
        """Return normalized records from AWS Config or CloudTrail per ``spec['mode']``."""
        if self._client_factory is None:
            return []
        mode_raw = spec.get("mode", "config")
        mode = (str(mode_raw) if not isinstance(mode_raw, str) else mode_raw).lower()
        try:
            if mode == "config":
                return self._query_config(spec)
            if mode == "cloudtrail":
                return self._query_cloudtrail(spec)
        except Exception:
            return []
        return []
