"""Git intelligence — files-changed-together, blame analysis, hot files, contributors.

Provides model-friendly structured git data without raw CLI output.
"""

from __future__ import annotations

import logging
import subprocess
from typing import Any

logger = logging.getLogger(__name__)


class GitIntelligence:
    """Analyzes git history for code intelligence signals."""

    def __init__(self, repo_path: str) -> None:
        self._repo = repo_path

    def _run_git(self, args: list[str]) -> subprocess.CompletedProcess[str] | None:
        try:
            return subprocess.run(
                ["git", "-C", self._repo, *args],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except Exception as exc:
            logger.debug("git command failed: %s", exc)
            return None

    def files_changed_together(self, limit: int = 20) -> list[dict[str, Any]]:
        """Find files that are frequently changed together in the same commit."""
        result = self._run_git([
            "log", "--name-only", "--format=", "-n", str(limit * 5)
        ])
        if result is None or result.returncode != 0:
            return []

        file_pairs: dict[str, int] = {}
        current_files: list[str] = []
        for line in result.stdout.split("\n"):
            stripped = line.strip()
            if not stripped:
                if len(current_files) > 1:
                    for i in range(len(current_files)):
                        for j in range(i + 1, len(current_files)):
                            pair = f"{current_files[i]}||{current_files[j]}"
                            file_pairs[pair] = file_pairs.get(pair, 0) + 1
                current_files = []
            else:
                current_files.append(stripped)

        sorted_pairs = sorted(file_pairs.items(), key=lambda x: x[1], reverse=True)[:limit]
        return [{"files": p[0].split("||"), "count": p[1]} for p in sorted_pairs]

    def blame_analysis(self, file_path: str) -> dict[str, Any]:
        """Get blame information for a file."""
        result = self._run_git(["blame", "--line-porcelain", file_path])
        if result is None or result.returncode != 0:
            return {}

        authors: dict[str, int] = {}
        line_count = 0
        for line in result.stdout.split("\n"):
            if line.startswith("author "):
                author = line[7:].strip()
                authors[author] = authors.get(author, 0) + 1
            if line.startswith("\t"):
                line_count += 1

        return {
            "file": file_path,
            "total_lines": line_count,
            "author_breakdown": sorted(
                [{"author": k, "lines": v} for k, v in authors.items()],
                key=lambda x: x["lines"],
                reverse=True,
            ),
        }

    def recent_contributors(self, limit: int = 10) -> list[dict[str, Any]]:
        """List recent contributors to the repository."""
        result = self._run_git([
            "shortlog", "-sne", "-n", str(limit), "HEAD"
        ])
        if result is None or result.returncode != 0:
            return []

        contributors: list[dict[str, Any]] = []
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.strip().split("\t", 1)
            if len(parts) == 2:
                count = int(parts[0].strip())
                name_email = parts[1].strip()
                contributors.append({
                    "name": name_email.split("<")[0].strip(),
                    "email": name_email.split("<")[1].split(">")[0] if "<" in name_email else "",
                    "commits": count,
                })
        return contributors

    def recent_commits(self, limit: int = 20) -> list[dict[str, Any]]:
        """Get recent commits with structured data."""
        result = self._run_git([
            "log", "-n", str(limit), "--format=%H\t%s\t%an\t%aI"
        ])
        if result is None or result.returncode != 0:
            return []

        commits: list[dict[str, Any]] = []
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split("\t", 3)
            if len(parts) == 4:
                commits.append({
                    "hash": parts[0][:7],
                    "message": parts[1],
                    "author": parts[2],
                    "date": parts[3],
                })
        return commits

    def hot_files(self, limit: int = 10) -> list[dict[str, Any]]:
        """Find the most frequently changed files."""
        result = self._run_git([
            "log", "--format=", "--name-only", "-n", str(limit * 10)
        ])
        if result is None or result.returncode != 0:
            return []

        file_counts: dict[str, int] = {}
        for line in result.stdout.split("\n"):
            stripped = line.strip()
            if not stripped:
                continue
            for part in stripped.split("\t"):
                part = part.strip()
                if part:
                    file_counts[part] = file_counts.get(part, 0) + 1

        sorted_files = sorted(file_counts.items(), key=lambda x: x[1], reverse=True)[:limit]
        return [{"path": f, "changes": c} for f, c in sorted_files]
