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

import ipaddress
import json as _json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

# A transport is any callable matching ``http_get(url, headers) -> (status, json)``.
Transport = Callable[[str, dict[str, str]], tuple[int, Any]]

_DEFAULT_BASE_URL = "https://api.github.com"
_DEFAULT_TIMEOUT = 15.0
_KIND = "pipeline"

# Hostnames that must never be reached, matched literally (lowercased, no DNS).
_BLOCKED_HOST_NAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "metadata",
        "metadata.google.internal",
        "metadata.goog",
    }
)


def _is_blocked_host(host: str) -> bool:
    """Return True if *host* is a loopback / private / link-local / metadata target.

    Purely literal: if the host parses as an IP we classify it; otherwise we
    only reject known-bad literal names. No name resolution is performed.
    """
    h = host.strip().lower().rstrip(".")
    if not h:
        return True
    if h in _BLOCKED_HOST_NAMES:
        return True
    # Strip IPv6 brackets if present.
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False  # a non-IP, non-blocklisted hostname is allowed
    return bool(
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _validate_base_url(base_url: str) -> str:
    """Validate and normalize *base_url*, refusing internal/metadata targets."""
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"base_url must be http(s): {base_url!r}")
    host = parsed.hostname or ""
    if not host:
        raise ValueError(f"base_url has no host: {base_url!r}")
    if _is_blocked_host(host):
        raise ValueError(f"base_url host is blocked (loopback/private/metadata): {host!r}")
    return base_url.rstrip("/")


def _default_http_get(url: str, headers: dict[str, str]) -> tuple[int, Any]:
    """Real, time-bounded urllib transport. Never uses a shell."""
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=_DEFAULT_TIMEOUT) as resp:
            raw = resp.read()
            status = int(resp.getcode() or 0)
    except urllib.error.HTTPError as exc:  # non-2xx still carries a status + body
        raw = exc.read()
        status = int(exc.code)
    body: Any = None
    if raw:
        try:
            body = _json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            body = None
    return status, body


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

    def __init__(self, config: dict[str, Any], *, http_get: Transport | None = None) -> None:
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

    def _normalize(self, run: dict[str, Any]) -> dict[str, Any]:
        name = run.get("name") or ""
        branch = run.get("head_branch") or ""
        status_value = run.get("conclusion") or run.get("status") or ""
        return {
            "ts": _parse_ts(run.get("updated_at")),
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

    def health(self) -> dict[str, Any]:
        """Probe the runs endpoint. Never raises."""
        try:
            status, _ = self._http_get(self._runs_url(), self._headers())
        except Exception as exc:  # health must never propagate
            return {"ok": False, "detail": f"transport error: {exc}"}
        if 200 <= status < 300:
            return {"ok": True, "detail": f"HTTP {status}"}
        return {"ok": False, "detail": f"HTTP {status}"}

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
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
        records: list[dict[str, Any]] = []
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

    def fetch_failed_logs(self, run_id: int | str) -> list[dict[str, Any]]:
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
