"""Honeycomb observability connector (self-contained).

A read-only source that runs a Honeycomb high-cardinality query and normalizes
each result row into a flat record. It is deliberately *self-contained*: it
imports nothing from sibling connector modules and defines no shared base. The
HTTP transport is injectable so the whole thing is testable without a network.

Security posture:
  * The API key is ALWAYS read from an environment variable named by
    ``config['api_key_env']`` and sent as the ``X-Honeycomb-Team`` header. It is
    never hardcoded or accepted inline in config.
  * The configured ``base_url`` host is checked against a literal block-list of
    loopback / link-local / private / metadata hosts (NO DNS resolution — we do
    not turn a name into an address, we only reject obviously-internal literals).
  * Every transport call is time-bound (a positive ``timeout`` is always passed).
  * ``health()`` never raises; it reports failures via its return value.
"""

from __future__ import annotations

import logging
import os
from typing import Protocol, cast, runtime_checkable

logger = logging.getLogger(__name__)

from general_ludd.connectors._protocols import HttpResponse
from general_ludd.security.ssrf import is_url_blocked

__all__ = ["HoneycombSource"]

DEFAULT_BASE_URL = "https://api.honeycomb.io"
DEFAULT_TIMEOUT = 30.0
TEAM_HEADER = "X-Honeycomb-Team"


@runtime_checkable
class _Transport(Protocol):
    """Injectable HTTP transport.

    Implementations must accept this keyword-only signature and return an object
    satisfying :class:`HttpResponse`.
    """

    def __call__(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: object = None,
        timeout: float | None = None,
    ) -> HttpResponse: ...


