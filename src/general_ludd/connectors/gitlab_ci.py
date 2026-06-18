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
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import datetime
from typing import Any

from general_ludd.security.ssrf import is_url_blocked

__all__ = ["GitlabCiSource"]

_KIND = "pipeline"
_DEFAULT_BASE_URL = "https://gitlab.com"
_DEFAULT_TIMEOUT = 10.0

# A transport is any callable matching ``http_get(url, headers) -> (status, json)``.
Transport = Callable[[str, dict[str, str]], "tuple[int, Any]"]


def _validate_base_url(base_url: str) -> str:
    """Validate and normalize *base_url*, refusing internal/metadata targets."""
    if is_url_blocked(base_url, scheme_allowlist=("http", "https")):
        raise ValueError(
            f"base_url host is blocked (loopback/private/metadata): {base_url!r}"
        )
    return base_url.rstrip("/")


def _parse_ts(value: Any) -> float | None:
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


def _default_http_get(url: str, headers: dict[str, str]) -> tuple[int, Any]:
    """Real, time-bounded urllib transport. Never uses a shell."""
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=_DEFAULT_TIMEOUT) as resp:
            raw = resp.read()
            status = int(resp.getcode() or 0)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        status = int(exc.code)
    body: Any = None
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
        config: dict[str, Any],
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
        self.name: str = config.get("name") or f"gitlab-ci:{self.project_id}"
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

    def _pipelines_url(self, spec: dict[str, Any]) -> str:
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

    def _normalize(self, pipeline: dict[str, Any]) -> dict[str, Any]:
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
    def health(self) -> dict[str, Any]:
        """Probe the pipelines endpoint. Never raises."""
        try:
            status, _ = self._http_get(self._pipelines_url({}), self._headers())
        except Exception as exc:  # health must never propagate
            return {"ok": False, "detail": f"transport error: {exc}"}
        if 200 <= status < 300:
            return {"ok": True, "detail": f"HTTP {status}"}
        return {"ok": False, "detail": f"HTTP {status}"}

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
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
        records: list[dict[str, Any]] = []
        for pipeline in body:
            if isinstance(pipeline, dict):
                records.append(self._normalize(pipeline))
        return records

    def fetch_jobs(self, pipeline_id: Any) -> list[dict[str, Any]]:
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
