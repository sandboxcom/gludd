"""Azure DevOps pipeline (build) observability source (self-contained).

This module is intentionally standalone: it does not import the connectors
base/__init__ or any sibling connector. SSRF guarding, the transport contract
and record normalization are duplicated here so the file can be dropped in and
tested in isolation.

HTTP is performed through an INJECTABLE transport with the contract
``http_get(url, headers) -> (status, json)``. The default implementation is a
small, time-bounded ``urllib`` wrapper; tests pass a mock so no real network is
touched. The transport never uses a shell.

Auth is HTTP Basic with an empty username and a Personal Access Token (PAT) as
the password, i.e. ``base64(":" + PAT)``. The PAT is read from an environment
variable at call time and is NEVER hardcoded.
"""

from __future__ import annotations

import base64
import ipaddress
import json as _json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import datetime
from typing import Any

__all__ = ["AzureDevOpsSource"]

_KIND = "pipeline"
_DEFAULT_BASE_URL = "https://dev.azure.com"
_DEFAULT_TIMEOUT = 10.0
_API_VERSION = "7.0"

# A transport is any callable matching ``http_get(url, headers) -> (status, json)``.
Transport = Callable[[str, dict[str, str]], "tuple[int, Any]"]

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
    """Return True if *host* is a loopback/private/link-local/metadata target.

    Purely literal: if the host parses as an IP we classify it; otherwise we
    only reject known-bad literal names. No name resolution is performed.
    """
    h = host.strip().lower().rstrip(".")
    if not h:
        return True
    if h in _BLOCKED_HOST_NAMES:
        return True
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False
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
        raise ValueError(
            f"base_url host is blocked (loopback/private/metadata): {host!r}"
        )
    return base_url.rstrip("/")


def _parse_ts(value: Any) -> float | None:
    """Parse an ISO-8601 timestamp into a POSIX float, or None."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    # Azure timestamps can carry sub-second precision longer than 6 digits.
    if "." in text:
        head, _, tail = text.partition(".")
        frac = tail
        tz = ""
        for marker in ("+", "-"):
            idx = tail.find(marker)
            if idx != -1:
                frac, tz = tail[:idx], tail[idx:]
                break
        if tz == "" and tail.endswith(":00"):  # already-normalized offset edge
            frac = tail
        digits = "".join(c for c in frac if c.isdigit())[:6]
        text = f"{head}.{digits}{tz}" if digits else f"{head}{tz}"
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


class AzureDevOpsSource:
    """Observability source for Azure DevOps build pipelines (duck-typed)."""

    KIND: str = _KIND

    def __init__(
        self,
        config: dict[str, Any],
        *,
        http_get: Transport | None = None,
    ) -> None:
        config = config or {}
        org = config.get("org") or config.get("organization")
        project = config.get("project")
        if not org:
            raise ValueError("config['org'] is required")
        if not project:
            raise ValueError("config['project'] is required")
        self.org: str = str(org)
        self.project: str = str(project)
        self.base_url: str = _validate_base_url(
            str(config.get("base_url") or _DEFAULT_BASE_URL)
        )
        # PAT is read from this env var at call time and used as Basic-auth pwd.
        self.token_env: str = str(config.get("token_env") or "AZURE_DEVOPS_PAT")
        self.name: str = config.get("name") or f"azure-devops:{self.org}/{self.project}"
        self._http_get: Transport = http_get or _default_http_get

    # -- internal helpers ---------------------------------------------------
    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": "general-ludd-connector",
        }
        pat = os.environ.get(self.token_env)
        if pat:
            token = base64.b64encode(f":{pat}".encode()).decode("ascii")
            headers["Authorization"] = f"Basic {token}"
        return headers

    def _builds_url(self, spec: dict[str, Any]) -> str:
        params: dict[str, str] = {"api-version": _API_VERSION}
        status_filter = spec.get("statusFilter") or spec.get("status")
        if status_filter:
            params["statusFilter"] = str(status_filter)
        result_filter = spec.get("resultFilter") or spec.get("result")
        if result_filter:
            params["resultFilter"] = str(result_filter)
        top = spec.get("top") or spec.get("limit")
        if isinstance(top, int) and top > 0:
            params["$top"] = str(top)
        base = (
            f"{self.base_url}/"
            f"{urllib.parse.quote(self.org, safe='')}/"
            f"{urllib.parse.quote(self.project, safe='')}/_apis/build/builds"
        )
        return f"{base}?{urllib.parse.urlencode(params)}"

    def _normalize(self, build: dict[str, Any]) -> dict[str, Any]:
        definition = build.get("definition")
        def_name = ""
        if isinstance(definition, dict):
            def_name = str(definition.get("name") or "")
        build_number = build.get("buildNumber") or ""
        result = build.get("result")
        status = build.get("status")
        level_or_status = str(result or status or "")
        return {
            "ts": _parse_ts(build.get("finishTime"))
            or _parse_ts(build.get("queueTime")),
            "source": self.name,
            "kind": _KIND,
            "level_or_status": level_or_status,
            "message": f"{def_name} #{build_number}".strip(),
            "value": None,
            "labels": {
                "id": build.get("id"),
                "sourceBranch": build.get("sourceBranch"),
                "sourceVersion": build.get("sourceVersion"),
            },
            "raw": build,
        }

    # -- public API ---------------------------------------------------------
    def health(self) -> dict[str, Any]:
        """Probe the builds endpoint. Never raises."""
        try:
            status, _ = self._http_get(self._builds_url({}), self._headers())
        except Exception as exc:  # health must never propagate
            return {"ok": False, "detail": f"transport error: {exc}"}
        if 200 <= status < 300:
            return {"ok": True, "detail": f"HTTP {status}"}
        return {"ok": False, "detail": f"HTTP {status}"}

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        """List recent builds and return normalized records.

        Supported *spec* keys: ``statusFilter``/``status``,
        ``resultFilter``/``result``, ``top``/``limit`` (int).
        """
        spec = spec or {}
        try:
            status, body = self._http_get(self._builds_url(spec), self._headers())
        except Exception:
            return []
        if not (200 <= status < 300) or not isinstance(body, dict):
            return []
        builds = body.get("value")
        if not isinstance(builds, list):
            return []
        records: list[dict[str, Any]] = []
        for build in builds:
            if isinstance(build, dict):
                records.append(self._normalize(build))
        return records

    def fetch_logs(self, build_id: Any) -> list[dict[str, Any]]:
        """GET builds/{id}/logs -> raw list of log dicts. Never raises."""
        url = (
            f"{self.base_url}/"
            f"{urllib.parse.quote(self.org, safe='')}/"
            f"{urllib.parse.quote(self.project, safe='')}/_apis/build/builds/"
            f"{urllib.parse.quote(str(build_id), safe='')}/logs"
            f"?api-version={_API_VERSION}"
        )
        try:
            status, body = self._http_get(url, self._headers())
        except Exception:
            return []
        if not (200 <= status < 300) or not isinstance(body, dict):
            return []
        logs = body.get("value")
        if not isinstance(logs, list):
            return []
        return [log for log in logs if isinstance(log, dict)]