class HoneycombSource:
    """Honeycomb high-cardinality query source.

    Config keys:
      * ``dataset`` (required): Honeycomb dataset / environment slug.
      * ``api_key_env`` (required): name of the env var holding the API key.
      * ``base_url`` (optional): defaults to ``https://api.honeycomb.io``.
      * ``name`` (optional): instance name; defaults to ``honeycomb``.
      * ``transport`` (optional): injectable callable; if omitted a stub that
        always errors is used (so nothing accidentally hits the real network in
        an environment without an explicit transport wired in).
      * ``timeout`` (optional): per-request timeout in seconds.
    """

    KIND = "traces"

    def __init__(self, config: dict[str, object]) -> None:
        self.name: str = str(config.get("name") or "honeycomb")
        self.dataset: str = str(config.get("dataset", ""))
        self._api_key_env: str = str(config.get("api_key_env", ""))

        base_url = str(config.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
        if is_url_blocked(base_url, scheme_allowlist=("http", "https")):
            raise ValueError(
                f"refusing internal/loopback base_url host: {base_url!r}"
            )
        self.base_url: str = base_url

        timeout = config.get("timeout")
        self._timeout: float = float(str(timeout)) if timeout is not None else DEFAULT_TIMEOUT

        transport = config.get("transport")
        self._transport: _Transport = cast(_Transport, transport) if transport is not None else _no_transport

    # ------------------------------------------------------------------ #
    # auth / headers
    # ------------------------------------------------------------------ #
    def _api_key(self) -> str | None:
        return os.environ.get(self._api_key_env)

    def _headers(self) -> dict[str, str]:
        key = self._api_key()
        headers = {"Content-Type": "application/json"}
        if key:
            headers[TEAM_HEADER] = key
        return headers

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    # ------------------------------------------------------------------ #
    # health
    # ------------------------------------------------------------------ #
    def health(self) -> dict[str, object]:
        """Probe ``GET /1/auth``. Never raises; reports via the return value."""
        if not self._api_key():
            return {
                "ok": False,
                "detail": f"missing API key (env {self._api_key_env} unset)",
            }
        try:
            resp = self._transport(
                "GET",
                self._url("/1/auth"),
                headers=self._headers(),
                timeout=self._timeout,
            )
        except Exception as exc:  # health must never raise
            logger.warning("health check failed", exc_info=True)
            return {"ok": False, "detail": "health check failed"}

        if resp.status_code != 200:
            return {
                "ok": False,
                "detail": f"auth returned HTTP {resp.status_code}",
            }
        try:
            body = resp.json()
            team = (body.get("team") or {}).get("name") if isinstance(body, dict) else None
        except Exception as exc:  # never raise from health
            logger.warning("health check failed (bad auth body)", exc_info=True)
            return {"ok": False, "detail": "health check failed"}
        return {"ok": True, "detail": f"authenticated as team {team or '?'}"}

    # ------------------------------------------------------------------ #
    # query
    # ------------------------------------------------------------------ #
    def query(self, spec: dict[str, object]) -> list[dict[str, object]]:
        """Run a query and return normalized records.

        Flow: POST a query spec to create a query, POST to create a query
        result, then (if not already resolved) GET the result by id. Each result
        row is normalized into a flat record.
        """
        query_spec = spec.get("query_spec") or {}
        style = str(spec.get("style") or "traces")
        headers = self._headers()

        # 1) Create the query.
        create_resp = self._transport(
            "POST",
            self._url(f"/1/queries/{self.dataset}"),
            headers=headers,
            json=query_spec,
            timeout=self._timeout,
        )
        query_id = self._extract_id(create_resp)

        # 2) Create the query result for that query.
        result_resp = self._transport(
            "POST",
            self._url(f"/1/query_results/{self.dataset}"),
            headers=headers,
            json={"query_id": query_id, "disable_series": False},
            timeout=self._timeout,
        )
        result_body = self._safe_json(result_resp)

        # 3) If the create response already carries data (single-shot / canned),
        #    use it; otherwise GET the result by id until complete (one fetch).
        if not self._has_rows(result_body):
            result_id = result_body.get("id") if isinstance(result_body, dict) else None
            if result_id:
                fetch_resp = self._transport(
                    "GET",
                    self._url(f"/1/query_results/{self.dataset}/{result_id}"),
                    headers=headers,
                    timeout=self._timeout,
                )
                result_body = self._safe_json(fetch_resp)

        rows = self._rows(result_body)
        return [self._normalize_row(row, style) for row in rows]

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _safe_json(resp: HttpResponse) -> object:
        try:
            return resp.json()
        except Exception:
            return {}

    def _extract_id(self, resp: HttpResponse) -> str:
        body = self._safe_json(resp)
        if isinstance(body, dict) and body.get("id"):
            return str(body["id"])
        raise ValueError("query create response missing 'id'")

    @staticmethod
    def _rows(body: object) -> list[dict[str, object]]:
        if not isinstance(body, dict):
            return []
        data = body.get("data")
        if not isinstance(data, dict):
            return []
        results = data.get("results")
        if not isinstance(results, list):
            return []
        return [r for r in results if isinstance(r, dict)]

    def _has_rows(self, body: object) -> bool:
        return bool(self._rows(body))

    def _normalize_row(self, row: dict[str, object], style: str) -> dict[str, object]:
        ts = row.get("time") or row.get("start_time")
        data = row.get("data")
        data = data if isinstance(data, dict) else {}

        # Split a row's data into calculation values vs. breakdown labels.
        calc_value: object = None
        level_or_status: object = None
        message_parts: list[str] = []
        labels: dict[str, object] = {}

        for key, val in data.items():
            if self._looks_like_calculation(key):
                if calc_value is None:
                    calc_value = val
                continue
            if (
                key in ("level", "status", "http.status_code", "severity")
                and level_or_status is None
            ):
                level_or_status = val
            if key == "message":
                message_parts.append(str(val))
                continue
            if key == "name":
                message_parts.append(str(val))
            labels[key] = val

        # Events style: prefer an explicit message + count-like calculation.
        if style == "events" and not message_parts and labels:
            message_parts = [str(v) for v in labels.values()]

        if not message_parts:
            message_parts = [f"{k}={v}" for k, v in labels.items()]

        return {
            "ts": ts,
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": level_or_status,
            "message": " ".join(message_parts),
            "value": calc_value,
            "labels": labels,
            "raw": row,
        }

    @staticmethod
    def _looks_like_calculation(key: str) -> bool:
        """Heuristic: Honeycomb calc columns look like ``OP(column)`` or COUNT."""
        if key == "COUNT":
            return True
        return "(" in key and key.endswith(")")


def _no_transport(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json: object = None,
    timeout: float | None = None,
) -> HttpResponse:
    raise RuntimeError(
        "no HTTP transport configured for HoneycombSource "
        "(pass config['transport'])"
    )
