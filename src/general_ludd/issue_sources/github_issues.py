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

import os
from typing import Any

from general_ludd.issue_sources.base import IssueRecord, IssueSource, Transition

# ---------------------------------------------------------------------------
# IssueSource-compatible adapter (the adapter-family API: SOURCE, fetch,
# write_back). This class is what the test suite imports as GitHubIssuesSource.
# ---------------------------------------------------------------------------

_DEFAULT_BASE_URL = "https://api.github.com"
_DEFAULT_TIMEOUT = 30.0
_MAX_ISSUE_NUMBER_DIGITS = 20


def _issue_number_path_segment(external_id: str) -> str:
    """Return a canonical positive ASCII issue number or fail closed."""
    if (
        not external_id
        or len(external_id) > _MAX_ISSUE_NUMBER_DIGITS
        or not external_id.isascii()
        or not external_id.isdecimal()
        or external_id.startswith("0")
    ):
        raise ValueError(
            "GitHub issue external_id must be a 1-20 digit positive ASCII decimal"
        )
    return external_id


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
        """Initialize one repository-scoped adapter with an optional transport."""
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
        # Re-run the SSRF guard against the reassigned base_url.
        self._guard_base_url()

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
        # Defense-in-depth: re-check the SSRF guard per request in case
        # ``base_url`` was mutated after construction.
        self._guard_base_url()
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
        issue_number = _issue_number_path_segment(external_id)
        if transition is Transition.DONE:
            status_code, _ = self._call(
                "PATCH",
                f"/repos/{self._repo}/issues/{issue_number}",
                {"state": "closed"},
            )
            return 200 <= status_code < 300
        if transition is Transition.CLAIM:
            if self._claim_label:
                status_code, _ = self._call(
                    "POST",
                    f"/repos/{self._repo}/issues/{issue_number}/labels",
                    {"labels": [self._claim_label]},
                )
                return 200 <= status_code < 300
            # No claim label configured: no-op success (nothing to push).
            return True
        return False
