"""Grafana OnCall incident-source connector.

Self-contained: no imports from sibling connectors or a shared base. Pulls
alert groups from a Grafana OnCall instance and normalizes them into the
incident record shape used across gludd's incident sources.

SECURITY NOTES:
  - The API token is read ONLY from the environment variable named by
    ``config['token_env']`` (default ``GRAFANA_ONCALL_TOKEN``). It is never
    accepted inline, never hardcoded, and never written to any record, label,
    log line, or raised error.
  - ``base_url`` is REQUIRED (Grafana OnCall is instance-specific; there is no
    universal public host). It is SSRF-guarded: private/loopback/link-local/
    non-public hosts are rejected unless ``allow_private=True``. Self-hosted
    on-prem instances therefore require an explicit ``allow_private`` opt-in.
  - HTTP requests are time-bound and never use a shell.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import socket
from collections.abc import Mapping
from typing import Protocol, TypedDict, cast
from urllib.parse import urlsplit

import httpx

from general_ludd.security.ssrf import _ip_addr_is_blocked, host_is_blocked

logger = logging.getLogger(__name__)

_DEFAULT_TOKEN_ENV = "GRAFANA_ONCALL_TOKEN"
_DEFAULT_TIMEOUT = 15.0
_DEFAULT_LIMIT = 100


# --------------------------------------------------------------------------- #
# Typed API-response shapes (Grafana OnCall REST API).
# --------------------------------------------------------------------------- #
class GrafanaAlertGroup(TypedDict, total=False):
    """One element of the OnCall ``alert_groups`` response array.

    ``integration``, ``team`` and ``acknowledged_by`` are ``object`` because
    OnCall may emit either a string id or a nested object depending on the
    endpoint revision; callers treat them as opaque labels.
    """

    id: str
    state: str
    title: str
    created_at: str
    integration: object
    team: object
    acknowledged_by: object


class GrafanaOnCallResultsResponse(TypedDict, total=False):
    """Paginated response shape of ``GET /api/v1/alert_groups``."""

    results: list[GrafanaAlertGroup]
    data: list[GrafanaAlertGroup]


class GrafanaOnCallQuerySpec(TypedDict, total=False):
    """Caller-supplied query selection accepted by :meth:`GrafanaOnCallSource.query`."""

    limit: int
    state: str


class GrafanaOnCallConfig(TypedDict, total=False):
    """Caller-supplied config accepted by :meth:`GrafanaOnCallSource.__init__`."""

    name: str
    base_url: str
    token_env: str
    timeout: float | int | str
    limit: int | str
    allow_private: bool


class CallableTransport(Protocol):
    """Compact method-and-URL callback used by generated workflows."""

    def __call__(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        timeout: float,
    ) -> tuple[int, object]: ...


class GrafanaOnCallSource:
    """Incident source backed by the Grafana OnCall API.

    GET {base_url}/api/v1/alert_groups
    Header: ``Authorization: <token>`` (raw token, no scheme prefix)
    """

    KIND = "incidents"

    def __init__(
        self,
        config: Mapping[str, object] | None = None,
        transport: httpx.BaseTransport | CallableTransport | None = None,
    ) -> None:
        config = dict(config or {})
        self.name: str = str(config.get("name", "grafana_oncall"))
        self._token_env: str = str(config.get("token_env", _DEFAULT_TOKEN_ENV))
        self._timeout: float = float(cast(float | int | str | bool, config.get("timeout", _DEFAULT_TIMEOUT)))
        self._limit: int = int(cast(int | float | str | bool, config.get("limit", _DEFAULT_LIMIT)))
        self._allow_private: bool = bool(config.get("allow_private", False))

        base_url = config.get("base_url")
        if not base_url:
            raise ValueError("GrafanaOnCallSource requires config['base_url']")
        base_url = str(base_url)
        # base_url is always a caller-supplied override here -> always guarded.
        _guard_ssrf(base_url, allow_private=self._allow_private)
        self._base_url: str = base_url.rstrip("/")
        if transport is not None and callable(transport) and not callable(
            getattr(transport, "handle_request", None)
        ):
            callback = transport

            def handler(request: httpx.Request) -> httpx.Response:
                status, body = callback(
                    request.method,
                    str(request.url),
                    headers=dict(request.headers),
                    timeout=self._timeout,
                )
                if isinstance(body, bytes):
                    return httpx.Response(status, content=body)
                if isinstance(body, str):
                    return httpx.Response(status, text=body)
                return httpx.Response(status, json=body)

            self._transport: httpx.BaseTransport | None = httpx.MockTransport(
                handler
            )
        else:
            self._transport = cast("httpx.BaseTransport | None", transport)

    # -- secrets -----------------------------------------------------------

    def _token(self) -> str:
        token = os.environ.get(self._token_env)
        if not token:
            raise _MissingToken(
                f"environment variable {self._token_env} is not set"
            )
        return token

    def _client(self) -> httpx.Client | _CallableClient:
        if callable(self._transport) and not isinstance(self._transport, httpx.BaseTransport):
            return _CallableClient(self._transport)
        return httpx.Client(timeout=self._timeout, transport=self._transport)

    # -- health ------------------------------------------------------------

    def health(self) -> dict[str, object]:
        """Return ``{'ok': bool, 'detail': str}``. Never raises."""
        try:
            token = self._token()
        except _MissingToken as exc:
            logger.warning("grafana_oncall missing token", exc_info=True)
            return {"ok": False, "detail": type(exc).__name__}
        try:
            with self._client() as client:
                resp = client.get(
                    f"{self._base_url}/api/v1/alert_groups",
                    headers=_auth_header(token),
                    params={"perpage": 1},
                )
            if resp.status_code == 200:
                return {"ok": True, "detail": "grafana oncall reachable"}
            return {
                "ok": False,
                "detail": f"unexpected status {resp.status_code}",
            }
        except Exception as exc:  # health must never raise
            return {"ok": False, "detail": _safe_err(exc)}

    # -- query -------------------------------------------------------------

    def query(self, spec: GrafanaOnCallQuerySpec | None = None) -> list[dict[str, object]]:
        """Fetch alert groups and normalize to incident records."""
        spec = spec or {}
        limit_val = spec.get("limit", self._limit)
        perpage = int(limit_val) if isinstance(limit_val, (int, float)) else self._limit
        params: dict[str, object] = {"perpage": perpage}
        state = spec.get("state")
        if state:
            params["state"] = str(state)

        token = self._token()
        with self._client() as client:
            resp = client.get(
                f"{self._base_url}/api/v1/alert_groups",
                headers=_auth_header(token),
                params=cast("Mapping[str, str | int | list[str]]", params),
            )
            resp.raise_for_status()
            payload = resp.json()

        groups: list[object]
        if isinstance(payload, dict):
            raw = payload.get("results")
            if raw is None:
                raw = payload.get("data")
            groups = raw if isinstance(raw, list) else []
        elif isinstance(payload, list):
            groups = payload
        else:
            groups = []
        return [
            rec
            for group in groups
            for rec in [self._normalize(cast("Mapping[str, object]", group))]
            if isinstance(group, Mapping) and rec is not None
        ]

    def _normalize(self, group: Mapping[str, object]) -> dict[str, object]:
        state = group.get("state")
        return {
            "ts": group.get("created_at"),
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": state,
            "message": group.get("title"),
            "value": None,
            "labels": {
                "integration": group.get("integration"),
                "team": group.get("team"),
                "state": state,
                "acknowledged_by": group.get("acknowledged_by"),
            },
            "raw": dict(group),
        }


# ---------------------------------------------------------------------------
# helpers (module-private; not shared with sibling connectors)
# ---------------------------------------------------------------------------


class _MissingToken(RuntimeError):
    """Raised internally when the configured token env var is absent."""


class _CallableClient:
    """Minimal context-managed client for ``(method, url) -> (status, body)`` fakes."""

    def __init__(self, transport: object) -> None:
        self._transport = transport

    def __enter__(self) -> _CallableClient:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def get(self, url: str, **kwargs: object) -> _TupleResponse:
        result = self._transport("GET", url, **kwargs)  # type: ignore[operator]
        if isinstance(result, tuple) and len(result) == 2:
            return _TupleResponse(result[0], result[1])
        return cast(_TupleResponse, result)


class _TupleResponse:
    def __init__(self, status: object, body: object) -> None:
        self.status_code = int(status) if isinstance(status, int) else 0
        self._body = body

    def json(self) -> object:
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _auth_header(token: str) -> dict[str, str]:
    return {
        "Authorization": token,
        "Accept": "application/json",
    }


def _safe_err(exc: Exception) -> str:
    """Best-effort error string that never leaks the request URL/credentials."""
    return f"{type(exc).__name__}"


def _guard_ssrf(base_url: str, *, allow_private: bool) -> None:
    """Reject base URLs whose host is private/loopback/metadata unless opted in.

    The literal-host decision (loopback/metadata NAMES, the ``.localhost`` TLD,
    and literal metadata/private/reserved/non-global IPs) is delegated to the
    canonical :func:`general_ludd.security.ssrf.host_is_blocked` so it can
    never drift from the single source of truth — this closes a real gap the
    connector's own bespoke name blocklist had (it only recognized the bare
    name ``"localhost"`` and 4 TLD suffixes, not ``"metadata"``,
    ``"instance-data"``, the ``ip6-*`` loopback aliases, or the Alibaba
    ``100.100.100.200`` metadata IP).

    On top of that canonical decision this connector keeps ONE extra check
    local rather than promoting it into the shared module: an internal-TLD
    suffix blocklist (``.local``/``.internal``/``.lan``/``.intranet``/
    ``.corp``). That is a policy call specific to Grafana OnCall (a
    self-hosted instance is commonly reached by exactly such an internal DNS
    name) rather than a generally-sound rule for every connector/guard in the
    codebase — some environments legitimately use one of those suffixes for a
    public-facing service, so it stays an additive, connector-local rule.

    For a hostname that is not an IP literal, this resolves it (fail CLOSED:
    an unresolvable host is refused, never silently allowed — a DNS failure
    could mask an internal/rebinding target) and re-checks every resolved
    address through the canonical numeric classifier
    :func:`general_ludd.security.ssrf._ip_addr_is_blocked` (adds the
    ``not is_global`` catch for TEST-NET/documentation ranges the old local
    flag set was missing).
    """
    if allow_private:
        return
    host = urlsplit(base_url).hostname
    if not host:
        raise ValueError("base_url has no host")

    if host_is_blocked(host):
        raise ValueError(
            f"base_url host {host!r} is blocked by SSRF policy "
            "(set allow_private=True to override)"
        )

    lowered = host.lower()
    if lowered.endswith((".local", ".internal", ".lan", ".intranet", ".corp")):
        raise ValueError(
            f"base_url host {host!r} is an internal name "
            "(set allow_private=True to override)"
        )

    try:
        ipaddress.ip_address(host)
        return  # literal public IP -- host_is_blocked above already vetted it.
    except ValueError:
        pass

    # host is a DNS name (not a literal): resolve and re-check every address.
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        # Fail closed: an unresolvable host must not be silently allowed
        # (a DNS failure could mask an internal/rebinding target). Match
        # the cilium_hubble / nomad connectors' fail-closed posture.
        raise ValueError(
            f"base_url host {host!r} could not be resolved "
            "(set allow_private=True to override)"
        ) from exc

    for info in infos:
        addr = str(info[4][0]).split("%")[0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if _ip_addr_is_blocked(ip):
            raise ValueError(
                f"base_url host {host!r} resolves to a non-public address "
                "(set allow_private=True to override)"
            )
