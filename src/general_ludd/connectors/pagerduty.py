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

import logging
import os
from collections.abc import Callable, Mapping
from typing import Protocol, TypedDict, cast, runtime_checkable

from general_ludd.connectors._protocols import HttpResponse
from general_ludd.security.ssrf import is_url_blocked

logger = logging.getLogger(__name__)


def _invoke_get(transport: object, url: str, **kwargs: object) -> HttpResponse:
    """Invoke an injected GET transport, including callable tuple doubles."""
    getter = getattr(transport, "get", None)
    if callable(getter):
        try:
            result = getter(url, **kwargs)
        except TypeError as exc:
            # Accept kwargs-only fakes used by integrations that derive the
            # endpoint from their configured client. Re-raise the original
            # error when the fallback has the same signature mismatch.
            try:
                result = getter(**kwargs)
            except TypeError:
                raise exc from None
    elif callable(transport):
        result = transport("GET", url, **kwargs)
    else:
        raise TypeError("transport must expose get or be callable")
    if isinstance(result, tuple) and len(result) == 2:

        class _TupleResponse:
            status_code = int(result[0]) if isinstance(result[0], int) else 0

            def json(self) -> object:
                return result[1]

        return cast(HttpResponse, _TupleResponse())
    return cast(HttpResponse, result)


DEFAULT_BASE_URL = "https://api.pagerduty.com"
DEFAULT_TIMEOUT = 15.0
ACCEPT_HEADER = "application/vnd.pagerduty+json;version=2"


# --------------------------------------------------------------------------- #
# Typed API-response shapes (PagerDuty REST API v2).
# --------------------------------------------------------------------------- #
class PagerDutyServiceRef(TypedDict, total=False):
    """A service reference embedded in an incident."""

    id: str
    summary: str
    type: str


class PagerDutyEscalationPolicyRef(TypedDict, total=False):
    """An escalation-policy reference embedded in an incident."""

    id: str
    summary: str
    type: str


class PagerDutyAssignee(TypedDict, total=False):
    """An assignee object inside an assignment."""

    summary: str


class PagerDutyAssignment(TypedDict, total=False):
    """One entry of an incident's ``assignments[]`` array."""

    assignee: PagerDutyAssignee


class PagerDutyIncident(TypedDict, total=False):
    """One incident from ``GET /incidents`` — the ``incidents[]`` item."""

    id: str
    title: str
    status: str
    urgency: str
    created_at: str
    service: PagerDutyServiceRef
    escalation_policy: PagerDutyEscalationPolicyRef
    assignments: list[PagerDutyAssignment]


class PagerDutyIncidentsResponse(TypedDict, total=False):
    """Top-level response of ``GET /incidents``."""

    incidents: list[PagerDutyIncident]


class PagerDutyLogEntriesResponse(TypedDict, total=False):
    """Top-level response of ``GET /incidents/{id}/log_entries``."""

    log_entries: list[Mapping[str, object]]


class PagerDutyQuerySpec(TypedDict, total=False):
    """Caller-supplied query selection accepted by :meth:`PagerDutySource.query`."""

    since: str
    until: str
    statuses: list[str]
    service_ids: list[str]


@runtime_checkable
class HttpTransport(Protocol):
    """Minimal injectable transport returning an ``HttpResponse``."""

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = ...,
        params: Mapping[str, object] | None = ...,
        timeout: float | None = ...,
    ) -> HttpResponse:
        """Fetch ``url`` and return an ``HttpResponse``."""
        ...


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
        params: Mapping[str, object] | None = None,
        timeout: float | None = None,
    ) -> HttpResponse:
        import httpx

        return httpx.get(
            url,
            headers=headers,
            params=cast("Mapping[str, str | int | list[str]]", params) if params else None,
            timeout=timeout,
            follow_redirects=False,
        )


class _TupleTransport:
    """Adapt the small ``(status, payload)`` test transport contract."""

    def __init__(self, fn: object) -> None:
        self._fn = cast("Callable[..., object]", fn)

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: Mapping[str, object] | None = None,
        timeout: float | None = None,
    ) -> HttpResponse:
        result = self._fn(
            "GET",
            url,
            headers=headers or {},
            params=params,
            timeout=timeout,
        )
        status, payload = result if isinstance(result, tuple) and len(result) == 2 else (0, {})

        class Response:
            status_code = int(status)

            def json(self_nonlocal) -> object:
                return payload

        return cast(HttpResponse, Response())


