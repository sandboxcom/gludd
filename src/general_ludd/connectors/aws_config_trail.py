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

from collections.abc import Callable
from typing import Any

ClientFactory = Callable[..., Any]


def _default_factory(region: str | None, timeout: float) -> ClientFactory | None:
    """Build a boto3-backed client_factory, or None if boto3 is unavailable.

    The import is guarded so the module is importable (and testable) on hosts
    without boto3 installed.
    """
    try:
        import boto3  # type: ignore[import-not-found]
        from botocore.config import Config  # type: ignore[import-not-found]
    except Exception:
        return None

    cfg = Config(connect_timeout=timeout, read_timeout=timeout, retries={"max_attempts": 2})

    def _factory(service_name: str, **kwargs: Any) -> Any:
        params: dict[str, Any] = {"config": cfg}
        if region:
            params["region_name"] = region
        params.update(kwargs)
        return boto3.client(service_name, **params)

    return _factory


class AwsConfigTrailSource:
    """AWS Config / CloudTrail infra-state source."""

    KIND = "infra"
    name = "aws_config_trail"

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = dict(config)
        self._region: str | None = config.get("region")
        self._timeout = float(config.get("timeout", 10.0))
        self._max_results = int(config.get("max_results", 100))
        self._default_resource_type = str(
            config.get("resource_type", "AWS::EC2::Instance")
        )
        # Injectable factory wins; otherwise attempt a guarded boto3 factory.
        if "client_factory" in config:
            self._client_factory: ClientFactory | None = config["client_factory"]
        else:
            self._client_factory = _default_factory(self._region, self._timeout)

    # -- internals -----------------------------------------------------------

    def _client(self, service_name: str) -> Any:
        if self._client_factory is None:
            return None
        return self._client_factory(service_name)

    @staticmethod
    def _first(d: dict[str, Any], *keys: str) -> Any:
        for k in keys:
            if k in d and d[k] is not None:
                return d[k]
        return None

    def _normalize_config_item(self, item: dict[str, Any]) -> dict[str, Any]:
        resource_id = str(self._first(item, "resourceId") or "")
        resource_type = str(self._first(item, "resourceType") or "")
        status = self._first(item, "configurationItemStatus") or "UNKNOWN"
        region = str(self._first(item, "awsRegion") or self._region or "")
        message = " ".join(p for p in (resource_type, resource_id) if p).strip()
        labels = {
            "awsRegion": region,
            "resourceType": resource_type,
            "availabilityZone": str(self._first(item, "availabilityZone") or ""),
        }
        return {
            "ts": self._first(item, "configurationItemCaptureTime"),
            "source": self.name,
            "kind": "infra",
            "level_or_status": status,
            "message": message,
            "value": self._first(item, "configurationStateId"),
            "labels": labels,
            "raw": item,
        }

    def _normalize_event(self, event: dict[str, Any]) -> dict[str, Any]:
        event_name = str(self._first(event, "EventName") or "")
        username = str(self._first(event, "Username") or "")
        event_source = str(self._first(event, "EventSource") or "")
        region = str(self._first(event, "AwsRegion", "awsRegion") or self._region or "")
        message = " ".join(p for p in (event_name, username) if p).strip()
        labels = {
            "EventSource": event_source,
            "awsRegion": region,
        }
        return {
            "ts": self._first(event, "EventTime"),
            "source": self.name,
            "kind": "infra",
            "level_or_status": "audit",
            "message": message,
            "value": self._first(event, "EventId"),
            "labels": labels,
            "raw": event,
        }

    # -- mode handlers -------------------------------------------------------

    def _query_config(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        client = self._client("config")
        if client is None:
            return []
        resource_type = str(spec.get("resourceType", self._default_resource_type))
        limit = int(spec.get("limit", self._max_results))
        listed = client.list_discovered_resources(
            resourceType=resource_type, limit=limit
        )
        identifiers = listed.get("resourceIdentifiers", []) if isinstance(listed, dict) else []
        rows: list[dict[str, Any]] = []
        for ident in identifiers:
            if not isinstance(ident, dict):
                continue
            resource_id = ident.get("resourceId")
            if not resource_id:
                continue
            history = client.get_resource_config_history(
                resourceType=ident.get("resourceType", resource_type),
                resourceId=resource_id,
                limit=1,
            )
            items = history.get("configurationItems", []) if isinstance(history, dict) else []
            for item in items:
                if isinstance(item, dict):
                    rows.append(self._normalize_config_item(item))
        return rows

    def _query_cloudtrail(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        client = self._client("cloudtrail")
        if client is None:
            return []
        kwargs: dict[str, Any] = {"MaxResults": int(spec.get("limit", self._max_results))}
        if spec.get("lookup_attributes"):
            kwargs["LookupAttributes"] = spec["lookup_attributes"]
        if spec.get("start_time"):
            kwargs["StartTime"] = spec["start_time"]
        if spec.get("end_time"):
            kwargs["EndTime"] = spec["end_time"]
        result = client.lookup_events(**kwargs)
        events = result.get("Events", []) if isinstance(result, dict) else []
        rows: list[dict[str, Any]] = []
        for event in events:
            if isinstance(event, dict):
                rows.append(self._normalize_event(event))
        return rows

    # -- public API ----------------------------------------------------------

    def health(self) -> dict[str, Any]:
        try:
            if self._client_factory is None:
                return {"ok": False, "detail": "boto3 unavailable"}
            # Probe the factory without performing any network call.
            try:
                self._client_factory("config")
            except Exception as exc:
                return {"ok": False, "detail": f"client factory error: {exc!r}"}
            return {"ok": True, "detail": f"ready region={self._region or 'default'}"}
        except Exception as exc:  # pragma: no cover - defensive; never raises
            return {"ok": False, "detail": f"health error: {exc!r}"}

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        if self._client_factory is None:
            return []
        mode = str(spec.get("mode", "config")).lower()
        try:
            if mode == "config":
                return self._query_config(spec)
            if mode == "cloudtrail":
                return self._query_cloudtrail(spec)
        except Exception:
            return []
        return []
