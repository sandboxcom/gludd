"""Self-contained Jenkins observability connector.

Fetches recent builds from the Jenkins JSON API and normalizes each build into
a flat observability record. The HTTP transport is *injectable* (the ``http_get``
callable) so the connector can be unit-tested with a mocked transport and never
touches the network in tests.

Security properties:
  * Literal-host SSRF block on ``base_url`` — loopback / private / link-local /
    cloud-metadata hosts are rejected at construction time. The check is purely
    literal (parses the host as an IP / known name); it performs NO DNS
    resolution, so it cannot be used as a DNS side-channel and cannot hang.
  * Basic-auth credentials are read from ``os.environ`` (never passed inline) via
    the ``user_env`` / ``token_env`` config keys.
  * Time-bound: the default transport uses an explicit total timeout.
  * No ``shell=True`` / no subprocess anywhere.

This module is intentionally standalone: it imports nothing from the rest of the
``general_ludd`` package (no base class, no package ``__init__`` side effects, no
sibling connectors).
"""

from __future__ import annotations

import base64
import json
import logging
import os
from typing import Protocol, TypedDict
from urllib.parse import quote, urlencode, urlparse

import httpx

from general_ludd.security.ssrf import is_url_blocked

logger = logging.getLogger(__name__)

# (status_code, parsed_json_body) — the transport contract.
HttpResponse = tuple[int, object]

# Default per-request timeout (seconds) for the real transport.
_DEFAULT_TIMEOUT_S = 10.0

# Sentinel for builds with no result yet (in-progress / aborted-null).
_UNKNOWN_STATUS = "UNKNOWN"

_COLOR_RESULTS = {
    "blue": "SUCCESS",
    "red": "FAILURE",
    "yellow": "UNSTABLE",
    "aborted": "ABORTED",
    "disabled": "DISABLED",
    "notbuilt": "NOT_BUILT",
}


class HttpGet(Protocol):
    """Injectable transport: maps a URL + headers to an (status, json) pair."""

    def __call__(self, url: str, headers: dict[str, str]) -> HttpResponse:
        """Fetch ``url`` with ``headers`` and return ``(status_code, parsed_json)``."""
        ...


class Record(TypedDict):
    """Normalized observability record emitted by :meth:`JenkinsSource.query`."""

    ts: float
    source: str
    kind: str
    level_or_status: str
    message: str
    value: None
    labels: dict[str, object]
    raw: dict[str, object]


