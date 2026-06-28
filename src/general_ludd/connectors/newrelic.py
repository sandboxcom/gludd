"""New Relic NerdGraph (NRQL) observability connector.

Self-contained source connector that runs NRQL queries against New Relic's
NerdGraph GraphQL endpoint and normalizes each result row into a flat record.

Design constraints honored here:
  * Config-driven: no hardcoded credentials. The API key is read from the
    environment variable named by ``api_key_env`` and sent as the ``API-Key``
    header (never logged, never embedded in source).
  * SSRF defense: the configured ``api_url`` is validated against a literal
    host denylist (loopback, link-local, RFC1918, metadata) WITHOUT performing
    DNS resolution. Hostnames that are not literal IPs are allowed only when
    they are not in the obvious internal set; we never resolve to decide.
  * Injectable HTTP transport: callers (and tests) may pass any callable with
    the signature ``transport(url, *, headers, json, timeout) -> Response``.
    This keeps the connector testable with a mocked transport and free of any
    real network dependency at import time.
  * ``health()`` never raises; it returns ``{"ok": bool, "detail": str}``.
  * ``query(spec)`` returns ``list[dict]`` of normalized records with the keys
    ts, source, kind, level_or_status, message, value, labels, raw.

No shell, no ``shell=True``, no base-class / sibling / package imports.
"""

from __future__ import annotations

import json as _json
import os
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit

from general_ludd.connectors.normalize import sanitize_metric_value
from general_ludd.security.ssrf import is_url_blocked

__all__ = ["NewRelicSource"]


# --------------------------------------------------------------------------- #
# Transport protocol
# --------------------------------------------------------------------------- #
@runtime_checkable
class _Response(Protocol):
    """Minimal response shape the connector relies on.

    Compatible with ``requests``/``httpx`` responses and trivially mockable.
    """

    status_code: int

    def json(self) -> Any:  # pragma: no cover - structural typing only
        ...


# transport(url, *, headers, json, timeout) -> _Response
Transport = Callable[..., _Response]


# --------------------------------------------------------------------------- #
# SSRF: literal-host denylist (NO DNS resolution)
# --------------------------------------------------------------------------- #
def _validate_api_url(api_url: str) -> str:
    """Validate scheme/host of *api_url*; raise ValueError if unsafe.

    Returns the normalized url string on success.
    """
    if is_url_blocked(api_url, scheme_allowlist=("http", "https")):
        parts = urlsplit(api_url)
        host = parts.hostname or ""
        raise ValueError(f"api_url host is internal / blocked: {host!r}")
    return api_url


# --------------------------------------------------------------------------- #
# Default transport (lazy import; never required for testing)
# --------------------------------------------------------------------------- #
def _default_transport(
    url: str, *, headers: dict[str, str], json: Any, timeout: float
) -> _Response:
    """Real HTTP POST transport using ``requests``; imported lazily.

    Tests inject their own transport and never reach this path.
    """
    import requests  # local import keeps the module import-light & offline

    # allow_redirects=False: never follow a 30x off the configured endpoint — a
    # redirect to an internal/metadata address would be an SSRF pivot.
    return requests.post(
        url, headers=headers, json=json, timeout=timeout, allow_redirects=False
    )


# --------------------------------------------------------------------------- #
# NRQL helpers
# --------------------------------------------------------------------------- #
def _nerdgraph_body(account_id: int | str, nrql: str) -> dict[str, str]:
    """Build the NerdGraph GraphQL body wrapping an NRQL query.

    The NRQL string is embedded inside a GraphQL string literal; we escape
    backslashes and double quotes to avoid breaking the GraphQL document.
    """
    safe_nrql = nrql.replace("\\", "\\\\").replace('"', '\\"')
    query = (
        "query { actor { account(id: "
        f"{account_id}"
        ') { nrql(query: "'
        f"{safe_nrql}"
        '") { results } } } }'
    )
    return {"query": query}


_TIMESTAMP_KEYS = ("timestamp", "beginTimeSeconds", "beginTime")


def _coerce_ts(row: dict[str, Any]) -> Any:
    """Extract a timestamp from a result row, trying known NRQL keys."""
    for key in _TIMESTAMP_KEYS:
        if key in row and row[key] is not None:
            return row[key]
    return None


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _first_numeric_aggregate(row: dict[str, Any], skip: frozenset[str]) -> Any:
    """Return the first numeric value in *row* (skipping timestamp keys)."""
    for key, value in row.items():
        if key in skip:
            continue
        if _is_number(value):
            return value
    return None


