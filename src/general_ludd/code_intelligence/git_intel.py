"""Git intelligence — files-changed-together, blame analysis, hot files, contributors.

Provides model-friendly structured git data without raw CLI output.

Hardening notes
---------------
``GitIntelligence`` runs ``git -C <repo> ...`` where the repo path and, for some
methods, a ref/path come from caller input.  To prevent option-injection and
path-escape attacks the following invariants hold:

* The repo path is resolved with :func:`os.path.realpath` and must point at an
  existing directory; otherwise the call fails closed (returns ``None``/empty).
* Any caller-supplied ref/path token is validated: a leading ``-`` (option
  injection, e.g. ``--output=/etc/passwd``) and shell metacharacters are
  rejected.  Validation is enforced even though we never go through a shell —
  ``git`` itself treats a leading ``-`` token as an option.
* Caller-supplied positional refs/paths are always placed after a literal
  ``--`` end-of-options separator so ``git`` cannot interpret them as flags.
* Commands are always built in argv list-form (never a shell string).
"""

from __future__ import annotations

import logging
import os
import subprocess
from typing import cast

logger = logging.getLogger(__name__)

# Characters that must never appear in a caller-supplied ref/path token.  Even
# though we build argv list-form (no shell), these guard against a token being
# smuggled somewhere a shell *is* eventually involved and keep refs/paths sane.
_FORBIDDEN_TOKEN_CHARS = set(";|&$`<>\n\r\t\\\"'*?()[]{}!~ ")


class GitIntelError(ValueError):
    """Raised when a caller-supplied repo path or ref/path is unsafe."""


def _validate_token(token: str, *, kind: str = "ref/path") -> str:
    """Reject a caller-supplied ref/path that could be used for injection.

    Rejects:
      * non-``str`` / empty tokens
      * a leading ``-`` (git option injection)
      * shell metacharacters and whitespace
    """
    if not isinstance(token, str) or token == "":
        raise GitIntelError(f"empty or non-string {kind}")
    if token.startswith("-"):
        raise GitIntelError(f"{kind} may not start with '-' (option injection): {token!r}")
    bad = sorted(_FORBIDDEN_TOKEN_CHARS & set(token))
    if bad:
        raise GitIntelError(f"{kind} contains forbidden characters {bad!r}: {token!r}")
    return token


class GitIntelligence:
    """Analyzes git history for code intelligence signals."""

    def __init__(self, repo_path: str) -> None:
        self._repo = repo_path

    def _resolve_repo(self) -> str | None:
        """Resolve and confine the repo path; return ``None`` if unsafe.

        The path is canonicalised with :func:`os.path.realpath` (collapsing
        ``..`` and symlinks) and must point at an existing directory.
        """
        if not isinstance(self._repo, str) or self._repo == "":
            logger.debug("git repo path empty or non-string: %r", self._repo)
            return None
        real = os.path.realpath(self._repo)
        if not os.path.isdir(real):
            logger.debug("git repo path is not an existing directory: %r", real)
            return None
        return real

    def _run_git(
        self,
        args: list[str],
        *,
        refs: list[str] | None = None,
    ) -> subprocess.CompletedProcess[str] | None:
        """Run ``git -C <repo> <args> [-- <refs>]`` in argv list-form.

        ``args`` are trusted, internally-constructed flags.  ``refs`` are
        caller-supplied positional refs/paths: each is validated and placed
        after a literal ``--`` end-of-options separator.
        """
        repo = self._resolve_repo()
        if repo is None:
            return None

        argv = ["git", "-C", repo, *args]
        if refs:
            try:
                validated = [_validate_token(r) for r in refs]
            except GitIntelError as exc:
                logger.debug("rejected unsafe git ref/path: %s", exc)
                return None
            argv += ["--", *validated]

        try:
            return subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except Exception as exc:
            logger.debug("git command failed: %s", exc)
            return None

    def files_changed_together(self, limit: int = 20) -> list[dict[str, object]]:
        """Find files that are frequently changed together in the same commit."""
        result = self._run_git([
            "log", "--name-only", "--format=", "-n", str(int(limit) * 5)
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

    def blame_analysis(self, file_path: str) -> dict[str, object]:
        """Get blame information for a file."""
        result = self._run_git(
            ["blame", "--line-porcelain"],
            refs=[file_path],
        )
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
                key=lambda x: cast(int, x["lines"]),
                reverse=True,
            ),
        }

    def recent_contributors(self, limit: int = 10) -> list[dict[str, object]]:
        """List recent contributors to the repository."""
        result = self._run_git([
            "shortlog", "-sne", "-n", str(int(limit)), "HEAD"
        ])
        if result is None or result.returncode != 0:
            return []

        contributors: list[dict[str, object]] = []
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

    def recent_commits(self, limit: int = 20) -> list[dict[str, object]]:
        """Get recent commits with structured data."""
        result = self._run_git([
            "log", "-n", str(int(limit)), "--format=%H\t%s\t%an\t%aI"
        ])
        if result is None or result.returncode != 0:
            return []

        commits: list[dict[str, object]] = []
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

    def hot_files(self, limit: int = 10) -> list[dict[str, object]]:
        """Find the most frequently changed files."""
        result = self._run_git([
            "log", "--format=", "--name-only", "-n", str(int(limit) * 10)
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
