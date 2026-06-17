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
  * injectable HTTP transport (stdlib ``urllib`` default), time-bound, no shell.
"""

from __future__ import annotations

import ipaddress
import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlsplit

Transport = Callable[[str, str, Mapping[str, str], float], "tuple[int, bytes]"]

# Argo workflow status.phase -> normalized status (passed through verbatim; this
# map documents the recognized set and provides a stable default for unknowns).
_PHASE_STATUSES = frozenset({"Pending", "Running", "Succeeded", "Failed", "Error", "Skipped"})


class ConnectorError(RuntimeError):
    """Raised for configuration / transport problems specific to this connector."""


def _host_is_private_literal(host: str) -> bool:
    """True if ``host`` is a non-public IP *literal* (no DNS resolution)."""
    candidate = host
    if candidate.startswith("[") and candidate.endswith("]"):
        candidate = candidate[1:-1]
    try:
        ip = ipaddress.ip_address(candidate)
    except ValueError:
        return False
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _guard_base_url(base_url: str, allow_private: bool) -> str:
    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https"):
        raise ConnectorError(f"base_url must be http(s): {base_url!r}")
    if not parts.hostname:
        raise ConnectorError(f"base_url has no host: {base_url!r}")
    if not allow_private and _host_is_private_literal(parts.hostname):
        raise ConnectorError(
            f"base_url host is a private/loopback literal (SSRF blocked; set allow_private=True "
            f"to permit in-cluster targets): {parts.hostname}"
        )
    return base_url.rstrip("/")


def _urllib_transport(method: str, url: str, headers: Mapping[str, str], timeout: float) -> tuple[int, bytes]:
    # Scheme is validated (http/https only) by _guard_base_url before any request.
    req = urllib.request.Request(url, method=method, headers=dict(headers))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(resp.status), resp.read()
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read()


class ArgoWorkflowsSource:
    """Normalizes Argo Workflows into gludd pipeline events."""

    KIND = "pipeline"

    def __init__(self, config: Mapping[str, Any], transport: Transport | None = None) -> None:
        self._config = dict(config)
        self.name = str(self._config.get("name", "argo_workflows"))
        self.allow_private = bool(self._config.get("allow_private", False))
        self.base_url = _guard_base_url(str(self._config["base_url"]), self.allow_private)
        self.namespace = str(self._config.get("namespace", "argo"))
        self.timeout = float(self._config.get("timeout", 10.0))
        self._token_env = str(self._config.get("token_env", "ARGO_TOKEN"))
        self._token = os.environ.get(self._token_env, "")
        self._transport: Transport = transport or _urllib_transport

    # -- internals ---------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _request(self, method: str, url: str) -> tuple[int, bytes]:
        return self._transport(method, url, self._headers(), self.timeout)

    def _workflows_url(self) -> str:
        return f"{self.base_url}/api/v1/workflows/{self.namespace}"

    def _normalize_workflow(self, workflow: Mapping[str, Any]) -> dict[str, Any]:
        metadata = workflow.get("metadata") or {}
        status_obj = workflow.get("status") or {}
        if not isinstance(metadata, Mapping):
            metadata = {}
        if not isinstance(status_obj, Mapping):
            status_obj = {}
        ts = status_obj.get("finishedAt") or status_obj.get("startedAt")
        phase = status_obj.get("phase")
        level_or_status = phase if phase in _PHASE_STATUSES else (str(phase) if phase is not None else None)
        return {
            "ts": ts,
            "source": "argo_workflows",
            "kind": "pipeline",
            "level_or_status": level_or_status,
            "message": metadata.get("name"),
            "value": status_obj.get("progress"),
            "labels": {
                "namespace": metadata.get("namespace") or self.namespace,
                "uid": metadata.get("uid"),
            },
            "raw": dict(workflow),
        }

    # -- public API --------------------------------------------------------
    def health(self) -> dict[str, Any]:
        try:
            status, _ = self._request("GET", self._workflows_url())
        except Exception as exc:  # health must never raise
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
        if 200 <= status < 300:
            return {"ok": True, "detail": f"HTTP {status}"}
        return {"ok": False, "detail": f"HTTP {status}"}

    def query(self, spec: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        status, body = self._request("GET", self._workflows_url())
        if not (200 <= status < 300):
            raise ConnectorError(f"argo workflows query failed: HTTP {status}")
        payload = json.loads(body.decode("utf-8") or "{}")
        if not isinstance(payload, Mapping):
            raise ConnectorError("argo workflows response was not a JSON object")
        items = payload.get("items")
        # Argo returns null `items` when the list is empty.
        if items is None:
            items = []
        if not isinstance(items, list):
            raise ConnectorError("argo 'items' field was not a JSON array")
        return [self._normalize_workflow(w) for w in items if isinstance(w, Mapping)]

    def fetch_jobs(self, spec: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        """Helper alias: each Argo *workflow* is the unit of pipeline work here."""
        return self.query(spec)
