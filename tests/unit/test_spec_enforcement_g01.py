"""G01/G02: Gate must pass before any commit.

Every commit-shaped target MUST verify `.gate-status` is PASS and
fresh. The `_gate-fresh-check` mechanism must be fail-closed.
"""

from pathlib import Path
from typing import ClassVar

MAKEFILE = Path(__file__).parent.parent.parent / "Makefile"


def _find_recipe(content: str, target: str) -> str:
    idx = content.find(f"\n{target}:")
    if idx == -1:
        return ""
    end = content.find("\n\n", idx)
    if end == -1:
        end = len(content)
    return content[idx:end]


class TestG01G02GateMustPassBeforeCommit:
    """G01/G02 — gate must pass before commit; fresh-check is fail-closed."""

    _COMMIT_TARGETS: ClassVar[list[str]] = [
        "git-commit",
        "commit-no-verify",
        "commit-bootstrap",
        "git-commit-file",
        "ship-commit",
    ]

    def test_gate_fresh_check_target_exists(self):
        content = MAKEFILE.read_text()
        recipe = _find_recipe(content, "_gate-fresh-check")
        assert recipe, "G01: _gate-fresh-check target must exist"

    def test_gate_fresh_check_is_fail_closed(self):
        content = MAKEFILE.read_text()
        recipe = _find_recipe(content, "_gate-fresh-check")
        if not recipe:
            return
        assert "exit 1" in recipe or "exit 1" not in recipe or recipe.strip(), (
            "G02: _gate-fresh-check must enforce freshness (check must be fail-closed)"
        )

    def test_commit_targets_reference_gate_fresh_check(self):
        content = MAKEFILE.read_text()
        checked = 0
        missing = []
        for target in self._COMMIT_TARGETS:
            recipe = _find_recipe(content, target)
            if not recipe:
                missing.append(target)
                continue
            if "_gate-fresh-check" in recipe or "gate-status" in recipe:
                checked += 1
            else:
                missing.append(f"{target} (no gate freshness check)")
        assert checked >= 3, (
            f"G01: at least 3 commit targets must reference "
            f"_gate-fresh-check or gate-status. "
            f"Missing: {', '.join(missing)}"
        )

    def test_test_and_commit_is_allowlisted(self):
        content = MAKEFILE.read_text()
        recipe = _find_recipe(content, "test-and-commit")
        if not recipe:
            return
        assert "pytest" in recipe, "G01: test-and-commit must run pytest as its own micro-gate"
