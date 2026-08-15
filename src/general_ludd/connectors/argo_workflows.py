"""Argo Workflows pipeline connector.

Self-contained source for normalizing Argo *Workflows* into the gludd
pipeline-event shape. No base class / sibling imports.

Argo's API server is frequently the Kubernetes API server (or an in-cluster
service), which lives on a private/internal address. So — like the kubernetes
connector — the literal-host SSRF guard is opt-out via ``allow_private=True``
in config; by default a private/loopback literal base_url is rejected.

Contract (shared by every pipeline connector):
  * ``KIND = "pipeline"``
  * ``name`` attribute
  * ``__init__(config)`` — config-driven; the bearer token is read from the env
    var named by ``config["token_env"]`` and is NEVER hardcoded
  * literal-host SSRF guard on ``base_url`` (no DNS lookup) unless ``allow_private``
  * ``health() -> {"ok", "detail"}`` — never raises
  * ``query(spec) -> list[dict]`` — normalized ``ts/source/kind/level_or_status/
    message/value/labels/raw`` events
  * injectable HTTP transport (``httpx`` default), time-bound, no shell.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Mapping
from typing import TypedDict, cast
from urllib.parse import quote, urlsplit

import httpx

from general_ludd.connectors._errors import ConnectorConfigError
from general_ludd.security.ssrf import is_url_blocked

logger = logging.getLogger(__name__)

Transport = Callable[[str, str, Mapping[str, str], float], "tuple[int, bytes]"]

# Argo workflow status.phase -> normalized status (passed through verbatim; this
# map documents the recognized set and provides a stable default for unknowns).
_PHASE_STATUSES = frozenset({"Pending", "Running", "Succeeded", "Failed", "Error", "Skipped"})


# --------------------------------------------------------------------------- #
# Typed API-response shapes (Argo Workflows REST API).
# --------------------------------------------------------------------------- #
class ArgoObjectMeta(TypedDict, total=False):
    """The ``metadata`` block on an Argo workflow (k8s ObjectMeta shape)."""

    name: str
    namespace: str
    uid: str


class ArgoWorkflowStatus(TypedDict, total=False):
    """The ``status`` block on an Argo workflow."""

    phase: str
    startedAt: str
    finishedAt: str
    progress: str


class ArgoWorkflow(TypedDict, total=False):
    """One element of the Argo ``items[]`` array."""

    metadata: ArgoObjectMeta
    status: ArgoWorkflowStatus


class ArgoWorkflowsListResponse(TypedDict, total=False):
    """Top-level response of ``GET /api/v1/workflows/{namespace}``."""

    items: list[ArgoWorkflow] | None


class ArgoQuerySpec(TypedDict, total=False):
    """Caller-supplied query selection accepted by :meth:`ArgoWorkflowsSource.query`.

    Currently unused (Argo's list endpoint takes no filter params in this
    connector), but kept for contract symmetry with sibling pipeline connectors.
    """


def _guard_base_url(base_url: str, allow_private: bool) -> str:
    parts = urlsplit(base_url)
    # Always-on scheme + present-host check: a bad scheme / hostless URL is
    # rejected even when allow_private=True.
    if parts.scheme not in ("http", "https"):
        raise ConnectorConfigError(f"base_url must be http(s): {base_url!r}")
    if not parts.hostname:
        raise ConnectorConfigError(f"base_url has no host: {base_url!r}")
    # Private/loopback/metadata literal decision delegates to the canonical
    # SSRF guard so this connector's blocklist can never drift.
    if not allow_private and is_url_blocked(base_url, scheme_allowlist=("http", "https")):
        raise ConnectorConfigError(
            f"base_url host is a private/loopback literal (SSRF blocked; set allow_private=True "
            f"to permit in-cluster targets): {parts.hostname}"
        )
    return base_url.rstrip("/")


def _httpx_transport(method: str, url: str, headers: Mapping[str, str], timeout: float) -> tuple[int, bytes]:
    # Scheme is validated (http/https only) by _guard_base_url before any request.
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        resp = client.request(method, url, headers=dict(headers))
    return resp.status_code, resp.content


class ArgoWorkflowsSource:
    """Normalizes Argo Workflows into gludd pipeline events."""

    KIND = "pipeline"

    def __init__(
        self,
        config: Mapping[str, object],
        transport: Transport | None = None,
        *,
        http_get: Callable[..., tuple[int, object]] | None = None,
    ) -> None:
        """Build the source from connector config; ``transport`` and ``http_get`` are exclusive."""
        if transport is not None and http_get is not None:
            raise ValueError("transport and http_get are mutually exclusive")
        self._config = dict(config)
        self.name = str(self._config.get("name", "argo_workflows"))
        self.allow_private = bool(self._config.get("allow_private", False))
        self.base_url = _guard_base_url(
            str(self._config.get("base_url", "https://argo.example.com")), self.allow_private
        )
        self.namespace = str(self._config.get("namespace", "argo"))
        self.timeout = float(cast(float | int | str | bool, self._config.get("timeout", 10.0)))
        self._token_env = str(self._config.get("token_env", "ARGO_TOKEN"))
        self._token = os.environ.get(self._token_env, "")
        transport_impl: Transport
        if http_get is not None:

            def _legacy(_method: str, url: str, headers: Mapping[str, str], _timeout: float) -> tuple[int, bytes]:
                status, body = http_get(url, dict(headers))
                if isinstance(body, bytes):
                    return status, body
                if isinstance(body, str):
                    return status, body.encode()
                return status, json.dumps(body).encode()

            transport_impl = _legacy
        else:
            transport_impl = transport or _httpx_transport
        self._transport = transport_impl

    # -- internals ---------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _request(self, method: str, url: str) -> tuple[int, bytes]:
        return self._transport(method, url, self._headers(), self.timeout)

    def _workflows_url(self) -> str:
        return f"{self.base_url}/api/v1/workflows/{quote(self.namespace, safe='')}"

    def _normalize_workflow(self, workflow: Mapping[str, object]) -> dict[str, object]:
        metadata_obj = workflow.get("metadata") or {}
        status_obj = workflow.get("status") or {}
        metadata: Mapping[str, object] = metadata_obj if isinstance(metadata_obj, Mapping) else {}
        status: Mapping[str, object] = status_obj if isinstance(status_obj, Mapping) else {}
        ts = status.get("finishedAt") or status.get("startedAt")
        phase = status.get("phase")
        level_or_status = phase if phase in _PHASE_STATUSES else (str(phase) if phase is not None else None)
        return {
            "ts": ts,
            "source": "argo_workflows",
            "kind": "pipeline",
            "level_or_status": level_or_status,
            "message": metadata.get("name"),
            "value": status.get("progress"),
            "labels": {
                "namespace": metadata.get("namespace") or self.namespace,
                "uid": metadata.get("uid"),
            },
            "raw": dict(workflow),
        }

    # -- public API --------------------------------------------------------
    def health(self) -> dict[str, object]:
        """Probe the source. Never raises."""
        try:
            status, _ = self._request("GET", self._workflows_url())
        except Exception:  # health must never raise
            logger.warning("health check failed", exc_info=True)
            return {"ok": False, "detail": "health check failed"}
        if 200 <= status < 300:
            return {"ok": True, "detail": f"HTTP {status}"}
        return {"ok": False, "detail": f"HTTP {status}"}

    def query(self, spec: ArgoQuerySpec | None = None) -> list[dict[str, object]]:
        """Return normalized workflow records from the Argo API."""
        status, body = self._request("GET", self._workflows_url())
        if not (200 <= status < 300):
            raise ConnectorConfigError(f"argo workflows query failed: HTTP {status}")
        payload = json.loads(body.decode("utf-8") or "{}")
        if not isinstance(payload, Mapping):
            raise ConnectorConfigError("argo workflows response was not a JSON object")
        items_obj = payload.get("items")
        # Argo returns null `items` when the list is empty.
        items: list[object] = items_obj if isinstance(items_obj, list) else []
        return [self._normalize_workflow(cast("Mapping[str, object]", w)) for w in items if isinstance(w, Mapping)]

    def fetch_jobs(self, spec: ArgoQuerySpec | None = None) -> list[dict[str, object]]:
        """Helper alias: each Argo *workflow* is the unit of pipeline work here."""
        return self.query(spec)
