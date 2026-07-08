"""GitHub Actions observability connector.

A self-contained, duck-typed observability *source*. It deliberately does NOT
import a shared base class — the interface (``KIND``, ``name``, ``health()``,
``query()``) is structural so the connector can be dropped in anywhere a
pipeline source is expected without a coupling to connector scaffolding.

HTTP is performed through an INJECTABLE transport with the contract
``http_get(url, headers) -> (status, json)``. The default implementation is a
small, time-bounded ``urllib`` wrapper; tests pass a mock so no real network is
touched.

Security:
- The auth token is read from ``os.environ[token_env]`` at call time — never
  hardcoded and never logged.
- ``base_url`` is validated against a small LITERAL-host blocklist (loopback,
  RFC-1918, link-local, the cloud-metadata address/hostnames). No DNS is
  performed — the check is purely on the literal host in the URL, so it cannot
  be used to amplify into a resolver-based SSRF.
- Requests are time-bounded; ``shell=True`` is never used.
"""

from __future__ import annotations

import json as _json
import os
from collections.abc import Callable
from datetime import UTC, datetime

import httpx

from general_ludd.security.ssrf import is_url_blocked

# A transport is any callable matching ``http_get(url, headers) -> (status, json)``.
Transport = Callable[[str, dict[str, str]], tuple[int, object]]

_DEFAULT_BASE_URL = "https://api.github.com"
_DEFAULT_TIMEOUT = 15.0
_KIND = "pipeline"


def _validate_base_url(base_url: str) -> str:
    """Validate and normalize *base_url*, refusing internal/metadata targets."""
    if is_url_blocked(base_url, scheme_allowlist=("http", "https")):
        raise ValueError(f"base_url host is blocked (loopback/private/metadata): {base_url!r}")
    return base_url.rstrip("/")


def _default_http_get(url: str, headers: dict[str, str]) -> tuple[int, object]:
    """Real, time-bounded httpx transport. Never uses a shell.

    Redirects are NOT followed so a redirect-to-metadata SSRF cannot be
    silently chased; the caller's ``is_url_blocked`` guard already validated
    the literal host.
    """
    with httpx.Client(timeout=_DEFAULT_TIMEOUT, follow_redirects=False) as client:
        resp = client.get(url, headers=headers)
    body: object = None
    if resp.content:
        try:
            body = _json.loads(resp.content.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            body = None
    return resp.status_code, body


def _parse_ts(updated_at: str | None) -> float | None:
    """Parse an ISO-8601 'Z' timestamp into a UTC epoch float, or None."""
    if not updated_at:
        return None
    value = updated_at
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.timestamp()


class GitHubActionsSource:
    """Observability source for GitHub Actions workflow runs (duck-typed)."""

    KIND: str = _KIND

    def __init__(self, config: dict[str, object], *, http_get: Transport | None = None) -> None:
        repo = config.get("repo")
        if not repo or "/" not in str(repo):
            raise ValueError("config['repo'] must be 'owner/name'")
        self.repo: str = str(repo)
        self.base_url: str = _validate_base_url(str(config.get("base_url") or _DEFAULT_BASE_URL))
        self.token_env: str = str(config.get("token_env") or "GITHUB_TOKEN")
        self.name: str = f"github-actions:{self.repo}"
        self._http_get: Transport = http_get or _default_http_get

    # -- internal helpers -------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "general-ludd-connector",
        }
        token = os.environ.get(self.token_env)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _runs_url(self) -> str:
        return f"{self.base_url}/repos/{self.repo}/actions/runs"

    def _normalize(self, run: dict[str, object]) -> dict[str, object]:
        name = run.get("name") or ""
        branch = run.get("head_branch") or ""
        status_value = run.get("conclusion") or run.get("status") or ""
        return {
            "ts": _parse_ts(str(run.get("updated_at"))) if run.get("updated_at") is not None else None,
            "source": self.name,
            "kind": _KIND,
            "level_or_status": str(status_value),
            "message": f"{name} @ {branch}".strip(),
            "value": None,
            "labels": {
                "run_id": run.get("id"),
                "head_sha": run.get("head_sha"),
                "event": run.get("event"),
                "workflow": run.get("path"),
            },
            "raw": run,
        }

    # -- public, duck-typed interface ------------------------------------

    def health(self) -> dict[str, object]:
        """Probe the runs endpoint. Never raises."""
        try:
            status, _ = self._http_get(self._runs_url(), self._headers())
        except Exception as exc:  # health must never propagate
            return {"ok": False, "detail": f"transport error: {exc}"}
        if 200 <= status < 300:
            return {"ok": True, "detail": f"HTTP {status}"}
        return {"ok": False, "detail": f"HTTP {status}"}

    def query(self, spec: dict[str, object]) -> list[dict[str, object]]:
        """List recent workflow runs and return normalized records.

        Supported *spec* filters: ``limit`` (int), ``branch`` (str), ``status``
        (matched against a run's conclusion or status).
        """
        spec = spec or {}
        status, body = self._http_get(self._runs_url(), self._headers())
        if not (200 <= status < 300) or not isinstance(body, dict):
            return []
        runs = body.get("workflow_runs") or []

        branch = spec.get("branch")
        want_status = spec.get("status")
        records: list[dict[str, object]] = []
        for run in runs:
            if not isinstance(run, dict):
                continue
            if branch is not None and run.get("head_branch") != branch:
                continue
            if want_status is not None:
                run_status = run.get("conclusion") or run.get("status")
                if run_status != want_status:
                    continue
            records.append(self._normalize(run))

        limit = spec.get("limit")
        if isinstance(limit, int) and limit >= 0:
            records = records[:limit]
        return records

    def fetch_failed_logs(self, run_id: int | str) -> list[dict[str, object]]:
        """Return the jobs list for a run (stub for failure-log drill-down)."""
        url = f"{self.base_url}/repos/{self.repo}/actions/runs/{run_id}/jobs"
        try:
            status, body = self._http_get(url, self._headers())
        except Exception:  # drill-down is best-effort
            return []
        if not (200 <= status < 300) or not isinstance(body, dict):
            return []
        jobs = body.get("jobs") or []
        return [j for j in jobs if isinstance(j, dict)]
