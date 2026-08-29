from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from general_ludd.history.git_indexer import (
    CommitRecord,
    FileChange,
    GitHistoryIndexer,
    SearchResult,
    search_history,
)


class TestGitHistoryIndexer:
    def test_init_creates_db_directory(self, tmp_path: Path) -> None:
        db_path = tmp_path / "sub" / "index.db"
        GitHistoryIndexer(repo_path=tmp_path, db_path=db_path)
        assert db_path.parent.exists()

    def test_schema_creates_tables(self, tmp_path: Path) -> None:
        db_path = tmp_path / "index.db"
        indexer = GitHistoryIndexer(repo_path=tmp_path, db_path=db_path)
        conn = indexer._get_conn()
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        table_names = {t[0] for t in tables}
        assert "commits" in table_names
        assert "files_changed" in table_names
        conn.close()

    def test_schema_creates_indexes(self, tmp_path: Path) -> None:
        db_path = tmp_path / "index.db"
        indexer = GitHistoryIndexer(repo_path=tmp_path, db_path=db_path)
        conn = indexer._get_conn()
        indexes = conn.execute("SELECT name FROM sqlite_master WHERE type='index' ORDER BY name").fetchall()
        idx_names = {i[0] for i in indexes}
        assert "idx_commits_date" in idx_names
        assert "idx_commits_author" in idx_names
        assert "idx_files_changed_path" in idx_names
        assert "idx_files_changed_commit" in idx_names
        conn.close()

    def test_index_inserts_commits(self, tmp_path: Path) -> None:
        db_path = tmp_path / "index.db"
        git_dir = tmp_path / "repo"
        git_dir.mkdir()
        subprocess.run(["git", "-C", str(git_dir), "init"], capture_output=True)
        subprocess.run(["git", "-C", str(git_dir), "config", "user.email", "test@test.com"], capture_output=True)
        subprocess.run(["git", "-C", str(git_dir), "config", "user.name", "Test"], capture_output=True)
        (git_dir / "a.txt").write_text("hello")
        subprocess.run(["git", "-C", str(git_dir), "add", "a.txt"], capture_output=True)
        subprocess.run(["git", "-C", str(git_dir), "commit", "-m", "first commit"], capture_output=True)

        indexer = GitHistoryIndexer(repo_path=git_dir, db_path=db_path)
        count = indexer.index()
        assert count >= 1

        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT hash, author, message FROM commits").fetchall()
        assert len(rows) >= 1
        assert rows[0][2] == "first commit"
        conn.close()

    def test_index_inserts_files_changed(self, tmp_path: Path) -> None:
        db_path = tmp_path / "index.db"
        git_dir = tmp_path / "repo"
        git_dir.mkdir()
        subprocess.run(["git", "-C", str(git_dir), "init"], capture_output=True)
        subprocess.run(["git", "-C", str(git_dir), "config", "user.email", "t@t.com"], capture_output=True)
        subprocess.run(["git", "-C", str(git_dir), "config", "user.name", "T"], capture_output=True)
        (git_dir / "x.py").write_text("x=1")
        subprocess.run(["git", "-C", str(git_dir), "add", "x.py"], capture_output=True)
        subprocess.run(["git", "-C", str(git_dir), "commit", "-m", "add x"], capture_output=True)

        indexer = GitHistoryIndexer(repo_path=git_dir, db_path=db_path)
        indexer.index()

        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT path, change_type FROM files_changed").fetchall()
        assert len(rows) >= 1
        assert any(r[0] == "x.py" for r in rows)
        conn.close()

    def test_search_by_query_message(self, tmp_path: Path) -> None:
        db_path = tmp_path / "index.db"
        git_dir = tmp_path / "repo"
        git_dir.mkdir()
        subprocess.run(["git", "-C", str(git_dir), "init"], capture_output=True)
        subprocess.run(["git", "-C", str(git_dir), "config", "user.email", "t@t.com"], capture_output=True)
        subprocess.run(["git", "-C", str(git_dir), "config", "user.name", "T"], capture_output=True)
        (git_dir / "f.py").write_text("x=1")
        subprocess.run(["git", "-C", str(git_dir), "add", "f.py"], capture_output=True)
        subprocess.run(["git", "-C", str(git_dir), "commit", "-m", "fix security bug"], capture_output=True)
        (git_dir / "f.py").write_text("x=2")
        subprocess.run(["git", "-C", str(git_dir), "add", "f.py"], capture_output=True)
        subprocess.run(["git", "-C", str(git_dir), "commit", "-m", "add feature"], capture_output=True)

        indexer = GitHistoryIndexer(repo_path=git_dir, db_path=db_path)
        indexer.index()

        results = indexer.search(query="security")
        assert len(results) >= 1
        assert any("security" in r.message for r in results)

    def test_search_by_query_multiple_files_does_not_crash(self, tmp_path: Path) -> None:
        # Regression test: sqlite3.OperationalError: DISTINCT aggregates must
        # have exactly one argument. GROUP_CONCAT(DISTINCT f.path, '|') passes
        # TWO arguments (expr + separator) alongside DISTINCT, which sqlite
        # rejects outright -- any query-mode search() call with a commit that
        # touches >1 file hits this. Reproduces `make git-search Q=...`.
        db_path = tmp_path / "index.db"
        git_dir = tmp_path / "repo"
        git_dir.mkdir()
        subprocess.run(["git", "-C", str(git_dir), "init"], capture_output=True)
        subprocess.run(["git", "-C", str(git_dir), "config", "user.email", "t@t.com"], capture_output=True)
        subprocess.run(["git", "-C", str(git_dir), "config", "user.name", "T"], capture_output=True)
        (git_dir / "a.py").write_text("a=1")
        (git_dir / "b.py").write_text("b=1")
        subprocess.run(["git", "-C", str(git_dir), "add", "a.py", "b.py"], capture_output=True)
        subprocess.run(["git", "-C", str(git_dir), "commit", "-m", "RG_SHA256 checksum fix"], capture_output=True)

        indexer = GitHistoryIndexer(repo_path=git_dir, db_path=db_path)
        indexer.index()

        results = indexer.search(query="RG_SHA256")
        assert len(results) == 1
        assert set(results[0].matched_paths) == {"a.py", "b.py"}

    def test_search_by_author(self, tmp_path: Path) -> None:
        db_path = tmp_path / "index.db"
        git_dir = tmp_path / "repo"
        git_dir.mkdir()
        subprocess.run(["git", "-C", str(git_dir), "init"], capture_output=True)
        subprocess.run(["git", "-C", str(git_dir), "config", "user.email", "alice@x.com"], capture_output=True)
        subprocess.run(["git", "-C", str(git_dir), "config", "user.name", "Alice"], capture_output=True)
        (git_dir / "f.py").write_text("x=1")
        subprocess.run(["git", "-C", str(git_dir), "add", "f.py"], capture_output=True)
        subprocess.run(["git", "-C", str(git_dir), "commit", "-m", "commit by alice"], capture_output=True)

        indexer = GitHistoryIndexer(repo_path=git_dir, db_path=db_path)
        indexer.index()

        results = indexer.search(author="Alice")
        assert len(results) >= 1

    def test_search_by_path(self, tmp_path: Path) -> None:
        db_path = tmp_path / "index.db"
        git_dir = tmp_path / "repo"
        git_dir.mkdir()
        subprocess.run(["git", "-C", str(git_dir), "init"], capture_output=True)
        subprocess.run(["git", "-C", str(git_dir), "config", "user.email", "t@t.com"], capture_output=True)
        subprocess.run(["git", "-C", str(git_dir), "config", "user.name", "T"], capture_output=True)
        (git_dir / "daemon.py").write_text("x=1")
        subprocess.run(["git", "-C", str(git_dir), "add", "daemon.py"], capture_output=True)
        subprocess.run(["git", "-C", str(git_dir), "commit", "-m", "daemon change"], capture_output=True)

        indexer = GitHistoryIndexer(repo_path=git_dir, db_path=db_path)
        indexer.index()

        results = indexer.search(path_filter="daemon")
        assert len(results) >= 1

    def test_search_by_since(self, tmp_path: Path) -> None:
        db_path = tmp_path / "index.db"
        git_dir = tmp_path / "repo"
        git_dir.mkdir()
        subprocess.run(["git", "-C", str(git_dir), "init"], capture_output=True)
        subprocess.run(["git", "-C", str(git_dir), "config", "user.email", "t@t.com"], capture_output=True)
        subprocess.run(["git", "-C", str(git_dir), "config", "user.name", "T"], capture_output=True)
        (git_dir / "f.py").write_text("x=1")
        subprocess.run(["git", "-C", str(git_dir), "add", "f.py"], capture_output=True)
        subprocess.run(["git", "-C", str(git_dir), "commit", "-m", "old commit"], capture_output=True)

        indexer = GitHistoryIndexer(repo_path=git_dir, db_path=db_path)
        indexer.index()

        results = indexer.search(since="2000-01-01")
        assert len(results) >= 1

        results_future = indexer.search(since="2099-01-01")
        assert len(results_future) == 0

    def test_search_pagination(self, tmp_path: Path) -> None:
        db_path = tmp_path / "index.db"
        git_dir = tmp_path / "repo"
        git_dir.mkdir()
        subprocess.run(["git", "-C", str(git_dir), "init"], capture_output=True)
        subprocess.run(["git", "-C", str(git_dir), "config", "user.email", "t@t.com"], capture_output=True)
        subprocess.run(["git", "-C", str(git_dir), "config", "user.name", "T"], capture_output=True)
        for i in range(5):
            (git_dir / f"f{i}.py").write_text(f"x={i}")
            subprocess.run(["git", "-C", str(git_dir), "add", f"f{i}.py"], capture_output=True)
            subprocess.run(["git", "-C", str(git_dir), "commit", "-m", f"commit {i}"], capture_output=True)

        indexer = GitHistoryIndexer(repo_path=git_dir, db_path=db_path)
        indexer.index()

        assert len(indexer.search(limit=2)) == 2
        assert len(indexer.search(limit=100)) == 5
        page1 = indexer.search(limit=3, offset=0)
        page2 = indexer.search(limit=3, offset=3)
        assert len(page1) == 3
        assert len(page2) == 2
        hashes_page1 = {r.hash for r in page1}
        hashes_page2 = {r.hash for r in page2}
        assert not hashes_page1 & hashes_page2

    def test_stats(self, tmp_path: Path) -> None:
        db_path = tmp_path / "index.db"
        git_dir = tmp_path / "repo"
        git_dir.mkdir()
        subprocess.run(["git", "-C", str(git_dir), "init"], capture_output=True)
        subprocess.run(["git", "-C", str(git_dir), "config", "user.email", "t@t.com"], capture_output=True)
        subprocess.run(["git", "-C", str(git_dir), "config", "user.name", "T"], capture_output=True)
        (git_dir / "a.py").write_text("x=1")
        subprocess.run(["git", "-C", str(git_dir), "add", "a.py"], capture_output=True)
        subprocess.run(["git", "-C", str(git_dir), "commit", "-m", "init"], capture_output=True)

        indexer = GitHistoryIndexer(repo_path=git_dir, db_path=db_path)
        indexer.index()

        stats = indexer.stats()
        assert stats["total_commits"] >= 1
        assert isinstance(stats["last_indexed"], str)
        assert "last_indexed" in stats

    def test_empty_index_search_returns_empty(self, tmp_path: Path) -> None:
        db_path = tmp_path / "index.db"
        indexer = GitHistoryIndexer(repo_path=tmp_path, db_path=db_path)
        indexer.index()
        results = indexer.search(query="nonexistent")
        assert results == []

    def test_search_commit_without_changed_paths_returns_empty_path_list(self, tmp_path: Path) -> None:
        indexer = GitHistoryIndexer(tmp_path, tmp_path / "history.db")
        conn = indexer._get_conn()
        conn.execute(
            "INSERT INTO commits(hash, author, date, message, insertions, deletions) VALUES(?,?,?,?,?,?)",
            ("abc", "Ada", "2026-01-01", "metadata only", 0, 0),
        )
        conn.commit()
        conn.close()

        results = indexer.search(query="metadata")

        assert len(results) == 1
        assert results[0].matched_paths == []

    def test_index_non_git_repo_returns_zero_not_raises(self, tmp_path: Path) -> None:
        # Regression: a non-repo path has ZERO commits to index -- an empty
        # result, not a crash. Previously `git log` exited non-zero and
        # _parse_git_log raised RuntimeError("git log failed: fatal: not a git
        # repository ...").
        db_path = tmp_path / "index.db"
        not_a_repo = tmp_path / "plain_dir"
        not_a_repo.mkdir()
        indexer = GitHistoryIndexer(repo_path=not_a_repo, db_path=db_path)
        assert indexer.index() == 0
        assert indexer.search() == []

    def test_newest_commit_fields_are_not_shifted(self, tmp_path: Path) -> None:
        # Regression: stdout STARTS with "__COMMIT__\n", but the split delimiter
        # is "\n__COMMIT__\n", so the first/newest block kept its own marker
        # line and every field shifted by one -- hash='__COMMIT__', author=<sha>,
        # date='T', message=<iso-date>. Assert the NEWEST commit parses cleanly.
        db_path = tmp_path / "index.db"
        git_dir = tmp_path / "repo"
        git_dir.mkdir()
        subprocess.run(["git", "-C", str(git_dir), "init"], capture_output=True)
        subprocess.run(["git", "-C", str(git_dir), "config", "user.email", "t@t.com"], capture_output=True)
        subprocess.run(["git", "-C", str(git_dir), "config", "user.name", "Zoe"], capture_output=True)
        (git_dir / "old.py").write_text("old=1")
        subprocess.run(["git", "-C", str(git_dir), "add", "old.py"], capture_output=True)
        subprocess.run(["git", "-C", str(git_dir), "commit", "-m", "older commit"], capture_output=True)
        (git_dir / "new.py").write_text("new=1")
        subprocess.run(["git", "-C", str(git_dir), "add", "new.py"], capture_output=True)
        subprocess.run(["git", "-C", str(git_dir), "commit", "-m", "newest commit"], capture_output=True)

        indexer = GitHistoryIndexer(repo_path=git_dir, db_path=db_path)
        indexer.index()

        # Assert NO record is corrupted, rather than indexing by sort order:
        # both commits can land in the same second, so `ORDER BY date DESC`
        # ties and limit=1 is nondeterministic. Pre-fix, exactly one record
        # (the newest block) came back mangled, so this is the stronger check.
        results = indexer.search()
        assert len(results) == 2
        for r in results:
            assert r.hash != "__COMMIT__"
            assert len(r.hash) == 40
            assert r.author == "Zoe"
            assert r.date.startswith("20")
        assert {r.message for r in results} == {"older commit", "newest commit"}
        by_message = {r.message: r for r in results}
        assert by_message["newest commit"].matched_paths == ["new.py"]
        assert by_message["older commit"].matched_paths == ["old.py"]

    def test_reindex_is_idempotent(self, tmp_path: Path) -> None:
        db_path = tmp_path / "index.db"
        git_dir = tmp_path / "repo"
        git_dir.mkdir()
        subprocess.run(["git", "-C", str(git_dir), "init"], capture_output=True)
        subprocess.run(["git", "-C", str(git_dir), "config", "user.email", "t@t.com"], capture_output=True)
        subprocess.run(["git", "-C", str(git_dir), "config", "user.name", "T"], capture_output=True)
        (git_dir / "f.py").write_text("x=1")
        subprocess.run(["git", "-C", str(git_dir), "add", "f.py"], capture_output=True)
        subprocess.run(["git", "-C", str(git_dir), "commit", "-m", "init"], capture_output=True)

        indexer = GitHistoryIndexer(repo_path=git_dir, db_path=db_path)
        c1 = indexer.index()
        c2 = indexer.index()
        assert c1 == c2

    def test_parse_git_log_handles_binary_rename_and_malformed_blocks(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        stdout = (
            "__COMMIT__\nabc\nAda\n2026-01-01T00:00:00Z\nmessage\n"
            "-\t2\tbinary.dat\n1\t-\tone.py\nold => new\n"
            "__COMMIT__\nshort"
        )
        monkeypatch.setattr(
            "general_ludd.history.git_indexer.subprocess.run",
            lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout=stdout, stderr=""),
        )

        commits = list(GitHistoryIndexer(tmp_path, tmp_path / "history.db")._parse_git_log())

        assert len(commits) == 1
        assert commits[0].insertions == 1
        assert commits[0].deletions == 2
        assert [change.change_type for change in commits[0].files] == ["M", "M", "R"]

    def test_parse_git_log_raises_for_non_benign_failure(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "general_ludd.history.git_indexer.subprocess.run",
            lambda *_args, **_kwargs: SimpleNamespace(returncode=2, stdout="", stderr="permission denied"),
        )

        with pytest.raises(RuntimeError, match="permission denied"):
            list(GitHistoryIndexer(tmp_path, tmp_path / "history.db")._parse_git_log())

    def test_module_level_search_history(self, tmp_path: Path) -> None:
        db_path = tmp_path / "index.db"
        git_dir = tmp_path / "repo"
        git_dir.mkdir()
        subprocess.run(["git", "-C", str(git_dir), "init"], capture_output=True)
        subprocess.run(["git", "-C", str(git_dir), "config", "user.email", "t@t.com"], capture_output=True)
        subprocess.run(["git", "-C", str(git_dir), "config", "user.name", "T"], capture_output=True)
        (git_dir / "f.py").write_text("x=1")
        subprocess.run(["git", "-C", str(git_dir), "add", "f.py"], capture_output=True)
        subprocess.run(["git", "-C", str(git_dir), "commit", "-m", "hello world"], capture_output=True)

        GitHistoryIndexer(repo_path=git_dir, db_path=db_path).index()

        results = search_history(db_path=db_path, query="hello")
        assert len(results) >= 1
        assert any("hello" in r.message.lower() for r in results)


