"""Standalone GitHub issue-source adapter.

Self-contained: imports no sibling modules and defines no shared base. The
adapter is config-driven, reads its token from the environment, blocks SSRF to
internal hosts on the configured ``base_url`` (literal-host only, no DNS), and
talks to the GitHub REST API through an injectable HTTP transport so tests can
run fully mocked.

Normalized issue dict shape (``fetch_issues``)::

    {
        "external_id": str,   # issue number as string
        "source": "github",
        "title": str,
        "description": str,
        "status": "open" | "closed",
        "assignee": str | None,
        "labels": list[str],
        "priority": str | None,
        "url": str,
        "updated_ts": str | None,
        "raw": dict,          # original API payload
    }
"""

from __future__ import annotations

import ipaddress
import os
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlparse

SYSTEM = "github"

_DEFAULT_BASE_URL = "https://api.github.com"
_DEFAULT_TIMEOUT = 30.0

# Label names that map to a coarse priority signal.
_PRIORITY_LABELS = {
    "p0": "critical",
    "p1": "high",
    "p2": "medium",
    "p3": "low",
    "critical": "critical",
    "urgent": "critical",
    "high": "high",
    "medium": "medium",
    "low": "low",
}


@runtime_checkable
class HTTPResponse(Protocol):
    """Minimal response surface the adapter needs from a transport."""

    status_code: int

    def json(self) -> Any: ...


@runtime_checkable
class HTTPTransport(Protocol):
    """Injectable HTTP transport.

    A callable that performs a single time-bound request and returns an
    :class:`HTTPResponse`. The default production transport wraps ``httpx``.
    """

    def __call__(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        timeout: float,
    ) -> HTTPResponse: ...


def _is_internal_host(host: str) -> bool:
    """Return ``True`` if ``host`` is a literal internal/loopback target.

    Literal-host only — this never performs DNS resolution. A hostname that is
    not an IP literal is treated as external (resolution-time SSRF is out of
    scope for a config-time guard and would require a DNS lookup).
    """

    if not host:
        return True
    bare = host.strip().lower()
    # Strip an IPv6 zone id / brackets if present.
    bare = bare.strip("[]").split("%", 1)[0]
    if bare in {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}:
        return True
    try:
        ip = ipaddress.ip_address(bare)
    except ValueError:
        return False
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _default_transport() -> HTTPTransport:
    import httpx

    def _transport(
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        timeout: float,
    ) -> HTTPResponse:
        resp = httpx.request(
            method,
            url,
            headers=headers,
            params=params,
            json=json,
            timeout=timeout,
        )
        return resp  # httpx.Response satisfies HTTPResponse

    return _transport


