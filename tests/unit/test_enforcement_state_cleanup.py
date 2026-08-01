"""Structural tests for the supported reload-enforcement Makefile target.

The old broad cleanup target was replaced by a scoped live-state reset.
"""

from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
MAKEFILE = ROOT / "Makefile"

EXPECTED_FILES = [
    "/tmp/gludd-tool-streak.json",
    "/tmp/gludd-mainthread-streak.json",
    "/tmp/gludd-watchdog-disengage.json",
    "/tmp/gludd-session-start.json",
    "/tmp/gludd-enhancement-ratio.json",
    "/tmp/gludd-task-deadlines.json",
    "/tmp/gludd-task-stale.json",
    "/tmp/gludd-multitask-state.json",
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
        assert _recipe("reload-enforcement"), (
            "reload-enforcement target must exist in Makefile"
        )

    def test_references_all_expected_files(self):
        recipe = _recipe("reload-enforcement")
        for f in EXPECTED_FILES:
            assert f in recipe, (
                f"reload-enforcement must reset {f}"
            )

    def test_uses_rm_f_for_safe_removal(self):
        recipe = _recipe("reload-enforcement")
        rm_lines = [line for line in recipe.split("\n") if "rm " in line]
        for line in rm_lines:
            assert "-f" in line, (
                f"rm must use -f flag for safe removal of non-existent files: {line.strip()}"
            )
