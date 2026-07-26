from __future__ import annotations

import os
import sqlite3
import subprocess
from pathlib import Path

from general_ludd.git_automation.git_index import index_git_history
from general_ludd.git_automation.git_search import search_git_history
from general_ludd.git_automation.git_stats import get_git_stats, git_diff, git_log, git_show


def _init_git_repo(path: Path, email: str = "test@test.com", name: str = "Test") -> None:
    subprocess.run(["git", "-C", str(path), "init"], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", email], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", name], capture_output=True, check=True)


def _commit(path: Path, filename: str, content: str, message: str) -> None:
    (path / filename).write_text(content)
    subprocess.run(["git", "-C", str(path), "add", filename], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-m", message], capture_output=True, check=True)


class TestGitIndex:
    def test_indexes_git_log_to_sqlite(self, tmp_path: Path) -> None:
        db_path = tmp_path / "index.db"
        git_dir = tmp_path / "repo"
        git_dir.mkdir()
        _init_git_repo(git_dir)
        _commit(git_dir, "a.py", "x=1", "first commit")

        count = index_git_history(repo_path=str(git_dir), db_path=str(db_path))
        assert count == 1

        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT hash, author, message FROM commits").fetchall()
        assert len(rows) == 1
        assert rows[0][2] == "first commit"
        conn.close()

    def test_creates_database_if_missing(self, tmp_path: Path) -> None:
        db_path = tmp_path / "does" / "not" / "exist" / "index.db"
        git_dir = tmp_path / "repo"
        git_dir.mkdir()
        _init_git_repo(git_dir)
        _commit(git_dir, "f.py", "x=1", "init")

        assert not db_path.exists()
        index_git_history(repo_path=str(git_dir), db_path=str(db_path))
        assert db_path.exists()

    def test_increments_existing_index(self, tmp_path: Path) -> None:
        db_path = tmp_path / "index.db"
        git_dir = tmp_path / "repo"
        git_dir.mkdir()
        _init_git_repo(git_dir)
        _commit(git_dir, "a.py", "x=1", "first")

        index_git_history(repo_path=str(git_dir), db_path=str(db_path))

        _commit(git_dir, "b.py", "x=2", "second")
        count = index_git_history(repo_path=str(git_dir), db_path=str(db_path))
        assert count == 2

    def test_indexes_author_date_message_sha(self, tmp_path: Path) -> None:
        db_path = tmp_path / "index.db"
        git_dir = tmp_path / "repo"
        git_dir.mkdir()
        _init_git_repo(git_dir, email="alice@x.com", name="Alice")
        _commit(git_dir, "f.py", "x=1", "fix security bug")

        index_git_history(repo_path=str(git_dir), db_path=str(db_path))

        conn = sqlite3.connect(str(db_path))
        row = conn.execute("SELECT hash, author, date, message FROM commits").fetchone()
        assert row is not None
        assert len(row[0]) == 40
        assert row[1] == "Alice"
        assert row[2].startswith("20")
        assert row[3] == "fix security bug"
        conn.close()

    def test_handles_empty_repo_gracefully(self, tmp_path: Path) -> None:
        db_path = tmp_path / "index.db"
        git_dir = tmp_path / "repo"
        git_dir.mkdir()
        _init_git_repo(git_dir)

        count = index_git_history(repo_path=str(git_dir), db_path=str(db_path))
        assert count == 0


class TestGitSearch:
    def test_searches_commit_messages(self, tmp_path: Path) -> None:
        db_path = tmp_path / "index.db"
        git_dir = tmp_path / "repo"
        git_dir.mkdir()
        _init_git_repo(git_dir)
        _commit(git_dir, "a.py", "x=1", "fix security bug")
        _commit(git_dir, "b.py", "x=2", "add feature")
        index_git_history(repo_path=str(git_dir), db_path=str(db_path))

        results = search_git_history(query="security", db_path=str(db_path), repo_path=str(git_dir))
        assert len(results) >= 1
        assert any("security" in r["message"] for r in results)

    def test_searches_author_names(self, tmp_path: Path) -> None:
        db_path = tmp_path / "index.db"
        git_dir = tmp_path / "repo"
        git_dir.mkdir()
        _init_git_repo(git_dir, email="alice@x.com", name="Alice")
        _commit(git_dir, "a.py", "x=1", "commit by alice")
        index_git_history(repo_path=str(git_dir), db_path=str(db_path))

        results = search_git_history(author="Alice", db_path=str(db_path), repo_path=str(git_dir))
        assert len(results) >= 1
        assert results[0]["author"] == "Alice"

    def test_returns_empty_for_no_matches(self, tmp_path: Path) -> None:
        db_path = tmp_path / "index.db"
        git_dir = tmp_path / "repo"
        git_dir.mkdir()
        _init_git_repo(git_dir)
        _commit(git_dir, "a.py", "x=1", "init")
        index_git_history(repo_path=str(git_dir), db_path=str(db_path))

        results = search_git_history(query="nonexistent", db_path=str(db_path), repo_path=str(git_dir))
        assert results == []

    def test_supports_case_insensitive_search(self, tmp_path: Path) -> None:
        db_path = tmp_path / "index.db"
        git_dir = tmp_path / "repo"
        git_dir.mkdir()
        _init_git_repo(git_dir)
        _commit(git_dir, "a.py", "x=1", "Fix UPPERCASE Bug")
        _commit(git_dir, "b.py", "x=2", "lowercase thing")
        index_git_history(repo_path=str(git_dir), db_path=str(db_path))

        upper = search_git_history(query="UPPERCASE", db_path=str(db_path), repo_path=str(git_dir))
        assert len(upper) >= 1

        lower = search_git_history(query="lowercase", db_path=str(db_path), repo_path=str(git_dir))
        assert len(lower) >= 1

    def test_returns_sha_and_message_for_matches(self, tmp_path: Path) -> None:
        db_path = tmp_path / "index.db"
        git_dir = tmp_path / "repo"
        git_dir.mkdir()
        _init_git_repo(git_dir)
        _commit(git_dir, "a.py", "x=1", "fix bug")
        commit_sha = subprocess.run(
            ["git", "-C", str(git_dir), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        index_git_history(repo_path=str(git_dir), db_path=str(db_path))

        results = search_git_history(query="fix", db_path=str(db_path), repo_path=str(git_dir))
        assert len(results) >= 1
        assert results[0]["hash"] == commit_sha
        assert "fix" in results[0]["message"]


class TestGitStats:
    def test_reports_total_commit_count(self, tmp_path: Path) -> None:
        db_path = tmp_path / "index.db"
        git_dir = tmp_path / "repo"
        git_dir.mkdir()
        _init_git_repo(git_dir)
        _commit(git_dir, "a.py", "x=1", "c1")
        _commit(git_dir, "b.py", "x=2", "c2")
        _commit(git_dir, "c.py", "x=3", "c3")
        index_git_history(repo_path=str(git_dir), db_path=str(db_path))

        stats = get_git_stats(repo_path=str(git_dir), db_path=str(db_path))
        assert stats["total_commits"] == 3

    def test_reports_authors_list(self, tmp_path: Path) -> None:
        db_path = tmp_path / "index.db"
        git_dir = tmp_path / "repo"
        git_dir.mkdir()
        _init_git_repo(git_dir, email="alice@x.com", name="Alice")
        _commit(git_dir, "a.py", "x=1", "c1")
        index_git_history(repo_path=str(git_dir), db_path=str(db_path))

        stats = get_git_stats(repo_path=str(git_dir), db_path=str(db_path))
        assert "authors" in stats
        assert "Alice" in stats["authors"]

    def test_reports_date_range(self, tmp_path: Path) -> None:
        db_path = tmp_path / "index.db"
        git_dir = tmp_path / "repo"
        git_dir.mkdir()
        _init_git_repo(git_dir)
        _commit(git_dir, "a.py", "x=1", "c1")
        index_git_history(repo_path=str(git_dir), db_path=str(db_path))

        stats = get_git_stats(repo_path=str(git_dir), db_path=str(db_path))
        assert "date_range" in stats
        dr = stats["date_range"]
        assert isinstance(dr, dict)
        assert "earliest" in dr
        assert "latest" in dr
        assert dr["earliest"] != ""
        assert dr["latest"] != ""

    def test_reports_index_size(self, tmp_path: Path) -> None:
        db_path = tmp_path / "index.db"
        git_dir = tmp_path / "repo"
        git_dir.mkdir()
        _init_git_repo(git_dir)
        _commit(git_dir, "a.py", "x=1", "c1")
        index_git_history(repo_path=str(git_dir), db_path=str(db_path))

        stats = get_git_stats(repo_path=str(git_dir), db_path=str(db_path))
        assert "db_size_bytes" in stats
        assert stats["db_size_bytes"] > 0
        assert stats["db_size_bytes"] == os.path.getsize(db_path)

    def test_works_without_index_just_in_memory(self, tmp_path: Path) -> None:
        git_dir = tmp_path / "repo"
        git_dir.mkdir()
        _init_git_repo(git_dir)
        _commit(git_dir, "a.py", "x=1", "test commit")

        log = git_log(repo_path=str(git_dir), limit=5)
        assert len(log) >= 1
        assert log[0]["message"] == "test commit"


class TestGitQuery:
    def test_git_log_returns_recent_commits(self, tmp_path: Path) -> None:
        git_dir = tmp_path / "repo"
        git_dir.mkdir()
        _init_git_repo(git_dir)
        _commit(git_dir, "a.py", "x=1", "first")
        _commit(git_dir, "b.py", "x=2", "second")
        _commit(git_dir, "c.py", "x=3", "third")

        log = git_log(repo_path=str(git_dir), limit=10)
        assert len(log) == 3
        assert log[0]["message"] == "third"
        assert log[1]["message"] == "second"
        assert log[2]["message"] == "first"

        limited = git_log(repo_path=str(git_dir), limit=2)
        assert len(limited) == 2

    def test_git_show_returns_last_commit_diff(self, tmp_path: Path) -> None:
        git_dir = tmp_path / "repo"
        git_dir.mkdir()
        _init_git_repo(git_dir)
        _commit(git_dir, "a.py", "x=1", "test commit")

        result = git_show(repo_path=str(git_dir))
        assert "output" in result
        assert "test commit" in result["output"]
        assert result["sha"] == "HEAD"

    def test_git_diff_returns_statistics(self, tmp_path: Path) -> None:
        git_dir = tmp_path / "repo"
        git_dir.mkdir()
        _init_git_repo(git_dir)
        _commit(git_dir, "a.py", "x=1", "init")
        (git_dir / "a.py").write_text("x=1\ny=2")
        subprocess.run(["git", "-C", str(git_dir), "add", "a.py"], capture_output=True, check=True)

        result = git_diff(repo_path=str(git_dir))
        assert "output" in result

    def test_all_accept_limit_parameter(self, tmp_path: Path) -> None:
        git_dir = tmp_path / "repo"
        git_dir.mkdir()
        _init_git_repo(git_dir)
        for i in range(5):
            _commit(git_dir, f"f{i}.py", f"x={i}", f"commit {i}")

        log_all = git_log(repo_path=str(git_dir), limit=10)
        assert len(log_all) == 5

        log_2 = git_log(repo_path=str(git_dir), limit=2)
        assert len(log_2) == 2
