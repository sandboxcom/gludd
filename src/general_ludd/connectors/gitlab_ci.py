"""GitLab CI pipeline observability source (self-contained).

This module is intentionally standalone: it does not import the connectors
base/__init__ or any sibling connector. SSRF guarding, the transport contract
and record normalization are duplicated here so the file can be dropped in and
tested in isolation.

HTTP is performed through an INJECTABLE transport with the contract
``http_get(url, headers) -> (status, json)``. The default implementation is a
small, time-bounded ``urllib`` wrapper; tests pass a mock so no real network is
touched. The transport never uses a shell.
"""

from __future__ import annotations

import json as _json
import logging
import os
import urllib.parse
from collections.abc import Callable
from datetime import datetime

import httpx

from general_ludd.connectors._util import validate_base_url as _validate_base_url

logger = logging.getLogger(__name__)

__all__ = ["GitlabCiSource"]

_KIND = "pipeline"
_DEFAULT_BASE_URL = "https://gitlab.com"
_DEFAULT_TIMEOUT = 10.0

Transport = Callable[[str, dict[str, str]], "tuple[int, object]"]


def _parse_ts(value: object) -> float | None:
    """Parse an ISO-8601 timestamp into a POSIX float, or None."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
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


class GitlabCiSource:
    """Observability source for GitLab CI pipelines (duck-typed)."""

    KIND: str = _KIND

    def __init__(
        self,
        config: dict[str, object],
        *,
        http_get: Transport | None = None,
    ) -> None:
        config = config or {}
        project_id = config.get("project_id")
        if project_id in (None, ""):
            raise ValueError("config['project_id'] is required")
        self.project_id: str = str(project_id)
        self.base_url: str = _validate_base_url(
            str(config.get("base_url") or _DEFAULT_BASE_URL)
        )
        # PRIVATE-TOKEN header value is read from this env var at call time.
        self.token_env: str = str(config.get("token_env") or "GITLAB_PRIVATE_TOKEN")
        _name = config.get("name")
        self.name: str = str(_name) if _name else f"gitlab-ci:{self.project_id}"
        self._http_get: Transport = http_get or _default_http_get

    # -- internal helpers ---------------------------------------------------
    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": "general-ludd-connector",
        }
        token = os.environ.get(self.token_env)
        if token:
            headers["PRIVATE-TOKEN"] = token
        return headers

    def _pipelines_url(self, spec: dict[str, object]) -> str:
        params: dict[str, str] = {}
        ref = spec.get("ref")
        if ref:
            params["ref"] = str(ref)
        status = spec.get("status")
        if status:
            params["status"] = str(status)
        per_page = spec.get("per_page") or spec.get("limit")
        if isinstance(per_page, int) and per_page > 0:
            params["per_page"] = str(per_page)
        base = (
            f"{self.base_url}/api/v4/projects/"
            f"{urllib.parse.quote(self.project_id, safe='')}/pipelines"
        )
        if params:
            return f"{base}?{urllib.parse.urlencode(params)}"
        return base

    def _normalize(self, pipeline: dict[str, object]) -> dict[str, object]:
        ref = pipeline.get("ref") or ""
        sha = pipeline.get("sha") or ""
        status = pipeline.get("status") or ""
        return {
            "ts": _parse_ts(pipeline.get("updated_at"))
            or _parse_ts(pipeline.get("created_at")),
            "source": self.name,
            "kind": _KIND,
            "level_or_status": str(status),
            "message": f"{ref} @ {sha}".strip(),
            "value": None,
            "labels": {
                "id": pipeline.get("id"),
                "web_url": pipeline.get("web_url"),
                "source": pipeline.get("source"),
            },
            "raw": pipeline,
        }

    # -- public API ---------------------------------------------------------
    def health(self) -> dict[str, object]:
        """Probe the pipelines endpoint. Never raises."""
        try:
            status, _ = self._http_get(self._pipelines_url({}), self._headers())
        except Exception:  # health must never propagate
            logger.warning("health check failed", exc_info=True)
            return {"ok": False, "detail": "health check failed"}
        if 200 <= status < 300:
            return {"ok": True, "detail": f"HTTP {status}"}
        return {"ok": False, "detail": f"HTTP {status}"}

    def query(self, spec: dict[str, object]) -> list[dict[str, object]]:
        """List recent pipelines and return normalized records.

        Supported *spec* keys: ``ref`` (str), ``status`` (str),
        ``per_page``/``limit`` (int).
        """
        spec = spec or {}
        try:
            status, body = self._http_get(self._pipelines_url(spec), self._headers())
        except Exception:
            return []
        if not (200 <= status < 300) or not isinstance(body, list):
            return []
        records: list[dict[str, object]] = []
        for pipeline in body:
            if isinstance(pipeline, dict):
                records.append(self._normalize(pipeline))
        return records

    def fetch_jobs(self, pipeline_id: object) -> list[dict[str, object]]:
        """GET pipelines/{id}/jobs -> raw list of job dicts. Never raises."""
        url = (
            f"{self.base_url}/api/v4/projects/"
            f"{urllib.parse.quote(self.project_id, safe='')}/pipelines/"
            f"{urllib.parse.quote(str(pipeline_id), safe='')}/jobs"
        )
        try:
            status, body = self._http_get(url, self._headers())
        except Exception:
            return []
        if not (200 <= status < 300) or not isinstance(body, list):
            return []
        return [job for job in body if isinstance(job, dict)]
