"""AWS Config/CloudTrail response shapes and client construction."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, TypedDict, cast, runtime_checkable


class ConfigResourceIdentifier(TypedDict, total=False):
    resourceType: str
    resourceId: str
    resourceName: str


class ListDiscoveredResourcesResponse(TypedDict, total=False):
    resourceIdentifiers: list[ConfigResourceIdentifier]


class ConfigurationItem(TypedDict, total=False):
    resourceId: str
    resourceType: str
    configurationItemStatus: str
    configurationStateId: str
    configurationItemCaptureTime: object
    awsRegion: str
    availabilityZone: str


class GetResourceConfigHistoryResponse(TypedDict, total=False):
    configurationItems: list[ConfigurationItem]


class CloudTrailLookupEvent(TypedDict, total=False):
    EventId: str
    EventName: str
    EventTime: object
    Username: str
    EventSource: str
    AwsRegion: str
    awsRegion: str
    CloudTrailEvent: str


class LookupEventsResponse(TypedDict, total=False):
    Events: list[CloudTrailLookupEvent]


class HealthStatus(TypedDict):
    ok: bool
    detail: str


class NormalizedRecord(TypedDict):
    ts: object
    source: str
    kind: str
    level_or_status: str
    message: str
    value: object
    labels: dict[str, str]
    raw: object


@runtime_checkable
class _Client(Protocol):
    def __getattr__(self, name: str) -> Any: ...


ClientFactory = Callable[[str], _Client | None]


class _TupleAwsClient:
    """Adapt one method-first callback to a service-specific client."""

    def __init__(self, fn: Callable[..., object], service_name: str) -> None:
        self._fn = fn
        self._service_name = service_name

    def lookup_events(self, **kwargs: object) -> object:
        result = self._fn("lookup_events", service_name=self._service_name, **kwargs)
        return result[1] if isinstance(result, tuple) and len(result) == 2 else result


def _default_factory(region: str | None, timeout: float) -> ClientFactory | None:
    """Build a bounded boto3 client factory when the optional extra exists."""
    try:
        import importlib

        boto3 = importlib.import_module("boto3")
        config_type = importlib.import_module("botocore.config").Config
    except Exception:
        return None
    config = config_type(
        connect_timeout=timeout,
        read_timeout=timeout,
        retries={"max_attempts": 2},
    )

    def _factory(service_name: str) -> _Client | None:
        params: dict[str, object] = {"config": config}
        if region:
            params["region_name"] = region
        return cast(_Client, boto3.client(service_name, **params))

    return _factory


__all__ = (
    "ClientFactory",
    "CloudTrailLookupEvent",
    "ConfigResourceIdentifier",
    "ConfigurationItem",
    "GetResourceConfigHistoryResponse",
    "HealthStatus",
    "ListDiscoveredResourcesResponse",
    "LookupEventsResponse",
    "NormalizedRecord",
    "_Client",
    "_TupleAwsClient",
    "_default_factory",
)