class GitHubIssueSource:
    """Config-driven GitHub issue adapter with an injectable transport."""

    name = SYSTEM

    def __init__(
        self,
        config: dict[str, Any],
        *,
        transport: HTTPTransport | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self._config = dict(config)
        self._env = dict(env) if env is not None else dict(os.environ)

        base_url = str(config.get("base_url") or _DEFAULT_BASE_URL).rstrip("/")
        self._base_url = base_url
        self._repo = str(config.get("repo") or "")
        self._token_env = str(config.get("token_env") or "GITHUB_TOKEN")
        self._timeout = float(config.get("timeout") or _DEFAULT_TIMEOUT)

        parsed = urlparse(base_url)
        self._host = parsed.hostname or ""
        self._scheme = parsed.scheme or "https"
        self._base_internal = _is_internal_host(self._host)

        self._transport = transport

    # -- internal helpers ------------------------------------------------

    def _token(self) -> str:
        return self._env.get(self._token_env, "")

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "general-ludd-agent",
        }
        token = self._token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _get_transport(self) -> HTTPTransport:
        if self._transport is None:
            self._transport = _default_transport()
        return self._transport

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> HTTPResponse:
        if self._base_internal:
            raise PermissionError(f"refusing request to internal host: {self._host!r}")
        url = f"{self._base_url}{path}"
        return self._get_transport()(
            method,
            url,
            headers=self._headers(),
            params=params,
            json=json,
            timeout=self._timeout,
        )

    @staticmethod
    def _normalize(issue: dict[str, Any]) -> dict[str, Any]:
        labels_raw = issue.get("labels") or []
        labels = [
            lbl.get("name", "") if isinstance(lbl, dict) else str(lbl)
            for lbl in labels_raw
        ]
        priority: str | None = None
        for ln in labels:
            mapped = _PRIORITY_LABELS.get(ln.lower())
            if mapped is not None:
                priority = mapped
                break

        assignee_obj = issue.get("assignee")
        assignee = (
            assignee_obj.get("login")
            if isinstance(assignee_obj, dict)
            else None
        )

        state = str(issue.get("state") or "open").lower()
        status = "closed" if state == "closed" else "open"

        number = issue.get("number")
        external_id = str(number) if number is not None else str(issue.get("id", ""))

        return {
            "external_id": external_id,
            "source": SYSTEM,
            "title": issue.get("title") or "",
            "description": issue.get("body") or "",
            "status": status,
            "assignee": assignee,
            "labels": labels,
            "priority": priority,
            "url": issue.get("html_url") or issue.get("url") or "",
            "updated_ts": issue.get("updated_at"),
            "raw": issue,
        }

    # -- public contract -------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Return ``{'ok': bool, 'detail': str}``; never raises."""

        if self._base_internal:
            return {"ok": False, "detail": f"internal base_url blocked: {self._host}"}
        if not self._repo:
            return {"ok": False, "detail": "missing 'repo' in config"}
        if not self._token():
            return {"ok": False, "detail": f"missing token in env {self._token_env}"}
        try:
            resp = self._request("GET", f"/repos/{self._repo}")
        except Exception as exc:  # health must never raise
            return {"ok": False, "detail": f"request failed: {exc}"}
        ok = 200 <= resp.status_code < 300
        return {
            "ok": ok,
            "detail": f"GET /repos/{self._repo} -> {resp.status_code}",
        }

    def fetch_issues(self, spec: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Fetch and normalize issues; PRs (items with ``pull_request``) are skipped."""

        spec = spec or {}
        params: dict[str, str] = {}
        state = spec.get("state")
        if state:
            params["state"] = str(state)
        labels = spec.get("labels")
        if labels:
            params["labels"] = (
                ",".join(str(label) for label in labels)
                if isinstance(labels, (list, tuple))
                else str(labels)
            )
        since = spec.get("since")
        if since:
            params["since"] = str(since)

        resp = self._request("GET", f"/repos/{self._repo}/issues", params=params)
        data = resp.json()
        if not isinstance(data, list):
            return []
        normalized: list[dict[str, Any]] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            if "pull_request" in item:  # GitHub returns PRs from the issues endpoint
                continue
            normalized.append(self._normalize(item))
        return normalized

    def update_status(
        self,
        external_id: str,
        status: str,
        comment: str | None = None,
    ) -> dict[str, Any]:
        """Transition an issue to open/closed, with an optional comment."""

        norm = str(status).lower()
        if norm not in {"open", "closed"}:
            raise ValueError(f"status must be 'open' or 'closed', got {status!r}")
        resp = self._request(
            "PATCH",
            f"/repos/{self._repo}/issues/{external_id}",
            json={"state": norm},
        )
        result: dict[str, Any] = {
            "external_id": str(external_id),
            "status": norm,
            "status_code": resp.status_code,
            "ok": 200 <= resp.status_code < 300,
        }
        if comment:
            comment_resp = self.add_comment(external_id, comment)
            result["comment"] = comment_resp
        return result

    def add_comment(self, external_id: str, comment: str) -> dict[str, Any]:
        """Post a comment on an issue."""

        resp = self._request(
            "POST",
            f"/repos/{self._repo}/issues/{external_id}/comments",
            json={"body": comment},
        )
        return {
            "external_id": str(external_id),
            "status_code": resp.status_code,
            "ok": 200 <= resp.status_code < 300,
            "raw": resp.json() if 200 <= resp.status_code < 300 else None,
        }


# ---------------------------------------------------------------------------
# IssueSource-compatible adapter (the adapter-family API: SOURCE, fetch,
# write_back). This class is what the test suite imports as GitHubIssuesSource.
# The older GitHubIssueSource (singular) is kept for backward compatibility.
# ---------------------------------------------------------------------------

from general_ludd.issue_sources.base import IssueRecord, IssueSource, Transition  # noqa: E402


class GitHubIssuesSource(IssueSource):
    """GitHub Issues adapter conforming to the :class:`IssueSource` contract.

    Adds ``SOURCE = "github_issues"`` and implements :meth:`fetch` /
    :meth:`write_back` on top of a thin injectable transport so tests can run
    fully mocked without network access.

    Transport protocol (injectable via ``transport=``):
        ``(method: str, url: str, headers: dict, body: Any | None) -> (int, Any)``

    When *no* transport is supplied the adapter constructs an httpx-based one.
    """

    SOURCE = "github_issues"

    # ``claim_label`` — optional GitHub label to apply when CLAIM-ing an issue.
    _claim_label: str | None

    def __init__(
        self,
        config: dict[str, Any],
        *,
        transport: Any | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        # Validate ``repo`` early; fail closed on a missing slash.
        repo = str(config.get("repo") or "")
        if "/" not in repo:
            raise ValueError(
                f"GitHubIssuesSource: config['repo'] must be 'owner/name', got {repo!r}"
            )

        # Build the base_url-less config for the base class (we always supply a
        # base_url, so require_base_url=False avoids the URL-present assertion).
        base_url_cfg = dict(config)
        base_url_cfg.setdefault("base_url", _DEFAULT_BASE_URL)
        super().__init__(base_url_cfg, transport=None, require_base_url=False)
        # Override base_url with the raw string (the base class sets it from config).
        self.base_url = str(base_url_cfg["base_url"]).rstrip("/")

        self._repo = repo
        self._token_env = str(config.get("token_env") or "GITHUB_TOKEN")
        self._env = dict(env) if env is not None else dict(os.environ)
        self._timeout = float(config.get("timeout") or _DEFAULT_TIMEOUT)
        self._claim_label = config.get("claim_label") or None
        self._raw_transport = transport

    # -- internal helpers ------------------------------------------------

    def _token(self) -> str:
        return self._env.get(self._token_env, "")

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "general-ludd-agent",
        }
        token = self._token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _call(
        self,
        method: str,
        path: str,
        body: Any | None = None,
    ) -> tuple[int, Any]:
        """Issue one HTTP request via the injectable transport."""
        url = f"{self.base_url}{path}"
        hdrs = self._headers()
        if self._raw_transport is not None:
            result: tuple[int, Any] = self._raw_transport(method, url, hdrs, body)
            return result
        # Default httpx-based transport.
        import httpx

        resp = httpx.request(
            method,
            url,
            headers=hdrs,
            json=body,
            timeout=self._timeout,
        )
        return resp.status_code, resp.json()

    # -- IssueSource contract -------------------------------------------

    def fetch(self, spec: dict[str, Any] | None = None) -> list[IssueRecord]:
        """Fetch and normalize open GitHub issues; PRs are skipped."""
        status_code, data = self._call("GET", f"/repos/{self._repo}/issues")
        if not (200 <= status_code < 300) or not isinstance(data, list):
            return []
        records: list[IssueRecord] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            if "pull_request" in item:
                continue
            labels_raw = item.get("labels") or []
            labels = [
                lbl.get("name", "") if isinstance(lbl, dict) else str(lbl)
                for lbl in labels_raw
            ]
            assignee_obj = item.get("assignee")
            assignee: str | None = (
                assignee_obj.get("login") if isinstance(assignee_obj, dict) else None
            )
            number = item.get("number")
            external_id = str(number) if number is not None else str(item.get("id", ""))
            records.append(
                IssueRecord(
                    external_id=external_id,
                    source=self.SOURCE,
                    title=str(item.get("title") or ""),
                    body=str(item.get("body") or ""),
                    status="closed" if str(item.get("state") or "open").lower() == "closed" else "open",
                    priority=None,
                    assignee=assignee,
                    labels=labels,
                    url=str(item.get("html_url") or item.get("url") or ""),
                    updated_at=None,
                    raw=item,
                )
            )
        return records

    def write_back(self, external_id: str, transition: Transition) -> bool:
        """Apply ``transition`` to the issue; return True on success."""
        if transition is Transition.DONE:
            status_code, _ = self._call(
                "PATCH",
                f"/repos/{self._repo}/issues/{external_id}",
                {"state": "closed"},
            )
            return 200 <= status_code < 300
        if transition is Transition.CLAIM:
            if self._claim_label:
                status_code, _ = self._call(
                    "POST",
                    f"/repos/{self._repo}/issues/{external_id}/labels",
                    {"labels": [self._claim_label]},
                )
                return 200 <= status_code < 300
            # No claim label configured: no-op success (nothing to push).
            return True
        return False
