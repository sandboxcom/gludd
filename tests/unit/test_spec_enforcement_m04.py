"""M04: Merge is atomic.

A merge MUST commit all resolved files in a single merge commit.
Partial merges are forbidden. All merge targets must use `--no-ff`
or `git merge` without `--no-commit` to ensure atomicity.
"""

from pathlib import Path
from typing import ClassVar

MAKEFILE = Path(__file__).parent.parent.parent / "Makefile"


def _find_target_recipe(content: str, target: str) -> str:
    idx = content.find(f"\n{target}:")
    if idx == -1:
        return ""
    end = content.find("\n\n", idx)
    if end == -1:
        end = len(content)
    return content[idx:end]


class TestM04MergeIsAtomic:
    """M04 — merge targets use --no-ff and do not use --no-commit."""

    _MERGE_TARGETS: ClassVar[list[str]] = [
        "git-merge",
        "agent-merge",
        "agent-merge-dev",
        "feature-done",
        "development-merge-to-master",
    ]

    def test_merge_targets_use_no_ff(self):
        content = MAKEFILE.read_text()
        violations = []
        for target in self._MERGE_TARGETS:
            recipe = _find_target_recipe(content, target)
            if not recipe:
                continue
            if "--no-ff" not in recipe:
                violations.append(f"'{target}' missing --no-ff flag")
        assert not violations, "M04 VIOLATION — merge targets missing --no-ff:\n" + "\n".join(violations)

    def test_no_merge_target_uses_no_commit(self):
        content = MAKEFILE.read_text()
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

    def test_merge_abort_available(self):
        content = MAKEFILE.read_text()
        recipe = _find_target_recipe(content, "git-merge-abort")
        assert recipe, "M04: git-merge-abort must exist for atomicity recovery"

    def test_development_merge_uses_no_ff(self):
        content = MAKEFILE.read_text()
        recipe = _find_target_recipe(content, "development-merge-to-master")
        assert recipe, "M04: development-merge-to-master must exist"
        assert "--no-ff" in recipe, "M04: development-merge-to-master must use --no-ff for atomic merge"
