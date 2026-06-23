"""Pyroscope continuous-profiling connector (self-contained).

``PyroscopeSource`` fetches a Pyroscope *flamebearer* render and normalizes each
flame frame into the project's eight-key telemetry-record shape. Like its Parca
sibling it is deliberately *self-contained* — no imports from the connector
``base`` or any sibling module — depending only on ``httpx`` for the default
transport (tests inject their own).

Contract
--------
- ``KIND = 'traces'`` (continuous CPU profiles are modeled under the traces kind).
- ``__init__(config, *, transport=None)`` — fully config-driven.
- The bearer token is read at call time from the environment variable *named* by
  ``config['token_env']``; it is never stored or inlined.
- ``base_url`` passes a literal-host SSRF guard (no DNS) before any request;
  loopback / private / link-local / metadata hosts are rejected unless
  ``config['allow_private']`` is truthy. Non-http(s) schemes are always rejected.
- ``health() -> {'ok': bool, 'detail': str}`` never raises.
- ``query(spec) -> list[dict]`` returns normalized records; on any transport
  error it returns ``[]`` (fail-soft).
- The HTTP transport is injectable; every request is time-bound; no ``shell``.

Wire format
-----------
``GET {base_url}/render?query=&from=&until=&format=json`` returns a flamebearer:

    {"flamebearer": {"names": [...], "levels": [[off, total, self, idx, ...], ...]},
     "metadata": {"appName": ..., "name": ..., "units": ...}}

Each ``levels`` row is a flattened sequence of ``(offset, total, self, name_index)``
quads. One normalized record is emitted per frame whose ``self`` sample count is
positive: ``value`` = self samples, ``message`` = frame name, and
``labels`` = ``{app, profile_type}`` from the metadata.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Protocol

import httpx

from general_ludd.connectors._ssrf_guard import _assert_safe_base_url

logger = logging.getLogger(__name__)

_RENDER_PATH = "/render"


# --------------------------------------------------------------------------- #
# Injectable transport contract
# --------------------------------------------------------------------------- #
class _Response(Protocol):
    status_code: int

    def json(self) -> Any: ...


class _Transport(Protocol):
    def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = ...,
        headers: dict[str, str] | None = ...,
        timeout: float | None = ...,
    ) -> _Response: ...


class _HttpxTransport:
    """Default transport: a per-request ``httpx`` GET wrapper (stateless)."""

    def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> _Response:
        with httpx.Client(timeout=timeout) as client:
            return client.get(url, params=params, headers=headers)


# --------------------------------------------------------------------------- #
# Connector
# --------------------------------------------------------------------------- #
class PyroscopeSource:
    """A Pyroscope flamebearer source normalized into telemetry records."""

    KIND: str = "traces"

    def __init__(self, config: dict[str, Any], *, transport: _Transport | None = None) -> None:
        self.name: str = str(config.get("name", "pyroscope"))

        base_url = str(config.get("base_url", "")).rstrip("/")
        if not base_url:
            raise ValueError("PyroscopeSource requires a base_url")
        self._allow_private = bool(config.get("allow_private", False))
        _assert_safe_base_url(base_url, allow_private=self._allow_private)
        self._base_url = base_url

        self._query: str = str(config.get("query", ""))
        self._token_env = config.get("token_env")
        self._timeout: float = float(config.get("timeout", 20.0))
        self._transport: _Transport = transport if transport is not None else _HttpxTransport()

    # -- helpers ----------------------------------------------------------- #
    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self._token_env:
            token = os.environ.get(str(self._token_env))
            if token:
                headers["Authorization"] = f"Bearer {token}"
        return headers

    def _render_url(self) -> str:
        return f"{self._base_url}{_RENDER_PATH}"

    def _params(self, spec: dict[str, Any]) -> dict[str, Any]:
        params: dict[str, Any] = {
            "query": spec.get("query", self._query),
            "format": "json",
        }
        if spec.get("from") is not None:
            params["from"] = spec.get("from")
        if spec.get("until") is not None:
            params["until"] = spec.get("until")
        return params

    @staticmethod
    def _frame_records(payload: dict[str, Any], source: str, kind: str) -> list[dict[str, Any]]:
        """Flatten a flamebearer ``levels`` table into per-frame records.

        Each ``levels`` row is a flat list of ``(offset, total, self, name_index)``
        quads. Frames with ``self == 0`` (pure aggregators / the synthetic root)
        are skipped — only frames that actually accrued samples are emitted.
        """
        flame = payload.get("flamebearer") or {}
        names = flame.get("names") or []
        levels = flame.get("levels") or []

        metadata = payload.get("metadata") or {}
        app = metadata.get("appName") or metadata.get("name")
        profile_type = metadata.get("name") or metadata.get("appName")

        records: list[dict[str, Any]] = []
        for row in levels:
            if not isinstance(row, list):
                continue
            for i in range(0, len(row) - 3, 4):
                try:
                    self_samples = float(row[i + 2])
                    name_index = int(row[i + 3])
                except (TypeError, ValueError, IndexError):
                    continue
                if self_samples <= 0:
                    continue
                frame_name = str(names[name_index]) if 0 <= name_index < len(names) else "(unknown)"
                labels: dict[str, Any] = {"app": app, "profile_type": profile_type}
                records.append(
                    {
                        "ts": None,
                        "source": source,
                        "kind": kind,
                        "level_or_status": "info",
                        "message": frame_name,
                        "value": self_samples,
                        "labels": labels,
                        "raw": {"names_index": name_index, "row": row},
                    }
                )
        return records

    # -- public API -------------------------------------------------------- #
    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        """Fetch a flamebearer render and normalize its frames. Fail-soft -> []."""
        try:
            resp = self._transport.get(
                self._render_url(),
                params=self._params(spec),
                headers=self._headers(),
                timeout=self._timeout,
            )
        except Exception as exc:
            logger.warning("Pyroscope render transport error: %s", exc)
            return []

        if resp.status_code != 200:
            logger.warning("Pyroscope render returned HTTP %s", resp.status_code)
            return []

        try:
            payload = resp.json()
        except Exception as exc:
            logger.warning("Pyroscope render returned undecodable body: %s", exc)
            return []

        return self._frame_records(payload, self.name, self.KIND)

    def health(self) -> dict[str, Any]:
        """Probe the render endpoint with a bounded request. Never raises."""
        try:
            resp = self._transport.get(
                self._render_url(),
                params={"query": self._query, "format": "json"},
                headers=self._headers(),
                timeout=self._timeout,
            )
        except Exception as exc:
            return {"ok": False, "detail": f"transport error: {exc}"}

        if resp.status_code == 200:
            return {"ok": True, "detail": "render reachable (HTTP 200)"}
        return {"ok": False, "detail": f"render returned HTTP {resp.status_code}"}