def _default_http_get(url: str, headers: dict[str, str]) -> HttpResponse:
    """Real, time-bound transport used when none is injected.

    Only ``http``/``https`` are allowed; the request is bounded by an explicit
    timeout and never follows redirects. The mocked tests never reach this path.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"unsupported url scheme: {parsed.scheme!r}")

    resp = httpx.get(url, headers=headers, timeout=_DEFAULT_TIMEOUT_S, follow_redirects=False)
    parsed_body: object = json.loads(resp.content) if resp.content else {}
    return resp.status_code, parsed_body


class JenkinsSource:
    """Observability source backed by the Jenkins JSON build API."""

    KIND = "pipeline"

    def __init__(self, config: dict[str, object], *, http_get: HttpGet | None = None) -> None:
        """Build the source from connector config; requires a non-empty ``base_url``."""
        base_url = str(config.get("base_url", "")).rstrip("/")
        if not base_url:
            raise ValueError("base_url is required")

        parsed = urlparse(base_url)
        if is_url_blocked(base_url, scheme_allowlist=("http", "https")):
            raise ValueError(
                "base_url blocked by SSRF policy (bad scheme, missing host, or internal/loopback/private):"
                f" {base_url!r}"
            )

        self.base_url = base_url
        self._parsed = parsed
        job = config.get("job")
        self.job: str | None = str(job) if job else None
        self._user_env = str(config.get("user_env", "JENKINS_USER"))
        self._token_env = str(config.get("token_env", "JENKINS_TOKEN"))
        self._http_get: HttpGet = http_get or _default_http_get

        label = self.job or "all"
        self.name = f"jenkins:{parsed.hostname or 'unknown'}:{label}"

    # -- internal helpers --------------------------------------------------

    def _auth_headers(self) -> dict[str, str]:
        user = os.environ.get(self._user_env, "")
        token = os.environ.get(self._token_env, "")
        headers: dict[str, str] = {"Accept": "application/json"}
        if user or token:
            raw = f"{user}:{token}".encode()
            headers["Authorization"] = f"Basic {base64.b64encode(raw).decode('ascii')}"
        return headers

    def _builds_url(self) -> str:
        if self.job:
            tree = "builds[number,result,timestamp,url,duration]"
            params = urlencode({"tree": tree})
            return f"{self.base_url}/job/{quote(self.job, safe='')}/api/json?{params}"
        tree = "jobs[name,url,color,lastBuild[number,result,timestamp,url,duration]]"
        params = urlencode({"tree": tree})
        return f"{self.base_url}/api/json?{params}"

    def _normalize(self, build: dict[str, object]) -> Record:
        ts_ms = build.get("timestamp")
        ts = float(ts_ms) / 1000.0 if isinstance(ts_ms, (int, float)) else 0.0

        result = build.get("result")
        status = str(result) if result else _UNKNOWN_STATUS

        number = build.get("number")
        root_job_name = build.get("_job_name")
        job_label = str(root_job_name) if root_job_name else (self.job or "job")
        message = f"{job_label}#{number}" if number is not None else job_label

        labels: dict[str, object] = {
            "number": number,
            "url": build.get("url"),
            "duration_ms": build.get("duration"),
        }
        raw_payload = build.get("_raw")
        raw = dict(raw_payload) if isinstance(raw_payload, dict) else dict(build)
        return Record(
            ts=ts,
            source=self.name,
            kind=self.KIND,
            level_or_status=status,
            message=message,
            value=None,
            labels=labels,
            raw=raw,
        )

    @staticmethod
    def _extract_builds(payload: object) -> list[dict[str, object]]:
        builds: list[dict[str, object]] = []
        if isinstance(payload, dict):
            raw_builds = payload.get("builds")
            if isinstance(raw_builds, list):
                for item in raw_builds:
                    if isinstance(item, dict):
                        builds.append(item)
            raw_jobs = payload.get("jobs")
            if isinstance(raw_jobs, list):
                for job in raw_jobs:
                    if not isinstance(job, dict):
                        continue
                    last_build = job.get("lastBuild")
                    if not isinstance(last_build, dict):
                        continue
                    build = dict(last_build)
                    build["_job_name"] = job.get("name")
                    build["_raw"] = dict(job)
                    if not build.get("url"):
                        job_url = job.get("url")
                        number = build.get("number")
                        if isinstance(job_url, str) and number is not None:
                            build["url"] = f"{job_url.rstrip('/')}/{number}/"
                    if not build.get("result"):
                        color = job.get("color")
                        if isinstance(color, str):
                            build["result"] = JenkinsSource._result_from_color(color)
                    builds.append(build)
        return builds

    @staticmethod
    def _result_from_color(color: str) -> str:
        normalized = color.lower()
        if normalized.endswith("_anime"):
            return "RUNNING"
        return _COLOR_RESULTS.get(normalized, _UNKNOWN_STATUS)

    # -- public API --------------------------------------------------------

    def health(self) -> dict[str, object]:
        """Probe the API. Never raises — returns an ``ok``/``status`` dict."""
        try:
            status, _ = self._http_get(self._builds_url(), headers=self._auth_headers())
        except Exception:  # health must never propagate
            logger.warning("health check failed", exc_info=True)
            return {"ok": False, "status": None, "source": self.name, "error": "health check failed"}
        return {"ok": 200 <= int(status) < 300, "status": int(status), "source": self.name}

    def query(self, spec: dict[str, object] | None = None) -> list[Record]:
        """Fetch recent builds and return normalized records.

        ``spec`` filters: ``limit`` (int, truncate) and ``result`` (status string
        to keep). A non-2xx response yields an empty list rather than raising.
        """
        spec = spec or {}
        status, payload = self._http_get(self._builds_url(), headers=self._auth_headers())
        if not (200 <= int(status) < 300):
            return []

        records = [self._normalize(b) for b in self._extract_builds(payload)]

        result_filter = spec.get("result")
        if isinstance(result_filter, str) and result_filter:
            records = [r for r in records if r["level_or_status"] == result_filter]

        limit = spec.get("limit")
        if isinstance(limit, int) and limit >= 0:
            records = records[:limit]

        return records
