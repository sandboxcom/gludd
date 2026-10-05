"""Verify the unit-1a → unit-1a1 + unit-1a2 shard split is balanced.

The canonical registry in `scripts/ci_named_shard_files.py` splits the old
`unit-1a` shard (which ran 28-57 min in CI) into two halves, while the
workflow matrix carries only their names:

    unit-1a1  → tests/unit/test_a[a-m]*.py
    unit-1a2  → tests/unit/test_a[n-z]*.py  +  tests/unit/test_a[0-9]*.py

The digit-prefixed files (test_a03_*, test_a3_*, test_a6_*) are routed to
unit-1a2 because the [a-m]/[n-z] letter ranges alone do not match a leading
digit — silently dropping them would be a real coverage regression. They
go to the smaller half to preserve balance.

This module asserts neither half carries more than 70% of the total
`test_a*.py` file count — the structural guard against one leg silently
absorbing nearly all the load while the other no-ops. The 70% threshold
leaves headroom for the alphabet distribution to shift as new tests are
added without requiring a rebalance every release.

The file-count proxy is used (rather than measured runtime) because CI
runtime data is not available to the local test suite; file count is the
cheap, deterministic, locally-verifiable signal that catches a gross
imbalance (e.g. a typo that puts 95% of files in one shard).
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent  # repo root
WORKFLOW = ROOT / ".github" / "workflows" / "build.yml"
UNIT_DIR = ROOT / "tests" / "unit"

MAX_SHARE = 0.70  # neither shard may exceed 70% of the total


def _sorted_a_files() -> list[str]:
    """All tests/unit/test_a*.py filenames (basenames, sorted)."""
    files = sorted(p.name for p in UNIT_DIR.glob("test_a*.py"))
    assert files, "expected non-empty tests/unit/test_a*.py file set"
    return files


def _workflow_shards() -> list[str]:
    """Parse the shard-name axis delegated by the hosted workflow."""
    text = WORKFLOW.read_text()
    list_line = re.search(r"^\s*shard:\s*\[([^\]]+)\]", text, re.MULTILINE)
    assert list_line, "matrix `shard:` list not found in build.yml"
    return [label.strip() for label in list_line.group(1).split(",")]


def _matches_1a1(name: str) -> bool:
    """Whether `name` matches the unit-1a1 glob test_a[a-m]*.py."""
    prefix = "test_a"
    suffix = ".py"
    if not (name.startswith(prefix) and name.endswith(suffix)):
        return False
    middle = name[len(prefix) : len(name) - len(suffix)]
    if not middle:
        return False
    return "a" <= middle[0] <= "m"


def _matches_1a2(name: str) -> bool:
    """Whether `name` matches either unit-1a2 glob.

    unit-1a2 owns test_a[n-z]*.py (letters n-z) AND test_a[0-9]*.py
    (digit-prefixed files like test_a03_*, test_a3_*, test_a6_*).
    """
    prefix = "test_a"
    suffix = ".py"
    if not (name.startswith(prefix) and name.endswith(suffix)):
        return False
    middle = name[len(prefix) : len(name) - len(suffix)]
    if not middle:
        return False
    first = middle[0]
    return ("n" <= first <= "z") or first.isdigit()


class TestShardSplitBalance:
    """The unit-1a1 / unit-1a2 split must be balanced and declared in CI."""

    def test_workflow_declares_both_shards(self) -> None:
        """Both unit-1a1 and unit-1a2 must appear in the matrix name axis."""
        labels = _workflow_shards()
        assert "unit-1a1" in labels, (
            f"unit-1a1 missing from build.yml shard matrix; have: {sorted(labels)}"
        )
        assert "unit-1a2" in labels, (
            f"unit-1a2 missing from build.yml shard matrix; have: {sorted(labels)}"
        )
        # The old monolithic shard must be gone — its presence would cause
        # double-execution of the entire test_a*.py range.
        assert "unit-1a" not in labels, (
            "unit-1a still present in build.yml; should be replaced by "
            "unit-1a1 + unit-1a2"
        )

    def test_workflow_uses_expected_glob_boundaries(self) -> None:
        """Canonical globs must use the agreed [a-m] / [n-z]+[0-9] split."""
        from scripts.ci_named_shard_files import SHARDS

        first_patterns, _first_excludes = SHARDS["unit-1a1"]
        second_patterns, _second_excludes = SHARDS["unit-1a2"]
        assert first_patterns == ("tests/unit/test_a[a-m]*.py",), (
            f"unit-1a1 testpaths changed: {first_patterns!r}"
        )
        # unit-1a2 owns BOTH the n-z letter range AND the 0-9 digit prefix
        # range (the latter catches test_a03_*, test_a3_*, test_a6_* which
        # neither letter range matches).
        assert "tests/unit/test_a[n-z]*.py" in second_patterns, (
            f"unit-1a2 missing test_a[n-z]*.py: {second_patterns!r}"
        )
        assert "tests/unit/test_a[0-9]*.py" in second_patterns, (
            f"unit-1a2 missing test_a[0-9]*.py (digit catch-all): "
            f"{second_patterns!r}"
        )

    def test_old_unit_1a_removed_from_matrix_list(self) -> None:
        """The matrix `shard:` list line must not contain the old label."""
        labels = _workflow_shards()
        assert "unit-1a" not in labels, (
            f"unit-1a still in shard list: {labels}"
        )
        assert "unit-1a1" in labels and "unit-1a2" in labels, (
            f"unit-1a1/unit-1a2 missing from shard list: {labels}"
        )

    def test_split_is_balanced_by_file_count(self) -> None:
        """Neither half may exceed 70% of the total test_a*.py file count."""
        files = _sorted_a_files()

        first_half = [f for f in files if _matches_1a1(f)]
        second_half = [f for f in files if _matches_1a2(f)]

        # Sanity: the two halves together must cover the full set. A gap or
        # overlap would silently drop or double-count files (the original
        # task spec had [a-m]/[n-z] only, which dropped 5 digit-prefixed
        # files — this assertion is what caught it).
        union = set(first_half) | set(second_half)
        missing = set(files) - union
        assert not missing, (
            f"shard globs do not cover all test_a*.py files; missing: "
            f"{sorted(missing)}"
        )

        total = len(files)
        assert total > 0
        first_share = len(first_half) / total
        second_share = len(second_half) / total

        assert first_share <= MAX_SHARE, (
            f"unit-1a1 (test_a[a-m]*.py) holds {len(first_half)}/{total} "
            f"({first_share:.0%}) — exceeds {MAX_SHARE:.0%} cap; rebalance "
            f"the [a-m]/[n-z] boundary"
        )
        assert second_share <= MAX_SHARE, (
            f"unit-1a2 (test_a[n-z]*.py + test_a[0-9]*.py) holds "
            f"{len(second_half)}/{total} ({second_share:.0%}) — exceeds "
            f"{MAX_SHARE:.0%} cap; rebalance the [a-m]/[n-z] boundary"
        )

    def test_split_covers_each_file_exactly_once(self) -> None:
        """No file may match both globs (would double-run in CI)."""
        files = _sorted_a_files()
        first = {f for f in files if _matches_1a1(f)}
        second = {f for f in files if _matches_1a2(f)}
        overlap = first & second
        assert not overlap, (
            f"files matched by both unit-1a1 and unit-1a2 globs (would "
            f"double-run): {sorted(overlap)}"
        )
