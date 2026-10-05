"""Buildkite pipeline connector.

Self-contained source for normalizing Buildkite *builds* into the gludd
pipeline-event shape. No base class / sibling imports: everything this needs
lives in this module.

Contract (shared by every pipeline connector):
  * ``KIND = "pipeline"``
  * ``name`` attribute identifying the configured source
  * ``__init__(config)`` — fully config-driven; the API token is read from the
    env var named by ``config["token_env"]`` and is NEVER hardcoded
  * literal-host SSRF guard on ``base_url`` (no DNS lookup; rejects private /
    loopback / link-local / reserved IP literals)
  * ``health() -> {"ok": bool, "detail": str}`` — never raises
  * ``query(spec) -> list[dict]`` — normalized events with keys ``ts``,
    ``source``, ``kind``, ``level_or_status``, ``message``, ``value``,
    ``labels`` and ``raw``
  * injectable HTTP transport (defaults to ``httpx``) so
    tests run fully mocked, with a per-request timeout and no ``shell=True``.
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

# A transport is a callable: (method, url, headers, timeout) -> (status, body).
Transport = Callable[[str, str, Mapping[str, str], float], "tuple[int, bytes]"]

DEFAULT_BASE_URL = "https://api.buildkite.com"


# --------------------------------------------------------------------------- #
# Typed API-response shapes (Buildkite REST API v2).
# --------------------------------------------------------------------------- #
class BuildkiteBuild(TypedDict, total=False):
    """One element of the Buildkite ``builds`` JSON array response."""

    id: str
    number: int
    state: str
    branch: str
    commit: str
    finished_at: str
    created_at: str
    web_url: str


class BuildkiteLogResponse(TypedDict, total=False):
    """Response shape of ``GET .../jobs/{job_id}/log`` — ``{"content": "..."}``."""

    content: str


class BuildkiteQuerySpec(TypedDict, total=False):
    """Caller-supplied query selection accepted by :meth:`BuildkiteSource.query`.

    Currently unused (Buildkite's builds endpoint takes no filter params in
    this connector), but kept for contract symmetry with sibling pipeline
    connectors.
    """


def _guard_base_url(base_url: str) -> str:
    """Validate + normalize ``base_url``; raise on an SSRF-risky literal host."""
    if is_url_blocked(base_url, scheme_allowlist=("http", "https")):
        parts = urlsplit(base_url)
        if parts.scheme not in ("http", "https"):
            raise ConnectorConfigError(f"base_url must be http(s): {base_url!r}")
        if not parts.hostname:
            raise ConnectorConfigError(f"base_url has no host: {base_url!r}")
        raise ConnectorConfigError(f"base_url host is a private/loopback literal (SSRF blocked): {parts.hostname}")
    return base_url.rstrip("/")


def _httpx_transport(method: str, url: str, headers: Mapping[str, str], timeout: float) -> tuple[int, bytes]:
    """Default httpx transport. Time-bound; never uses a shell; no redirect follows.

    The URL scheme is validated (http/https only) by ``_guard_base_url`` before
    any request is built, so the call is not an unguarded open.
    """
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        resp = client.request(method, url, headers=dict(headers))
    return resp.status_code, resp.content


class BuildkiteSource:
    """Normalizes Buildkite pipeline *builds* into gludd pipeline events."""

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
        self.name = str(self._config.get("name", "buildkite"))
        self.base_url = _guard_base_url(str(self._config.get("base_url", DEFAULT_BASE_URL)))
        self.org = str(self._config.get("org", ""))
        self.pipeline = str(self._config.get("pipeline", ""))
        self.timeout = float(cast(float | int | str | bool, self._config.get("timeout", 10.0)))
        self._token_env = str(self._config.get("token_env", "BUILDKITE_TOKEN"))
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
        headers = {"Accept": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _request(self, method: str, url: str) -> tuple[int, bytes]:
        return self._transport(method, url, self._headers(), self.timeout)

    def _builds_url(self) -> str:
        return (
            f"{self.base_url}/v2/organizations/{quote(self.org, safe='')}"
            f"/pipelines/{quote(self.pipeline, safe='')}/builds"
        )

    @staticmethod
    def _normalize_build(build: Mapping[str, object]) -> dict[str, object]:
        ts = build.get("finished_at") or build.get("created_at")
        branch_obj = build.get("branch") or ""
        branch = branch_obj if isinstance(branch_obj, str) else str(branch_obj)
        commit_obj = build.get("commit") or ""
        commit = commit_obj if isinstance(commit_obj, str) else str(commit_obj)
        message = f"{branch}@{commit[:12]}" if commit else branch
        return {
            "ts": ts,
            "source": "buildkite",
            "kind": "pipeline",
            "level_or_status": build.get("state"),
            "message": message,
            "value": build.get("number"),
            "labels": {
                "number": build.get("number"),
                "web_url": build.get("web_url"),
            },
            "raw": dict(build),
        }

    # -- public API --------------------------------------------------------
    def health(self) -> dict[str, object]:
        """Probe the builds endpoint. Never raises."""
        try:
            status, _ = self._request("GET", self._builds_url())
        except Exception:  # health must never raise
            logger.warning("health check failed", exc_info=True)
            return {"ok": False, "detail": "health check failed"}
        if 200 <= status < 300:
            return {"ok": True, "detail": f"HTTP {status}"}
        return {"ok": False, "detail": f"HTTP {status}"}

    def query(self, spec: BuildkiteQuerySpec | None = None) -> list[dict[str, object]]:
        """Fetch + normalize builds. ``spec`` is accepted for contract symmetry."""
        status, body = self._request("GET", self._builds_url())
        if not (200 <= status < 300):
            raise ConnectorConfigError(f"buildkite builds query failed: HTTP {status}")
        payload = json.loads(body.decode("utf-8") or "[]")
        if not isinstance(payload, list):
            raise ConnectorConfigError("buildkite builds response was not a JSON array")
        return [self._normalize_build(cast("Mapping[str, object]", b)) for b in payload if isinstance(b, Mapping)]

    def fetch_log(self, job_id: str) -> str:
        """Fetch the raw log content for a single job within this pipeline."""
        url = (
            f"{self.base_url}/v2/organizations/{quote(self.org, safe='')}"
            f"/pipelines/{quote(self.pipeline, safe='')}"
            f"/builds/jobs/{quote(str(job_id), safe='')}/log"
        )
        status, body = self._request("GET", url)
        if not (200 <= status < 300):
            raise ConnectorConfigError(f"buildkite log fetch failed: HTTP {status}")
        decoded = body.decode("utf-8")
        try:
            doc = json.loads(decoded)
        except json.JSONDecodeError:
            return decoded
        if isinstance(doc, Mapping) and "content" in doc:
            content_obj = doc["content"]
            return content_obj if isinstance(content_obj, str) else str(content_obj)
        return decoded
