"""M04: Merge is atomic.

A merge MUST commit all resolved files in a single merge commit.
Partial merges are forbidden. Feature merges retain their atomic commit policy;
canonical master promotion fast-forwards to one already validated development SHA.
The retired master alias must not perform any merge.
"""

from pathlib import Path
from typing import ClassVar

from scripts.check_make_target_contract import _stanzas
from scripts.makefile_layout import compose_makefile

MAKEFILE = Path(__file__).parent.parent.parent / "Makefile"


def _find_target_recipe(content: str, target: str) -> str:
    return _stanzas(content).get(target, "")


class TestM04MergeIsAtomic:
    """M04 — merge targets use --no-ff and do not use --no-commit."""

    _MERGE_TARGETS: ClassVar[list[str]] = [
        "git-merge",
        "agent-merge",
        "agent-merge-dev",
        "feature-done",
    ]

    def test_merge_targets_use_no_ff(self) -> None:
        content = compose_makefile(MAKEFILE)
        violations = []
        for target in self._MERGE_TARGETS:
            recipe = _find_target_recipe(content, target)
            if not recipe:
                continue
            if target in {"agent-merge", "agent-merge-dev"}:
                assert "outcome=retired" in recipe and "native_status=2" in recipe
                assert "exit 2" in recipe and "git merge" not in recipe and "git checkout" not in recipe
                next_owner = "release-promote" if target == "agent-merge" else "development-merge-forward"
                assert next_owner in recipe
                canonical = _find_target_recipe(content, "development-fast-forward")
                assert "merge --ff-only" in canonical
                assert "EXPECTED_BEFORE_SHA" in canonical and "EXPECTED_AFTER_SHA" in canonical
            elif "--no-ff" not in recipe:
                violations.append(f"'{target}' missing --no-ff flag")
        assert not violations, "M04 VIOLATION — merge targets missing --no-ff:\n" + "\n".join(violations)

    def test_no_merge_target_uses_no_commit(self) -> None:
        content = compose_makefile(MAKEFILE)
        violations = []
        for target in self._MERGE_TARGETS:
            recipe = _find_target_recipe(content, target)
            if not recipe:
                continue
            if "--no-commit" in recipe:
                violations.append(f"'{target}' uses --no-commit (partial merge forbidden)")
        if violations:
            raise AssertionError(
                "M04 VIOLATION — merge targets using --no-commit (partial merge allowed):\n" + "\n".join(violations)
            )

    def test_merge_abort_available(self) -> None:
        content = compose_makefile(MAKEFILE)
        recipe = _find_target_recipe(content, "git-merge-abort")
        assert recipe, "M04: git-merge-abort must exist for atomicity recovery"

    def test_retired_alias_preserves_atomic_canonical_promotion(self) -> None:
        content = compose_makefile(MAKEFILE)
        recipe = _find_target_recipe(content, "development-merge-to-master")
        assert recipe, "M04: development-merge-to-master must remain discoverable"
        assert "outcome=retired" in recipe and "exit 2" in recipe
        assert "git merge" not in recipe and "git checkout" not in recipe
        canonical = _find_target_recipe(content, "release-promote")
        assert 'merge --ff-only "$$DEV_SHA"' in canonical
        assert "merge --no-ff" not in canonical
        assert "merge --no-commit" not in canonical
