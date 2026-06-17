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
  * injectable HTTP transport (defaults to a stdlib ``urllib`` transport) so
    tests run fully mocked, with a per-request timeout and no ``shell=True``.
"""

from __future__ import annotations

import ipaddress
import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlsplit

# A transport is a callable: (method, url, headers, timeout) -> (status, body).
Transport = Callable[[str, str, Mapping[str, str], float], "tuple[int, bytes]"]

DEFAULT_BASE_URL = "https://api.buildkite.com"


class ConnectorError(RuntimeError):
    """Raised for configuration / transport problems specific to this connector."""


def _host_is_private_literal(host: str) -> bool:
    """True if ``host`` is an IP *literal* in a non-public range.

    Pure literal inspection — performs NO DNS resolution (a hostname that is not
    already an IP literal is treated as public here; resolution-time SSRF is a
    separate concern intentionally left to the transport layer).
    """
    candidate = host
    if candidate.startswith("[") and candidate.endswith("]"):
        candidate = candidate[1:-1]
    try:
        ip = ipaddress.ip_address(candidate)
    except ValueError:
        return False
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _guard_base_url(base_url: str) -> str:
    """Validate + normalize ``base_url``; raise on an SSRF-risky literal host."""
    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https"):
        raise ConnectorError(f"base_url must be http(s): {base_url!r}")
    if not parts.hostname:
        raise ConnectorError(f"base_url has no host: {base_url!r}")
    if _host_is_private_literal(parts.hostname):
        raise ConnectorError(f"base_url host is a private/loopback literal (SSRF blocked): {parts.hostname}")
    return base_url.rstrip("/")


def _urllib_transport(method: str, url: str, headers: Mapping[str, str], timeout: float) -> tuple[int, bytes]:
    """Default stdlib transport. Time-bound; never uses a shell.

    The URL scheme is validated (http/https only) by ``_guard_base_url`` before
    any request is built, so the urllib call is not an unguarded open.
    """
    req = urllib.request.Request(url, method=method, headers=dict(headers))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(resp.status), resp.read()
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read()


class BuildkiteSource:
    """Normalizes Buildkite pipeline *builds* into gludd pipeline events."""

    KIND = "pipeline"

    def __init__(self, config: Mapping[str, Any], transport: Transport | None = None) -> None:
        self._config = dict(config)
        self.name = str(self._config.get("name", "buildkite"))
        self.base_url = _guard_base_url(str(self._config.get("base_url", DEFAULT_BASE_URL)))
        self.org = str(self._config["org"])
        self.pipeline = str(self._config["pipeline"])
        self.timeout = float(self._config.get("timeout", 10.0))
        self._token_env = str(self._config.get("token_env", "BUILDKITE_TOKEN"))
        self._token = os.environ.get(self._token_env, "")
        self._transport: Transport = transport or _urllib_transport

    # -- internals ---------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _request(self, method: str, url: str) -> tuple[int, bytes]:
        return self._transport(method, url, self._headers(), self.timeout)

    def _builds_url(self) -> str:
        return f"{self.base_url}/v2/organizations/{self.org}/pipelines/{self.pipeline}/builds"

    @staticmethod
    def _normalize_build(build: Mapping[str, Any]) -> dict[str, Any]:
        ts = build.get("finished_at") or build.get("created_at")
        branch = build.get("branch") or ""
        commit = build.get("commit") or ""
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
    def health(self) -> dict[str, Any]:
        """Probe the builds endpoint. Never raises."""
        try:
            status, _ = self._request("GET", self._builds_url())
        except Exception as exc:  # health must never raise
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
        if 200 <= status < 300:
            return {"ok": True, "detail": f"HTTP {status}"}
        return {"ok": False, "detail": f"HTTP {status}"}

    def query(self, spec: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        """Fetch + normalize builds. ``spec`` is accepted for contract symmetry."""
        status, body = self._request("GET", self._builds_url())
        if not (200 <= status < 300):
            raise ConnectorError(f"buildkite builds query failed: HTTP {status}")
        payload = json.loads(body.decode("utf-8") or "[]")
        if not isinstance(payload, list):
            raise ConnectorError("buildkite builds response was not a JSON array")
        return [self._normalize_build(b) for b in payload if isinstance(b, Mapping)]

    def fetch_log(self, job_id: str) -> str:
        """Fetch the raw log content for a single job within this pipeline."""
        url = f"{self.base_url}/v2/organizations/{self.org}/pipelines/{self.pipeline}/builds/jobs/{job_id}/log"
        status, body = self._request("GET", url)
        if not (200 <= status < 300):
            raise ConnectorError(f"buildkite log fetch failed: HTTP {status}")
        decoded = body.decode("utf-8")
        try:
            doc = json.loads(decoded)
        except json.JSONDecodeError:
            return decoded
        if isinstance(doc, Mapping) and "content" in doc:
            return str(doc["content"])
        return decoded
