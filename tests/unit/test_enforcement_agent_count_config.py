"""Structural tests for multitask_config.ts as the canonical enforcement config.

Verifies:
1. MIN_DISPATCHES exported with default 10
2. enforce-multitask.ts imports and uses MIN_DISPATCHES from multitask_config.ts
3. HARD_MAX_DISPATCHES = 10 constant exists
4. MAX_ZERO_STREAK = 2 constant exists
5. AGENTS.md mentions multitask_config.ts as canonical source
"""

from __future__ import annotations

import re
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parents[2] / ".opencode/lib/multitask_config.ts"
PLUGIN_PATH = Path(__file__).resolve().parents[2] / ".opencode/plugin/enforce-multitask.ts"
AGENTS_PATH = Path(__file__).resolve().parents[2] / "AGENTS.md"


def _config_src() -> str:
    return CONFIG_PATH.read_text()


def _plugin_src() -> str:
    return PLUGIN_PATH.read_text()


def _agents_src() -> str:
    return AGENTS_PATH.read_text()


class TestMinDispatchesExport:
    """multitask_config.ts exports MIN_DISPATCHES with default 10."""

    def test_export_declaration_exists(self):
        src = _config_src()
        m = re.search(r"export\s+const\s+MIN_DISPATCHES\s*=\s*integerFromEnv", src)
        assert m, "MIN_DISPATCHES must be exported via integerFromEnv resolver"

    def test_default_value_is_10(self):
        src = _config_src()
        m = re.search(
            r"export\s+const\s+MIN_DISPATCHES\s*=\s*integerFromEnv\(\s*\["
            r'"GLUDD_MIN_DISPATCHES"\s*,\s*"GLUDD_MULTITASK_MIN_DISPATCHES"'
            r"\s*\],\s*(\d+)",
            src,
        )
        assert m, "MIN_DISPATCHES integerFromEnv call not found"
        assert int(m.group(1)) == 10, f"MIN_DISPATCHES default must be 10, got {m.group(1)}"

    def test_env_var_names_correct(self):
        src = _config_src()
        assert '"GLUDD_MIN_DISPATCHES"' in src, "GLUDD_MIN_DISPATCHES env var must be supported"
        assert '"GLUDD_MULTITASK_MIN_DISPATCHES"' in src, "GLUDD_MULTITASK_MIN_DISPATCHES env var must be supported"


class TestEnforceMultitaskImportsMinDispatches:
    """enforce-multitask.ts imports and uses MIN_DISPATCHES from multitask_config.ts."""

    def test_import_from_multitask_config(self):
        src = _plugin_src()
        assert "from" in src and "multitask_config" in src, "must import from multitask_config.ts"
        assert "MIN_DISPATCHES" in src, "MIN_DISPATCHES must be imported"

    def test_import_path_is_correct(self):
        src = _plugin_src()
        assert re.search(r"from\s+\"\.\./lib/multitask_config\.ts\"", src), (
            "must import from ../lib/multitask_config.ts"
        )

    def test_min_dispatches_used_in_plugin_body(self):
        src = _plugin_src()
        assert "MIN_DISPATCHES" in src.split(";")[0] or any(
            line.strip().startswith("MIN_DISPATCHES") for line in src.split("\n")
        ), "MIN_DISPATCHES must appear in enforce-multitask.ts body"

    def test_required_dispatches_derived_from_min(self):
        src = _plugin_src()
        assert "REQUIRED_DISPATCHES" in src, "REQUIRED_DISPATCHES must be declared"
        req_line = [line for line in src.split("\n") if "REQUIRED_DISPATCHES" in line and "=" in line]
        assert len(req_line) >= 1, "REQUIRED_DISPATCHES assignment not found"
        combined = "\n".join(req_line)
        assert "MIN_DISPATCHES" in combined, "REQUIRED_DISPATCHES must reference MIN_DISPATCHES"


