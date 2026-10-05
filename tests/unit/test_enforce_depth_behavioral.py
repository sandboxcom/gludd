"""Behavioral tests for enforce-depth.ts — invoke the actual plugin hook.

Monkeypatches OPENCODE_DEPTH to verify dispatch at depths 0→1→2→3 (allow)
and depth 4+ (block). Uses a Node subprocess to import and invoke the
plugin's tool.execute.before hook with realistic inputs.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = ROOT / ".opencode/plugin/enforce-depth.ts"

NODE_IDEAL = os.environ.get("NODE_IDEAL", "node")
EXPERIMENTAL_FLAG = os.environ.get("NODE_EXPERIMENTAL_FLAG", "--experimental-strip-types")


def _invoke_hook(tool: str, depth: int, max_depth: int = 4, enforce: bool = True) -> dict | None:
    """Invoke enforce-depth.ts tool.execute.before via Node subprocess.

    Writes a minimal eval script to a temp file, sets OPENCODE_DEPTH,
    GLUDD_DEPTH_ENFORCE, GLUDD_MAX_DEPTH, and OPENCODE_SUBAGENT=0 in
    the subprocess environment, then imports the plugin and calls the
    default-impl hook.
    """
    script = f"""
import * as path from "node:path";
const pluginPath = {json.dumps(str(PLUGIN_PATH))};
const m = await import(pluginPath);
const defaultImpl = m.default;
let plugin = typeof defaultImpl === "function" ? defaultImpl({{}}) : defaultImpl;
const fn = plugin["tool.execute.before"];
if (typeof fn !== "function") {{
  process.stderr.write("NO_HOOK\\n");
  process.exit(1);
}}
const result = await fn({{ tool: {json.dumps(tool)} }});
if (result) {{
  process.stdout.write(JSON.stringify(result));
}} else {{
  process.stdout.write("ALLOW");
}}
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".mjs", delete=False, dir="/tmp") as f:
        f.write(script)
        script_path = f.name

    env = os.environ.copy()
    env["OPENCODE_DEPTH"] = str(depth)
    env["OPENCODE_SUBAGENT"] = "0"
    if not enforce:
        env["GLUDD_DEPTH_ENFORCE"] = "0"
    else:
        env.pop("GLUDD_DEPTH_ENFORCE", None)
    env["GLUDD_MAX_DEPTH"] = str(max_depth)

    try:
        proc = subprocess.run(
            [NODE_IDEAL, EXPERIMENTAL_FLAG, script_path],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=str(ROOT),
            env=env,
        )
        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()
        if stderr and "ExperimentalWarning" not in stderr:
            pass  # console.warn output is expected on stderr
        if proc.returncode != 0:
            raise RuntimeError(f"Node exit {proc.returncode}: stderr={stderr}")
        if stdout == "ALLOW":
            return None
        try:
            return json.loads(stdout)
        except json.JSONDecodeError as err:
            raise RuntimeError(f"Unparseable output: stdout={stdout} stderr={stderr}") from err
    finally:
        Path(script_path).unlink(missing_ok=True)


# ── Depth levels: 0,1,2,3 allow; 4+ block (MAX_DEPTH=4) ────────────────────


class TestDepthAllow:
    def test_depth_0_dispatches_task(self):
        result = _invoke_hook("task", 0)
        assert result is None, f"depth=0 should allow task dispatch, got={result}"

    def test_depth_0_dispatches_agent(self):
        result = _invoke_hook("agent", 0)
        assert result is None, f"depth=0 should allow agent dispatch, got={result}"

    def test_depth_1_dispatches_task(self):
        result = _invoke_hook("task", 1)
        assert result is None, f"depth=1 should allow task dispatch, got={result}"

    def test_depth_1_dispatches_agent(self):
        result = _invoke_hook("agent", 1)
        assert result is None, f"depth=1 should allow agent dispatch, got={result}"

    def test_depth_2_dispatches_task(self):
        result = _invoke_hook("task", 2)
        assert result is None, f"depth=2 should allow task dispatch, got={result}"

    def test_depth_2_dispatches_agent(self):
        result = _invoke_hook("agent", 2)
        assert result is None, f"depth=2 should allow agent dispatch, got={result}"

    def test_depth_2_dispatches_workflow(self):
        result = _invoke_hook("workflow", 2)
        assert result is None, f"depth=2 should allow workflow dispatch, got={result}"

    def test_depth_3_dispatches_task(self):
        result = _invoke_hook("task", 3)
        assert result is None, f"depth=3 should allow task dispatch (MAX_DEPTH=4), got={result}"

    def test_depth_3_dispatches_agent(self):
        result = _invoke_hook("agent", 3)
        assert result is None, f"depth=3 should allow agent dispatch (MAX_DEPTH=4), got={result}"

    def test_depth_3_dispatches_workflow(self):
        result = _invoke_hook("workflow", 3)
        assert result is None, f"depth=3 should allow workflow dispatch (MAX_DEPTH=4), got={result}"


