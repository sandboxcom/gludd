"""Fixed-origin HTTP transport for bounded Azure Container Apps ARM reads."""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Mapping
from typing import Final, Self

import httpx

_ARM_ORIGIN: Final = "https://management.azure.com"
ENVIRONMENT_PREFLIGHT_API_VERSION: Final = "2025-07-01"
_ENVIRONMENT_LIFECYCLE_API_VERSION: Final = "2025-07-01"
_CONTAINER_APP_API_VERSION: Final = "2025-01-01"
_MAX_RESPONSE_BYTES: Final = 1024 * 1024
_MAX_TOKEN_CHARS: Final = 8192
_MAX_INVENTORY_ITEMS: Final = 256
_RESOURCE_GROUP_PATTERN = re.compile(r"(?=.{1,90}\Z)[A-Za-z0-9_().-]+(?<!\.)")
_RESOURCE_NAME_PATTERN = re.compile(r"(?=.{1,64}\Z)[A-Za-z0-9_.-]+")


class AzureContainerAppARMError(RuntimeError):
    """Report a fixed-context ARM refusal without response or credential data."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        """Initialize a censored error with an optional HTTP status code."""
        super().__init__(message)
        self.status_code = status_code


class _DuplicateJSONField(ValueError):
    pass


def _reject_duplicate_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJSONField
        result[key] = value
    return result


def _resource_root(
    subscription_id: str,
    resource_group: str,
    environment_name: str,
) -> str:
    try:
        canonical_subscription = str(uuid.UUID(subscription_id))
    except (ValueError, AttributeError):
        canonical_subscription = ""
    if subscription_id != canonical_subscription:
        raise ValueError("subscription_id must be a canonical UUID")
    if _RESOURCE_GROUP_PATTERN.fullmatch(resource_group) is None:
        raise ValueError("resource_group must be a safe Azure resource name")
    if _RESOURCE_NAME_PATTERN.fullmatch(environment_name) is None:
        raise ValueError("environment_name must be a safe Azure resource name")
    return (
        f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}/"
        f"providers/Microsoft.App/managedEnvironments/{environment_name}"
    )


def _resource_group_prefix(resource_root: str) -> str:
    marker = "/managedEnvironments/"
    if marker not in resource_root:
        raise ValueError("ARM resource path could not be constructed")
    prefix, _separator, _name = resource_root.partition(marker)
    return prefix


def _container_app_path(
    subscription_id: str,
    resource_group: str,
    app_name: str,
) -> str:
    root = _resource_root(subscription_id, resource_group, app_name)
    return (
        f"{_resource_group_prefix(root)}/containerApps/{app_name}"
        f"?api-version={_CONTAINER_APP_API_VERSION}"
    )


def _container_app_list_path(resource_root: str) -> str:
    return (
        f"{_resource_group_prefix(resource_root)}/containerApps"
        f"?api-version={_CONTAINER_APP_API_VERSION}"
    )


def _validate_request(
    path: str,
    bearer_token: str,
    approved_paths: frozenset[str],
) -> None:
    if not isinstance(path, str) or path not in approved_paths:
        raise ValueError("path must be an approved read-only ARM path")
    if (
        not isinstance(bearer_token, str)
        or not bearer_token
        or len(bearer_token) > _MAX_TOKEN_CHARS
        or any(character.isspace() or ord(character) < 32 for character in bearer_token)
    ):
        raise ValueError("bearer token has an invalid shape")


def _read_response(response: httpx.Response) -> bytes:
    if response.status_code != 200:
        raise AzureContainerAppARMError(
            f"Azure Resource Manager read returned status {response.status_code}",
            status_code=response.status_code,
        )
    content_type = response.headers.get("content-type", "").partition(";")[0].strip()
    if content_type.casefold() != "application/json":
        raise AzureContainerAppARMError(
            "Azure Resource Manager response must use a JSON content type"
        )
    raw_length = response.headers.get("content-length")
    if raw_length is not None:
        try:
            content_length = int(raw_length)
        except ValueError:
            raise AzureContainerAppARMError(
                "Azure Resource Manager response has an invalid content length"
            ) from None
        if content_length < 0 or content_length > _MAX_RESPONSE_BYTES:
            raise AzureContainerAppARMError("Azure Resource Manager response is too large")
    chunks: list[bytes] = []
    received = 0
    for chunk in response.iter_bytes():
        received += len(chunk)
        if received > _MAX_RESPONSE_BYTES:
            raise AzureContainerAppARMError("Azure Resource Manager response is too large")
        chunks.append(chunk)
    return b"".join(chunks)


def _decode_json(raw: bytes) -> object:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise AzureContainerAppARMError(
            "Azure Resource Manager response must contain valid UTF-8 JSON"
        ) from None
    try:
        return json.loads(text, object_pairs_hook=_reject_duplicate_fields)
    except _DuplicateJSONField:
        raise AzureContainerAppARMError(
            "Azure Resource Manager response contains a duplicate JSON field"
        ) from None
    except (json.JSONDecodeError, ValueError, TypeError):
        raise AzureContainerAppARMError(
            "Azure Resource Manager response must contain valid JSON"
        ) from None


class _BoundedARMReader:
    def __init__(
        self,
        approved_paths: frozenset[str],
        *,
        client: httpx.Client | None,
        max_connections: int,
    ) -> None:
        self._approved_paths = approved_paths
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=_ARM_ORIGIN,
            follow_redirects=False,
            timeout=httpx.Timeout(10.0),
            limits=httpx.Limits(
                max_connections=max_connections,
                max_keepalive_connections=1,
            ),
            trust_env=False,
            headers={"accept": "application/json"},
        )

    def get(
        self,
        path: str,
        bearer_token: str,
        *,
        absent_on_404: bool = False,
        lifecycle: bool = False,
    ) -> object | None:
        _validate_request(path, bearer_token, self._approved_paths)
        try:
            with self._client.stream(
                "GET",
                path,
                headers={"authorization": f"Bearer {bearer_token}"},
                follow_redirects=False,
            ) as response:
                if absent_on_404 and response.status_code == 404:
                    return None
                return _decode_json(_read_response(response))
        except AzureContainerAppARMError:
            raise
        except httpx.HTTPError:
            qualifier = " lifecycle" if lifecycle else ""
            raise AzureContainerAppARMError(
                f"Azure Resource Manager{qualifier} read failed"
            ) from None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


class _OwnedARMTransport:
    _reader: _BoundedARMReader

    def close(self) -> None:
        """Close only a client allocated by this transport."""
        self._reader.close()

    def __enter__(self) -> Self:
        """Return this transport as an owned context resource."""
        return self

    def __exit__(
        self,
        _exception_type: object,
        _exception: object,
        _traceback: object,
    ) -> None:
        """Close the owned transport when its context exits."""
        self.close()


class HttpxARMJSONTransport(_OwnedARMTransport):
    """Issue bounded GETs for one named environment at the fixed ARM origin."""

    def __init__(
        self,
        *,
        subscription_id: str,
        resource_group: str,
        environment_name: str,
        client: httpx.Client | None = None,
    ) -> None:
        """Bind the transport to one exact managed environment."""
        root = _resource_root(subscription_id, resource_group, environment_name)
        approved = frozenset(
            {
                f"{root}?api-version={ENVIRONMENT_PREFLIGHT_API_VERSION}",
                f"{root}/usages?api-version={ENVIRONMENT_PREFLIGHT_API_VERSION}",
                f"{root}/workloadProfileStates?api-version={ENVIRONMENT_PREFLIGHT_API_VERSION}",
            }
        )
        self._reader = _BoundedARMReader(approved, client=client, max_connections=2)

    def get_json(self, path: str, bearer_token: str) -> object:
        """GET one allowlisted ARM resource and decode bounded JSON."""
        return self._reader.get(path, bearer_token)


class HttpxContainerAppARMTransport(_OwnedARMTransport):
    """Issue bounded GETs for one exact Container App at the fixed ARM origin."""

    def __init__(
        self,
        *,
        subscription_id: str,
        resource_group: str,
        app_name: str,
        client: httpx.Client | None = None,
    ) -> None:
        """Bind the transport to one exact Container App resource."""
        self._path = _container_app_path(subscription_id, resource_group, app_name)
        self._reader = _BoundedARMReader(
            frozenset({self._path}),
            client=client,
            max_connections=1,
        )

    def get_json(self, bearer_token: str) -> object | None:
        """Return the exact app document, or ``None`` only for an exact 404."""
        return self._reader.get(self._path, bearer_token, absent_on_404=True)


def _inventory_app_ids(
    document: object,
    *,
    environment_id: str,
    app_id_prefix: str,
) -> tuple[str, ...]:
    try:
        if not isinstance(document, Mapping):
            raise ValueError
        if set(document) - {"value", "nextLink"}:
            raise ValueError
        if document.get("nextLink") not in (None, ""):
            raise ValueError
        items = document["value"]
        if not isinstance(items, list) or len(items) > _MAX_INVENTORY_ITEMS:
            raise ValueError
        seen: set[str] = set()
        matching: list[str] = []
        for item in items:
            if not isinstance(item, Mapping):
                raise ValueError
            resource_id = item.get("id")
            properties = item.get("properties")
            if not isinstance(resource_id, str) or not isinstance(properties, Mapping):
                raise ValueError
            normalized_id = resource_id.casefold()
            if not normalized_id.startswith(app_id_prefix.casefold()):
                raise ValueError
            app_name = resource_id[len(app_id_prefix) :]
            if _RESOURCE_NAME_PATTERN.fullmatch(app_name) is None or normalized_id in seen:
                raise ValueError
            seen.add(normalized_id)
            observed_environment = properties.get("managedEnvironmentId")
            if not isinstance(observed_environment, str):
                raise ValueError
            if observed_environment.casefold() == environment_id.casefold():
                matching.append(resource_id)
        return tuple(sorted(matching, key=str.casefold))
    except (KeyError, TypeError, ValueError):
        raise AzureContainerAppARMError(
            "Azure Resource Manager app inventory is incomplete or ambiguous"
        ) from None


class HttpxContainerAppEnvironmentLifecycleTransport(_OwnedARMTransport):
    """Read one environment and its resource-group app inventory, never mutate."""

    def __init__(
        self,
        *,
        subscription_id: str,
        resource_group: str,
        environment_name: str,
        client: httpx.Client | None = None,
    ) -> None:
        """Bind both reads to one exact resource group and environment."""
        self._environment_id = _resource_root(
            subscription_id,
            resource_group,
            environment_name,
        )
        self._environment_path = (
            f"{self._environment_id}?api-version={_ENVIRONMENT_LIFECYCLE_API_VERSION}"
        )
        self._inventory_path = _container_app_list_path(self._environment_id)
        inventory_suffix = f"?api-version={_CONTAINER_APP_API_VERSION}"
        self._app_id_prefix = self._inventory_path.removesuffix(inventory_suffix) + "/"
        self._reader = _BoundedARMReader(
            frozenset({self._environment_path, self._inventory_path}),
            client=client,
            max_connections=2,
        )

    def get_environment(self, bearer_token: str) -> object | None:
        """Return the exact environment document, or ``None`` only on 404."""
        return self._reader.get(
            self._environment_path,
            bearer_token,
            absent_on_404=True,
            lifecycle=True,
        )

    def list_environment_app_ids(self, bearer_token: str) -> tuple[str, ...]:
        """Return bounded app IDs that independently reference this environment."""
        document = self._reader.get(
            self._inventory_path,
            bearer_token,
            lifecycle=True,
        )
        return _inventory_app_ids(
            document,
            environment_id=self._environment_id,
            app_id_prefix=self._app_id_prefix,
        )


__all__ = [
    "ENVIRONMENT_PREFLIGHT_API_VERSION",
    "AzureContainerAppARMError",
    "HttpxARMJSONTransport",
    "HttpxContainerAppARMTransport",
    "HttpxContainerAppEnvironmentLifecycleTransport",
]
