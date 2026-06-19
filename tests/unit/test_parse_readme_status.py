# ruff: noqa: I001
"""
Unit tests for scripts/parse_readme_status.py.

Verifies:
- parse_readme() returns a non-empty list of feature dicts
- Each feature has the required keys with correct types
- Bucket classification is correct
- local_only flag is set for rows containing "(local)"
- Section tracking works
- parse returns 0 violations when run against the real README
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

# Add scripts/ to path so we can import directly
SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from parse_readme_status import _bucket, _parse_pct, parse_readme  # noqa: E402


# ---------------------------------------------------------------------------
# _parse_pct
# ---------------------------------------------------------------------------


class TestParsePct:
    def test_plain_percentage(self) -> None:
        assert _parse_pct("100%") == 100

    def test_percentage_with_local(self) -> None:
        assert _parse_pct("75% (local)") == 75

    def test_zero_percent(self) -> None:
        assert _parse_pct("0%") == 0

    def test_no_percentage_returns_minus_one(self) -> None:
        assert _parse_pct("Evidence") == -1

    def test_partial_percent(self) -> None:
        assert _parse_pct("20%") == 20


# ---------------------------------------------------------------------------
# _bucket
# ---------------------------------------------------------------------------


class TestBucket:
    def test_100_is_full(self) -> None:
        assert _bucket(100) == "full"

    def test_0_is_none(self) -> None:
        assert _bucket(0) == "none"

    def test_positive_is_partial(self) -> None:
        assert _bucket(75) == "partial"
        assert _bucket(1) == "partial"
        assert _bucket(99) == "partial"


# ---------------------------------------------------------------------------
# parse_readme — against real README
# ---------------------------------------------------------------------------


class TestParseReadme:
    @pytest.fixture(scope="class")
    def features(self) -> list[dict]:
        readme = Path(__file__).parent.parent.parent / "README.md"
        assert readme.exists(), f"README.md not found at {readme}"
        result = parse_readme(readme)
        assert result, "parse_readme returned empty list — check README table format"
        return result

    def test_returns_nonempty_list(self, features: list[dict]) -> None:
        assert len(features) > 0

    def test_feature_has_required_keys(self, features: list[dict]) -> None:
        required = {"title", "pct", "pct_label", "evidence", "bucket", "local_only", "section"}
        for f in features:
            missing = required - f.keys()
            assert not missing, f"Feature missing keys {missing}: {f['title']!r}"

    def test_pct_is_int_in_range(self, features: list[dict]) -> None:
        for f in features:
            assert isinstance(f["pct"], int), f"pct is not int: {f}"
            assert 0 <= f["pct"] <= 100, f"pct out of range: {f}"

    def test_bucket_consistency(self, features: list[dict]) -> None:
        for f in features:
            expected = _bucket(f["pct"])
            assert f["bucket"] == expected, (
                f"bucket mismatch for {f['title']!r}: got {f['bucket']!r}, "
                f"expected {expected!r}"
            )

    def test_local_only_flag_set_for_local_rows(self, features: list[dict]) -> None:
        local_rows = [f for f in features if f["local_only"]]
        # README has several "(local)" rows — at least 1 must be found
        assert len(local_rows) >= 1, "Expected at least 1 local_only=True row in README"

    def test_local_only_false_for_non_local_rows(self, features: list[dict]) -> None:
        non_local = [f for f in features if not f["local_only"]]
        for f in non_local:
            assert "(local)" not in f["pct_label"].lower(), (
                f"local_only=False but pct_label contains '(local)': {f}"
            )

    def test_section_is_nonempty_string(self, features: list[dict]) -> None:
        for f in features:
            assert isinstance(f["section"], str)
            assert f["section"], f"section is empty for feature {f['title']!r}"

    def test_multiple_sections_present(self, features: list[dict]) -> None:
        sections = {f["section"] for f in features}
        assert len(sections) >= 3, f"Expected >= 3 sections, got {sections}"

    def test_some_full_features(self, features: list[dict]) -> None:
        full = [f for f in features if f["bucket"] == "full"]
        assert len(full) >= 10, f"Expected >= 10 shipped features, got {len(full)}"

    def test_some_none_features(self, features: list[dict]) -> None:
        none = [f for f in features if f["bucket"] == "none"]
        assert len(none) >= 3, f"Expected >= 3 unstarted features, got {len(none)}"


# ---------------------------------------------------------------------------
# CLI smoke test — script runs end-to-end and emits valid JSON
# ---------------------------------------------------------------------------


class TestParseReadmeCLI:
    def test_cli_outputs_valid_json(self) -> None:
        script = SCRIPTS_DIR / "parse_readme_status.py"
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).parent.parent.parent),
        )
        assert result.returncode == 0, f"Script failed: {result.stderr}"
        data = json.loads(result.stdout)
        assert "features" in data
        assert "counts" in data
        assert data["counts"]["total"] > 0

    def test_cli_counts_consistent(self) -> None:
        script = SCRIPTS_DIR / "parse_readme_status.py"
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).parent.parent.parent),
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        counts = data["counts"]
        assert counts["full"] + counts["partial"] + counts["none"] == counts["total"]


# ---------------------------------------------------------------------------
# Synthetic README — parse_readme against crafted markdown
# ---------------------------------------------------------------------------


class TestParseReadmeSynthetic:
    def _make_readme(self, content: str, tmp_path: Path) -> Path:
        p = tmp_path / "README.md"
        p.write_text(content, encoding="utf-8")
        return p

    def test_parses_simple_table(self, tmp_path: Path) -> None:
        md = textwrap.dedent("""\
            ## Feature & Task Completion Status

            ### Core Engine

            | Feature / Task | % | Evidence |
            |---|---|---|
            | Widget boots | 100% | `tests/unit/test_widget.py` |
            | Gadget syncs | 75% | partial |
            | Doodad works | 0% | design-only |
        """)
        readme = self._make_readme(md, tmp_path)
        features = parse_readme(readme)
        assert len(features) == 3
        titles = [f["title"] for f in features]
        assert "Widget boots" in titles
        assert "Gadget syncs" in titles
        assert "Doodad works" in titles

    def test_skips_header_rows(self, tmp_path: Path) -> None:
        md = textwrap.dedent("""\
            ## Feature & Task Completion Status

            ### Sec

            | Feature / Task | % | Evidence |
            |---|---|---|
            | Real feature | 50% | test.py |
        """)
        readme = self._make_readme(md, tmp_path)
        features = parse_readme(readme)
        # Header row "Feature / Task | % | Evidence" should be skipped (no digits in %)
        assert all("%" not in f["title"] for f in features)
        assert len(features) == 1

    def test_local_only_detected(self, tmp_path: Path) -> None:
        md = textwrap.dedent("""\
            ## Feature & Task Completion Status

            ### Sec

            | Feature / Task | % | Evidence |
            |---|---|---|
            | Thing | 100% (local) | local test |
        """)
        readme = self._make_readme(md, tmp_path)
        features = parse_readme(readme)
        assert len(features) == 1
        assert features[0]["local_only"] is True

    def test_section_assigned(self, tmp_path: Path) -> None:
        md = textwrap.dedent("""\
            ## Feature & Task Completion Status

            ### Alpha Section

            | Feature / Task | % | Evidence |
            |---|---|---|
            | Foo | 100% | t |
        """)
        readme = self._make_readme(md, tmp_path)
        features = parse_readme(readme)
        assert features[0]["section"] == "Alpha Section"
