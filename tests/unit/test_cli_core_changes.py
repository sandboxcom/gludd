"""Unit tests for cli_core_changes utility functions."""

from __future__ import annotations

from general_ludd.cli_core_changes import (
    _as_text,
    _excluded,
    classify,
)


class TestClassify:
    def test_src_general_ludd_is_core(self):
        result = classify("/home/user/project/src/general_ludd/routers/x.py")
        assert result == "core"

    def test_src_general_ludd_subdir_is_core(self):
        result = classify("/worktree/src/general_ludd/daemon.py")
        assert result == "core"

    def test_src_general_ludd_suffix_not_core(self):
        result = classify("/path/src/general_ludd_notes/x.py")
        assert result == "user"

    def test_random_path_is_user(self):
        result = classify("/home/user/my_project/config.yml")
        assert result == "user"

    def test_empty_path_is_user(self):
        result = classify("")
        assert result == "user"


class TestExcluded:
    def test_pyc_is_excluded(self):
        assert _excluded("module.cpython-311.pyc") is True

    def test_git_is_excluded(self):
        assert _excluded(".git/objects/ab/cdef1234") is True

    def test_py_is_not_excluded(self):
        assert _excluded("src/general_ludd/daemon.py") is False

    def test_db_file_is_excluded(self):
        assert _excluded("src/general_ludd/db/bucket.db") is True


class TestAsText:
    def test_string_is_identity(self):
        assert _as_text("hello") == "hello"

    def test_bytes_is_decoded(self):
        assert _as_text(b"hello bytes") == "hello bytes"

    def test_none_is_empty(self):
        assert _as_text(None) == ""

    def test_binary_bytes_are_replaced(self):
        result = _as_text(b"\xff\xfeinvalid")
        assert "invalid" in result