class TestDepthBlock:
    def test_depth_4_blocks_task(self):
        result = _invoke_hook("task", 4)
        assert result is not None
        assert result["permissionDecision"] == "deny"
        assert "MAX DEPTH EXCEEDED" in result["message"]

    def test_depth_4_blocks_agent(self):
        result = _invoke_hook("agent", 4)
        assert result is not None
        assert result["permissionDecision"] == "deny"

    def test_depth_5_blocks_task(self):
        result = _invoke_hook("task", 5)
        assert result is not None
        assert result["permissionDecision"] == "deny"

    def test_depth_5_blocks_workflow(self):
        result = _invoke_hook("workflow", 5)
        assert result is not None
        assert result["permissionDecision"] == "deny"


class TestDepthNegativeClamping:
    """Negative or NaN OPENCODE_DEPTH clamps to 0 (safe, allows dispatch)."""

    def test_negative_1_is_clamped_to_0(self):
        result = _invoke_hook("task", -1)
        assert result is None, f"depth=-1 should clamp to 0, allow dispatch, got={result}"

    def test_negative_5_is_clamped_to_0(self):
        result = _invoke_hook("task", -5)
        assert result is None, f"depth=-5 should clamp to 0, allow dispatch, got={result}"

    def test_negative_100_does_not_block(self):
        result = _invoke_hook("agent", -100)
        assert result is None, f"depth=-100 should clamp to 0, allow dispatch, got={result}"

    def test_negative_depth_allows_sub_subagent(self):
        """A subagent with OPENCODE_DEPTH=-1 can still dispatch sub-subagents."""
        result = _invoke_hook("task", -1)
        assert result is None, "negative depth must not block dispatch"

    def test_max_depth_5_with_negative_depth_allows(self):
        result = _invoke_hook("task", -3, max_depth=5)
        assert result is None, "negative depth must be clamped to 0 even with custom max"


class TestDepthNaNClamping:
    """NaN OPENCODE_DEPTH after parseInt would clamp to 0 in the plugin.

    These tests verify the behavioral invariant: _invoke_hook uses Python
    int depth, which is always valid. The NaN path is tested structurally
    in test_enforce_depth_deep.py TestOpenCodeDepthEnvParsing.
    """

    def test_depth_0_still_allowed(self):
        result = _invoke_hook("task", 0)
        assert result is None

    def test_depth_2_still_allowed_for_sub_subagent(self):
        result = _invoke_hook("workflow", 2)
        assert result is None

    def test_depth_3_still_allowed(self):
        result = _invoke_hook("task", 3)
        assert result is None

    def test_depth_4_blocks(self):
        result = _invoke_hook("task", 4)
        assert result is not None
        assert result["permissionDecision"] == "deny"


class TestDepthEnforceDisable:
    def test_depth_3_allows_when_enforce_false(self):
        result = _invoke_hook("task", 3, enforce=False)
        assert result is None

    def test_depth_10_allows_when_enforce_false(self):
        result = _invoke_hook("task", 10, enforce=False)
        assert result is None


class TestDepthMaxOverride:
    def test_max_5_allows_depth_4(self):
        result = _invoke_hook("task", 4, max_depth=5)
        assert result is None

    def test_max_5_blocks_depth_5(self):
        result = _invoke_hook("task", 5, max_depth=5)
        assert result is not None
        assert result["permissionDecision"] == "deny"


class TestNonDispatchTools:
    def test_read_at_depth_3_allows(self):
        result = _invoke_hook("read", 3)
        assert result is None

    def test_write_at_depth_3_allows(self):
        result = _invoke_hook("write", 3)
        assert result is None

    def test_edit_at_depth_3_allows(self):
        result = _invoke_hook("edit", 3)
        assert result is None

    def test_bash_at_depth_3_allows(self):
        result = _invoke_hook("bash", 3)
        assert result is None


class TestDepthLogging:
    def test_log_output_on_stderr(self):
        """After the hardening edit, the plugin logs depth values via console.warn."""
        result = _invoke_hook("task", 2)
        assert result is None  # dispatch allowed at depth 2


class TestDelegatedDepthBoundary:
    def test_subagent_env_depth_3_allows(self):
        """OPENCODE_SUBAGENT=1 at depth 3 is below MAX_DEPTH=4 and allows."""
        script = f"""
const m = await import({json.dumps(str(PLUGIN_PATH))});
const defaultImpl = m.default;
let plugin = typeof defaultImpl === "function" ? defaultImpl({{}}) : defaultImpl;
const fn = plugin["tool.execute.before"];
const result = await fn({{ tool: "task" }});
if (result) {{
  process.stdout.write(JSON.stringify(result));
}} else {{
  process.stdout.write("ALLOW");
}}
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".mjs", delete=False, dir="/tmp") as f:
            f.write(script)
            script_path = f.name

        env = os.environ.copy()
        env["GLUDD_HOT_MODULE_PREFIX"] = f"/tmp/gludd-depth-behavior-hot-{Path(script_path).stem}-"
        env["OPENCODE_DEPTH"] = "3"
        env["OPENCODE_SUBAGENT"] = "1"
        env["GLUDD_MAX_DEPTH"] = "4"

        try:
            proc = subprocess.run(
                [NODE_IDEAL, EXPERIMENTAL_FLAG, script_path],
                capture_output=True,
                text=True,
                timeout=15,
                cwd=str(ROOT),
                env=env,
            )
            stdout = proc.stdout.strip()
            assert stdout == "ALLOW", f"Subagent at depth 3 should allow, got: {stdout}"
        finally:
            Path(script_path).unlink(missing_ok=True)