# --------------------------------------------------------------------------- #
# Connector
# --------------------------------------------------------------------------- #
class NewRelicSource:
    """New Relic NerdGraph (NRQL) metrics source connector.

    NRQL can span logs / metrics / events; we classify as ``metrics`` because
    the normalized records surface the first numeric aggregate as ``value``.
    """

    #: Stable kind for this connector.
    KIND: str = "metrics"

    DEFAULT_API_URL: str = "https://api.newrelic.com/graphql"
    DEFAULT_TIMEOUT: float = 30.0

    def __init__(
        self,
        config: dict[str, Any],
        *,
        transport: Transport | None = None,
    ) -> None:
        """Initialize from a config mapping.

        Recognized config keys:
          * ``name``        -- instance name (default ``"newrelic"``).
          * ``api_url``     -- NerdGraph endpoint (default DEFAULT_API_URL).
          * ``account_id``  -- New Relic account id (required for real queries).
          * ``api_key_env`` -- env var holding the API key (default
            ``"NEW_RELIC_API_KEY"``). The value is read at request time and
            sent as the ``API-Key`` header.
          * ``timeout``     -- per-request timeout in seconds.
        """
        self.config: dict[str, Any] = dict(config or {})
        self.name: str = str(self.config.get("name", "newrelic"))

        api_url = str(self.config.get("api_url", self.DEFAULT_API_URL))
        # Fail fast on an unsafe endpoint (SSRF literal-host block, no DNS).
        self.api_url: str = _validate_api_url(api_url)

        self.account_id: Any = self.config.get("account_id")
        self.api_key_env: str = str(self.config.get("api_key_env", "NEW_RELIC_API_KEY"))
        self.timeout: float = float(self.config.get("timeout", self.DEFAULT_TIMEOUT))

        self._transport: Transport = transport or _default_transport

    # -- internal request -------------------------------------------------- #
    def _api_key(self) -> str:
        key = os.environ.get(self.api_key_env)
        if not key:
            raise RuntimeError(
                f"missing API key: environment variable {self.api_key_env!r} is unset"
            )
        return key

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "API-Key": self._api_key(),
        }

    def _run_nrql(self, nrql: str) -> list[dict[str, Any]]:
        """POST an NRQL query to NerdGraph and return the raw results list."""
        if self.account_id is None:
            raise RuntimeError("config 'account_id' is required to run NRQL")

        body = _nerdgraph_body(self.account_id, nrql)
        resp = self._transport(
            self.api_url,
            headers=self._headers(),
            json=body,
            timeout=self.timeout,
        )

        status = getattr(resp, "status_code", 200)
        if status >= 400:
            raise RuntimeError(f"NerdGraph HTTP {status}")

        payload = resp.json()
        if not isinstance(payload, dict):
            raise RuntimeError("NerdGraph response was not a JSON object")

        if payload.get("errors"):
            raise RuntimeError(f"NerdGraph errors: {payload['errors']!r}")

        try:
            results = payload["data"]["actor"]["account"]["nrql"]["results"]
        except (KeyError, TypeError) as exc:
            raise RuntimeError(f"NerdGraph response missing nrql.results: {exc}") from exc

        if results is None:
            return []
        if not isinstance(results, list):
            raise RuntimeError("NerdGraph nrql.results was not a list")
        return [r for r in results if isinstance(r, dict)]

    # -- normalization ----------------------------------------------------- #
    def _normalize_row(self, row: dict[str, Any]) -> dict[str, Any]:
        ts = _coerce_ts(row)
        ts_keys = frozenset(k for k in _TIMESTAMP_KEYS if k in row)
        value = sanitize_metric_value(_first_numeric_aggregate(row, skip=ts_keys))

        labels: dict[str, Any] = {
            k: v
            for k, v in row.items()
            if k not in ts_keys and not _is_number(v)
        }

        return {
            "ts": ts,
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": None,
            "message": _json.dumps(row, sort_keys=True, default=str),
            "value": value,
            "labels": labels,
            "raw": row,
        }

    # -- public API -------------------------------------------------------- #
    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        """Run the NRQL in ``spec['nrql']`` and return normalized records."""
        if not isinstance(spec, dict):
            raise TypeError("spec must be a mapping")
        nrql = spec.get("nrql")
        if not nrql or not isinstance(nrql, str):
            raise ValueError("spec['nrql'] is required and must be a non-empty string")

        rows = self._run_nrql(nrql)
        return [self._normalize_row(row) for row in rows]

    def health(self) -> dict[str, Any]:
        """Trivial NRQL health probe; never raises.

        Runs ``SELECT 1`` and reports ``{"ok", "detail"}``.
        """
        try:
            self._run_nrql("SELECT 1")
        except Exception as exc:  # health must never raise
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
        return {"ok": True, "detail": "ok"}
