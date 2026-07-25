"""Structural tests for the `make clean-enforcement-state` target (BP.17).

Verifies the target exists, uses safe `rm -f` removal, and references every
enforcement state file written by the plugins under `.opencode/`.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
MAKEFILE = ROOT / "Makefile"

EXPECTED_STATE_FILES = [
    "/tmp/gludd-tool-streak.json",
    "/tmp/gludd-mainthread-streak.json",
    "/tmp/gludd-read-grind.json",
    "/tmp/gludd-model-util.json",
    "/tmp/gludd-force-delegate.json",
    "/tmp/gludd-force-dispatch.json",
    "/tmp/gludd-stagnant-streak.json",
    "/tmp/gludd-release-deadline.json",
    "/tmp/gludd-disengage-next",
    "/tmp/gludd-watchdog-disengage",
    "/tmp/gludd-watchdog-disengage.json",
    "/tmp/gludd-disengage-audit.jsonl",
    "/tmp/gludd-ci-poll-streak.json",
    "/tmp/gludd-ci-poll-counter.json",
    "/tmp/gludd-ci-check-state.json",
    "/tmp/gludd-session-start.json",
    "/tmp/gludd-enhancement-ratio.json",
    "/tmp/gludd-task-deadlines.json",
    "/tmp/gludd-task-stale.json",
    "/tmp/gludd-multitask-state.json",
    "/tmp/gludd-block-counter.json",
    "/tmp/gludd-persist-stop-block.json",
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

    def test_recipe_has_rm_commands(self):
        recipe = _recipe("clean-enforcement-state")
        rm_lines = [line for line in recipe.splitlines() if "rm " in line]
        assert len(rm_lines) >= 20, (
            "clean-enforcement-state must contain rm commands for all state files; "
            f"found {len(rm_lines)}"
        )

    def test_rm_uses_force_flag(self):
        recipe = _recipe("clean-enforcement-state")
        rm_lines = [line for line in recipe.splitlines() if "rm " in line]
        for line in rm_lines:
            assert "-f" in line, (
                f"rm must use -f for safe removal of non-existent files: {line.strip()}"
            )

    def test_references_all_expected_state_files(self):
        recipe = _recipe("clean-enforcement-state")
        missing = [f for f in EXPECTED_STATE_FILES if f not in recipe]
        assert not missing, (
            "clean-enforcement-state must clean these state files: " + ", ".join(missing)
        )

    def test_does_not_remove_lock_or_override_files(self):
        recipe = _recipe("clean-enforcement-state")
        protected = [
            "/tmp/gludd-commit.lock",
            "/tmp/gludd-floor-override",
            "/tmp/gludd-ceiling-override",
        ]
        for path in protected:
            assert path not in recipe, (
                f"clean-enforcement-state must not remove active lock/override: {path}"
            )
