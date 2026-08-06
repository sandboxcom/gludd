"""Verify the 10-agent floor is always active — not gated on env var presence.

Prior to 2026-08-05, REQUIRED_DISPATCHES was 0 unless GLUDD_MIN_DISPATCHES or
GLUDD_MULTITASK_MIN_DISPATCHES was explicitly set in the environment.  The
config module's MIN_DISPATCHES default of 10 was unused because the plugin
checked for the env var before consulting the constant.  This meant the
under-floor block, zero-streak block, and thin-wave text.complete block were
all dead code in a default session.
"""

from __future__ import annotations

import re
from pathlib import Path

PLUGIN_PATH = Path(__file__).resolve().parents[2] / ".opencode/plugin/enforce-multitask.ts"
CONFIG_PATH = Path(__file__).resolve().parents[2] / ".opencode/lib/multitask_config.ts"


def _plugin_source() -> str:
    return CONFIG_PATH.read_text() + "\n" + PLUGIN_PATH.read_text()


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


class TestFloorAlwaysActive:
    """REQUIRED_DISPATCHES is always set — not gated on env var presence."""

    def test_required_dispatches_not_gated_on_env_presence(self) -> None:
        """REQUIRED_DISPATCHES must use MIN_DISPATCHES directly.

        The old code used HAS_CONFIGURED_MIN_DISPATCHES / CONFIGURED_MIN_DISPATCHES
        to gate on the env var.  Those variables must be absent; REQUIRED_DISPATCHES
        must be assigned from MIN_DISPATCHES unconditionally.
        """
        src = PLUGIN_PATH.read_text()
        assert "HAS_CONFIGURED_MIN_DISPATCHES" not in src, (
            "HAS_CONFIGURED_MIN_DISPATCHES must be removed — floor is now unconditional"
        )
        assert "CONFIGURED_MIN_DISPATCHES" not in src, (
            "CONFIGURED_MIN_DISPATCHES must be removed — floor is now unconditional"
        )

    def test_required_dispatches_uses_min_dispatches(self) -> None:
        """REQUIRED_DISPATCHES references MIN_DISPATCHES directly."""
        src = PLUGIN_PATH.read_text()
        m = re.search(r"const REQUIRED_DISPATCHES\s*=\s*(.*?);", src, re.DOTALL)
        assert m, "REQUIRED_DISPATCHES declaration not found"
        expr = m.group(1)
        assert "MIN_DISPATCHES" in expr, "REQUIRED_DISPATCHES must reference MIN_DISPATCHES from config module"

    def test_min_dispatches_default_is_10(self) -> None:
        """Config module defaults MIN_DISPATCHES to 10."""
        cfg = _config_source()
        assert _extract_env_default(cfg, "GLUDD_MIN_DISPATCHES") == 10
        assert _extract_env_default(cfg, "GLUDD_MULTITASK_MIN_DISPATCHES") == 10

    def test_floor_comment_says_hard_floor(self) -> None:
        """Comment at REQUIRED_DISPATCHES must say 'hard floor not a recommendation'."""
        src = PLUGIN_PATH.read_text()
        idx = src.find("REQUIRED_DISPATCHES")
        assert idx >= 0, "REQUIRED_DISPATCHES not found"
        # Look in the preceding comments
        before = src[max(0, idx - 500) : idx]
        assert "hard floor" in before.lower(), "Comment must call ten 'a hard floor' not 'a recommendation'"

    def test_comment_no_longer_says_never_unconditional_floor(self) -> None:
        """The old comment 'never an unconditional floor' must be gone."""
        src = PLUGIN_PATH.read_text()
        assert "never an unconditional floor" not in src, "Old comment 'never an unconditional floor' must be removed"
        assert "recommendation for large waves" not in src, (
            "Old comment 'recommendation for large waves' must be removed"
        )

    def test_under_floor_comment_says_always_active(self) -> None:
        """Comment inside under-floor block says 'always active' not 'explicitly configured'."""
        src = PLUGIN_PATH.read_text()
        idx = src.find("UNDER-FLOOR HARD BLOCK")
        assert idx >= 0, "UNDER-FLOOR HARD BLOCK section not found"
        # Look at text within the under-floor section itself (after the marker)
        after = src[idx : idx + 1200]
        assert "always active" in after.lower() or "defaults to 10" in after.lower() or "hard floor" in after.lower(), (
            "Comment inside under-floor block must indicate floor is always active"
        )
        assert "explicitly configured" not in after.lower(), "Old comment about 'explicitly configured' must be removed"

    def test_disable_via_gludd_min_dispatches_zero(self) -> None:
        """Setting GLUDD_MIN_DISPATCHES=0 must produce REQUIRED_DISPATCHES=0.

        Math.max(0, ...) + MIN_DISPATCHES=0 yields 0, disabling the floor entirely.
        """
        # Verify the expression includes Math.max(0, ...) so zero disables
        src = PLUGIN_PATH.read_text()
        m = re.search(r"const REQUIRED_DISPATCHES\s*=\s*(.*?);", src, re.DOTALL)
        expr = m.group(1) if m else ""
        assert "Math.max(0" in expr, "REQUIRED_DISPATCHES must use Math.max(0, ...) so zero disables the floor"

    def test_under_floor_gate_still_requires_dispatches_gt_zero(self) -> None:
        """The under-floor block still checks REQUIRED_DISPATCHES > 0 for correctness.

        When REQUIRED_DISPATCHES is 0 (floor disabled via env var), the block must not fire.
        """
        src = PLUGIN_PATH.read_text()
        uf_idx = src.find("UNDER-FLOOR HARD BLOCK")
        assert uf_idx >= 0, "UNDER-FLOOR HARD BLOCK section not found"
        after = src[uf_idx : uf_idx + 2000]
        assert "REQUIRED_DISPATCHES > 0" in after, "Under-floor block must gate on REQUIRED_DISPATCHES > 0"
