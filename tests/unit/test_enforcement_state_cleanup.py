"""Structural tests for the clean-enforcement-state Makefile target.

Verifies the target exists and references all enforcement state files under /tmp/gludd-*.
"""

from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
MAKEFILE = ROOT / "Makefile"

EXPECTED_FILES = [
    "/tmp/gludd-tool-streak.json",
    "/tmp/gludd-mainthread-streak.json",
    "/tmp/gludd-ci-poll-streak.json",
    "/tmp/gludd-stagnant-streak.json",
    "/tmp/gludd-release-deadline.json",
    "/tmp/gludd-force-dispatch.json",
    "/tmp/gludd-block-counter.json",
    "/tmp/gludd-persist-stop-block.json",
    "/tmp/gludd-disengage-audit.jsonl",
]


def _recipe(target: str) -> str:
    content = MAKEFILE.read_text()
    marker = f"\n{target}:"
    assert marker in content, f"Makefile target '{target}' not found"
    start = content.index(marker) + len(marker)
    next_target = content.find("\n\n", start)
    if next_target == -1:
        return content[start:]
    return content[start:next_target]


class TestCleanEnforcementStateTarget:
    def test_target_exists(self):
        assert _recipe("clean-enforcement-state"), (
            "clean-enforcement-state target must exist in Makefile"
        )

    def test_references_all_expected_files(self):
        recipe = _recipe("clean-enforcement-state")
        for f in EXPECTED_FILES:
            assert f in recipe, (
                f"clean-enforcement-state must clean {f}"
            )

    def test_uses_rm_f_for_safe_removal(self):
        recipe = _recipe("clean-enforcement-state")
        rm_lines = [l for l in recipe.split("\n") if "rm " in l]
        for line in rm_lines:
            assert "-f" in line, (
                f"rm must use -f flag for safe removal of non-existent files: {line.strip()}"
            )
