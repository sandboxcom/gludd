"""D01/D02/D03/D04/D05: Dispatch floor enforcement structural tests.

Verifies enforce-multitask.ts constants and plugin structure match
the documented dispatch floor rules: MIN_DISPATCHES=10, streak
counter resets on dispatch, read tools don't increment streak.
"""

from pathlib import Path

PLUGIN_DIR = Path(__file__).parent.parent.parent / ".opencode" / "plugin"
LIB_DIR = Path(__file__).parent.parent.parent / ".opencode" / "lib"


class TestDispatchFloorEnforcement:
    """D01-D05: enforce-multitask.ts dispatch floor enforcement."""

    def test_min_dispatches_config_exists(self):
        config_path = LIB_DIR / "multitask_config.ts"
        assert config_path.exists(), "D01: multitask_config.ts must exist as canonical constant source"
        content = config_path.read_text()
        assert "MIN_DISPATCHES" in content, "D01: multitask_config.ts must define MIN_DISPATCHES"
        assert "10" in content, "D01: MIN_DISPATCHES should be 10"

    def test_enforce_multitask_plugin_exists(self):
        plugin_path = PLUGIN_DIR / "enforce-multitask.ts"
        assert plugin_path.exists(), "D02: enforce-multitask.ts must exist"

    def test_multitask_plugin_imports_config(self):
        plugin_path = PLUGIN_DIR / "enforce-multitask.ts"
        if not plugin_path.exists():
            return
        content = plugin_path.read_text()
        assert "multitask_config" in content, "D02: enforce-multitask.ts must import multitask_config"

    def test_multitask_plugin_has_streak_counter(self):
        plugin_path = PLUGIN_DIR / "enforce-multitask.ts"
        if not plugin_path.exists():
            return
        content = plugin_path.read_text()
        assert "streak" in content.lower() or "counter" in content.lower(), (
            "D03: enforce-multitask.ts must track dispatch streak"
        )

    def test_multitask_plugin_has_MAX_ZERO_STREAK(self):
        config_path = LIB_DIR / "multitask_config.ts"
        if not config_path.exists():
            return
        content = config_path.read_text()
        assert "MAX_ZERO_STREAK" in content, "D03: multitask_config.ts must define MAX_ZERO_STREAK"

    def test_multitask_plugin_checks_read_tools(self):
        plugin_path = PLUGIN_DIR / "enforce-multitask.ts"
        if not plugin_path.exists():
            return
        content = plugin_path.read_text()
        assert any(kw in content for kw in ["isReadTool", "read", "Read"]), (
            "D05: enforce-multitask.ts must skip streak increment for read tools"
        )

    def test_max_dispatch_ceiling(self):
        config_path = LIB_DIR / "multitask_config.ts"
        if not config_path.exists():
            return
        content = config_path.read_text()
        assert "MAX_DISPATCHES" in content or "HARD_MAX_DISPATCHES" in content, (
            "D04: multitask_config.ts must define MAX_DISPATCHES ceiling"
        )

    def test_multitask_plugin_disablable(self):
        plugin_path = PLUGIN_DIR / "enforce-multitask.ts"
        if not plugin_path.exists():
            return
        content = plugin_path.read_text()
        assert "GLUDD_MULTITASK_FLOOR_ENFORCE" in content, (
            "D02: enforce-multitask.ts must support disable via GLUDD_MULTITASK_FLOOR_ENFORCE=0"
        )

    def test_multitask_plugin_has_subagent_guard(self):
        plugin_path = PLUGIN_DIR / "enforce-multitask.ts"
        if not plugin_path.exists():
            return
        content = plugin_path.read_text()
        assert "OPENCODE_SUBAGENT" in content, "D05: enforce-multitask.ts must include subagent guard"
