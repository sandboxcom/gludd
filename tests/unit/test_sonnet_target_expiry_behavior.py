"""BP.15 — Behavioral verification of time-bound sonnet target auto-expiry.

Invokes the REAL enforce-delegate.ts readTargetShare() function (via its
tool.execute.before hook) to verify that until_epoch expiry logic works:

  - When until_epoch is in the FUTURE → config's target_share is enforced
  - When until_epoch is in the PAST   → reverts to default (0.91 = 10:1 band)
  - Missing config file               → defaults to band mode (0.91)
  - Env var GLUDD_SONNET_TARGET_SHARE  → overrides config file target_share
  - Config format is valid JSON with target_share + until_epoch fields

This is a REAL behavioral test (per AGENTS.md "Self-Test Quality"): it imports
the actual plugin module, calls the actual hook, and observes the actual
deny/allow decision — not a reimplementation of the logic.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = ROOT / ".opencode" / "plugin"
PLUGIN_PATH = PLUGIN_DIR / "enforce-delegate.ts"

pytestmark = pytest.mark.skipif(
    not PLUGIN_PATH.is_file(),
    reason=f"enforce-delegate.ts not found at {PLUGIN_PATH}",
)

# State that forces a clear behavioral signal:
#   10 sonnet + 1 opus = 11 entries → share = 10/11 ≈ 0.909
#   Dispatching another opus → projected = 10/12 = 0.833
#
#   target=0.91 → 0.833 < 0.91 → DENIED (message includes "target=91%")
#   target=0.50 → 0.833 ≥ 0.50 → ALLOWED (no model-ratio error)
NO_HEADROOM_HISTORY = ["sonnet"] * 10 + ["opus"]


def _run_hook_with_config(
    config_path: str | None,
    state_path: str,
    env_share: str | None = None,
) -> dict:
    """Invoke the real enforce-delegate.ts hook with a controlled config + state.

    Returns a dict with keys:
      - denied (bool): whether the hook threw a model-ratio error
      - message (str): the error message if denied, else ""
      - target_pct (int|None): extracted target=N% from the message
    """
    env = {
        "GLUDD_MODEL_UTIL_STATE": state_path,
        "GLUDD_MODEL_UTIL_ENFORCE": "1",
        "GLUDD_MAIN_MODEL": "claude-3-opus-20240229",
        "GLUDD_FORCE_DELEGATE": "0",
        "GLUDD_MAINTHREAD_STREAK_ENFORCE": "0",
        "GLUDD_DISENGAGE_PATH": f"/tmp/gludd-disengage-hermetic-{os.getpid()}.json",
        "OPENCODE_SUBAGENT": "",
    }
    if config_path is not None:
        env["GLUDD_SONNET_TARGET_CONFIG"] = config_path
    if env_share is not None:
        env["GLUDD_SONNET_TARGET_SHARE"] = env_share
    else:
        env.pop("GLUDD_SONNET_TARGET_SHARE", None)

    code = f"""
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
try {{
  await plugin['tool.execute.before'](
    {{tool: 'task'}},
    {{args: {{model: 'opus'}}}}
  )
  console.log(JSON.stringify({{denied: false, message: ""}}))
}} catch (e) {{
  console.log(JSON.stringify({{denied: true, message: String(e.message || e)}}))
}}
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".mjs", delete=False) as f:
        f.write(code)
        tmp_script = f.name
    try:
        proc = subprocess.run(
            ["node", "--experimental-strip-types", tmp_script],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=str(ROOT),
            env={**os.environ, **env},
        )
        if proc.returncode != 0:
            raise AssertionError(
                f"Node exit {proc.returncode}:\nstderr: {proc.stderr[:800]}\n"
                f"stdout: {proc.stdout[:400]}"
            )
        data = json.loads(proc.stdout.strip().splitlines()[-1])
        msg = data.get("message", "")
        target_pct = None
        m = re.search(r"target=(\d+)%", msg)
        if m:
            target_pct = int(m.group(1))
        data["target_pct"] = target_pct
        return data
    finally:
        try:
            os.unlink(tmp_script)
        except OSError:
            pass


