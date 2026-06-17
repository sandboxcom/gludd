"""GCP Cloud Asset Inventory infra-state connector.

Self-contained source that queries the Cloud Asset Inventory
``searchAllResources`` endpoint and normalizes each returned resource into the
project's standard event row shape::

    {ts, source, kind='infra', level_or_status, message, value, labels, raw}

Design constraints honored here:

* ``KIND = 'infra'`` and a stable ``name`` attribute.
* ``__init__(config)`` is config-driven; the HTTP transport (an ``httpx.Client``
  -like object exposing ``.get(url, headers=, params=, timeout=)``) is
  injectable for testing — no real network is required.
* SSRF defense is performed against a *literal* host (no DNS resolution): the
  endpoint host is parsed and rejected if it is a loopback/link-local/private/
  metadata address or a bare internal hostname. Only ``https`` is allowed.
* ``health()`` never raises; it returns ``{'ok': bool, 'detail': str}``.
* ``query(spec)`` is time-bound (every request carries an explicit timeout) and
  returns a list of normalized dicts. No ``shell=True`` and no subprocesses.
"""

from __future__ import annotations

import ipaddress
import os
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit

DEFAULT_ENDPOINT = "https://cloudasset.googleapis.com/v1"
_ALLOWED_DEFAULT_HOST = "cloudasset.googleapis.com"

# Literal hostnames that must never be contacted (metadata / loopback). These are
# string-matched (no DNS) so a malicious config cannot smuggle an internal target.
_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "metadata",
        "metadata.google.internal",
        "metadata.goog",
    }
)


@runtime_checkable
class _Transport(Protocol):
    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = ...,
        params: dict[str, Any] | None = ...,
        timeout: float | None = ...,
    ) -> Any: ...


def _host_is_internal(host: str) -> bool:
    """Return True if *host* (a literal, already-extracted hostname) is internal.

    No name resolution is performed: an IP literal is range-checked, and a
    non-IP hostname is matched against a denylist of metadata/loopback names.
    """
    h = host.strip().lower()
    if not h:
        return True
    # strip IPv6 brackets if present
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        # not an IP literal -> treat as a hostname
        return h in _BLOCKED_HOSTNAMES
    return bool(
        ip.is_loopback
        or ip.is_link_local
        or ip.is_private
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _validate_endpoint(endpoint: str) -> str:
    """Validate the endpoint URL: https only, non-internal literal host."""
    parts = urlsplit(endpoint)
    if parts.scheme != "https":
        raise ValueError(f"endpoint must use https, got scheme={parts.scheme!r}")
    host = parts.hostname or ""
    if not host:
        raise ValueError("endpoint has no host")
    if _host_is_internal(host):
        raise ValueError(f"endpoint host {host!r} is internal/blocked (SSRF guard)")
    return endpoint.rstrip("/")


class GcpAssetInventorySource:
    """Cloud Asset Inventory resource-state source."""

    KIND = "infra"
    name = "gcp_asset_inventory"

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = dict(config)
        self._scope: str = str(config.get("scope", "")).strip()
        self._token_env: str = str(config.get("token_env", "GOOGLE_OAUTH_ACCESS_TOKEN"))
        self._endpoint = _validate_endpoint(str(config.get("endpoint", DEFAULT_ENDPOINT)))
        self._timeout = float(config.get("timeout", 10.0))
        self._page_size = int(config.get("page_size", 500))
        self._transport: _Transport | None = config.get("transport")

    # -- internals -----------------------------------------------------------

    def _token(self) -> str | None:
        tok = os.environ.get(self._token_env)
        return tok if tok else None

    def _search_url(self, scope: str) -> str:
        return f"{self._endpoint}/{scope}:searchAllResources"

    @staticmethod
    def _normalize(resource: dict[str, Any], source: str) -> dict[str, Any]:
        name = str(resource.get("name", ""))
        asset_type = str(resource.get("assetType", ""))
        state = resource.get("state")
        # level_or_status falls back to displayName-agnostic markers when state
        # is absent (e.g. buckets have no lifecycle state).
        level_or_status: Any = state if state is not None else (asset_type or "UNKNOWN")
        message = " ".join(p for p in (name, asset_type) if p).strip() or name
        labels = {
            "project": str(resource.get("project", "")),
            "location": str(resource.get("location", "")),
            "assetType": asset_type,
        }
        return {
            "ts": resource.get("updateTime"),
            "source": source,
            "kind": "infra",
            "level_or_status": level_or_status,
            "message": message,
            "value": None,
            "labels": labels,
            "raw": resource,
        }

    # -- public API ----------------------------------------------------------

    def health(self) -> dict[str, Any]:
        try:
            if self._transport is None:
                return {"ok": False, "detail": "no transport configured"}
            if not self._scope:
                return {"ok": False, "detail": "no scope configured"}
            if self._token() is None:
                return {
                    "ok": False,
                    "detail": f"missing bearer token in env {self._token_env}",
                }
            return {"ok": True, "detail": f"ready scope={self._scope}"}
        except Exception as exc:  # pragma: no cover - defensive; never raises
            return {"ok": False, "detail": f"health error: {exc!r}"}

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        if self._transport is None:
            return []
        scope = str(spec.get("scope", self._scope)).strip()
        if not scope:
            return []
        token = self._token()
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        params: dict[str, Any] = {"pageSize": self._page_size}
        if spec.get("query"):
            params["query"] = str(spec["query"])
        if spec.get("assetTypes"):
            params["assetTypes"] = spec["assetTypes"]

        resp = self._transport.get(
            self._search_url(scope),
            headers=headers,
            params=params,
            timeout=self._timeout,
        )
        raise_for_status = getattr(resp, "raise_for_status", None)
        if callable(raise_for_status):
            raise_for_status()
        body = resp.json()
        results = body.get("results", []) if isinstance(body, dict) else []
        rows: list[dict[str, Any]] = []
        for resource in results:
            if isinstance(resource, dict):
                rows.append(self._normalize(resource, self.name))
        return rows
