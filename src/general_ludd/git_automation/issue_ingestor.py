"""GitHub issue ingestion — polls labeled issues and creates todos."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class GitHubIssueIngestor:
    def __init__(
        self,
        owner: str = "",
        repo: str = "",
        label: str = "gludd",
        poll_interval_seconds: float = 300.0,
        seen_ids: set[int | str] | None = None,
    ) -> None:
        self._owner = owner
        self._repo = repo
        self._label = label
        self._poll_interval = poll_interval_seconds
        # Accept an externally-owned seen-id store so dedup can PERSIST across
        # ingestor instances (the maintenance router builds a fresh ingestor
        # per request). Default to a private set for standalone use.
        self._seen_ids: set[int | str] = seen_ids if seen_ids is not None else set()

    def is_configured(self) -> bool:
        return bool(self._owner and self._repo)

    async def poll_issues(self) -> list[dict[str, Any]]:
        if not self.is_configured():
            return []
        issues = await self._fetch_labeled_issues()
        new_todos: list[dict[str, Any]] = []
        for issue in issues:
            # GitHub's /issues endpoint returns both issues and PRs; PRs carry a
            # `pull_request` key and must never become work todos.
            if "pull_request" in issue:
                continue
            # Use `id` for dedup when present; fall back to `number` so two
            # distinct id-less issues don't collide on the same sentinel key.
            issue_id = issue.get("id") or f"number:{issue.get('number', '')}"
            if issue_id in self._seen_ids:
                continue
            self._seen_ids.add(issue_id)
            title = issue.get("title", "")
            body = issue.get("body", "")
            labels_raw = issue.get("labels", [])
            label_names = [
                lbl.get("name", "") if isinstance(lbl, dict) else str(lbl)
                for lbl in labels_raw
            ]
            work_type = "code"
            for ln in label_names:
                if ln in ("bug", "fix", "bug_fix"):
                    work_type = "bug_fix"
                elif ln in ("docs", "documentation"):
                    work_type = "docs"
                elif ln in ("test", "testing"):
                    work_type = "test"
            new_todos.append({
                "title": title,
                "description": body or "",
                "queue": "core",
                "priority": "medium",
                "work_type": work_type,
                "source": (
                    f"github:{self._owner}/{self._repo}"
                    f"#{issue.get('number', '')}"
                ),
            })
        return new_todos

    async def _fetch_labeled_issues(self) -> list[dict[str, Any]]:
        import json
        from urllib.parse import quote, urlencode
        from urllib.request import Request, urlopen

        # Escape request-supplied owner/repo/label. Unescaped interpolation let
        # a value like label="a&state=closed" smuggle extra query params (and
        # owner/repo with "/" or ".." traverse the path). quote(safe="") on the
        # path segments and urlencode on the query close that injection.
        owner = quote(self._owner, safe="")
        repo = quote(self._repo, safe="")
        query = urlencode(
            {"labels": self._label, "state": "open", "per_page": 50}
        )
        url = (
            f"https://api.github.com/repos/{owner}/{repo}/issues?{query}"
        )
        try:
            req = Request(url)
            req.add_header("Accept", "application/vnd.github.v3+json")
            req.add_header("User-Agent", "general-ludd-agent")
            with urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
            if isinstance(data, list):
                return data
            return []
        except Exception as exc:
            logger.warning("GitHub issue fetch failed: %s", exc)
            return []
