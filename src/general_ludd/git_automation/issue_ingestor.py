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
    ) -> None:
        self._owner = owner
        self._repo = repo
        self._label = label
        self._poll_interval = poll_interval_seconds
        self._seen_ids: set[int] = set()

    def is_configured(self) -> bool:
        return bool(self._owner and self._repo)

    async def poll_issues(self) -> list[dict[str, Any]]:
        if not self.is_configured():
            return []
        issues = await self._fetch_labeled_issues()
        new_todos: list[dict[str, Any]] = []
        for issue in issues:
            issue_id = issue.get("id", 0)
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
        from urllib.request import Request, urlopen

        url = (
            f"https://api.github.com/repos/{self._owner}/{self._repo}/issues"
            f"?labels={self._label}&state=open&per_page=50"
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
