"""Linear issue-tracking connector.

Self-contained connector — imports nothing from sibling connector modules or a
shared base. It pulls Linear issues via the GraphQL API at
``https://api.linear.app/graphql`` and normalizes them into the gludd event
envelope.

Security posture:

* The Linear API token is read **only** from an environment variable named by
  ``config['token_env']`` (never hardcoded, never logged).
* The GraphQL endpoint is SSRF-guarded against private / loopback / link-local
  hosts.
* The token is passed as a raw ``Authorization`` header (no ``Bearer`` prefix).
* The injected HTTP transport is time-bounded.
* ``health()`` never raises.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit

from general_ludd.connectors._protocols import HttpResponse
from general_ludd.security.ssrf import is_url_blocked

logger = logging.getLogger(__name__)

LINEAR_GRAPHQL_URL = "https://api.linear.app/graphql"

_ISSUES_QUERY = """\
query Issues($teamId: String!, $after: String) {
  team(id: $teamId) {
    issues(first: 50, after: $after) {
      nodes {
        id
        title
        description
        state { name }
        createdAt
        updatedAt
        assignee { name }
        priority
      }
      pageInfo {
        hasNextPage
        endCursor
      }
    }
  }
}"""

_HEALTH_QUERY = "query { viewer { id name } }"


@runtime_checkable
class Transport(Protocol):
    """Injectable, synchronous HTTP transport.

    A callable taking an HTTP method and URL plus keyword args and returning a
    :class:`HttpResponse`. The default implementation wraps :mod:`httpx`, but
    tests inject a fake so no network access ever occurs.
    """

    def __call__(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> HttpResponse:  # pragma: no cover - structural typing only
        ...


def _default_transport(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> HttpResponse:
    """Default transport backed by httpx (imported lazily to stay optional)."""
    import httpx

    resp = httpx.request(
        method,
        url,
        headers=headers,
        json=json,
        timeout=timeout,
        follow_redirects=False,
    )
    return resp


class LinearSource:
    """Linear issue-tracking source.

    Parameters
    ----------
    config:
        Mapping with keys:

        * ``team_id`` (required): Linear team UUID.
        * ``token_env`` (required): name of the env var holding the API token.
        * ``graphql_url`` (optional): override GraphQL endpoint (default
          ``https://api.linear.app/graphql``).
        * ``max_pages`` (optional): pagination bound (default 10).
        * ``timeout`` (optional): per-request timeout seconds (default 30).
    transport:
        Optional injected :class:`Transport`. Defaults to an httpx-backed one.
    """

    KIND = "tickets"
    name = "linear"

    def __init__(
        self,
        config: dict[str, Any],
        transport: Transport | None = None,
    ) -> None:
        self.config = dict(config)
        self.transport: Transport = transport or _default_transport

        self.team_id = str(self.config.get("team_id", "")).strip()
        if not self.team_id:
            raise ValueError("linear: 'team_id' is required")

        self.token_env = str(self.config.get("token_env", "")).strip()
        if not self.token_env:
            raise ValueError("linear: 'token_env' is required")

        self.max_pages = int(self.config.get("max_pages", 10))
        self.timeout = float(self.config.get("timeout", 30.0))

        self.url = str(self.config.get("graphql_url", LINEAR_GRAPHQL_URL)).strip()
        self._guard_ssrf(self.url)

    # -- internals ---------------------------------------------------------

    def _guard_ssrf(self, url: str) -> None:
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"linear: unsupported URL scheme: {parsed.scheme!r}")
        host = parsed.hostname or ""
        if is_url_blocked(url, scheme_allowlist=("http", "https")):
            raise ValueError(
                f"linear: refusing private/loopback host {host!r}"
            )

    def _token(self) -> str:
        token = os.environ.get(self.token_env)
        if not token:
            raise RuntimeError(
                f"linear: token env var {self.token_env!r} is unset or empty"
            )
        return token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": self._token(),
            "Content-Type": "application/json",
        }

    def _normalize(self, issue: dict[str, Any]) -> dict[str, Any]:
        state_info = issue.get("state") or {}
        assignee_info = issue.get("assignee") or {}
        return {
            "ts": issue.get("createdAt"),
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": state_info.get("name"),
            "message": issue.get("title"),
            "value": 1,
            "labels": {
                "linear_id": issue.get("id"),
                "state": state_info.get("name"),
                "assignee": assignee_info.get("name"),
                "priority": issue.get("priority"),
            },
            "raw": issue,
        }

    # -- public API --------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Probe the Linear API with a viewer query; never raises."""
        try:
            resp = self.transport(
                "POST",
                self.url,
                headers=self._headers(),
                json={"query": _HEALTH_QUERY},
                timeout=self.timeout,
            )
        except Exception:  # health must never raise
            logger.warning("health check failed", exc_info=True)
            return {"ok": False, "detail": "health check failed"}
        status = getattr(resp, "status_code", 0)
        if 200 <= status < 300:
            return {"ok": True, "detail": f"linear reachable (HTTP {status})"}
        return {"ok": False, "detail": f"linear HTTP {status}"}

    def query(self, spec: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Fetch + normalize Linear issues via GraphQL.

        Pagination follows the cursor from ``pageInfo.endCursor`` up to
        ``max_pages``.
        """
        spec = spec or {}
        out: list[dict[str, Any]] = []
        after_cursor: str | None = None

        for _ in range(max(1, self.max_pages)):
            variables: dict[str, Any] = {"teamId": self.team_id}
            if after_cursor:
                variables["after"] = after_cursor
            headers = self._headers()

            try:
                resp = self.transport(
                    "POST",
                    self.url,
                    headers=headers,
                    json={"query": _ISSUES_QUERY, "variables": variables},
                    timeout=self.timeout,
                )
            except Exception:
                logger.warning("linear query transport failed", exc_info=True)
                return out
            status = getattr(resp, "status_code", 0)
            if not (200 <= status < 300):
                raise RuntimeError(f"linear: query failed HTTP {status}")

            body = resp.json()
            data = (body or {}).get("data") or {}
            team = data.get("team") or {}
            issues = team.get("issues") or {}
            nodes = issues.get("nodes") or []

            for issue in nodes:
                if isinstance(issue, dict):
                    out.append(self._normalize(issue))

            page_info = issues.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")
            if not cursor:
                break
            after_cursor = cursor

        return out
