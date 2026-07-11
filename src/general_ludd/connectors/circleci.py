"""CircleCI pipeline observability source (self-contained).

This module is intentionally standalone: it does not import the connectors
base/__init__ or any sibling connector. SSRF guarding, the transport contract
and record normalization are duplicated here so the file can be dropped in and
tested in isolation.

HTTP is performed through an INJECTABLE transport with the contract
``http_get(url, headers) -> (status, json)``. The default implementation is a
small, time-bounded ``urllib`` wrapper; tests pass a mock so no real network is
touched. The transport never uses a shell.

Auth uses the ``Circle-Token`` header whose value is read from an environment
variable at call time and is NEVER hardcoded.
"""

from __future__ import annotations

import json as _json
import logging
import os
import urllib.parse
from collections.abc import Callable
from datetime import datetime

import httpx

from general_ludd.security.ssrf import is_url_blocked

logger = logging.getLogger(__name__)

__all__ = ["CircleCiSource"]

_KIND = "pipeline"
_DEFAULT_BASE_URL = "https://circleci.com"
_DEFAULT_TIMEOUT = 10.0

# A transport is any callable matching ``http_get(url, headers) -> (status, json)``.
Transport = Callable[[str, dict[str, str]], "tuple[int, object]"]


def _validate_base_url(base_url: str) -> str:
    """Validate and normalize *base_url*, refusing internal/metadata targets."""
    if is_url_blocked(base_url, scheme_allowlist=("http", "https")):
        raise ValueError(
            f"base_url host is blocked (loopback/private/metadata): {base_url!r}"
        )
    return base_url.rstrip("/")


def _parse_ts(value: object) -> float | None:
    """Parse an ISO-8601 timestamp into a POSIX float, or None."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if "." in text:
        head, _, tail = text.partition(".")
        frac = tail
        tz = ""
        for marker in ("+", "-"):
            idx = tail.find(marker)
            if idx != -1:
                frac, tz = tail[:idx], tail[idx:]
                break
        digits = "".join(c for c in frac if c.isdigit())[:6]
        text = f"{head}.{digits}{tz}" if digits else f"{head}{tz}"
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def _default_http_get(url: str, headers: dict[str, str]) -> tuple[int, object]:
    """Real, time-bounded httpx transport. Never uses a shell.

    ``follow_redirects=False`` blocks SSRF via redirect-to-metadata: a 3xx
    response is returned as-is (and rejected by callers' 2xx checks) rather
    than being transparently followed to an internal host.
    """
    with httpx.Client(timeout=_DEFAULT_TIMEOUT, follow_redirects=False) as client:
        resp = client.get(url, headers=headers)
    raw = resp.content
    status = int(resp.status_code)
    body: object = None
    if raw:
        try:
            body = _json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            body = None
    return status, body


class CircleCiSource:
    """Observability source for CircleCI pipelines (duck-typed)."""

    KIND: str = _KIND

    def __init__(
        self,
        config: dict[str, object],
        *,
        http_get: Transport | None = None,
    ) -> None:
        config = config or {}
        project_slug = config.get("project_slug")
        if not project_slug:
            raise ValueError("config['project_slug'] is required")
        self.project_slug: str = str(project_slug)
        self.base_url: str = _validate_base_url(
            str(config.get("base_url") or _DEFAULT_BASE_URL)
        )
        # Circle-Token header value is read from this env var at call time.
        self.token_env: str = str(config.get("token_env") or "CIRCLE_TOKEN")
        _name = config.get("name")
        self.name: str = str(_name) if _name else f"circleci:{self.project_slug}"
        self._http_get: Transport = http_get or _default_http_get

    # -- internal helpers ---------------------------------------------------
    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": "general-ludd-connector",
        }
        token = os.environ.get(self.token_env)
        if token:
            headers["Circle-Token"] = token
        return headers

    def _pipeline_url(self) -> str:
        return (
            f"{self.base_url}/api/v2/project/"
            f"{urllib.parse.quote(self.project_slug, safe='')}/pipeline"
        )

    def _normalize(self, pipeline: dict[str, object]) -> dict[str, object]:
        vcs = pipeline.get("vcs")
        revision = ""
        branch = ""
        if isinstance(vcs, dict):
            revision = str(vcs.get("revision") or "")
            branch = str(vcs.get("branch") or "")
        status = pipeline.get("state") or pipeline.get("status") or ""
        return {
            "ts": _parse_ts(pipeline.get("created_at"))
            or _parse_ts(pipeline.get("updated_at")),
            "source": self.name,
            "kind": _KIND,
            "level_or_status": str(status),
            "message": f"{revision} @ {branch}".strip(),
            "value": None,
            "labels": {
                "id": pipeline.get("id"),
                "number": pipeline.get("number"),
            },
            "raw": pipeline,
        }

    def _normalize_workflow(self, workflow: dict[str, object]) -> dict[str, object]:
        status = workflow.get("status") or workflow.get("state") or ""
        return {
            "ts": _parse_ts(workflow.get("created_at"))
            or _parse_ts(workflow.get("stopped_at")),
            "source": self.name,
            "kind": _KIND,
            "level_or_status": str(status),
            "message": str(workflow.get("name") or ""),
            "value": None,
            "labels": {
                "id": workflow.get("id"),
                "number": workflow.get("pipeline_number"),
            },
            "raw": workflow,
        }

    # -- public API ---------------------------------------------------------
    def health(self) -> dict[str, object]:
        """Probe the pipeline endpoint. Never raises."""
        try:
            status, _ = self._http_get(self._pipeline_url(), self._headers())
        except Exception:  # health must never propagate
            logger.warning("health check failed", exc_info=True)
            return {"ok": False, "detail": "health check failed"}
        if 200 <= status < 300:
            return {"ok": True, "detail": f"HTTP {status}"}
        return {"ok": False, "detail": f"HTTP {status}"}

    def query(self, spec: dict[str, object]) -> list[dict[str, object]]:
        """List recent pipelines and return normalized records.

        The CircleCI v2 list endpoint wraps results in an ``items`` array.
        """
        spec = spec or {}
        try:
            status, body = self._http_get(self._pipeline_url(), self._headers())
        except Exception:
            return []
        if not (200 <= status < 300) or not isinstance(body, dict):
            return []
        items = body.get("items")
        if not isinstance(items, list):
            return []
        records: list[dict[str, object]] = []
        for pipeline in items:
            if isinstance(pipeline, dict):
                records.append(self._normalize(pipeline))
        return records

    def fetch_workflows(self, pipeline_id: object) -> list[dict[str, object]]:
        """GET pipeline/{id}/workflow -> normalized workflow records.

        Never raises.
        """
        url = (
            f"{self.base_url}/api/v2/pipeline/"
            f"{urllib.parse.quote(str(pipeline_id), safe='')}/workflow"
        )
        try:
            status, body = self._http_get(url, self._headers())
        except Exception:
            return []
        if not (200 <= status < 300) or not isinstance(body, dict):
            return []
        items = body.get("items")
        if not isinstance(items, list):
            return []
        records: list[dict[str, object]] = []
        for workflow in items:
            if isinstance(workflow, dict):
                records.append(self._normalize_workflow(workflow))
        return records