def _write_state(path: str, history: list[str]) -> None:
    with open(path, "w") as f:
        json.dump({"history": history}, f)


def _write_config(path: str, target_share: float, until_epoch: int | None) -> None:
    cfg: dict = {"target_share": target_share}
    if until_epoch is not None:
        cfg["until_epoch"] = until_epoch
    with open(path, "w") as f:
        json.dump(cfg, f)


class TestSonnetTargetExpiryBehavior:
    """Behavioral tests that invoke the real readTargetShare() through the hook."""

    def test_future_epoch_enforces_config_target(self, tmp_path):
        """until_epoch in the future → config's target_share (0.50) is enforced.

        With target=0.50, a projected share of 0.833 has headroom → ALLOWED.
        """
        state_path = str(tmp_path / "state.json")
        cfg_path = str(tmp_path / "config.json")
        _write_state(state_path, NO_HEADROOM_HISTORY)
        _write_config(cfg_path, target_share=0.50, until_epoch=int(time.time()) + 86400)

        result = _run_hook_with_config(cfg_path, state_path)
        assert not result["denied"], (
            f"target=0.50 should allow 0.833 share (headroom exists), "
            f"but got denied: {result['message']}"
        )

    def test_past_epoch_reverts_to_default(self, tmp_path):
        """until_epoch in the past → reverts to default 0.91 → DENIED.

        Config says 0.50 but the epoch has expired, so the hook uses the
        default 0.91. With projected share 0.833 < 0.91 → DENIED, and the
        deny message shows target=91%.
        """
        state_path = str(tmp_path / "state.json")
        cfg_path = str(tmp_path / "config.json")
        _write_state(state_path, NO_HEADROOM_HISTORY)
        _write_config(cfg_path, target_share=0.50, until_epoch=int(time.time()) - 86400)

        result = _run_hook_with_config(cfg_path, state_path)
        assert result["denied"], (
            "Expired until_epoch should revert to default 0.91 and deny "
            "(0.833 < 0.91), but dispatch was allowed"
        )
        assert result["target_pct"] == 91, (
            f"Deny message should show target=91% (default after expiry), "
            f"got target_pct={result['target_pct']}: {result['message']}"
        )

    def test_missing_config_defaults_to_band_mode(self, tmp_path):
        """No config file at all → defaults to 0.91 → DENIED with target=91%."""
        state_path = str(tmp_path / "state.json")
        nonexistent_cfg = str(tmp_path / "nonexistent_config.json")
        _write_state(state_path, NO_HEADROOM_HISTORY)

        result = _run_hook_with_config(nonexistent_cfg, state_path)
        assert result["denied"], (
            "Missing config should default to 0.91 and deny (0.833 < 0.91)"
        )
        assert result["target_pct"] == 91, (
            f"Deny message should show target=91% (default), "
            f"got target_pct={result['target_pct']}: {result['message']}"
        )

    def test_env_var_overrides_expired_config(self, tmp_path):
        """GLUDD_SONNET_TARGET_SHARE=0.50 beats even an expired config → ALLOWED.

        Env var has highest priority: it overrides the config file entirely,
        so even with an expired until_epoch, the env-set 0.50 target applies.
        With target=0.50, projected 0.833 ≥ 0.50 → headroom exists → ALLOWED.
        """
        state_path = str(tmp_path / "state.json")
        cfg_path = str(tmp_path / "config.json")
        _write_state(state_path, NO_HEADROOM_HISTORY)
        _write_config(cfg_path, target_share=0.99, until_epoch=int(time.time()) - 86400)

        result = _run_hook_with_config(cfg_path, state_path, env_share="0.50")
        assert not result["denied"], (
            f"GLUDD_SONNET_TARGET_SHARE=0.50 should override expired config and allow, "
            f"but got denied: {result['message']}"
        )

    def test_env_var_overrides_active_config(self, tmp_path):
        """GLUDD_SONNET_TARGET_SHARE overrides even a valid (unexpired) config."""
        state_path = str(tmp_path / "state.json")
        cfg_path = str(tmp_path / "config.json")
        _write_state(state_path, NO_HEADROOM_HISTORY)
        _write_config(cfg_path, target_share=0.50, until_epoch=int(time.time()) + 86400)

        # Env says 0.91 (stricter than config's 0.50)
        result = _run_hook_with_config(cfg_path, state_path, env_share="0.91")
        assert result["denied"], (
            "GLUDD_SONNET_TARGET_SHARE=0.91 should override config's 0.50 and deny"
        )
        assert result["target_pct"] == 91, (
            f"Should show target=91% (from env override), "
            f"got {result['target_pct']}"
        )


