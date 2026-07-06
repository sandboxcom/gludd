"""
tests/unit/test_readme_status_gate.py

TDD tests for scripts/check_readme_status_current.py.

Run with:
    make test-unit TESTFILE='tests/unit/test_readme_status_gate.py'
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, cast

import pytest

# ---------------------------------------------------------------------------
# Helpers — load the script as a module without running main()
# ---------------------------------------------------------------------------

_SCRIPT_PATH = Path(__file__).resolve().parent.parent.parent / "scripts" / "check_readme_status_current.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_readme_status_current", _SCRIPT_PATH)
    mod = cast(Any, importlib.util).module_from_spec(spec)
    cast(Any, spec.loader).exec_module(mod)
    return mod


_mod = _load_module()


# ---------------------------------------------------------------------------
# Unit tests for helpers
# ---------------------------------------------------------------------------


class TestNormalize:
    def test_strips_leading_v(self):
        assert _mod._normalize("v0.1.0-alpha.2") == "0.1.0-alpha.2"

    def test_already_stripped(self):
        assert _mod._normalize("0.1.0-alpha.2") == "0.1.0-alpha.2"

    def test_lowercases(self):
        assert _mod._normalize("V0.1.0-ALPHA.2") == "0.1.0-alpha.2"

    def test_strips_whitespace(self):
        assert _mod._normalize("  v1.2.3  ") == "1.2.3"


class TestReadPyprojectVersion:
    def test_reads_version(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "foo"\nversion = "0.1.0-alpha.2"\n'
        )
        assert _mod._read_pyproject_version(tmp_path) == "0.1.0-alpha.2"

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            _mod._read_pyproject_version(tmp_path)

    def test_missing_version_key_raises(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "foo"\n')
        with pytest.raises(ValueError, match="Could not find"):
            _mod._read_pyproject_version(tmp_path)


class TestFindReadmeStatusLine:
    def test_finds_bold_markdown_line(self, tmp_path):
        (tmp_path / "README.md").write_text(
            "## Features\n\n**Status as of v0.1.0-alpha.2 — 2026-06-18**\n"
        )
        assert _mod._find_readme_status_line(tmp_path) == "v0.1.0-alpha.2"

    def test_finds_plain_line(self, tmp_path):
        (tmp_path / "README.md").write_text("Status as of 0.1.0-alpha.2\n")
        assert _mod._find_readme_status_line(tmp_path) == "0.1.0-alpha.2"

    def test_returns_none_when_absent(self, tmp_path):
        (tmp_path / "README.md").write_text("# Project\nNo status line here.\n")
        assert _mod._find_readme_status_line(tmp_path) is None

    def test_missing_readme_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            _mod._find_readme_status_line(tmp_path)


# ---------------------------------------------------------------------------
# Integration tests — call main() end-to-end via a monkeypatched repo root
# ---------------------------------------------------------------------------


def _make_repo(tmp_path: Path, pyproject_version: str, readme_content: str) -> Path:
    """Scaffold a minimal fake repo root."""
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "general-ludd-agent"\nversion = "{pyproject_version}"\n'
    )
    (tmp_path / "README.md").write_text(readme_content)
    return tmp_path


class TestMainExitCodes:
    """main() must exit 0 on match, non-zero on mismatch or missing line."""

    def _run(self, tmp_path: Path, argv: list[str], monkeypatch) -> int:
        """Call main() with a monkeypatched repo root and sys.argv."""
        # Patch the two helper functions so main() reads from tmp_path instead of
        # the real repo root. Capture the ORIGINAL implementations first — otherwise
        # the replacement lambda would resolve _mod._read_pyproject_version to itself
        # at call time (it has just been reassigned), causing infinite recursion.
        orig_read_pyproject = _mod._read_pyproject_version
        orig_find_status_line = _mod._find_readme_status_line
        monkeypatch.setattr(
            _mod, "_read_pyproject_version",
            lambda _root: orig_read_pyproject(tmp_path),
        )
        monkeypatch.setattr(
            _mod, "_find_readme_status_line",
            lambda _root: orig_find_status_line(tmp_path),
        )
        monkeypatch.setattr(sys, "argv", ["check_readme_status_current.py", *argv])
        return _mod.main()

    def test_exit_0_on_exact_match_with_v_prefix(self, tmp_path, monkeypatch):
        _make_repo(
            tmp_path,
            pyproject_version="0.1.0-alpha.2",
            readme_content="**Status as of v0.1.0-alpha.2 — 2026-06-18**\n",
        )
        rc = self._run(tmp_path, [], monkeypatch)
        assert rc == 0, "Should exit 0 when README version matches pyproject (v-prefix normalised)"

    def test_exit_0_on_tag_arg_match(self, tmp_path, monkeypatch):
        _make_repo(
            tmp_path,
            pyproject_version="0.1.0-alpha.999",
            readme_content="Status as of v0.1.0-alpha.2\n",
        )
        rc = self._run(tmp_path, ["v0.1.0-alpha.2"], monkeypatch)
        assert rc == 0, "TAG arg 'v0.1.0-alpha.2' should match README 'v0.1.0-alpha.2'"

    def test_exit_nonzero_on_version_mismatch(self, tmp_path, monkeypatch):
        _make_repo(
            tmp_path,
            pyproject_version="0.1.0-alpha.2",
            readme_content="**Status as of v0.1.0-alpha.1 — 2026-06-01**\n",
        )
        rc = self._run(tmp_path, [], monkeypatch)
        assert rc != 0, "Should exit non-zero when README version is stale (alpha.1 != alpha.2)"

    def test_exit_nonzero_on_missing_status_line(self, tmp_path, monkeypatch):
        _make_repo(
            tmp_path,
            pyproject_version="0.1.0-alpha.2",
            readme_content="# Project\nNo status table here.\n",
        )
        rc = self._run(tmp_path, [], monkeypatch)
        assert rc != 0, "Should exit non-zero when README has no 'Status as of' line"

    def test_exit_nonzero_on_tag_arg_mismatch(self, tmp_path, monkeypatch):
        _make_repo(
            tmp_path,
            pyproject_version="0.1.0-alpha.2",
            readme_content="**Status as of v0.1.0-alpha.1 — old**\n",
        )
        rc = self._run(tmp_path, ["v0.1.0-alpha.2"], monkeypatch)
        assert rc != 0, "TAG arg alpha.2 should not match README alpha.1"

    def test_exit_0_when_readme_has_no_v_prefix_but_pyproject_matches(
        self, tmp_path, monkeypatch
    ):
        _make_repo(
            tmp_path,
            pyproject_version="0.1.0-alpha.2",
            readme_content="Status as of 0.1.0-alpha.2 (released)\n",
        )
        rc = self._run(tmp_path, [], monkeypatch)
        assert rc == 0, "Should exit 0 — normalised comparison: both become '0.1.0-alpha.2'"
