"""Prove enforce-depth.ts enables subagent nesting: depth 0→1→2.

Verifies the depth plugin allows the full nesting chain:
  orchestrator (depth=0) → subagent (depth=1) → sub-subagent (depth=2)
while blocking a fourth level (depth=3). Invokes the actual plugin hook via
Node subprocess at each level. Also verifies that isSubagent() is NOT used
as a guard — depth enforcement must fire inside subagent contexts.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = ROOT / ".opencode/plugin/enforce-depth.ts"

NODE_IDEAL = os.environ.get("NODE_IDEAL", "node")
EXPERIMENTAL_FLAG = os.environ.get("NODE_EXPERIMENTAL_FLAG", "--experimental-strip-types")


# ── Plugin invocation helpers ────────────────────────────────────────────


def _invoke(
    tool: str,
    depth: int,
    *,
    max_depth: int = 3,
    enforce: bool = True,
    is_subagent: bool = False,
) -> dict | None:
    """Invoke enforce-depth.ts tool.execute.before via Node subprocess.

    Sets OPENCODE_DEPTH, GLUDD_MAX_DEPTH, OPENCODE_SUBAGENT, and
    GLUDD_DEPTH_ENFORCE in the subprocess environment, then imports
    the plugin module and calls the hook.
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
    env["GLUDD_HOT_MODULE_PREFIX"] = f"/tmp/gludd-depth-chain-hot-{Path(script_path).stem}-"
    env["OPENCODE_DEPTH"] = str(depth)
    env["OPENCODE_SUBAGENT"] = "1" if is_subagent else "0"
    if not enforce:
        env["GLUDD_DEPTH_ENFORCE"] = "0"
    else:
        env.pop("GLUDD_DEPTH_ENFORCE", None)
    env["GLUDD_MAX_DEPTH"] = str(max_depth)
    env["GLUDD_DISENGAGE_PATH"] = f"/tmp/gludd-depth-chain-disengage-{Path(script_path).stem}"
    env["GLUDD_DISENGAGE_NEXT_PATH"] = f"/tmp/gludd-depth-chain-next-{Path(script_path).stem}"

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
            raise RuntimeError(f"Node exit {proc.returncode}: stderr={stderr} stdout={stdout}")
        if stdout == "ALLOW":
            return None
        try:
            return json.loads(stdout)
        except json.JSONDecodeError as err:
            raise RuntimeError(f"Unparseable output: stdout={stdout!r} stderr={stderr!r}") from err
    finally:
        Path(script_path).unlink(missing_ok=True)


# ── Nesting chain: depth 0 → 1 → 2 (allowed), depth 3 (blocked) ─────────


class TestNestingChainOrchestrator:
    """Orchestrator (depth=0) can dispatch subagents."""

    def test_orchestrator_dispatches_task(self):
        assert _invoke("task", 0) is None

    def test_orchestrator_dispatches_agent(self):
        assert _invoke("agent", 0) is None

    def test_orchestrator_dispatches_workflow(self):
        assert _invoke("workflow", 0) is None

    def test_orchestrator_as_subagent_context_still_allows(self):
        """Even if OPENCODE_SUBAGENT=1, depth=0 allows dispatch."""
        assert _invoke("task", 0, is_subagent=True) is None


class TestNestingChainSubagent:
    """Subagent (depth=1) can dispatch sub-subagents."""

    def test_subagent_dispatches_task(self):
        assert _invoke("task", 1) is None

    def test_subagent_dispatches_agent(self):
        assert _invoke("agent", 1) is None

    def test_subagent_dispatches_workflow(self):
        assert _invoke("workflow", 1) is None

    def test_subagent_as_subagent_context_still_allows(self):
        """OPENCODE_SUBAGENT=1 with depth=1 still allows dispatch."""
        assert _invoke("task", 1, is_subagent=True) is None


class TestNestingChainSubSubagent:
    """Sub-subagent (depth=2) can dispatch sub-sub-subagents."""

    def test_sub_subagent_dispatches_task(self):
        assert _invoke("task", 2) is None

    def test_sub_subagent_dispatches_agent(self):
        assert _invoke("agent", 2) is None

    def test_sub_subagent_dispatches_workflow(self):
        assert _invoke("workflow", 2) is None

    def test_sub_subagent_as_subagent_context_still_allows(self):
        """OPENCODE_SUBAGENT=1 with depth=2 still allows dispatch."""
        assert _invoke("task", 2, is_subagent=True) is None


class TestNestingChainTerminal:
    """Sub-sub-subagent (depth=3) CANNOT dispatch — nesting limit reached."""

    def test_depth_3_blocks_task(self):
        result = _invoke("task", 3)
        assert result is not None
        assert result["permissionDecision"] == "deny"
        assert "MAX DEPTH EXCEEDED" in result["message"]
        assert "depth=3" in result["message"]

    def test_depth_3_blocks_agent(self):
        result = _invoke("agent", 3)
        assert result is not None
        assert result["permissionDecision"] == "deny"

    def test_depth_3_blocks_workflow(self):
        result = _invoke("workflow", 3)
        assert result is not None
        assert result["permissionDecision"] == "deny"

    def test_depth_3_as_subagent_context_still_blocked(self):
        """OPENCODE_SUBAGENT=1 does NOT bypass depth enforcement.
        Depth=3 blocks even inside a subagent context — the isSubagent()
        guard has been intentionally removed from this plugin."""
        result = _invoke("task", 3, is_subagent=True)
        assert result is not None
        assert result["permissionDecision"] == "deny"
        assert "MAX DEPTH EXCEEDED" in result["message"]

    def test_depth_4_blocks_even_as_subagent(self):
        result = _invoke("agent", 4, is_subagent=True)
        assert result is not None
        assert result["permissionDecision"] == "deny"


# ── Structural: no isSubagent guard ──────────────────────────────────────


class TestNoSubagentGuard:
    """The depth plugin must NOT use isSubagent() as a bypass — it is the
    ONE plugin that enforces limits inside subagent contexts.
    """

    def test_does_not_import_is_subagent(self):
        src = PLUGIN_PATH.read_text()
        assert "isSubagent" not in src, (
            "enforce-depth.ts must NOT import or use isSubagent() — depth enforcement fires inside subagents"
        )

    def test_references_opencode_depth(self):
        src = PLUGIN_PATH.read_text()
        assert "OPENCODE_DEPTH" in src

    def test_no_is_subagent_call_in_default_impl(self):
        src = PLUGIN_PATH.read_text()
        idx = src.find('"tool.execute.before": async', src.find("defaultImpl"))
        after = src[idx : idx + 500] if idx > 0 else src
        assert "isSubagent" not in after, "defaultImpl must not call isSubagent()"

    def test_no_is_subagent_call_in_factory(self):
        src = PLUGIN_PATH.read_text()
        factory_idx = src.find("export default (")
        after = src[factory_idx:]
        assert "isSubagent" not in after, "factory proxy must not call isSubagent()"


# ── node v26 compat ─────────────────────────────────────────────────────


class TestNodeV26Compat:
    def test_no_nested_try_in_catch(self):
        src = PLUGIN_PATH.read_text()
        assert "catch { try" not in src
        assert "catch (e) { try" not in src

    def test_no_type_annotated_catch(self):
        src = PLUGIN_PATH.read_text()
        assert not re.search(r"catch\s*\(\s*\w+\s*:", src)
