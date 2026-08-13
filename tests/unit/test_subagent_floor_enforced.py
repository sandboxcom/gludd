"""Verify the ten-agent recommendation is an explicit opt-in minimum.

The active cost-efficiency directive keeps ten as the absolute dispatch ceiling
and recommended large-wave width, while simple work remains inline by default.
A minimum becomes mandatory only when GLUDD_MIN_DISPATCHES or
GLUDD_MULTITASK_MIN_DISPATCHES is explicitly present.
"""

from __future__ import annotations

import re
from pathlib import Path

PLUGIN_PATH = Path(__file__).resolve().parents[2] / ".opencode/plugin/enforce-multitask.ts"
CONFIG_PATH = Path(__file__).resolve().parents[2] / ".opencode/lib/multitask_config.ts"


def _config_source() -> str:
    return CONFIG_PATH.read_text()


def _extract_env_default(src: str, env_var: str) -> int:
    for call in re.finditer(
        r"integerFromEnv\(\s*\[(?P<names>[^]]+)\]\s*,\s*"
        r"(?P<default>\d+|[A-Z_]+)\s*,?\s*\)",
        src,
        re.DOTALL,
    ):
        if f'"{env_var}"' in call.group("names"):
            default = call.group("default")
            if default.isdigit():
                return int(default)
            constant = re.search(rf"{re.escape(default)}\s*=\s*(\d+)", src)
            assert constant, f"default constant {default} not found"
            return int(constant.group(1))
    raise AssertionError(f"env var {env_var} default not found in source")


class TestFloorExplicitOptIn:
    """REQUIRED_DISPATCHES is zero until an operator configures a minimum."""

    def test_required_dispatches_is_gated_on_env_presence(self) -> None:
        src = PLUGIN_PATH.read_text()
        assert "HAS_CONFIGURED_MIN_DISPATCHES" in src
        assert "process.env.GLUDD_MIN_DISPATCHES !== undefined" in src
        assert "process.env.GLUDD_MULTITASK_MIN_DISPATCHES !== undefined" in src
        assert "REQUIRED_DISPATCHES = HAS_CONFIGURED_MIN_DISPATCHES" in src
        assert re.search(r"REQUIRED_DISPATCHES[\s\S]{0,300}?:\s*0", src)

    def test_min_dispatches_recommendation_is_10(self) -> None:
        cfg = _config_source()
        assert _extract_env_default(cfg, "GLUDD_MIN_DISPATCHES") == 10
        assert _extract_env_default(cfg, "GLUDD_MULTITASK_MIN_DISPATCHES") == 10

    def test_required_dispatches_uses_min_dispatches(self) -> None:
        src = PLUGIN_PATH.read_text()
        assignment = re.search(
            r"const REQUIRED_DISPATCHES\s*=([\s\S]*?)\nconst WAVE_HISTORY_SIZE",
            src,
        )
        assert assignment
        assert "MIN_DISPATCHES" in assignment.group(1)

    def test_contract_comment_describes_opt_in_minimum(self) -> None:
        src = PLUGIN_PATH.read_text()
        idx = src.find("REQUIRED_DISPATCHES")
        assert idx >= 0
        before = src[max(0, idx - 600) : idx].lower()
        assert "explicit" in before or "configured" in before
        assert "recommendation" in before

    def test_under_floor_comment_describes_configured_minimum(self) -> None:
        src = PLUGIN_PATH.read_text()
        idx = src.find("UNDER-FLOOR HARD BLOCK")
        assert idx >= 0
        after = src[idx : idx + 1400].lower()
        assert "configured" in after
        assert "always active" not in after

    def test_explicit_zero_disables_minimum(self) -> None:
        src = PLUGIN_PATH.read_text()
        assignment = re.search(
            r"const REQUIRED_DISPATCHES\s*=([\s\S]*?)\nconst WAVE_HISTORY_SIZE",
            src,
        )
        assert assignment
        assert "Math.max(0" in assignment.group(1)
        assert re.search(r":\s*0", assignment.group(1))

    def test_under_floor_gate_requires_positive_minimum(self) -> None:
        src = PLUGIN_PATH.read_text()
        idx = src.find("UNDER-FLOOR HARD BLOCK")
        assert idx >= 0
        assert "REQUIRED_DISPATCHES > 0" in src[idx : idx + 2200]
