"""Asana project-tasks connector.

Self-contained connector — imports nothing from sibling connector modules or a
shared base. It pulls Asana project tasks via the
``GET /projects/{project_gid}/tasks`` API and normalizes them into the gludd
event envelope.

Security posture:

* The Asana API token is read **only** from an environment variable named by
  ``config['token_env']`` (never hardcoded, never logged).
* The API base URL is SSRF-guarded against private / loopback / link-local
  hosts unless ``config['allow_private']`` is explicitly truthy.
* The injected HTTP transport is time-bounded and never uses ``shell=True``.
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

API_BASE = "https://app.asana.com/api/1.0"

_DEFAULT_OPT_FIELDS = (
    "name,completed,modified_at,created_at,assignee.name,"
    "due_on,permalink_url,notes"
)


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
        params: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> HttpResponse:  # pragma: no cover - structural typing only
        ...


def _default_transport(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> HttpResponse:
    """Default transport backed by httpx (imported lazily to stay optional)."""
    import httpx

    resp = httpx.request(
        method,
        url,
        headers=headers,
        params=params,
        timeout=timeout,
        follow_redirects=False,
    )
    return resp


class AsanaSource:
    """Asana project-tasks source.

    Parameters
    ----------
    config:
        Mapping with keys:

        * ``project_gid`` (required): the Asana project GID.
        * ``token_env`` (required): name of the env var holding the API token.
        * ``base_url`` (optional): override API base URL
          (default ``https://app.asana.com/api/1.0``).
        * ``allow_private`` (optional): opt-in to permit private/loopback hosts
          (default ``False``).
        * ``max_pages`` (optional): pagination bound (default 10).
        * ``timeout`` (optional): per-request timeout seconds (default 30).
    transport:
        Optional injected :class:`Transport`. Defaults to an httpx-backed one.
    """

    KIND = "tasks"
    name = "asana"

    def __init__(
        self,
        config: dict[str, Any],
        transport: Transport | None = None,
    ) -> None:
        self.config = dict(config)
        self.transport: Transport = transport or _default_transport

        project_gid = str(self.config.get("project_gid", "")).strip()
        if not project_gid:
            raise ValueError("asana: 'project_gid' is required")
        self.project_gid = project_gid

        token_env = str(self.config.get("token_env", "")).strip()
        if not token_env:
            raise ValueError("asana: 'token_env' is required")
        self.token_env = token_env

        self.allow_private = bool(self.config.get("allow_private", False))
        self.max_pages = int(self.config.get("max_pages", 10))
        self.timeout = float(self.config.get("timeout", 30.0))

        self.base_url = str(self.config.get("base_url", API_BASE)).strip().rstrip("/")
        self._guard_ssrf(self.base_url)

        self._tasks_url = f"{self.base_url}/projects/{self.project_gid}/tasks"

    # -- internals ---------------------------------------------------------

    def _guard_ssrf(self, url: str) -> None:
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(
                f"asana: unsupported URL scheme: {parsed.scheme!r}"
            )
        host = parsed.hostname or ""
        if not self.allow_private and is_url_blocked(
            url, scheme_allowlist=("http", "https")
        ):
            raise ValueError(
                f"asana: refusing private/loopback host {host!r} "
                "(set allow_private=True to override)"
            )

    def _token(self) -> str:
        token = os.environ.get(self.token_env)
        if not token:
            raise RuntimeError(
                f"asana: token env var {self.token_env!r} is unset or empty"
            )
        return token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token()}",
            "Accept": "application/json",
        }

    def _normalize(self, record: dict[str, Any]) -> dict[str, Any]:
        assignee = record.get("assignee") or {}
        return {
            "ts": record.get("modified_at") or record.get("created_at"),
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": (
                "completed" if record.get("completed") else "open"
            ),
            "message": record.get("name"),
            "value": 1,
            "labels": {
                "gid": record.get("gid"),
                "assignee": assignee.get("name"),
                "due_on": record.get("due_on"),
                "completed": record.get("completed", False),
                "permalink_url": record.get("permalink_url"),
            },
            "raw": record,
        }

    # -- public API --------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """GET /projects/{project_gid}/tasks?limit=1; never raises."""
        try:
            resp = self.transport(
                "GET",
                self._tasks_url,
                headers=self._headers(),
                params={"limit": "1", "opt_fields": _DEFAULT_OPT_FIELDS},
                timeout=self.timeout,
            )
        except Exception:
            logger.warning("health check failed", exc_info=True)
            return {"ok": False, "detail": "health check failed"}
        status = getattr(resp, "status_code", 0)
        if 200 <= status < 300:
            return {
                "ok": True,
                "detail": f"asana reachable (HTTP {status})",
            }
        return {"ok": False, "detail": f"asana HTTP {status}"}

    def query(
        self, spec: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Fetch + normalize project tasks.

        ``spec`` may contain ``modified_since``, ``completed_since``,
        ``assignee``, ``limit``, and ``workspace`` — each passed as a
        query parameter. Pagination follows ``body["next_page"]["uri"]``
        up to ``max_pages``.
        """
        spec = spec or {}
        params: dict[str, str] = {"opt_fields": _DEFAULT_OPT_FIELDS}
        for key in (
            "modified_since",
            "completed_since",
            "assignee",
            "limit",
            "workspace",
        ):
            if key in spec:
                params[key] = str(spec[key])

        out: list[dict[str, Any]] = []
        page_url: str = self._tasks_url

        for _ in range(max(1, self.max_pages)):
            resp = self.transport(
                "GET",
                page_url,
                headers=self._headers(),
                params=params,
                timeout=self.timeout,
            )
            status = getattr(resp, "status_code", 0)
            if not (200 <= status < 300):
                raise RuntimeError(f"asana: query failed HTTP {status}")

            try:
                body = resp.json()
            except Exception:
                body = {}

            for record in body.get("data") or []:
                if isinstance(record, dict):
                    out.append(self._normalize(record))

            next_page = body.get("next_page")
            if not next_page or not next_page.get("uri"):
                break
            page_url = next_page["uri"]
            params = {}

        return out
