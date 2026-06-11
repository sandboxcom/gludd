"""Pull request delivery for completed agent work.

When a review decision is 'complete' and git_automation.push is true,
pushes the branch and opens a PR via 'gh pr create'.
"""

from __future__ import annotations

import logging
import subprocess
from typing import Any

logger = logging.getLogger(__name__)


class PRDelivery:
    def __init__(
        self,
        base_branch: str = "main",
        draft: bool = False,
        labels: list[str] | None = None,
    ) -> None:
        self._base_branch = base_branch
        self._draft = draft
        self._labels = labels or []

    def push_and_create_pr(
        self,
        repo_path: str,
        branch_name: str,
        todo_id: str,
        title: str,
        description: str = "",
    ) -> dict[str, Any]:
        if not self._check_gh_available():
            return {
                "pr_url": None,
                "error": "gh CLI not found — install GitHub CLI",
            }

        try:
            push_result = subprocess.run(
                ["git", "push", "origin", branch_name],
                cwd=repo_path, capture_output=True, text=True, timeout=30,
            )
            if push_result.returncode != 0:
                return {
                    "pr_url": None,
                    "error": f"Push failed: {push_result.stderr[:200]}",
                }

            pr_cmd = [
                "gh", "pr", "create",
                "--head", branch_name,
                "--base", self._base_branch,
                "--title", f"[gludd] {todo_id}: {title}",
                "--body", description or f"Automated PR for {todo_id}",
            ]
            if self._draft:
                pr_cmd.append("--draft")
            for label in self._labels:
                pr_cmd.extend(["--label", label])

            pr_result = subprocess.run(
                pr_cmd, cwd=repo_path,
                capture_output=True, text=True, timeout=30,
            )
            if pr_result.returncode == 0:
                pr_url = pr_result.stdout.strip()
                logger.info("PR created: %s", pr_url)
                return {"pr_url": pr_url, "error": None}
            return {
                "pr_url": None,
                "error": f"PR creation failed: {pr_result.stderr[:200]}",
            }
        except Exception as exc:
            return {"pr_url": None, "error": str(exc)}

    def _check_gh_available(self) -> bool:
        try:
            result = subprocess.run(
                ["gh", "--version"],
                capture_output=True, timeout=5,
            )
            return result.returncode == 0
        except Exception:
            return False
