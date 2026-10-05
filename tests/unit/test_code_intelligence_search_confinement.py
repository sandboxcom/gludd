from __future__ import annotations

import tempfile
from pathlib import Path
from unittest import mock

from general_ludd.code_intelligence.rg_search import RgSearch


class TestRootConfinement:
    def test_default_cwd_allowed(self) -> None:
        searcher = RgSearch(rg_path="echo")
        result = searcher._validate_root(".")
        assert isinstance(result, str)
        assert result == str(Path.cwd().resolve())

    def test_subdirectory_of_cwd_allowed(self) -> None:
        searcher = RgSearch(rg_path="echo")
        result = searcher._validate_root(str(Path.cwd() / "src"))
        assert isinstance(result, str)
        assert result == str((Path.cwd() / "src").resolve())

    def test_path_outside_cwd_denied(self) -> None:
        searcher = RgSearch(rg_path="echo")
        result = searcher._validate_root("/etc")
        assert result is not None
        assert not result.available
        assert "outside allowed directories" in (result.error or "")

    def test_dotdot_traversal_outside_denied(self) -> None:
        searcher = RgSearch(rg_path="echo")
        path = Path.cwd()
        if str(path) == "/":
            return
        result = searcher._validate_root(str(path / ".." / ".."))
        assert result is not None
        assert not result.available

    def test_custom_allowed_roots_allows_only_listed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            allowed = tmpdir
            inside = Path(tmpdir) / "sub"
            inside.mkdir()
            searcher = RgSearch(rg_path="echo", allowed_roots=[allowed])

            assert searcher._validate_root(str(inside)) == str(inside.resolve())
            assert searcher._validate_root("/etc") is not None

    def test_protected_path_denied(self) -> None:
        searcher = RgSearch(rg_path="echo")
        protected = Path.cwd() / ".opencode"
        result = searcher._validate_root(str(protected))
        assert result is not None
        assert not result.available
        assert "denied" in (result.error or "").lower()

    def test_nonexistent_path_resolves_and_denied(self) -> None:
        searcher = RgSearch(rg_path="echo")
        result = searcher._validate_root("/nonexistent_dir_xyz_12345")
        assert result is not None
        assert not result.available

    def test_relative_path_within_cwd_allowed(self) -> None:
        searcher = RgSearch(rg_path="echo")
        result = searcher._validate_root("src")
        assert isinstance(result, str)
        assert result == str((Path.cwd() / "src").resolve())

    def test_no_allowed_roots_defaults_to_cwd(self) -> None:
        searcher = RgSearch(rg_path="echo")
        result = searcher._validate_root(str(Path.cwd()))
        assert isinstance(result, str)
        assert result == str(Path.cwd().resolve())

    def test_oserror_from_resolve_returns_error(self) -> None:
        searcher = RgSearch(rg_path="echo")
        with mock.patch("pathlib.Path.resolve", side_effect=OSError("bad path")):
            result = searcher._validate_root("anything")
        assert result is not None
        assert not result.available
        assert "Cannot resolve" in (result.error or "")

    def test_search_uses_confinement(self) -> None:
        searcher = RgSearch(rg_path="echo")
        result = searcher.search("pattern", root="/etc")
        assert not result.available
        assert "outside allowed directories" in (result.error or "")

    def test_search_with_allowed_root_succeeds_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            searcher = RgSearch(rg_path="nonexistent_rg_xyz", allowed_roots=[tmpdir])
            result = searcher.search("pattern", root=tmpdir)
            assert not result.available
            assert "nonexistent_rg_xyz" in (result.error or "")
