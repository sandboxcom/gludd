"""Structural tests for the supported enforcement-state reset targets (BP.17).

The former destructive ``clean-enforcement-state`` target was replaced by
``reload-enforcement`` (live state reset) and ``crash-recovery`` (stale process
and state cleanup).  Pin their composed contract without reviving the obsolete
target.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
MAKEFILE = ROOT / "Makefile"

EXPECTED_STATE_FILES = [
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


class TestEnforcementStateResetTargets:
    def test_target_exists(self):
        assert _recipe("reload-enforcement")
        assert _recipe("crash-recovery")

    def test_recipe_has_reset_commands(self):
        recipe = _recipe("reload-enforcement")
        rm_lines = [line for line in recipe.splitlines() if "rm " in line]
        assert len(rm_lines) >= 5
        assert "json.dump" in recipe, "live streak counters must be reset, not deleted"

    def test_rm_uses_force_flag(self):
        recipe = _recipe("reload-enforcement")
        rm_lines = [line for line in recipe.splitlines() if "rm " in line]
        for line in rm_lines:
            assert "-f" in line, (
                f"rm must use -f for safe removal of non-existent files: {line.strip()}"
            )

    def test_references_all_expected_state_files(self):
        recipe = _recipe("reload-enforcement")
        missing = [f for f in EXPECTED_STATE_FILES if f not in recipe]
        assert not missing, (
            "reload-enforcement must reset these active state files: "
            + ", ".join(missing)
        )

    def test_does_not_remove_lock_or_override_files(self):
        recipe = _recipe("reload-enforcement")
        rm_recipe = "\n".join(
            line for line in recipe.splitlines() if "rm " in line
        )
        protected = [
            "/tmp/gludd-commit.lock",
            "/tmp/gludd-floor-override",
            "/tmp/gludd-ceiling-override",
        ]
        for path in protected:
            assert path not in rm_recipe, (
                f"reload-enforcement must not remove active lock/override: {path}"
            )

    def test_crash_recovery_composes_scoped_cleanup(self):
        recipe = _recipe("crash-recovery")
        assert "kill-stale" in recipe
        assert "rm -f /tmp/gludd-plugin-heartbeat-*.json" in recipe
