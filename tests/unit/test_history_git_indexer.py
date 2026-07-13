"""Unit tests for history/git_indexer.py — git history indexing and search data structures."""

from __future__ import annotations

import tempfile
from pathlib import Path

from general_ludd.history.git_indexer import (
    CommitRecord,
    FileChange,
    GitHistoryIndexer,
    SearchResult,
    _normalize_date,
    search_history,
)


class TestCommitRecord:
    def test_construction(self) -> None:
        cr = CommitRecord(
            hash="abc123",
            author="test",
            date="2025-01-01T00:00:00+00:00",
            message="test commit",
            insertions=10,
            deletions=5,
        )
        assert cr.hash == "abc123"
        assert cr.insertions == 10
        assert cr.deletions == 5

    def test_default_files_list(self) -> None:
        cr = CommitRecord(
            hash="abc123",
            author="test",
            date="2025-01-01",
            message="msg",
            insertions=0,
            deletions=0,
        )
        assert cr.files == []

    def test_with_file_changes(self) -> None:
        fc = FileChange(path="src/foo.py", change_type="M")
        cr = CommitRecord(
            hash="abc123",
            author="test",
            date="2025-01-01",
            message="msg",
            insertions=5,
            deletions=3,
            files=[fc],
        )
        assert len(cr.files) == 1
        assert cr.files[0].path == "src/foo.py"


class TestFileChange:
    def test_construction(self) -> None:
        fc = FileChange(path="src/bar.py", change_type="A")
        assert fc.path == "src/bar.py"
        assert fc.change_type == "A"

    def test_modified_type(self) -> None:
        fc = FileChange(path="src/baz.py", change_type="M")
        assert fc.change_type == "M"

    def test_renamed_type(self) -> None:
        fc = FileChange(path="old => new", change_type="R")
        assert fc.change_type == "R"


class TestSearchResult:
    def test_construction(self) -> None:
        sr = SearchResult(
            hash="def456",
            author="dev",
            date="2025-06-01",
            message="fix bug",
            insertions=1,
            deletions=1,
        )
        assert sr.hash == "def456"
        assert sr.matched_paths == []

    def test_to_dict(self) -> None:
        sr = SearchResult(
            hash="def456",
            author="dev",
            date="2025-06-01",
            message="fix bug",
            insertions=1,
            deletions=1,
            matched_paths=["a.py", "b.py"],
        )
        d = sr.to_dict()
        assert d["hash"] == "def456"
        assert d["matched_paths"] == ["a.py", "b.py"]
        assert "insertions" in d


class TestGitHistoryIndexer:
    def test_init_creates_db_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "subdir" / "git_history.db"
            indexer = GitHistoryIndexer(repo_path=tmp, db_path=db_path)
            assert db_path.parent.exists()
            assert indexer.repo_path == Path(tmp).resolve()

    def test_get_conn_creates_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            indexer = GitHistoryIndexer(repo_path=tmp, db_path=db_path)
            conn = indexer._get_conn()
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            table_names = {t[0] for t in tables}
            assert "commits" in table_names
            assert "files_changed" in table_names
            conn.close()

    def test_stats_empty_db(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            indexer = GitHistoryIndexer(repo_path=tmp, db_path=db_path)
            stats = indexer.stats()
            assert stats["total_commits"] == 0
            assert stats["unique_files"] == 0

    def test_default_db_path(self) -> None:
        indexer = GitHistoryIndexer(repo_path=".")
        assert indexer.db_path == Path(".gludd/git_history.db")


class TestNormalizeDate:
    def test_iso_date_passthrough(self) -> None:
        iso = "2025-01-01T12:00:00+00:00"
        assert _normalize_date(iso) == iso

    def test_date_only_gets_timestamp_suffix(self) -> None:
        assert _normalize_date("2025-01-01") == "2025-01-01T00:00:00+00:00"


class TestSearchHistory:
    def test_function_is_callable(self) -> None:
        assert callable(search_history)
