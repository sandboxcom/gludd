"""Azure Resource Graph observability connector.

Self-contained, duck-typed connector (no shared base class). Runs a KQL
Resource-Graph query against the Azure Resource Manager Resource Graph API and
normalizes each returned resource row into the project's flat observability
record shape.

Contract (duck-typed, shared informally with sibling connectors):
  - class attr ``KIND`` in {logs, metrics, traces, infra, pipeline}
  - instance attr ``name``
  - ``__init__(config: dict)`` — config-driven; the Bearer token is read from
    ``os.environ`` by the configured ``token_env`` name (NEVER hardcoded).
  - ``health() -> dict`` with keys ``ok`` / ``detail`` — never raises.
  - ``query(spec) -> list[dict]`` of normalized records with keys
    ``ts, source, kind, level_or_status, message, value, labels, raw``.

Any caller-overridable ``base_url`` is validated with a literal-host SSRF block
(no DNS): loopback / RFC-1918 / link-local / 169.254.169.254 / metadata names
are rejected.

HTTP transport is injectable (config key ``transport``) so tests mock it with a
canned response and no network is touched.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping
from typing import cast
from urllib.parse import urlsplit

from general_ludd.connectors._protocols import HttpResponse
from general_ludd.security.ssrf import is_url_blocked

logger = logging.getLogger(__name__)

# (method, url, headers, json_body, timeout) -> response with ``.status_code`` + ``.json()``.
Transport = Callable[[str, str, Mapping[str, str], object, float], HttpResponse]


class _TupleResponse:
    def __init__(self, status: object, body: object) -> None:
        self.status_code = int(status) if isinstance(status, int) else 0
        self._body = body

    def json(self) -> object:
        return self._body


def _adapt_http_get(fn: object) -> Transport:
    def call(method: str, url: str, headers: Mapping[str, str], body: object, timeout: float) -> HttpResponse:
        transport = cast(Callable[..., object], fn)
        result = transport(method, url, headers=headers, json=body, timeout=timeout)
        if isinstance(result, tuple) and len(result) == 2:
            return cast(HttpResponse, _TupleResponse(result[0], result[1]))
        return cast(HttpResponse, result)

    return call


# --- literal-host SSRF block (NO DNS) -------------------------------------------------

# Azure metadata host NOT covered by the canonical shared blocklist
# (general_ludd.security.ssrf.BLOCKED_HOST_NAMES). Kept here so this connector's
# coverage is never weaker than before the consolidation onto ``is_url_blocked``.
_EXTRA_BLOCKED_HOST_NAMES = frozenset({"metadata.azure.com"})


def _reject_if_internal(base_url: str) -> None:
    """Raise ``ValueError`` if ``base_url``'s literal host is internal/metadata.

    Pure literal inspection — performs NO DNS resolution. The private / loopback
    / link-local / reserved + cloud-metadata decision (localhost, the
    ``.localhost`` TLD, metadata.google.internal, 169.254.169.254,
    100.100.100.200, ...) is delegated to the canonical shared guard
    :func:`general_ludd.security.ssrf.is_url_blocked` so it can never drift
    weaker than the single source of truth. Two connector-specific rules are
    layered on top: the Azure metadata name and any single-label (dot-less) host,
    which cannot be a public FQDN.
    """
    if is_url_blocked(base_url):
        raise ValueError(f"refusing internal/metadata host: {base_url!r}")
    host = urlsplit(base_url).hostname or ""
    host_l = host.lower()
    if host_l in _EXTRA_BLOCKED_HOST_NAMES:
        raise ValueError(f"refusing internal/metadata host: {host!r}")
    # Single-label (dot-less, non-IP) hostnames cannot be a public FQDN; any IP
    # literal that reached here already passed the canonical guard (public).
    if "." not in host_l and ":" not in host_l:
        raise ValueError(f"refusing single-label/internal host: {host!r}")


def _parse_ts(value: object) -> float | None:
    """Parse a ``properties.timeCreated``-style value into epoch seconds, or None."""
    if value is None:
        return None
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        iso = text.replace("Z", "+00:00") if text.endswith("Z") else text
        try:
            from datetime import datetime

            return datetime.fromisoformat(iso).timestamp()
        except ValueError:
            return None
    return None


def _as_mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


class AzureResourceGraphSource:
    """Azure Resource Graph (infrastructure inventory) KQL query source."""

    KIND = "infra"

    DEFAULT_BASE_URL = "https://management.azure.com"
    API_VERSION = "2021-03-01"

    def __init__(self, config: dict[str, object], *, http_get: object | None = None) -> None:
        """Build the source from connector config; validates subscriptions and SSRF policy."""
        if not isinstance(config, dict):
            raise TypeError("config must be a dict")

        self.name: str = str(config.get("name", "azure-resource-graph"))

        subs: object = config.get("subscriptions", config.get("subscription_id", []))
        if isinstance(subs, str):
            subs = [subs]
        if not isinstance(subs, list):
            subs = []
        self._subscriptions: list[str] = [str(s) for s in subs if str(s).strip()]
        if not self._subscriptions:
            raise ValueError("config['subscriptions'] must list at least one subscription id")

        self._token_env: str = str(config.get("token_env", "AZURE_TOKEN")).strip()
        if not self._token_env:
            raise ValueError("config['token_env'] is required")

        self._base_url: str = str(config.get("base_url", self.DEFAULT_BASE_URL)).rstrip("/")
        # SSRF: validate any (possibly overridden) endpoint by literal host, no DNS.
        _reject_if_internal(self._base_url)

        self._timeout: float = float(str(config.get("timeout", 30.0)))

        transport = http_get if http_get is not None else config.get("transport")
        if http_get is not None and config.get("transport") is not None:
            raise ValueError("http_get and config['transport'] are mutually exclusive")
        if transport is None:
            transport = _default_transport
        if not callable(transport):
            raise TypeError("config['transport'] must be callable")
        if http_get is not None:
            self._transport = _adapt_http_get(transport)
        else:
            self._transport = cast(Transport, transport)

    # -- internals ---------------------------------------------------------------------

    def _token(self) -> str:
        token = os.environ.get(self._token_env)
        if not token:
            raise RuntimeError(f"missing bearer token in env var {self._token_env!r}")
        return token

    def _query_url(self) -> str:
        return f"{self._base_url}/providers/Microsoft.ResourceGraph/resources?api-version={self.API_VERSION}"

    def _post_kql(self, kql: str) -> object:
        headers = {
            "Authorization": f"Bearer {self._token()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        body = {"subscriptions": self._subscriptions, "query": kql}
        resp = self._transport("POST", self._query_url(), headers, body, self._timeout)
        if resp.status_code != 200:
            raise RuntimeError(f"resource graph query failed: HTTP {resp.status_code}")
        return resp.json()

    def _normalize_row(self, row: object) -> dict[str, object]:
        rowmap = _as_mapping(row)
        props = _as_mapping(rowmap.get("properties"))

        name = rowmap.get("name")
        rtype = rowmap.get("type")
        message_parts = [str(p) for p in (name, rtype) if p is not None]
        message = " ".join(message_parts) if message_parts else None

        labels = {
            "id": rowmap.get("id"),
            "resourceGroup": rowmap.get("resourceGroup"),
            "location": rowmap.get("location"),
            "type": rtype,
        }

        return {
            "ts": _parse_ts(props.get("timeCreated")),
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": props.get("provisioningState"),
            "message": message,
            "value": None,
            "labels": labels,
            "raw": rowmap if isinstance(row, Mapping) else row,
        }

    def _normalize(self, payload: object) -> list[dict[str, object]]:
        if not isinstance(payload, Mapping):
            return []
        data = payload.get("data")
        # The Resource Graph API returns ``data`` either as a list of objects
        # (objectArray) or as a {columns, rows} table; handle both.
        rows: list[object] = []
        if isinstance(data, list):
            rows = list(data)
        elif isinstance(data, Mapping):
            columns = data.get("columns") or []
            col_names = [str(c.get("name")) for c in columns if isinstance(c, Mapping)]
            for raw_row in data.get("rows") or []:
                rows.append(dict(zip(col_names, raw_row, strict=False)))
        return [self._normalize_row(r) for r in rows]

    # -- public API --------------------------------------------------------------------

    def query(self, spec: object) -> list[dict[str, object]]:
        """Run a Resource-Graph KQL query and return normalized records.

        ``spec`` may be a raw KQL string or a mapping with a ``query`` key.
        """
        if isinstance(spec, str):
            kql = spec
        elif isinstance(spec, Mapping):
            kql = str(spec.get("query", ""))
        else:
            raise TypeError("spec must be a KQL string or a mapping with 'query'")
        if not kql.strip():
            raise ValueError("empty KQL query")
        return self._normalize(self._post_kql(kql))

    def health(self) -> dict[str, object]:
        """``Resources | limit 1`` probe. Never raises."""
        try:
            payload = self._post_kql("Resources | limit 1")
        except Exception:  # health must never raise
            logger.warning("health check failed", exc_info=True)
            return {"ok": False, "detail": "health check failed"}
        count = len(self._normalize(payload))
        return {"ok": True, "detail": f"Resources | limit 1 returned {count} row(s)"}


def _default_transport(
    method: str,
    url: str,
    headers: Mapping[str, str],
    json_body: object,
    timeout: float,
) -> HttpResponse:
    """Default httpx-backed transport (no shell, time-bound). Imported lazily."""
    import httpx

    resp = httpx.request(method, url, headers=dict(headers), json=json_body, timeout=timeout)
    return resp
