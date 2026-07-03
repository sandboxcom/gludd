"""Travis CI pipeline connector.

Self-contained source for normalizing Travis CI *builds* (API v3) into the
gludd pipeline-event shape. No base class / sibling imports.

Contract (shared by every pipeline connector):
  * ``KIND = "pipeline"``
  * ``name`` attribute
  * ``__init__(config)`` — config-driven; the API token is read from the env var
    named by ``config["token_env"]`` and is NEVER hardcoded
  * literal-host SSRF guard on ``base_url`` (no DNS lookup)
  * ``health() -> {"ok", "detail"}`` — never raises
  * ``query(spec) -> list[dict]`` — normalized ``ts/source/kind/level_or_status/
    message/value/labels/raw`` events
  * injectable HTTP transport (stdlib ``urllib`` default), time-bound, no shell.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlsplit

from general_ludd.connectors._errors import ConnectorConfigError
from general_ludd.security.ssrf import is_url_blocked

Transport = Callable[[str, str, Mapping[str, str], float], "tuple[int, bytes]"]

DEFAULT_BASE_URL = "https://api.travis-ci.com"
TRAVIS_API_VERSION = "3"



def _guard_base_url(base_url: str) -> str:
    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https"):
        raise ConnectorConfigError(f"base_url must be http(s): {base_url!r}")
    if not parts.hostname:
        raise ConnectorConfigError(f"base_url has no host: {base_url!r}")
    # Delegate the private/loopback/metadata literal decision to the canonical
    # shared guard so this connector cannot drift weaker (no DNS resolution).
    if is_url_blocked(base_url, scheme_allowlist=("http", "https")):
        raise ConnectorConfigError(f"base_url host is a private/loopback literal (SSRF blocked): {parts.hostname}")
    return base_url.rstrip("/")


def _urllib_transport(method: str, url: str, headers: Mapping[str, str], timeout: float) -> tuple[int, bytes]:
    # Scheme is validated (http/https only) by _guard_base_url before any request.
    req = urllib.request.Request(url, method=method, headers=dict(headers))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(resp.status), resp.read()
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read()


class TravisSource:
    """Normalizes Travis CI builds (API v3) into gludd pipeline events."""

    KIND = "pipeline"

    def __init__(self, config: Mapping[str, Any], transport: Transport | None = None) -> None:
        self._config = dict(config)
        self.name = str(self._config.get("name", "travis"))
        self.base_url = _guard_base_url(str(self._config.get("base_url", DEFAULT_BASE_URL)))
        self.slug = str(self._config["slug"])
        self.timeout = float(self._config.get("timeout", 10.0))
        self._token_env = str(self._config.get("token_env", "TRAVIS_TOKEN"))
        self._token = os.environ.get(self._token_env, "")
        self._transport: Transport = transport or _urllib_transport

    # -- internals ---------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Travis-API-Version": TRAVIS_API_VERSION,
        }
        if self._token:
            headers["Authorization"] = f"token {self._token}"
        return headers

    def _request(self, method: str, url: str) -> tuple[int, bytes]:
        return self._transport(method, url, self._headers(), self.timeout)

    def _builds_url(self) -> str:
        # Travis wants the repo slug URL-encoded ("owner/repo" -> "owner%2Frepo").
        encoded = self.slug.replace("/", "%2F")
        return f"{self.base_url}/repo/{encoded}/builds"

    @staticmethod
    def _normalize_build(build: Mapping[str, Any]) -> dict[str, Any]:
        ts = build.get("finished_at")
        branch_obj = build.get("branch")
        branch = ""
        if isinstance(branch_obj, Mapping):
            branch = str(branch_obj.get("name") or "")
        elif isinstance(branch_obj, str):
            branch = branch_obj
        commit_obj = build.get("commit")
        commit = ""
        if isinstance(commit_obj, Mapping):
            commit = str(commit_obj.get("sha") or "")
        message = f"{branch}@{commit[:12]}" if commit else branch
        return {
            "ts": ts,
            "source": "travis",
            "kind": "pipeline",
            "level_or_status": build.get("state"),
            "message": message,
            "value": build.get("number"),
            "labels": {
                "id": build.get("id"),
                "number": build.get("number"),
            },
            "raw": dict(build),
        }

    # -- public API --------------------------------------------------------
    def health(self) -> dict[str, Any]:
        try:
            status, _ = self._request("GET", self._builds_url())
        except Exception as exc:  # health must never raise
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
        if 200 <= status < 300:
            return {"ok": True, "detail": f"HTTP {status}"}
        return {"ok": False, "detail": f"HTTP {status}"}

    def query(self, spec: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        status, body = self._request("GET", self._builds_url())
        if not (200 <= status < 300):
            raise ConnectorConfigError(f"travis builds query failed: HTTP {status}")
        payload = json.loads(body.decode("utf-8") or "{}")
        if not isinstance(payload, Mapping):
            raise ConnectorConfigError("travis builds response was not a JSON object")
        builds = payload.get("builds", [])
        if not isinstance(builds, list):
            raise ConnectorConfigError("travis 'builds' field was not a JSON array")
        return [self._normalize_build(b) for b in builds if isinstance(b, Mapping)]

    def fetch_log(self, job_id: str) -> str:
        """Fetch the raw log content for a single Travis job."""
        url = f"{self.base_url}/job/{job_id}/log"
        status, body = self._request("GET", url)
        if not (200 <= status < 300):
            raise ConnectorConfigError(f"travis log fetch failed: HTTP {status}")
        decoded = body.decode("utf-8")
        try:
            doc = json.loads(decoded)
        except json.JSONDecodeError:
            return decoded
        if isinstance(doc, Mapping) and "content" in doc:
            return str(doc["content"])
        return decoded
