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
import logging
import os
from collections.abc import Callable
from typing import Any, Protocol, cast, runtime_checkable
from urllib.parse import urlsplit

from general_ludd.security.ssrf import is_url_blocked

logger = logging.getLogger(__name__)

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


class _TupleResponse:
    def __init__(self, status: object, body: object) -> None:
        self.status_code = int(status) if isinstance(status, int) else 0
        self._body = body

    def json(self) -> object:
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _CallableTransport:
    def __init__(self, fn: object) -> None:
        self._fn = cast(Callable[..., object], fn)

    def get(self, url: str, **kwargs: object) -> _TupleResponse:
        result = self._fn("GET", url, **kwargs)
        if isinstance(result, tuple) and len(result) == 2:
            return _TupleResponse(result[0], result[1])
        return cast(_TupleResponse, result)


# Public compatibility names used by connector transports and their contract
# tests. The current tuple adapter remains the single implementation.
_CallbackResponse = _TupleResponse


def _coerce_transport(value: object) -> _Transport | None:
    if value is None:
        return None
    if isinstance(value, _Transport):
        return value
    if callable(value):
        return _CallableTransport(value)
    raise TypeError("transport must expose get() or be callable")


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
        # Reject single-label (dot-less, colon-less) hostnames: they cannot be
        # public FQDNs and are typically internal names (e.g. "internal").
        if "." not in h and ":" not in h:
            return True
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
    """Validate the endpoint URL: https only, non-internal literal host.

    The private/metadata literal decision is delegated to the canonical shared
    guard :func:`general_ludd.security.ssrf.is_url_blocked` (https-only here).
    A connector-specific single-label reject is retained BEFORE delegating,
    because ``is_url_blocked`` does not vet arbitrary dot-less public names like
    ``internal`` — those cannot be public FQDNs and are treated as internal.
    """
    parts = urlsplit(endpoint)
    if parts.scheme != "https":
        raise ValueError(f"endpoint must use https, got scheme={parts.scheme!r}")
    host = (parts.hostname or "").strip().lower()
    if not host:
        raise ValueError("endpoint has no host")
    # Reject single-label (dot-less, colon-less) hostnames, e.g. "internal".
    if "." not in host and ":" not in host:
        raise ValueError(f"endpoint host {host!r} is internal/blocked (SSRF guard)")
    if is_url_blocked(endpoint, scheme_allowlist=("https",)):
        raise ValueError(f"endpoint host {host!r} is internal/blocked (SSRF guard)")
    return endpoint.rstrip("/")


class GcpAssetInventorySource:
    """Cloud Asset Inventory resource-state source."""

    KIND = "infra"
    name = "gcp_asset_inventory"

    def __init__(self, config: dict[str, Any], *, transport: object | None = None) -> None:
        self._config = dict(config)
        scope = config.get("scope")
        if not scope and config.get("project_id"):
            scope = f"projects/{config['project_id']}"
        self._scope: str = str(scope or "").strip()
        self._token_env: str = str(config.get("token_env", "GOOGLE_OAUTH_ACCESS_TOKEN"))
        self._endpoint = _validate_endpoint(str(config.get("endpoint", DEFAULT_ENDPOINT)))
        self._timeout = float(config.get("timeout", 10.0))
        self._page_size = int(config.get("page_size", 500))
        injected = transport if transport is not None else config.get("transport")
        self._transport = _coerce_transport(injected)

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
        except Exception:  # pragma: no cover - defensive; never raises
            logger.warning("health check failed", exc_info=True)
            return {"ok": False, "detail": "health check failed"}

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
        results: object = []
        if isinstance(body, dict):
            results = body.get("results", body.get("assets", []))
        if not isinstance(results, list):
            results = []
        rows: list[dict[str, Any]] = []
        for resource in results:
            if isinstance(resource, dict):
                rows.append(self._normalize(resource, self.name))
        return rows
