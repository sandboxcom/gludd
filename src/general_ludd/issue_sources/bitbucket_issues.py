"""Bitbucket Cloud issue-source adapter.

Self-contained, config-driven adapter that lists issues from a Bitbucket Cloud
repository tracker via the v2.0 REST API and normalizes them into the gludd
issue dict shape. Supports state write-back (``PUT /issues/{id}``) and comment
write-back (``POST /issues/{id}/comments``).

Auth supports Bearer access tokens (``token_env``) or HTTP Basic
``username:app_password`` (``username`` + ``password_env``).

No dependency on sibling adapters, a base class, or package ``__init__`` side
effects. The default transport uses only the stdlib ``urllib``; tests inject a
mock transport.

Transport contract (injectable callable)::

    transport(method: str, url: str, headers: dict[str, str],
              json_body: dict | None, timeout: float) -> tuple[int, dict]

Returns ``(status_code, parsed_json_payload)``. The default transport never
uses ``shell=True`` and is strictly time-bound.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import TypedDict, cast
from urllib.parse import quote, urlsplit

from general_ludd.security.ssrf import host_is_blocked


class BitbucketAssignee(TypedDict, total=False):
    """Assignee sub-object on a Bitbucket issue."""

    display_name: str
    nickname: str
    uuid: str


class BitbucketContent(TypedDict, total=False):
    """Rich-text content sub-object on a Bitbucket issue."""

    raw: str


class BitbucketHtmlLink(TypedDict, total=False):
    """The ``html`` entry under a Bitbucket issue's ``links`` mapping."""

    href: str


class BitbucketLinks(TypedDict, total=False):
    """Links sub-object on a Bitbucket issue."""

    html: BitbucketHtmlLink


class BitbucketIssue(TypedDict, total=False):
    """A Bitbucket Cloud issue as returned by the v2.0 REST ``/issues`` endpoint.

    All fields are optional (``total=False``) because Bitbucket payloads vary
    by issue state and projection.
    """

    id: int | str
    title: str
    state: str
    kind: str
    priority: str
    updated_on: str
    assignee: BitbucketAssignee
    content: BitbucketContent
    links: BitbucketLinks


Transport = Callable[
    [str, str, Mapping[str, str], "dict[str, object] | None", float],
    "tuple[int, dict[str, object]]",
]

_DEFAULT_BASE_URL = "https://api.bitbucket.org"
_DEFAULT_TIMEOUT = 15.0


def _is_blocked_host(host: str) -> bool:
    """Return True if ``host`` is a literal that must be SSRF-blocked.

    Pure literal inspection; no DNS resolution so a hostile DNS answer cannot
    reclassify a name after the check. Delegates the loopback/metadata-name/
    IP-literal decision to the canonical
    :func:`general_ludd.security.ssrf.host_is_blocked` (so this adapter's
    classification — previously missing ``metadata.goog``/``instance-data``/
    the ``ip6-*`` aliases and the ``not is_global`` catch for CGNAT/TEST-NET
    ranges — can never drift from the single source of truth), then
    ADDITIVELY also rejects any bare hostname with no dot and any hostname
    ending in ``.local``/``.internal``/``.localhost``/``.intranet`` — a
    stricter rule this adapter had before consolidation that the canonical
    guard does not apply (it would be too strict for other callers).
    """
    host = (host or "").strip().lower()
    if not host:
        return True
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if host_is_blocked(host):
        return True
    if "." not in host:
        return True
    return host.endswith((".local", ".internal", ".localhost", ".intranet"))


def _default_transport(
    method: str,
    url: str,
    headers: Mapping[str, str],
    json_body: dict[str, object] | None,
    timeout: float,
) -> tuple[int, dict[str, object]]:
    data = None
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
    req = urllib.request.Request(url=url, data=data, method=method)
    for key, value in headers.items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = int(getattr(resp, "status", 0) or resp.getcode() or 0)
            body = resp.read()
    except urllib.error.HTTPError as exc:  # pragma: no cover - network path
        status = int(exc.code)
        body = exc.read() if hasattr(exc, "read") else b""
    payload: dict[str, object] = {}
    if body:
        try:
            parsed: object = json.loads(body.decode("utf-8"))
            payload = parsed if isinstance(parsed, dict) else {"data": parsed}
        except (ValueError, UnicodeDecodeError):
            payload = {}
    return status, payload


