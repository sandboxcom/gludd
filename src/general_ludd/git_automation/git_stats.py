"""Git history statistics and query helpers.

Provides index statistics (get_git_stats) and direct git query functions
(git_log, git_show, git_diff) that parallel the Makefile targets.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from general_ludd.history.git_indexer import GitHistoryIndexer

_GIT_TIMEOUT = 30.0


def get_git_stats(repo_path: str = ".", db_path: str = ".gludd/git_history.db") -> dict[str, object]:
    indexer = GitHistoryIndexer(repo_path=repo_path, db_path=db_path)
    conn = indexer._get_conn()
    try:
        total = conn.execute("SELECT COUNT(*) FROM commits").fetchone()[0]
        last = conn.execute("SELECT MAX(date) FROM commits").fetchone()[0]
        files_count = conn.execute("SELECT COUNT(DISTINCT path) FROM files_changed").fetchone()[0]
        authors = [r[0] for r in conn.execute("SELECT DISTINCT author FROM commits ORDER BY author").fetchall()]
        earliest = conn.execute("SELECT MIN(date) FROM commits").fetchone()[0]
        latest = conn.execute("SELECT MAX(date) FROM commits").fetchone()[0]
    finally:
        conn.close()

    stats: dict[str, object] = {
        "total_commits": total,
        "last_indexed": last or "",
        "unique_files": files_count,
        "authors": authors,
        "date_range": {"earliest": earliest or "", "latest": latest or ""},
    }
    db = Path(db_path)
    stats["db_size_bytes"] = db.stat().st_size if db.exists() else 0
    return stats


def git_log(repo_path: str = ".", limit: int = 10) -> list[dict[str, str]]:
    proc = subprocess.run(
        ["git", "-C", str(repo_path), "log", f"-{limit}", "--format=%H%x00%an%x00%ad%x00%s", "--date=iso-strict"],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT,
    )
    commits: list[dict[str, str]] = []
    for line in proc.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("\0")
        if len(parts) >= 4:
            commits.append({"hash": parts[0], "author": parts[1], "date": parts[2], "message": parts[3]})
    return commits


def git_show(repo_path: str = ".", sha: str = "HEAD") -> dict[str, str]:
    proc = subprocess.run(
        ["git", "-C", str(repo_path), "show", "--stat", "--oneline", sha],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT,
    )
    output = proc.stdout.strip() or "No diff"
    return {"sha": sha, "output": output}


def git_diff(repo_path: str = ".", files: str | list[str] | None = None) -> dict[str, str]:
    cmd = ["git", "-C", str(repo_path), "diff", "--stat", "HEAD"]
    if files:
        cmd.append("--")
        if isinstance(files, str):
            cmd.append(files)
        else:
            cmd.extend(files)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=_GIT_TIMEOUT)
    output = proc.stdout.strip() or "No diff"
    return {"output": output}
