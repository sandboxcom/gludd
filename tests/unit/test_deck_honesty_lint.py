# ruff: noqa: I001
"""
Unit tests for scripts/deck_honesty_lint.py.

Verifies:
- BANNED_TOKENS list is non-empty and all are strings
- lint_file catches banned marketing tokens
- lint_file catches percentage drift
- lint_file passes clean HTML
- lint_paths skips vendor/ directories
- _load_features returns a set of ints
- The built deck (if present) passes the honesty lint
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from deck_honesty_lint import (  # noqa: E402
    BANNED_TOKENS,
    _load_features,
    lint_file,
    lint_paths,
)


# ---------------------------------------------------------------------------
# BANNED_TOKENS sanity
# ---------------------------------------------------------------------------


class TestBannedTokens:
    def test_nonempty(self) -> None:
        assert len(BANNED_TOKENS) > 0

    def test_all_strings(self) -> None:
        assert all(isinstance(t, str) for t in BANNED_TOKENS)

    def test_known_tokens_present(self) -> None:
        tokens_lower = [t.lower() for t in BANNED_TOKENS]
        assert "seamless" in tokens_lower
        assert "blazing" in tokens_lower
        assert "enterprise-grade" in tokens_lower


# ---------------------------------------------------------------------------
# lint_file — banned token detection
# ---------------------------------------------------------------------------


class TestLintFileBannedTokens:
    def _write_html(self, tmp_path: Path, content: str) -> Path:
        p = tmp_path / "slide.html"
        p.write_text(content, encoding="utf-8")
        return p

    def test_detects_seamless(self, tmp_path: Path) -> None:
        html = "<p>This is a seamless experience.</p>"
        f = self._write_html(tmp_path, html)
        violations = lint_file(f, set())
        assert any("BANNED_TOKEN" in v and "seamless" in v for v in violations)

    def test_detects_blazing(self, tmp_path: Path) -> None:
        html = "<p>Blazing fast deployment.</p>"
        f = self._write_html(tmp_path, html)
        violations = lint_file(f, set())
        assert any("blazing" in v.lower() for v in violations)

    def test_case_insensitive(self, tmp_path: Path) -> None:
        html = "<p>SEAMLESS integration</p>"
        f = self._write_html(tmp_path, html)
        violations = lint_file(f, set())
        assert any("BANNED_TOKEN" in v for v in violations)

    def test_clean_html_no_violations(self, tmp_path: Path) -> None:
        html = "<p>Alpha software. Honest claims only.</p>"
        f = self._write_html(tmp_path, html)
        violations = lint_file(f, {100, 75, 0})
        assert violations == []

    def test_detects_enterprise_grade(self, tmp_path: Path) -> None:
        html = "<p>enterprise-grade solution</p>"
        f = self._write_html(tmp_path, html)
        violations = lint_file(f, set())
        assert any("BANNED_TOKEN" in v for v in violations)


# ---------------------------------------------------------------------------
# lint_file — percentage drift detection
# ---------------------------------------------------------------------------


class TestLintFilePercentageDrift:
    def _write_html(self, tmp_path: Path, content: str) -> Path:
        p = tmp_path / "slide.html"
        p.write_text(content, encoding="utf-8")
        return p

    def test_known_pct_no_violation(self, tmp_path: Path) -> None:
        html = "<p>Overall: 75% complete.</p>"
        f = self._write_html(tmp_path, html)
        violations = lint_file(f, {75})
        # 75 is in _STANDARD_PCTS so always passes regardless of readme_pcts
        assert violations == []

    def test_standard_pcts_always_pass(self, tmp_path: Path) -> None:
        # 0, 25, 50, 75, 100 are standard — always pass
        for pct in (0, 25, 50, 75, 100):
            html = f"<p>{pct}% shipped</p>"
            f = self._write_html(tmp_path, html)
            violations = lint_file(f, set())
            assert violations == [], f"{pct}% should not be flagged"

    def test_unusual_pct_not_in_readme_flagged(self, tmp_path: Path) -> None:
        html = "<p>73% of features done.</p>"
        f = self._write_html(tmp_path, html)
        # 73 is not standard and not in readme_pcts
        violations = lint_file(f, {100, 75, 50})
        assert any("PERCENTAGE_DRIFT" in v and "73%" in v for v in violations)

    def test_unusual_pct_in_readme_not_flagged(self, tmp_path: Path) -> None:
        html = "<p>73% of features done.</p>"
        f = self._write_html(tmp_path, html)
        violations = lint_file(f, {100, 75, 73})
        assert not any("73%" in v for v in violations)

    def test_slot_comment_lines_skipped(self, tmp_path: Path) -> None:
        html = "<!--SLOT:status-->73% complete<!--/SLOT-->"
        f = self._write_html(tmp_path, html)
        violations = lint_file(f, set())
        # Line contains SLOT marker — should be skipped
        assert not any("73%" in v for v in violations)

    def test_no_data_lines_skipped(self, tmp_path: Path) -> None:
        html = "<p>NO DATA -- run make deck-data</p> 73%"
        f = self._write_html(tmp_path, html)
        violations = lint_file(f, set())
        assert not any("73%" in v for v in violations)

    def test_html_comment_lines_skipped(self, tmp_path: Path) -> None:
        html = "<!-- 73% placeholder -->"
        f = self._write_html(tmp_path, html)
        violations = lint_file(f, set())
        assert violations == []


# ---------------------------------------------------------------------------
# lint_paths — directory traversal and vendor skip
# ---------------------------------------------------------------------------


class TestLintPaths:
    def test_lints_html_files_in_dir(self, tmp_path: Path) -> None:
        (tmp_path / "a.html").write_text("<p>Alpha software</p>", encoding="utf-8")
        (tmp_path / "b.html").write_text("<p>Also clean</p>", encoding="utf-8")
        violations = lint_paths(tmp_path, {100})
        assert violations == []

    def test_skips_vendor_dir(self, tmp_path: Path) -> None:
        vendor = tmp_path / "vendor"
        vendor.mkdir()
        (vendor / "reveal.html").write_text(
            "<p>seamless blazing enterprise-grade</p>", encoding="utf-8"
        )
        violations = lint_paths(tmp_path, set())
        assert violations == []

    def test_catches_violation_in_subdir(self, tmp_path: Path) -> None:
        sub = tmp_path / "slides"
        sub.mkdir()
        (sub / "bad.html").write_text("<p>seamless integration</p>", encoding="utf-8")
        violations = lint_paths(tmp_path, set())
        assert any("BANNED_TOKEN" in v for v in violations)

    def test_nonexistent_path_returns_skip(self, tmp_path: Path) -> None:
        # lint_paths is called only when path exists; if not, main() exits 0.
        # Verify lint_paths on a file path works
        p = tmp_path / "clean.html"
        p.write_text("<p>clean</p>", encoding="utf-8")
        violations = lint_paths(p, {100})
        assert violations == []


# ---------------------------------------------------------------------------
# _load_features — from real README
# ---------------------------------------------------------------------------


class TestLoadFeatures:
    def test_returns_set_of_ints(self) -> None:
        pcts = _load_features(None)
        assert isinstance(pcts, set)
        assert all(isinstance(v, int) for v in pcts)

    def test_contains_known_values(self) -> None:
        pcts = _load_features(None)
        # README has 100%, 0%, and various partial values
        assert 100 in pcts
        assert 0 in pcts

    def test_nonempty(self) -> None:
        pcts = _load_features(None)
        assert len(pcts) >= 5


# ---------------------------------------------------------------------------
# Integration: built deck passes lint (if present)
# ---------------------------------------------------------------------------


class TestBuiltDeckHonestyLint:
    def test_built_deck_passes_lint_if_present(self) -> None:
        build_dir = Path(__file__).parent.parent.parent / "docs" / "presentation" / "build"
        index = build_dir / "index.html"
        if not index.exists():
            pytest.skip("docs/presentation/build/index.html not built — run make deck")

        readme_pcts = _load_features(None)
        violations = lint_paths(index, readme_pcts)
        assert violations == [], (
            f"Built deck has {len(violations)} honesty violation(s):\n"
            + "\n".join(f"  {v}" for v in violations)
        )
