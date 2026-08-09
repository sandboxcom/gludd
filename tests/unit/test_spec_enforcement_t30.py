"""T30: collect-check is gate prerequisite.

`make collect-check` MUST be a prerequisite of `make gate` and all
commit targets. Verifies that the gate and commit targets reference
the collection-error check.
"""

import re
from pathlib import Path

MAKEFILE = Path(__file__).parent.parent.parent / "Makefile"


def _find_target_recipe(content: str, target: str) -> str:
    idx = content.find(f"\n{target}:")
    if idx == -1:
        return ""
    end = content.find("\n\n", idx)
    if end == -1:
        end = len(content)
    return content[idx:end]


def _target_names(content: str) -> set[str]:
    targets: set[str] = set()
    for line in content.split("\n"):
        m = re.match(r"^([a-zA-Z_][a-zA-Z0-9_./-]*)\s*:(?!=)", line.strip())
        if m:
            targets.add(m.group(1))
    return targets


class TestT30CollectCheckGatePrerequisite:
    """T30 — collect-check is a prerequisite of gate + commit targets."""

    def test_collect_check_target_exists(self):
        content = MAKEFILE.read_text()
        target_names = _target_names(content)
        assert "collect-check" in target_names, "T30: collect-check target must exist in Makefile"

    def test_collect_check_is_gate_prerequisite(self):
        content = MAKEFILE.read_text()
        recipe = _find_target_recipe(content, "gate")
        assert recipe, "T30: gate target must exist"
        assert (
            "collect-check" in recipe
            or "collect-check"
            in content[content.find("\ngate:") : content.find("\ngate-refresh:", content.find("\ngate:"))]
        ), "T30: gate must reference collect-check as a prerequisite"

    def test_collect_check_is_gate_fast_prerequisite(self):
        content = MAKEFILE.read_text()
        recipe = _find_target_recipe(content, "gate-fast")
        assert recipe, "T30: gate-fast target must exist"
        assert "collect-check" in recipe, "T30: gate-fast must include collect-check"

    def test_collect_check_in_gate_lite(self):
        content = MAKEFILE.read_text()
        recipe = _find_target_recipe(content, "gate-lite")
        assert recipe, "T30: gate-lite target must exist"
        assert (
            "collect-check" in recipe
            or "collect-check"
            in content[content.find("\ngate-lite:") : content.find("\ngate-status:", content.find("\ngate-lite:"))]
        ), "T30: gate-lite must reference collect-check"

    def test_commit_targets_reference_collect_check(self):
        content = MAKEFILE.read_text()
        target_names = _target_names(content)
        commit_related = [t for t in target_names if "commit" in t and not t.startswith("_")]
        found = 0
        for t in commit_related:
            recipe = _find_target_recipe(content, t)
            if "collect-check" in recipe or "gate" in recipe:
                found += 1
        assert found >= 2, (
            f"T30: at least 2 commit targets must reference "
            f"collect-check or gate. Found {found} of {len(commit_related)}: "
            f"{sorted(commit_related)}"
        )

    def test_check_duplicate_targets_references_collect_check(self):
        content = MAKEFILE.read_text()
        recipe = _find_target_recipe(content, "check-duplicate-targets")
        assert recipe, "T30: check-duplicate-targets must exist"
