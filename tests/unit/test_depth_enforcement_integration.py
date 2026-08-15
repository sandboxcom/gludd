"""Integration test for enforce-depth.ts — end-to-end plugin pipeline.

Invokes the FULL plugin factory + hot-reload proxy path via Node subprocess,
not just the defaultImpl hook. Tests the complete chain:
  import plugin → factory({}) → plugin["tool.execute.before"](input)
with realistic OPENCODE_DEPTH env values at each depth level.

Distinct from test_enforce_depth_behavioral.py which calls defaultImpl directly.
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


def _invoke_full_plugin(
    tool: str,
    depth: int,
    *,
    max_depth: int = 3,
    enforce: bool = True,
    is_subagent: bool = False,
) -> dict | None:
    """Invoke the FULL enforce-depth.ts plugin pipeline via Node subprocess.

    Imports the module (factory function), calls factory({}), then calls
    plugin["tool.execute.before"](input). This exercises the complete
    hot-reload proxy path, not just the defaultImpl hook.
    """
    script = f"""
const m = await import({json.dumps(str(PLUGIN_PATH))});
const factory = m.default;
let plugin = typeof factory === "function" ? factory({{}}) : factory;
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
    env["OPENCODE_SUBAGENT"] = "1" if is_subagent else "0"
    if not enforce:
        env["GLUDD_DEPTH_ENFORCE"] = "0"
    else:
        env.pop("GLUDD_DEPTH_ENFORCE", None)
    env["GLUDD_MAX_DEPTH"] = str(max_depth)
    # Hermetic disengage state: a disengaged orchestrator session must not
    # change enforcement results. Point both disengage paths at nonexistent
    # files so isDisengaged() returns false regardless of /tmp session state.
    env["GLUDD_DISENGAGE_PATH"] = "/tmp/gludd-depth-test-disengage-absent.json"
    env["GLUDD_DISENGAGE_NEXT_PATH"] = "/tmp/gludd-depth-test-disengage-next-absent.json"

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
        if proc.returncode != 0:
            raise RuntimeError(f"Node exit {proc.returncode}: stderr={stderr}: stdout={stdout}")
        if stdout == "ALLOW":
            return None
        try:
            return json.loads(stdout)
        except json.JSONDecodeError as err:
            raise RuntimeError(f"Unparseable output: {stdout!r} stderr={stderr!r}") from err
    finally:
        Path(script_path).unlink(missing_ok=True)


# ── Depth 0/1/2: all dispatch tools allowed ─────────────────────────────────


class TestDepthAllow:
    """Depths 0, 1, 2 permit dispatch of task, agent, and workflow."""

    def test_depth_0_task_allowed(self):
        assert _invoke_full_plugin("task", 0) is None

    def test_depth_0_agent_allowed(self):
        assert _invoke_full_plugin("agent", 0) is None

    def test_depth_0_workflow_allowed(self):
        assert _invoke_full_plugin("workflow", 0) is None

    def test_depth_1_task_allowed(self):
        assert _invoke_full_plugin("task", 1) is None

    def test_depth_1_agent_allowed(self):
        assert _invoke_full_plugin("agent", 1) is None

    def test_depth_1_workflow_allowed(self):
        assert _invoke_full_plugin("workflow", 1) is None

    def test_depth_2_task_allowed(self):
        assert _invoke_full_plugin("task", 2) is None

    def test_depth_2_agent_allowed(self):
        assert _invoke_full_plugin("agent", 2) is None

    def test_depth_2_workflow_allowed(self):
        assert _invoke_full_plugin("workflow", 2) is None


# ── Depth 3+: all dispatch tools blocked ────────────────────────────────────