class TestHardMaxDispatchesConstant:
    """HARD_MAX_DISPATCHES = 10 constant exists in multitask_config.ts."""

    def test_hard_max_dispatches_exported(self):
        src = _config_src()
        assert "export const HARD_MAX_DISPATCHES" in src, "HARD_MAX_DISPATCHES must be exported"

    def test_hard_max_dispatches_value_is_10(self):
        src = _config_src()
        m = re.search(r"HARD_MAX_DISPATCHES\s*=\s*(\d+)", src)
        assert m, "HARD_MAX_DISPATCHES assignment not found"
        assert int(m.group(1)) == 10, f"HARD_MAX_DISPATCHES must be 10, got {m.group(1)}"

    def test_max_dispatches_bounded_by_hard_max(self):
        src = _config_src()
        assert "Math.min(\n    HARD_MAX_DISPATCHES" in src, "MAX_DISPATCHES must be bounded by HARD_MAX_DISPATCHES"

    def test_hard_max_imported_by_enforce_multitask(self):
        src = _plugin_src()
        assert "HARD_MAX_DISPATCHES" in src and "multitask_config" in src, (
            "HARD_MAX_DISPATCHES must be imported from multitask_config in enforce-multitask.ts"
        )


class TestMaxZeroStreakConstant:
    """MAX_ZERO_STREAK = 2 constant exists in multitask_config.ts."""

    def test_max_zero_streak_exported(self):
        src = _config_src()
        assert "export const MAX_ZERO_STREAK" in src, "MAX_ZERO_STREAK must be exported"

    def test_max_zero_streak_value_is_2(self):
        src = _config_src()
        m = re.search(r"MAX_ZERO_STREAK\s*=\s*(\d+)", src)
        assert m, "MAX_ZERO_STREAK assignment not found"
        assert int(m.group(1)) == 2, f"MAX_ZERO_STREAK must be 2, got {m.group(1)}"

    def test_max_zero_streak_imported_by_enforce_multitask(self):
        src = _plugin_src()
        assert "MAX_ZERO_STREAK" in src, "MAX_ZERO_STREAK must be imported and used by enforce-multitask.ts"

    def test_max_zero_streak_used_in_zero_streak_check(self):
        src = _plugin_src()
        assert "MAX_ZERO_STREAK" in src and "zeroStreak" in src, "MAX_ZERO_STREAK must gate zero-streak enforcement"


class TestAgentsMdReferencesConfig:
    """AGENTS.md mentions multitask_config.ts as the canonical constants source."""

    def test_agents_md_mentions_multitask_config_ts(self):
        src = _agents_src()
        assert "multitask_config.ts" in src, "AGENTS.md must reference multitask_config.ts"

    def test_agents_md_calls_it_canonical(self):
        src = _agents_src()
        lines_with_ref = [line for line in src.split("\n") if "multitask_config.ts" in line]
        combined = " ".join(lines_with_ref)
        assert "canonical" in combined.lower(), "AGENTS.md must describe multitask_config.ts as canonical"

    def test_agents_md_mentions_single_source_of_truth(self):
        src = _agents_src()
        lines_with_ref = [line for line in src.split("\n") if "multitask_config.ts" in line]
        combined = " ".join(lines_with_ref)
        assert "single source of truth" in combined.lower() or "one-source" in combined.lower(), (
            "AGENTS.md must state multitask_config.ts is the single source of truth"
        )

    def test_agents_md_defines_min_dispatches_default_10(self):
        src = _agents_src()
        lines_with_ref = [line for line in src.split("\n") if "multitask_config.ts" in line]
        combined = " ".join(lines_with_ref)
        assert "MIN_DISPATCHES" in combined, "AGENTS.md must reference MIN_DISPATCHES alongside multitask_config.ts"

    def test_agents_md_defines_max_dispatches_default_10(self):
        src = _agents_src()
        lines_with_ref = [line for line in src.split("\n") if "multitask_config.ts" in line]
        combined = " ".join(lines_with_ref)
        assert "MAX_DISPATCHES" in combined, "AGENTS.md must reference MAX_DISPATCHES alongside multitask_config.ts"

    def test_agents_md_defines_hard_max_dispatches_10(self):
        src = _agents_src()
        lines_with_ref = [line for line in src.split("\n") if "multitask_config.ts" in line]
        combined = " ".join(lines_with_ref)
        assert "HARD_MAX_DISPATCHES" in combined and "10" in combined, (
            "AGENTS.md must state HARD_MAX_DISPATCHES=10 at canonical source"
        )