class BitbucketIssueSource:
    """Issue source backed by the Bitbucket Cloud v2.0 REST API."""

    SYSTEM = "bitbucket"

    def __init__(
        self,
        config: Mapping[str, object],
        *,
        transport: Transport | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._config: dict[str, object] = dict(config or {})
        self.name = str(self._config.get("name") or self.SYSTEM)
        self._env: Mapping[str, str] = env if env is not None else os.environ
        self._transport: Transport = transport or _default_transport

        base_url = str(self._config.get("base_url") or _DEFAULT_BASE_URL).rstrip("/")
        parts = urlsplit(base_url)
        if parts.scheme not in {"https", "http"} or not parts.hostname:
            raise ValueError(f"invalid base_url: {base_url!r}")
        if _is_blocked_host(parts.hostname):
            raise ValueError(f"base_url host is internal/blocked: {parts.hostname!r}")
        self._base_url = base_url

        raw_timeout = self._config.get("timeout", _DEFAULT_TIMEOUT)
        self._timeout = float(raw_timeout) if isinstance(raw_timeout, (int, float, str)) else _DEFAULT_TIMEOUT
        self._workspace = self._config.get("workspace")
        self._repo = self._config.get("repo") or self._config.get("repo_slug")

    # -- secrets ---------------------------------------------------------
    def _auth_header(self) -> str:
        token_env = str(self._config.get("token_env", "BITBUCKET_TOKEN"))
        token = self._env.get(token_env, "")
        if token:
            return f"Bearer {token}"
        username = str(self._config.get("username", ""))
        pw_env = str(self._config.get("password_env", "BITBUCKET_APP_PASSWORD"))
        password = self._env.get(pw_env, "")
        if username and password:
            raw = f"{username}:{password}".encode()
            return "Basic " + base64.b64encode(raw).decode("ascii")
        return ""

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        auth = self._auth_header()
        if auth:
            headers["Authorization"] = auth
        return headers

    def _repo_path(self, workspace: object, repo: object) -> str:
        if not workspace or not repo:
            raise ValueError("workspace and repo required (spec or config)")
        return f"/2.0/repositories/{workspace}/{repo}"

    def _request(
        self, method: str, path: str, body: dict[str, object] | None = None
    ) -> tuple[int, dict[str, object]]:
        url = f"{self._base_url}{path}"
        return self._transport(method, url, self._headers(), body, self._timeout)

    # -- health ----------------------------------------------------------
    def health(self) -> dict[str, object]:
        """Probe the authenticated-user endpoint. Never raises."""
        try:
            status, payload = self._request("GET", "/2.0/user")
        except Exception as exc:  # health must never raise
            return {"ok": False, "detail": f"transport error: {exc}"}
        if status != 200:
            return {"ok": False, "detail": f"http {status}"}
        if payload.get("error"):
            return {"ok": False, "detail": str(payload["error"])}
        if not (payload.get("uuid") or payload.get("account_id") or payload.get("username")):
            return {"ok": False, "detail": "no authenticated user"}
        return {"ok": True, "detail": "ok"}

    # -- normalization ---------------------------------------------------
    def _normalize(self, issue: BitbucketIssue) -> dict[str, object]:
        issue_id = str(issue.get("id") or "")
        assignee_obj = issue.get("assignee") or {}
        assignee = ""
        if isinstance(assignee_obj, Mapping):
            assignee = str(
                assignee_obj.get("display_name")
                or assignee_obj.get("nickname")
                or assignee_obj.get("uuid")
                or ""
            )
        content = issue.get("content") or {}
        description = ""
        if isinstance(content, Mapping):
            description = str(content.get("raw") or "")
        links = issue.get("links") or {}
        html = ""
        if isinstance(links, Mapping):
            html_obj = links.get("html") or {}
            if isinstance(html_obj, Mapping):
                html = str(html_obj.get("href") or "")
        kind = issue.get("kind")
        labels = [str(kind)] if kind else []
        return {
            "external_id": issue_id,
            "source": self.SYSTEM,
            "title": str(issue.get("title") or ""),
            "description": description,
            "status": str(issue.get("state") or ""),
            "assignee": assignee,
            "labels": labels,
            "priority": str(issue.get("priority") or ""),
            "url": html,
            "updated_ts": str(issue.get("updated_on") or ""),
            "raw": dict(issue),
        }

    @staticmethod
    def _extract_values(payload: object) -> list[BitbucketIssue]:
        """Narrow a Bitbucket list response to its issue entries."""
        values: list[BitbucketIssue] = []
        raw = payload.get("values") if isinstance(payload, Mapping) else None
        if isinstance(raw, list):
            for entry in raw:
                if isinstance(entry, dict):
                    values.append(cast(BitbucketIssue, entry))
        return values

    def fetch_issues(
        self, spec: Mapping[str, object] | None = None
    ) -> list[dict[str, object]]:
        spec_dict: dict[str, object] = dict(spec or {})
        workspace = spec_dict.get("workspace", self._workspace)
        repo = spec_dict.get("repo", self._repo)
        path = self._repo_path(workspace, repo) + "/issues"
        params: list[str] = []
        if spec_dict.get("q"):
            params.append("q=" + quote(str(spec_dict["q"])))
        if spec_dict.get("pagelen") is not None:
            raw_pagelen = spec_dict["pagelen"]
            if isinstance(raw_pagelen, (int, float, str)):
                params.append(f"pagelen={int(raw_pagelen)}")
        if params:
            path = path + "?" + "&".join(params)
        status, payload = self._request("GET", path)
        if status != 200:
            raise RuntimeError(f"bitbucket fetch failed: http {status}")
        return [self._normalize(v) for v in self._extract_values(payload)]

    # -- write-back ------------------------------------------------------
    def update_status(
        self, external_id: str, status: str, comment: str | None = None
    ) -> dict[str, object]:
        path = self._repo_path(self._workspace, self._repo) + f"/issues/{external_id}"
        code, payload = self._request("PUT", path, {"state": status})
        result: dict[str, object] = {
            "ok": code == 200 and not payload.get("error"),
            "external_id": str(external_id),
            "status": status,
            "http": code,
            "raw": payload,
        }
        if comment:
            result["comment"] = self.add_comment(external_id, comment)
        return result

    def add_comment(self, external_id: str, comment: str) -> dict[str, object]:
        path = self._repo_path(self._workspace, self._repo) + f"/issues/{external_id}/comments"
        code, payload = self._request("POST", path, {"content": {"raw": comment}})
        return {
            "ok": code in (200, 201) and not payload.get("error"),
            "external_id": str(external_id),
            "http": code,
            "raw": payload,
        }
