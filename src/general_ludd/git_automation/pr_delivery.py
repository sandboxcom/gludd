"""Pull request delivery for completed agent work.

When a review decision is 'complete' and git_automation.push is true,
pushes the branch and opens a PR via 'gh pr create'.
"""

from __future__ import annotations

import logging
import re
import subprocess
from typing import Any

logger = logging.getLogger(__name__)

# A git ref / remote name we are willing to pass to `git push` / `gh`. We do NOT
# go through a shell, so this is defence-in-depth — but a value carrying shell
# metacharacters, whitespace, or a leading '-' is never a legitimate branch or
# remote name and is a clear option/injection attempt, so we fail closed on it
# rather than handing it to subprocess. Allowed: letters, digits, and the small
# set of punctuation git refs legitimately use (./_-+@/). A leading '-' is
# rejected separately (it would be parsed as a git/gh OPTION).
_SAFE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+@/-]*$")


def _validate_ref(value: str, kind: str) -> str | None:
    """Return an error string if ``value`` is an unsafe ref/remote, else None."""
    if not value:
        return f"empty {kind} name"
    if value.startswith("-"):
        return (
            f"refusing {kind} name that begins with '-' (would be parsed as a "
            f"git/gh option): {value!r}"
        )
    if not _SAFE_REF.match(value):
        return (
            f"refusing {kind} name with disallowed characters "
            f"(possible shell/option injection): {value!r}"
        )
    return None


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
        remote: str = "origin",
    ) -> dict[str, Any]:
        # Fail closed BEFORE any subprocess call on a branch or remote that is
        # dash-leading (would be parsed as a git/gh OPTION, e.g.
        # --upload-pack=...) or carries shell metacharacters / whitespace (never
        # a valid ref/remote — a clear injection attempt). title/body are NOT
        # validated here: they are passed as discrete argv values, never
        # interpolated, so arbitrary text in them is inert.
        for value, kind in ((branch_name, "branch"), (remote, "remote")):
            err = _validate_ref(value, kind)
            if err is not None:
                return {"pr_url": None, "error": err}

        if not self._check_gh_available():
            return {
                "pr_url": None,
                "error": "gh CLI not found — install GitHub CLI",
            }

        try:
            push_result = subprocess.run(
                # argv is a list (never a shell string); `--` ends option
                # parsing before the remote/refspec positionals so neither can
                # be reinterpreted as an option.
                ["git", "push", remote, "--", branch_name],
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