class PagerDutySource:
    """Incident source backed by the PagerDuty REST API."""

    KIND = "incidents"

    def __init__(
        self,
        config: Mapping[str, object],
        *,
        transport: HttpTransport | None = None,
    ) -> None:
        """Build the source from connector config and select the transport."""
        self.config = dict(config)
        self.name = str(self.config.get("name", "pagerduty"))
        self.base_url = _validate_base_url(str(self.config.get("base_url", DEFAULT_BASE_URL)))
        self.token_env = str(self.config.get("token_env", "PAGERDUTY_TOKEN"))
        self.timeout = float(cast(float | int | str | bool, self.config.get("timeout", DEFAULT_TIMEOUT)))
        self._transport: HttpTransport = (
            _TupleTransport(transport)
            if callable(transport) and not hasattr(transport, "get")
            else transport or _DefaultTransport()
        )

    # -- internals --------------------------------------------------------

    def _token(self) -> str:
        token = os.environ.get(self.token_env)
        if not token:
            raise RuntimeError(f"PagerDuty token not found in environment variable {self.token_env!r}")
        return token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Token token={self._token()}",
            "Accept": ACCEPT_HEADER,
        }

    # -- public API -------------------------------------------------------

    def query(self, spec: PagerDutyQuerySpec | None = None) -> list[dict[str, object]]:
        """Return normalized incidents from ``/incidents``."""
        spec = spec or {}
        params: dict[str, object] = {}
        since = spec.get("since")
        if since:
            params["since"] = since
        until = spec.get("until")
        if until:
            params["until"] = until
        statuses = spec.get("statuses")
        if statuses:
            params["statuses[]"] = list(statuses)
        service_ids = spec.get("service_ids")
        if service_ids:
            params["service_ids[]"] = list(service_ids)

        resp = _invoke_get(
            self._transport,
            url=f"{self.base_url}/incidents",
            headers=self._headers(),
            params=params,
            timeout=self.timeout,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"PagerDuty /incidents returned status {resp.status_code}")
        payload = resp.json() or {}
        incidents: list[object] = []
        if isinstance(payload, Mapping):
            raw_incidents = payload.get("incidents")
            if isinstance(raw_incidents, list):
                incidents = raw_incidents
        return [self._normalize(cast("Mapping[str, object]", inc)) for inc in incidents if isinstance(inc, Mapping)]

    def fetch_log_entries(self, incident_id: str) -> list[Mapping[str, object]]:
        """Fetch raw log entries for one incident."""
        resp = _invoke_get(
            self._transport,
            url=f"{self.base_url}/incidents/{incident_id}/log_entries",
            headers=self._headers(),
            params={},
            timeout=self.timeout,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"PagerDuty log_entries returned status {resp.status_code}")
        payload = resp.json() or {}
        if isinstance(payload, Mapping):
            entries = payload.get("log_entries")
            if isinstance(entries, list):
                return [e for e in entries if isinstance(e, Mapping)]
        return []

    def health(self) -> dict[str, object]:
        """Probe the source. Never raises."""
        try:
            resp = _invoke_get(
                self._transport,
                url=f"{self.base_url}/incidents",
                headers=self._headers(),
                params={"limit": 1},
                timeout=self.timeout,
            )
        except Exception:  # never raises
            logger.warning("health check failed", exc_info=True)
            return {"ok": False, "detail": "health check failed"}
        status = int(getattr(resp, "status_code", 0))
        if status >= 400:
            return {"ok": False, "detail": f"http status {status}"}
        return {"ok": True, "detail": f"http status {status}"}

    # -- normalization ----------------------------------------------------

    def _normalize(self, inc: Mapping[str, object]) -> dict[str, object]:
        title_obj = inc.get("title", "")
        title = title_obj if isinstance(title_obj, str) else str(title_obj)
        urgency_obj = inc.get("urgency", "")
        urgency = urgency_obj if isinstance(urgency_obj, str) else str(urgency_obj)
        message = f"{title} (urgency={urgency})" if urgency else str(title)

        service_obj = inc.get("service") or {}
        service: Mapping[str, object] = service_obj if isinstance(service_obj, Mapping) else {}
        escalation_obj = inc.get("escalation_policy") or {}
        escalation: Mapping[str, object] = escalation_obj if isinstance(escalation_obj, Mapping) else {}
        assignments_obj = inc.get("assignments") or []
        assignments: list[object] = assignments_obj if isinstance(assignments_obj, list) else []
        assignees: list[str] = []
        for a in assignments:
            if not isinstance(a, Mapping):
                continue
            assignee_obj = a.get("assignee") or {}
            assignee = assignee_obj if isinstance(assignee_obj, Mapping) else {}
            summary_obj = assignee.get("summary", "")
            assignees.append(str(summary_obj))
        labels: dict[str, object] = {
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
            "raw": dict(inc),
        }
