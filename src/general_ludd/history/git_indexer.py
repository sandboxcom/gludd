from __future__ import annotations

import sqlite3
import subprocess
from collections.abc import Generator
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class CommitRecord:
    hash: str
    author: str
    date: str
    message: str
    insertions: int
    deletions: int
    files: list[FileChange] = field(default_factory=list)


@dataclass(slots=True)
class FileChange:
    path: str
    change_type: str  # A=added, M=modified, D=deleted, R=renamed


@dataclass(slots=True)
class SearchResult:
    hash: str
    author: str
    date: str
    message: str
    insertions: int
    deletions: int
    matched_paths: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "hash": self.hash,
            "author": self.author,
            "date": self.date,
            "message": self.message,
            "insertions": self.insertions,
            "deletions": self.deletions,
            "matched_paths": self.matched_paths,
        }


DEFAULT_DB_PATH = ".gludd/git_history.db"
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS commits (
    hash TEXT PRIMARY KEY,
    author TEXT NOT NULL,
    date TEXT NOT NULL,
    message TEXT NOT NULL,
    insertions INTEGER NOT NULL DEFAULT 0,
    deletions INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS files_changed (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    commit_hash TEXT NOT NULL,
    change_type TEXT NOT NULL CHECK(change_type IN ('A','M','D','R')),
    FOREIGN KEY (commit_hash) REFERENCES commits(hash) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_commits_date ON commits(date);
CREATE INDEX IF NOT EXISTS idx_commits_author ON commits(author);
CREATE INDEX IF NOT EXISTS idx_files_changed_path ON files_changed(path);
CREATE INDEX IF NOT EXISTS idx_files_changed_commit ON files_changed(commit_hash);
"""


class GitHistoryIndexer:
    def __init__(self, repo_path: str | Path = ".", db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self.repo_path = Path(repo_path).resolve()
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(SCHEMA_SQL)
        return conn

    def _parse_git_log(self) -> Generator[CommitRecord, None, None]:
        cmd = [
            "git", "-C", str(self.repo_path), "log",
            "--all", "--numstat", "--date=iso-strict",
            "--format=__COMMIT__%n%H%n%an%n%ad%n%s",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            stderr = proc.stderr or ""
            # A path that is not a git repo (or a repo with no commits yet) has
            # ZERO commits to index -- that is an empty result, not an error.
            # Any OTHER git failure is still surfaced, so real breakage is never
            # silently swallowed.
            benign = ("not a git repository", "does not have any commits yet")
            if any(marker in stderr.lower() for marker in benign):
                return
            raise RuntimeError(f"git log failed: {stderr}")

        # The --format above EMITS the __COMMIT__ marker as the first line of
        # every record, so stdout STARTS with "__COMMIT__\n". Splitting on the
        # newline-prefixed "\n__COMMIT__\n" therefore leaves the very first
        # (newest) block still carrying its own marker line, shifting that
        # commit's fields by one (hash <- "__COMMIT__", author <- sha, date <-
        # "T", message <- date). Strip the leading marker so every block starts
        # at the hash and the split offsets below line up for ALL commits.
        stdout = proc.stdout.strip()
        marker = "__COMMIT__\n"
        if stdout.startswith(marker):
            stdout = stdout[len(marker):]

        blocks = stdout.split("\n__COMMIT__\n")
        for block in blocks:
            if not block.strip():
                continue
            lines = block.strip().split("\n")
            if len(lines) < 4:
                continue
            commit_hash = lines[0].strip()
            author = lines[1].strip()
            date_str = lines[2].strip()
            message = lines[3].strip()

            insertions = 0
            deletions = 0
            files: list[FileChange] = []
            for line in lines[4:]:
                line = line.strip()
                if not line:
                    continue
                parts = line.split("\t")
                if len(parts) == 3:
                    added, removed, path = parts
                    if added != "-":
                        insertions += int(added)
                    if removed != "-":
                        deletions += int(removed)
                    change_type = "M"
                    files.append(FileChange(path=path, change_type=change_type))
                elif len(parts) == 1:
                    if "=>" in parts[0]:
                        files.append(FileChange(path=parts[0], change_type="R"))

            yield CommitRecord(
                hash=commit_hash,
                author=author,
                date=date_str,
                message=message,
                insertions=insertions,
                deletions=deletions,
                files=files,
            )

    def index(self) -> int:
        conn = self._get_conn()
        indexed = 0
        try:
            for commit in self._parse_git_log():
                conn.execute(
                    "INSERT OR IGNORE INTO commits"
                    "(hash, author, date, message, insertions, deletions)"
                    " VALUES(?,?,?,?,?,?)",
                    (commit.hash, commit.author, commit.date, commit.message, commit.insertions, commit.deletions),
                )
                if commit.files:
                    conn.executemany(
                        "INSERT INTO files_changed(path, commit_hash, change_type) VALUES(?,?,?)",
                        [(f.path, commit.hash, f.change_type) for f in commit.files],
                    )
                indexed += 1
            conn.commit()
        finally:
            conn.close()
        return indexed

    def search(
        self,
        query: str = "",
        since: str = "",
        author: str = "",
        path_filter: str = "",
        limit: int = 100,
        offset: int = 0,
    ) -> list[SearchResult]:
        conn = self._get_conn()
        results: list[SearchResult] = []
        try:
            where_clauses: list[str] = []
            params: list[str] = []

            if query:
                where_clauses.append("(c.message LIKE ? OR f.path LIKE ?)")
                like_q = f"%{query}%"
                params.extend([like_q, like_q])

            if since:
                where_clauses.append("c.date >= ?")
                params.append(_normalize_date(since))

            if author:
                where_clauses.append("c.author LIKE ?")
                params.append(f"%{author}%")

            if path_filter:
                where_clauses.append("f.path LIKE ?")
                params.append(f"%{path_filter}%")

            where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
            joined = any([query, path_filter])

            # NOTE: sqlite only allows a DISTINCT aggregate with exactly one
            # argument, so GROUP_CONCAT(DISTINCT f.path, '|') (two args) raises
            # "DISTINCT aggregates must have exactly one argument". Deduplicate
            # (hash, path) pairs in an inner subquery instead, then GROUP_CONCAT
            # the already-distinct paths without DISTINCT — same semantics
            # (matched rows per the WHERE filter, deduped paths, '|'-joined).
            if joined:
                select_sql = f"""
                    SELECT hash, author, date, message, insertions, deletions,
                           GROUP_CONCAT(path, '|') AS paths
                    FROM (
                        SELECT DISTINCT c.hash AS hash, c.author AS author, c.date AS date,
                               c.message AS message, c.insertions AS insertions,
                               c.deletions AS deletions, f.path AS path
                        FROM commits c
                        JOIN files_changed f ON c.hash = f.commit_hash
                        WHERE {where_sql}
                    )
                    GROUP BY hash
                    ORDER BY date DESC
                    LIMIT ? OFFSET ?
                """
            else:
                select_sql = f"""
                    SELECT hash, author, date, message, insertions, deletions,
                           GROUP_CONCAT(path, '|') AS paths
                    FROM (
                        SELECT DISTINCT c.hash AS hash, c.author AS author, c.date AS date,
                               c.message AS message, c.insertions AS insertions,
                               c.deletions AS deletions, f.path AS path
                        FROM commits c
                        LEFT JOIN files_changed f ON c.hash = f.commit_hash
                        WHERE {where_sql}
                    )
                    GROUP BY hash
                    ORDER BY date DESC
                    LIMIT ? OFFSET ?
                """

            params.extend([str(limit), str(offset)])
            rows = conn.execute(select_sql, params).fetchall()

            for row in rows:
                paths_str = row[6] or ""
                matched_paths = [p for p in paths_str.split("|") if p] if paths_str else []
                results.append(SearchResult(
                    hash=row[0],
                    author=row[1],
                    date=row[2],
                    message=row[3],
                    insertions=row[4],
                    deletions=row[5],
                    matched_paths=matched_paths,
                ))
        finally:
            conn.close()
        return results

    def stats(self) -> dict[str, object]:
        conn = self._get_conn()
        try:
            total = conn.execute("SELECT COUNT(*) FROM commits").fetchone()[0]
            last = conn.execute("SELECT MAX(date) FROM commits").fetchone()[0]
            files_count = conn.execute("SELECT COUNT(DISTINCT path) FROM files_changed").fetchone()[0]
        finally:
            conn.close()
        return {"total_commits": total, "last_indexed": last or "", "unique_files": files_count}


def _normalize_date(date_str: str) -> str:
    if "T" in date_str:
        return date_str
    return f"{date_str}T00:00:00+00:00"


def search_history(
    db_path: str | Path = DEFAULT_DB_PATH,
    query: str = "",
    since: str = "",
    author: str = "",
    path_filter: str = "",
    limit: int = 100,
    offset: int = 0,
) -> list[SearchResult]:
    indexer = GitHistoryIndexer(db_path=str(db_path))
    return indexer.search(query=query, since=since, author=author, path_filter=path_filter, limit=limit, offset=offset)
