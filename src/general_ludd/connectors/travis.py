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
  * injectable HTTP transport (``httpx`` default), time-bound, no shell.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Mapping
from typing import TypedDict, cast
from urllib.parse import quote, urlsplit

import httpx

from general_ludd.connectors._errors import ConnectorConfigError
from general_ludd.security.ssrf import is_url_blocked

logger = logging.getLogger(__name__)

Transport = Callable[[str, str, Mapping[str, str], float], "tuple[int, bytes]"]

DEFAULT_BASE_URL = "https://api.travis-ci.com"
TRAVIS_API_VERSION = "3"


# --------------------------------------------------------------------------- #
# Typed API-response shapes (Travis CI API v3).
# --------------------------------------------------------------------------- #
class TravisBranchRef(TypedDict, total=False):
    """The ``branch`` object embedded in a Travis build (API v3)."""

    name: str


class TravisCommitRef(TypedDict, total=False):
    """The ``commit`` object embedded in a Travis build (API v3)."""

    sha: str


class TravisBuild(TypedDict, total=False):
    """One element of the Travis ``builds[]`` array.

    ``branch`` may be a string (older payloads) or a ``TravisBranchRef``
    object (API v3) — both shapes are tolerated by :meth:`TravisSource._normalize_build`.
    """

    id: str
    number: int
    state: str
    finished_at: str
    branch: TravisBranchRef | str
    commit: TravisCommitRef


class TravisBuildsResponse(TypedDict, total=False):
    """Top-level response of ``GET /repo/{slug}/builds``."""

    builds: list[TravisBuild]


class TravisLogResponse(TypedDict, total=False):
    """Response shape of ``GET /job/{job_id}/log`` — ``{"content": "..."}``."""

    content: str


class TravisQuerySpec(TypedDict, total=False):
    """Caller-supplied query selection accepted by :meth:`TravisSource.query`.

    Currently unused (Travis's builds endpoint takes no filter params in this
    connector), but kept for contract symmetry with sibling pipeline connectors.
    """


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


def _httpx_transport(method: str, url: str, headers: Mapping[str, str], timeout: float) -> tuple[int, bytes]:
    # Scheme is validated (http/https only) by _guard_base_url before any request.
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        resp = client.request(method, url, headers=dict(headers))
    return resp.status_code, resp.content


class TravisSource:
    """Normalizes Travis CI builds (API v3) into gludd pipeline events."""

    KIND = "pipeline"

    def __init__(
        self,
        config: Mapping[str, object],
        transport: Transport | None = None,
        *,
        http_get: Callable[..., tuple[int, object]] | None = None,
    ) -> None:
        """Build the source from connector config; ``transport`` and ``http_get`` are exclusive."""
        if transport is not None and http_get is not None:
            raise ValueError("transport and http_get are mutually exclusive")
        self._config = dict(config)
        self.name = str(self._config.get("name", "travis"))
        self.base_url = _guard_base_url(str(self._config.get("base_url", DEFAULT_BASE_URL)))
        self.slug = str(self._config.get("slug") or self._config.get("repository", ""))
        self.timeout = float(cast(float | int | str | bool, self._config.get("timeout", 10.0)))
        self._token_env = str(self._config.get("token_env", "TRAVIS_TOKEN"))
        self._token = os.environ.get(self._token_env, "")
        transport_impl: Transport
        if http_get is not None:

            def _legacy(_method: str, url: str, headers: Mapping[str, str], _timeout: float) -> tuple[int, bytes]:
                status, body = http_get(url, dict(headers))
                if isinstance(body, bytes):
                    return status, body
                if isinstance(body, str):
                    return status, body.encode()
                return status, json.dumps(body).encode()

            transport_impl = _legacy
        else:
            transport_impl = transport or _httpx_transport
        self._transport = transport_impl

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
        encoded = quote(self.slug, safe="")
        return f"{self.base_url}/repo/{encoded}/builds"

    @staticmethod
    def _normalize_build(build: Mapping[str, object]) -> dict[str, object]:
        ts = build.get("finished_at")
        branch_obj = build.get("branch")
        branch = ""
        if isinstance(branch_obj, Mapping):
            name_obj = branch_obj.get("name") or ""
            branch = name_obj if isinstance(name_obj, str) else str(name_obj)
        elif isinstance(branch_obj, str):
            branch = branch_obj
        commit_obj = build.get("commit")
        commit = ""
        if isinstance(commit_obj, Mapping):
            sha_obj = commit_obj.get("sha") or ""
            commit = sha_obj if isinstance(sha_obj, str) else str(sha_obj)
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
    def health(self) -> dict[str, object]:
        """Probe the source. Never raises."""
        try:
            status, _ = self._request("GET", self._builds_url())
        except Exception:  # health must never raise
            logger.warning("health check failed", exc_info=True)
            return {"ok": False, "detail": "health check failed"}
        if 200 <= status < 300:
            return {"ok": True, "detail": f"HTTP {status}"}
        return {"ok": False, "detail": f"HTTP {status}"}

    def query(self, spec: TravisQuerySpec | None = None) -> list[dict[str, object]]:
        """Return normalized build records from the Travis builds API."""
        status, body = self._request("GET", self._builds_url())
        if not (200 <= status < 300):
            raise ConnectorConfigError(f"travis builds query failed: HTTP {status}")
        payload = json.loads(body.decode("utf-8") or "{}")
        if not isinstance(payload, Mapping):
            raise ConnectorConfigError("travis builds response was not a JSON object")
        builds_obj = payload.get("builds", [])
        builds: list[object] = builds_obj if isinstance(builds_obj, list) else []
        return [self._normalize_build(cast("Mapping[str, object]", b)) for b in builds if isinstance(b, Mapping)]

    def fetch_log(self, job_id: str) -> str:
        """Fetch the raw log content for a single Travis job."""
        url = f"{self.base_url}/job/{quote(str(job_id), safe='')}/log"
        status, body = self._request("GET", url)
        if not (200 <= status < 300):
            raise ConnectorConfigError(f"travis log fetch failed: HTTP {status}")
        decoded = body.decode("utf-8")
        try:
            doc = json.loads(decoded)
        except json.JSONDecodeError:
            return decoded
        if isinstance(doc, Mapping) and "content" in doc:
            content_obj = doc["content"]
            return content_obj if isinstance(content_obj, str) else str(content_obj)
        return decoded