class TestSonnetTargetConfigFormat:
    """Verify config file format and structural requirements."""

    def test_config_is_valid_json_with_required_fields(self, tmp_path):
        """Config file is valid JSON containing target_share and until_epoch."""
        cfg_path = str(tmp_path / "config.json")
        _write_config(cfg_path, target_share=0.67, until_epoch=int(time.time()) + 3600)

        with open(cfg_path) as f:
            cfg = json.load(f)

        assert "target_share" in cfg, "config must have target_share"
        assert isinstance(cfg["target_share"], (int, float)), (
            "target_share must be numeric"
        )
        assert "until_epoch" in cfg, "config must have until_epoch"
        assert isinstance(cfg["until_epoch"], int), (
            "until_epoch must be an integer (unix timestamp)"
        )

    def test_config_without_until_epoch_still_enforces_target(self, tmp_path):
        """Config with target_share but no until_epoch → target is used as-is.

        readTargetShare only checks until_epoch when it's present (typeof number).
        If absent, target_share applies without expiry.
        """
        state_path = str(tmp_path / "state.json")
        cfg_path = str(tmp_path / "config.json")
        _write_state(state_path, NO_HEADROOM_HISTORY)

        # Config with only target_share=0.50, no until_epoch
        _write_config(cfg_path, target_share=0.50, until_epoch=None)

        result = _run_hook_with_config(cfg_path, state_path)
        assert not result["denied"], (
            f"target=0.50 without until_epoch should be enforced directly (allowed), "
            f"but got denied: {result['message']}"
        )

    def test_invalid_json_config_falls_back_to_default(self, tmp_path):
        """Malformed JSON config → readTargetShare catches and returns default."""
        state_path = str(tmp_path / "state.json")
        cfg_path = str(tmp_path / "broken_config.json")
        _write_state(state_path, NO_HEADROOM_HISTORY)
        with open(cfg_path, "w") as f:
            f.write("{ this is not valid JSON !!!")

        result = _run_hook_with_config(cfg_path, state_path)
        assert result["denied"], (
            "Broken JSON should fall back to default 0.91 and deny"
        )
        assert result["target_pct"] == 91, (
            f"Should show target=91% (default from broken config), "
            f"got {result['target_pct']}"
        )

    def test_nan_target_share_falls_back_to_default(self, tmp_path):
        """target_share that parses to NaN → falls back to default 0.91."""
        state_path = str(tmp_path / "state.json")
        cfg_path = str(tmp_path / "config.json")
        _write_state(state_path, NO_HEADROOM_HISTORY)
        _write_config(cfg_path, target_share=0.50, until_epoch=int(time.time()) + 86400)
        # Overwrite target_share with a non-numeric value
        with open(cfg_path, "w") as f:
            json.dump({"target_share": "not-a-number", "until_epoch": int(time.time()) + 86400}, f)

        result = _run_hook_with_config(cfg_path, state_path)
        assert result["denied"], "NaN target_share should fall back to default 0.91 and deny"
        assert result["target_pct"] == 91, (
            f"Should show target=91% (default from NaN target_share), "
            f"got {result['target_pct']}"
        )