class TestSearchResult:
    def test_to_dict(self) -> None:
        sr = SearchResult(
            hash="abc123",
            author="Alice",
            date="2025-01-01",
            message="fix bug",
            insertions=10,
            deletions=3,
            matched_paths=["src/foo.py"],
        )
        d = sr.to_dict()
        assert d["hash"] == "abc123"
        assert d["author"] == "Alice"
        assert d["insertions"] == 10
        assert d["deletions"] == 3
        assert d["matched_paths"] == ["src/foo.py"]

    def test_to_dict_empty_paths(self) -> None:
        sr = SearchResult(
            hash="def456",
            author="Bob",
            date="2025-02-01",
            message="init",
            insertions=0,
            deletions=0,
        )
        d = sr.to_dict()
        assert d["matched_paths"] == []


class TestCommitRecord:
    def test_default_files_empty(self) -> None:
        cr = CommitRecord(hash="x", author="a", date="d", message="m", insertions=0, deletions=0)
        assert cr.files == []

    def test_with_files(self) -> None:
        cr = CommitRecord(
            hash="x",
            author="a",
            date="d",
            message="m",
            insertions=5,
            deletions=2,
            files=[FileChange(path="f.py", change_type="M")],
        )
        assert len(cr.files) == 1
        assert cr.files[0].path == "f.py"
        assert cr.files[0].change_type == "M"
