"""PagerDuty incident-source connector.

Self-contained: imports no sibling connector, base class, or package __init__
helper. Transport is injectable for testing; production falls back to ``httpx``.

Security notes:
* The API token is read from the environment (``token_env``); it is never
  hardcoded or accepted inline.
* ``base_url`` is validated against a literal-host SSRF blocklist with no DNS
  resolution, so loopback / link-local / private-range hosts are rejected.
* All requests are time-bounded and never use a shell.
"""

from __future__ import annotations

import os
from typing import Any, Protocol, runtime_checkable

from general_ludd.security.ssrf import is_url_blocked

DEFAULT_BASE_URL = "https://api.pagerduty.com"
DEFAULT_TIMEOUT = 15.0
ACCEPT_HEADER = "application/vnd.pagerduty+json;version=2"


@runtime_checkable
class HttpResponse(Protocol):
    status_code: int

    def json(self) -> Any: ...


@runtime_checkable
class HttpTransport(Protocol):
    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = ...,
        params: Any = ...,
        timeout: float | None = ...,
    ) -> HttpResponse: ...


def _validate_base_url(base_url: str) -> str:
    if is_url_blocked(base_url, scheme_allowlist=("http", "https")):
        raise ValueError(f"refusing to target internal/loopback host: {base_url!r}")
    return base_url.rstrip("/")


class _DefaultTransport:
    """Lazy ``httpx`` wrapper, used only when no transport is injected."""

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: Any = None,
        timeout: float | None = None,
    ) -> HttpResponse:
        import httpx

        return httpx.get(url, headers=headers, params=params, timeout=timeout)


class PagerDutySource:
    """Incident source backed by the PagerDuty REST API."""

    KIND = "incidents"

    def __init__(
        self,
        config: dict[str, Any],
        *,
        transport: HttpTransport | None = None,
    ) -> None:
        self.config = dict(config)
        self.name = str(self.config.get("name", "pagerduty"))
        self.base_url = _validate_base_url(
            str(self.config.get("base_url", DEFAULT_BASE_URL))
        )
        self.token_env = str(self.config.get("token_env", "PAGERDUTY_TOKEN"))
        self.timeout = float(self.config.get("timeout", DEFAULT_TIMEOUT))
        self._transport: HttpTransport = transport or _DefaultTransport()

    # -- internals --------------------------------------------------------

    def _token(self) -> str:
        token = os.environ.get(self.token_env)
        if not token:
            raise RuntimeError(
                f"PagerDuty token not found in environment variable "
                f"{self.token_env!r}"
            )
        return token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Token token={self._token()}",
            "Accept": ACCEPT_HEADER,
        }

    # -- public API -------------------------------------------------------

    def query(self, spec: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        spec = spec or {}
        params: dict[str, Any] = {}
        if spec.get("since"):
            params["since"] = spec["since"]
        if spec.get("until"):
            params["until"] = spec["until"]
        if spec.get("statuses"):
            params["statuses[]"] = list(spec["statuses"])
        if spec.get("service_ids"):
            params["service_ids[]"] = list(spec["service_ids"])

        resp = self._transport.get(
            f"{self.base_url}/incidents",
            headers=self._headers(),
            params=params,
            timeout=self.timeout,
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"PagerDuty /incidents returned status {resp.status_code}"
            )
        payload = resp.json() or {}
        incidents = payload.get("incidents", []) or []
        return [self._normalize(inc) for inc in incidents]

    def fetch_log_entries(self, incident_id: str) -> list[dict[str, Any]]:
        resp = self._transport.get(
            f"{self.base_url}/incidents/{incident_id}/log_entries",
            headers=self._headers(),
            params={},
            timeout=self.timeout,
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"PagerDuty log_entries returned status {resp.status_code}"
            )
        payload = resp.json() or {}
        entries = payload.get("log_entries", []) or []
        return list(entries)

    def health(self) -> dict[str, Any]:
        try:
            resp = self._transport.get(
                f"{self.base_url}/incidents",
                headers=self._headers(),
                params={"limit": 1},
                timeout=self.timeout,
            )
        except Exception as exc:  # never raises
            return {"ok": False, "detail": f"request failed: {exc}"}
        status = getattr(resp, "status_code", 0)
        if status >= 400:
            return {"ok": False, "detail": f"http status {status}"}
        return {"ok": True, "detail": f"http status {status}"}

    # -- normalization ----------------------------------------------------

    def _normalize(self, inc: dict[str, Any]) -> dict[str, Any]:
        title = inc.get("title", "")
        urgency = inc.get("urgency", "")
        message = f"{title} (urgency={urgency})" if urgency else str(title)

        service = inc.get("service") or {}
        escalation = inc.get("escalation_policy") or {}
        assignees = [
            (a.get("assignee") or {}).get("summary", "")
            for a in inc.get("assignments", []) or []
        ]
        labels = {
            "id": inc.get("id", ""),
            "service.summary": service.get("summary", ""),
            "escalation_policy": escalation.get("summary", ""),
            "assignees": ", ".join(a for a in assignees if a),
        }
        return {
            "ts": inc.get("created_at"),
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": inc.get("status", ""),
            "message": message,
            "value": None,
            "labels": labels,
            "raw": inc,
        }