class TestDepthBlock:
    """Depth >= MAX_DEPTH (default 3) blocks all dispatch tools."""

    def test_depth_3_task_blocked(self):
        result = _invoke_full_plugin("task", 3)
        assert result is not None
        assert result["permissionDecision"] == "deny"
        assert "MAX DEPTH EXCEEDED" in result["message"]
        assert "depth=3" in result["message"]

    def test_depth_3_agent_blocked(self):
        result = _invoke_full_plugin("agent", 3)
        assert result is not None
        assert result["permissionDecision"] == "deny"

    def test_depth_3_workflow_blocked(self):
        result = _invoke_full_plugin("workflow", 3)
        assert result is not None
        assert result["permissionDecision"] == "deny"

    def test_depth_4_task_blocked(self):
        result = _invoke_full_plugin("task", 4)
        assert result is not None
        assert result["permissionDecision"] == "deny"

    def test_depth_5_workflow_blocked(self):
        result = _invoke_full_plugin("workflow", 5)
        assert result is not None
        assert result["permissionDecision"] == "deny"


# ── GLUDD_MAX_DEPTH override ───────────────────────────────────────────────


class TestMaxDepthOverride:
    """GLUDD_MAX_DEPTH env var changes the blocking threshold."""

    def test_max_5_allows_depth_4(self):
        assert _invoke_full_plugin("task", 4, max_depth=5) is None

    def test_max_5_blocks_depth_5(self):
        result = _invoke_full_plugin("task", 5, max_depth=5)
        assert result is not None
        assert result["permissionDecision"] == "deny"
        assert "limit=5" in result["message"]

    def test_max_2_allows_depth_1(self):
        assert _invoke_full_plugin("task", 1, max_depth=2) is None

    def test_max_2_blocks_depth_2(self):
        result = _invoke_full_plugin("task", 2, max_depth=2)
        assert result is not None
        assert result["permissionDecision"] == "deny"

    def test_max_1_blocks_depth_1(self):
        result = _invoke_full_plugin("task", 1, max_depth=1)
        assert result is not None
        assert result["permissionDecision"] == "deny"


# ── GLUDD_DEPTH_ENFORCE=0 disables ──────────────────────────────────────────


class TestEnforceDisable:
    """GLUDD_DEPTH_ENFORCE=0 bypasses all enforcement."""

    def test_depth_3_allowed_when_enforce_disabled(self):
        assert _invoke_full_plugin("task", 3, enforce=False) is None

    def test_depth_10_allowed_when_enforce_disabled(self):
        assert _invoke_full_plugin("task", 10, enforce=False) is None

    def test_agent_depth_3_allowed_when_enforce_disabled(self):
        assert _invoke_full_plugin("agent", 3, enforce=False) is None


# ── Non-dispatch tools always pass through ──────────────────────────────────


class TestNonDispatchPassthrough:
    """read, write, edit, bash always pass regardless of depth."""

    def test_read_at_depth_3_allowed(self):
        assert _invoke_full_plugin("read", 3) is None

    def test_write_at_depth_3_allowed(self):
        assert _invoke_full_plugin("write", 3) is None

    def test_edit_at_depth_3_allowed(self):
        assert _invoke_full_plugin("edit", 3) is None

    def test_bash_at_depth_3_allowed(self):
        assert _invoke_full_plugin("bash", 3) is None

    def test_unknown_tool_at_depth_3_allowed(self):
        assert _invoke_full_plugin("grep", 3) is None


# ── Subagent enforcement (no bypass) ────────────────────────────────────────


class TestSubagentEnforced:
    """Depth enforcement fires INSIDE subagents — enforce-depth is the ONE
    depth-only plugin that intentionally does NOT bypass subagents
    (OPENCODE_DEPTH is framework-managed per nesting level)."""

    def test_subagent_depth_3_allows_task(self):
        assert _invoke_full_plugin("task", 3, max_depth=4, is_subagent=True) is None

    def test_subagent_depth_4_blocks_agent(self):
        result = _invoke_full_plugin("agent", 4, is_subagent=True)
        assert result is not None
        assert result["permissionDecision"] == "deny"
        assert "MAX DEPTH EXCEEDED" in result["message"]

    def test_subagent_depth_10_blocks_workflow(self):
        result = _invoke_full_plugin("workflow", 10, is_subagent=True)
        assert result is not None
        assert result["permissionDecision"] == "deny"
