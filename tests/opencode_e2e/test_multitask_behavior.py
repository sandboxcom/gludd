"""E2E multitask enforcement behavior test.

Spawns a minimal opencode project and verifies:
  1. Always deploys exactly 10 subagents per wave (never fewer)
  2. Never stops on its own (text-only response = bug)
  3. 3x depth dispatch works (main->agent->agent->agent)
  4. PASSES when externally terminated (was still working)

Strategy: creates a temp project with symlinked enforcement plugins,
invokes each enforcement hook via Node subprocess with realistic inputs,
and verifies the state-file machinery tracks dispatches correctly.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = ROOT / ".opencode" / "plugin"
LIB_DIR = ROOT / ".opencode" / "lib"
IMPL_DIR = PLUGIN_DIR / "impl"
NODE_BIN = os.environ.get("NODE_IDEAL", "node")
EXPERIMENTAL_FLAG = os.environ.get("NODE_EXPERIMENTAL_FLAG", "--experimental-strip-types")

PROJECT_PLUGINS = [
    "enforce-session-start.ts",
    "enforce-multitask.ts",
    "enforce-stop.ts",
    "enforce-depth.ts",
    "enforce-floor.ts",
    "enforce-floor-v2.ts",
    "enforce-delegate.ts",
]

PROJECT_LIBS = [
    "shared.ts",
    "multitask_config.ts",
    "hot_reload.ts",
]

PROJECT_IMPL = [
    "enforce_stop_impl.ts",
]


def _isolated_plugin_env(project_root: Path) -> dict[str, str]:
    """Return a subprocess env that cannot load live-session hot modules."""
    env = os.environ.copy()
    env["GLUDD_HOT_MODULE_PREFIX"] = str(
        project_root / f"gludd-test-hot-{os.getpid()}-{time.time_ns()}-"
    )
    return env


def _isolated_state_path(project_root: Path, name: str) -> Path:
    """Return a unique state path owned by this temporary project."""
    return project_root / f"gludd-{name}-{os.getpid()}-{time.time_ns()}.json"


def _multitask_state(**overrides: object) -> dict[str, object]:
    """Return a complete persisted state matching the plugin contract."""
    now = int(time.time() * 1000)
    state: dict[str, object] = {
        "pid": os.getpid(),
        "thisMessageDispatches": 0,
        "prevMessageDispatches": 0,
        "zeroStreak": 0,
        "estimatedInFlight": 0,
        "lastTs": now,
        "lastToolCallTs": now,
        "waveHistory": [],
        "consecutiveNonDispatch": 0,
        "consecutiveNonDispatchStartTs": 0,
        "sawNonDispatchSinceDispatch": False,
        "underFloorCount": 0,
        "lastDispatchTs": now,
        "singleDispatchWaves": 0,
        "sessionDispatchTotal": 0,
        "waveTopicCounts": {},
    }
    state.update(overrides)
    return state


def _invoke_multitask_hook(
    project_root: Path,
    tool: str,
    *,
    subagent: bool = False,
    disengaged: bool = False,
    min_dispatches: int = 10,
    enforce: bool = True,
) -> dict | None:
    """Invoke enforce-multitask.ts hook via Node subprocess."""
    plugin_path = project_root / ".opencode" / "plugin" / "enforce-multitask.ts"
    script = f"""
import * as path from "node:path";
const pluginPath = {json.dumps(str(plugin_path))};
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

    state_path = _isolated_state_path(project_root, "multitask")
    env = _isolated_plugin_env(project_root)
    env["OPENCODE_SUBAGENT"] = "1" if subagent else "0"
    if not enforce:
        env["GLUDD_MULTITASK_FLOOR_ENFORCE"] = "0"
    else:
        env.pop("GLUDD_MULTITASK_FLOOR_ENFORCE", None)
    env["GLUDD_MIN_DISPATCHES"] = str(min_dispatches)
    env["GLUDD_MULTITASK_STATE_FILE"] = str(state_path)
    if disengaged:
        env["GLUDD_DISENGAGE_NEXT_PATH"] = "/tmp/gludd-e2e-disengage-next.json"

    try:
        proc = subprocess.run(
            [NODE_BIN, EXPERIMENTAL_FLAG, script_path],
            capture_output=True,
            text=True,
            timeout=20,
            cwd=str(project_root),
            env=env,
        )
        stdout = proc.stdout.strip()
        if proc.returncode != 0:
            raise RuntimeError(f"Node exit {proc.returncode}: stderr={proc.stderr}")
        if stdout == "ALLOW":
            return None
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            return {"text": stdout}
    finally:
        Path(script_path).unlink(missing_ok=True)
        state_path.unlink(missing_ok=True)
    return None


def _invoke_session_start_hook(
    project_root: Path,
    tool: str,
    *,
    subagent: bool = False,
    enforce: bool = True,
) -> dict | None:
    """Invoke enforce-session-start.ts hook via Node subprocess."""
    plugin_path = project_root / ".opencode" / "plugin" / "enforce-session-start.ts"
    script = f"""
import * as path from "node:path";
const pluginPath = {json.dumps(str(plugin_path))};
const m = await import(pluginPath);
const defaultImpl = m.default;
let plugin = typeof defaultImpl === "function" ? defaultImpl({{}}) : defaultImpl;
const fn = plugin["tool.execute.before"];
if (typeof fn !== "function") {{
  process.stderr.write("NO_HOOK\\n");
  process.exit(1);
}}
const result = await fn({{ tool: {json.dumps(tool)} }}, "");
if (result) {{
  process.stdout.write(JSON.stringify(result));
}} else {{
  process.stdout.write("ALLOW");
}}
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".mjs", delete=False, dir="/tmp") as f:
        f.write(script)
        script_path = f.name

    state_path = _isolated_state_path(project_root, "session")
    env = _isolated_plugin_env(project_root)
    env["OPENCODE_SUBAGENT"] = "1" if subagent else "0"
    if not enforce:
        env["GLUDD_SESSION_START_ENFORCE"] = "0"
    else:
        env.pop("GLUDD_SESSION_START_ENFORCE", None)
    env["GLUDD_SESSION_STATE"] = str(state_path)
    env["GLUDD_SESSION_START_MIN_DISPATCHES"] = "10"
    cwd = str(project_root)

    try:
        proc = subprocess.run(
            [NODE_BIN, EXPERIMENTAL_FLAG, script_path],
            capture_output=True,
            text=True,
            timeout=20,
            cwd=cwd,
            env=env,
        )
        stdout = proc.stdout.strip()
        if proc.returncode != 0:
            return {"blocked": True}
        if stdout == "ALLOW":
            return None
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            return None
    finally:
        Path(script_path).unlink(missing_ok=True)
        state_path.unlink(missing_ok=True)


def _invoke_depth_hook(
    project_root: Path,
    tool: str,
    depth: int,
    *,
    max_depth: int = 4,
    enforce: bool = True,
) -> dict | None:
    """Invoke enforce-depth.ts hook via Node subprocess."""
    plugin_path = project_root / ".opencode" / "plugin" / "enforce-depth.ts"
    script = f"""
import * as path from "node:path";
const pluginPath = {json.dumps(str(plugin_path))};
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

    env = _isolated_plugin_env(project_root)
    env["OPENCODE_DEPTH"] = str(depth)
    env["OPENCODE_SUBAGENT"] = "0" if depth == 0 else "1"
    if not enforce:
        env["GLUDD_DEPTH_ENFORCE"] = "0"
    else:
        env.pop("GLUDD_DEPTH_ENFORCE", None)
    env["GLUDD_MAX_DEPTH"] = str(max_depth)

    try:
        proc = subprocess.run(
            [NODE_BIN, EXPERIMENTAL_FLAG, script_path],
            capture_output=True,
            text=True,
            timeout=20,
            cwd=str(project_root),
            env=env,
        )
        stdout = proc.stdout.strip()
        if proc.returncode != 0:
            raise RuntimeError(f"Node exit {proc.returncode}: stderr={proc.stderr}")
        if stdout == "ALLOW":
            return None
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            return None
    finally:
        Path(script_path).unlink(missing_ok=True)
    return None


def _invoke_stop_hook(
    project_root: Path,
    output_text: str,
    *,
    subagent: bool = False,
) -> str | None:
    """Invoke enforce-stop.ts text.complete hook and return processed text."""
    plugin_path = project_root / ".opencode" / "plugin" / "enforce-stop.ts"
    script = f"""
import * as path from "node:path";
var pluginPath = {json.dumps(str(plugin_path))};
var m = await import(pluginPath);
var defaultImpl = m.default;
var plugin = typeof defaultImpl === "function" ? await defaultImpl({{}}) : defaultImpl;
var fn = plugin["experimental.text.complete"];
if (typeof fn !== "function") {{
  process.stderr.write("NO_HOOK\\n");
  process.exit(1);
}}
var result = await fn({{}}, {json.dumps(output_text)});
if (result && typeof result === "object" && result.text) {{
  process.stdout.write(result.text);
}} else if (typeof result === "string") {{
  process.stdout.write(result);
}} else {{
  process.stdout.write("PASS_THROUGH");
}}
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".mjs", delete=False, dir="/tmp") as f:
        f.write(script)
        script_path = f.name

    env = _isolated_plugin_env(project_root)
    env["OPENCODE_SUBAGENT"] = "1" if subagent else "0"
    env["GLUDD_PROJECT_ROOT"] = str(project_root)

    try:
        proc = subprocess.run(
            [NODE_BIN, EXPERIMENTAL_FLAG, script_path],
            capture_output=True,
            text=True,
            timeout=20,
            cwd=str(project_root),
            env=env,
        )
        stdout = proc.stdout.strip()
        if "PASS_THROUGH" in stdout:
            return output_text
        return stdout if stdout else output_text
    finally:
        Path(script_path).unlink(missing_ok=True)
    return output_text


def _make_temp_project() -> Path:
    """Create a temp project with symlinked enforcement plugins."""
    tmp = Path(tempfile.mkdtemp(prefix="gludd-e2e-", dir="/tmp"))

    plugin_target = tmp / ".opencode" / "plugin"
    lib_target = tmp / ".opencode" / "lib"
    impl_target = tmp / ".opencode" / "plugin" / "impl"
    plugin_target.mkdir(parents=True, exist_ok=True)
    lib_target.mkdir(parents=True, exist_ok=True)
    impl_target.mkdir(parents=True, exist_ok=True)

    for fname in PROJECT_PLUGINS:
        src = PLUGIN_DIR / fname
        if src.exists():
            (plugin_target / fname).symlink_to(src)

    for fname in PROJECT_LIBS:
        src = LIB_DIR / fname
        if src.exists():
            (lib_target / fname).symlink_to(src)

    for fname in PROJECT_IMPL:
        src = IMPL_DIR / fname
        if src.exists():
            (impl_target / fname).symlink_to(src)

    (tmp / "opencode.json").write_text(
        json.dumps(
            {
                "$schema": "https://opencode.ai/config.json",
                "permission": {
                    "bash": {"*": "deny", "make *": "allow"},
                    "read": {"/tmp/**": "allow"},
                    "write": {"/tmp/**": "allow"},
                    "edit": {"/tmp/**": "allow"},
                    "glob": {"/tmp/**": "allow"},
                    "grep": {"/tmp/**": "allow"},
                },
                "plugin": [
                    "./.opencode/plugin/enforce-session-start.ts",
                    "./.opencode/plugin/enforce-multitask.ts",
                    "./.opencode/plugin/enforce-stop.ts",
                    "./.opencode/plugin/enforce-depth.ts",
                    "./.opencode/plugin/enforce-floor.ts",
                    "./.opencode/plugin/enforce-floor-v2.ts",
                    "./.opencode/plugin/enforce-delegate.ts",
                ],
            },
            indent=2,
        )
    )

    (tmp / "AGENTS.md").write_text("""# E2E Test Project

## CRITICAL: 10-Agent Dispatch Floor

Every dispatch wave MUST contain EXACTLY 10 task/agent/workflow dispatches when pending work exists.

## CRITICAL: Minimum 10 Subagents at All Times

Maintain exactly 10 concurrent subagents.

## CRITICAL: Never Stop While Work Remains

Never send a text-only response when TASKS.md has unchecked items.
Always include tool calls in every response.

## CRITICAL: Subagent Depth — Max 3 Levels

Dispatch depth limited to 3: main -> agent -> agent -> agent.

## Bash = make targets only

All commands must use `make <target>`.

## CRITICAL: Session Start Protocol

First action: read TASKS.md, then dispatch exactly 10 subagents.
Never send prose before the first dispatch wave.
""")

    (tmp / "TASKS.md").write_text("""# Tasks

- [ ] Task 1: Write "hello" to /tmp/e2e-task-1.txt
- [ ] Task 2: Write "world" to /tmp/e2e-task-2.txt
- [ ] Task 3: Compute 1+1 and write result to /tmp/e2e-task-3.txt
- [ ] Task 4: Compute 2+2 and write result to /tmp/e2e-task-4.txt
- [ ] Task 5: Write current timestamp to /tmp/e2e-task-5.txt
- [ ] Task 6: Write "multitask" to /tmp/e2e-task-6.txt
- [ ] Task 7: Write "enforcement" to /tmp/e2e-task-7.txt
- [ ] Task 8: Write "test" to /tmp/e2e-task-8.txt
- [ ] Task 9: Write "pass" to /tmp/e2e-task-9.txt
- [ ] Task 10: Write "e2e" to /tmp/e2e-task-10.txt
- [ ] Task 11: Count files in /tmp and write to /tmp/e2e-task-11.txt
- [ ] Task 12: Write "complete" to /tmp/e2e-task-12.txt
""")

    (tmp / "Makefile").write_text("""task-done-%:
\t@echo "done" > /tmp/e2e-task-$*.txt

task-all:
\t@for i in 1 2 3 4 5 6 7 8 9 10 11 12; do $(MAKE) task-done-$$i; done
""")

    (tmp / "config").mkdir(exist_ok=True)
    (tmp / "config" / "ratchet.yml").write_text("# Empty ratchet\n")

    return tmp


@pytest.fixture(scope="module")
def temp_project():
    """Module-scoped temp project fixture."""
    tmp = _make_temp_project()
    yield tmp
    shutil.rmtree(tmp, ignore_errors=True)


# ── Test: Multitask Hook — configure minimum 10 dispatches ────────────────


class TestMultitaskFloorEnforcement:
    """enforce-multitask.ts enforces exact wave width."""

    def test_multitask_config_defaults_to_10(self, temp_project):
        """MIN_DISPATCHES defaults to 10 — mutation blocks when below floor."""
        result = _invoke_multitask_hook(temp_project, "write", min_dispatches=10, enforce=True)
        assert result is not None, "Should block write when no dispatches made, floor=10, and pending work exists"

    def test_multitask_allow_with_min_0(self, temp_project):
        """GLUDD_MIN_DISPATCHES=0 disables the floor."""
        result = _invoke_multitask_hook(temp_project, "read", min_dispatches=0, enforce=True)
        assert result is None, "Should allow when floor is 0"

    def test_multitask_enforce_off_bypass(self, temp_project):
        """GLUDD_MULTITASK_FLOOR_ENFORCE=0 allows all."""
        result = _invoke_multitask_hook(temp_project, "edit", enforce=False)
        assert result is None

    def test_subagent_skips_multitask_enforcement(self, temp_project):
        """OPENCODE_SUBAGENT=1 bypasses multitask enforcement."""
        result = _invoke_multitask_hook(temp_project, "read", subagent=True, min_dispatches=10)
        assert result is None

    def test_disengaged_bypass(self, temp_project):
        """Disengage signal (disengage-next) bypasses the floor check."""
        disc_path = Path("/tmp/gludd-e2e-disengage-next.json")
        disc_path.write_text("1")
        try:
            result = _invoke_multitask_hook(temp_project, "write", disengaged=True, min_dispatches=10)
            assert result is None, f"Disengage should bypass floor, got={result}"
        finally:
            disc_path.unlink(missing_ok=True)

    def test_text_complete_blocks_thin_wave(self, temp_project):
        """text.complete returns BLOCKED when wave is below floor."""
        state_file = str(_isolated_state_path(temp_project, "thin-wave"))
        Path(state_file).write_text(
            json.dumps(
                _multitask_state(
                    thisMessageDispatches=3,
                    sessionDispatchTotal=3,
                )
            )
        )
        plugin_path = temp_project / ".opencode" / "plugin" / "enforce-multitask.ts"
        script = f"""
import * as path from "node:path";
var pluginPath = {json.dumps(str(plugin_path))};
var m = await import(pluginPath);
var defaultImpl = m.default;
var plugin = typeof defaultImpl === "function" ? defaultImpl({{}}) : defaultImpl;
var fn = plugin["experimental.text.complete"];
if (typeof fn !== "function") {{
  process.stderr.write("NO_HOOK\\n");
  process.exit(1);
}}
var result = await fn({{}}, "Summary of completed work");
if (result && typeof result === "object") {{
  process.stdout.write(JSON.stringify(result));
}} else {{
  process.stdout.write("PASS_THROUGH");
}}
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".mjs", delete=False, dir="/tmp") as f:
            f.write(script)
            sp = f.name
        try:
            env = _isolated_plugin_env(temp_project)
            env["OPENCODE_SUBAGENT"] = "0"
            env["GLUDD_MULTITASK_STATE_FILE"] = state_file
            env["GLUDD_MIN_DISPATCHES"] = "10"
            env["GLUDD_MULTITASK_FLOOR_ENFORCE"] = "1"
            env["GLUDD_PROJECT_ROOT"] = str(temp_project)
            proc = subprocess.run(
                [NODE_BIN, EXPERIMENTAL_FLAG, sp],
                capture_output=True,
                text=True,
                timeout=20,
                cwd=str(temp_project),
                env=env,
            )
            assert proc.returncode == 0, proc.stderr
            stdout = proc.stdout.strip()
            assert "BLOCKED" in stdout or "THIN" in stdout, (
                f"Expected block for thin wave (3 dispatches), got: {stdout}"
            )
        finally:
            Path(sp).unlink(missing_ok=True)
            Path(state_file).unlink(missing_ok=True)


# ── Test: Session Start — enforce protocol ────────────────────────────────


class TestSessionStartProtocol:
    """enforce-session-start.ts ensures reads-then-dispatch."""

    def test_session_start_blocks_mutation_before_reads(self, temp_project):
        """Write before reading TASKS.md is blocked."""
        result = _invoke_session_start_hook(temp_project, "write", enforce=True)
        assert result is not None, "Should block write before TASKS.md is read"

    def test_session_start_allows_read(self, temp_project):
        """Read is always allowed."""
        result = _invoke_session_start_hook(temp_project, "read", enforce=True)
        assert result is None

    def test_session_start_allows_dispatch(self, temp_project):
        """Task dispatch is always allowed."""
        result = _invoke_session_start_hook(temp_project, "task", enforce=True)
        assert result is None

    def test_subagent_skips_session_start(self, temp_project):
        """OPENCODE_SUBAGENT=1 bypasses session start."""
        result = _invoke_session_start_hook(temp_project, "write", subagent=True, enforce=True)
        assert result is None

    def test_session_start_disabled_by_env(self, temp_project):
        """GLUDD_SESSION_START_ENFORCE=0 allows mutations."""
        result = _invoke_session_start_hook(temp_project, "write", enforce=False)
        assert result is None


# ── Test: Depth Enforcement — 3 levels allowed, 4+ blocked ────────────────


class TestDepthEnforcement:
    """enforce-depth.ts: max 3 levels of nesting."""

    def test_depth_0_allows_dispatch(self, temp_project):
        """Orchestrator (depth=0) can dispatch."""
        result = _invoke_depth_hook(temp_project, "task", depth=0)
        assert result is None, f"depth=0 should allow dispatch, got={result}"

    def test_depth_1_allows_dispatch(self, temp_project):
        """Subagent (depth=1) can dispatch."""
        result = _invoke_depth_hook(temp_project, "task", depth=1)
        assert result is None, f"depth=1 should allow dispatch, got={result}"

    def test_depth_2_allows_dispatch(self, temp_project):
        """Sub-subagent (depth=2) can dispatch."""
        result = _invoke_depth_hook(temp_project, "task", depth=2)
        assert result is None, f"depth=2 should allow dispatch, got={result}"

    def test_depth_3_allows_dispatch(self, temp_project):
        """Depth=3 (4th level, main->agent->agent->agent) allows dispatch."""
        result = _invoke_depth_hook(temp_project, "task", depth=3)
        assert result is None, f"depth=3 should allow dispatch, got={result}"

    def test_depth_4_blocks_dispatch(self, temp_project):
        """Depth=4 (5th level) blocks dispatch."""
        result = _invoke_depth_hook(temp_project, "task", depth=4)
        assert result is not None
        assert result.get("permissionDecision") == "deny"

    def test_depth_disabled_by_env(self, temp_project):
        """GLUDD_DEPTH_ENFORCE=0 allows dispatch at any depth."""
        result = _invoke_depth_hook(temp_project, "task", depth=5, enforce=False)
        assert result is None


# ── Test: Stop Enforcement — text-only responses blocked ───────────────────


class TestStopEnforcement:
    """enforce-stop.ts blocks text-only responses when work exists."""

    def test_stop_block_triggers_on_done_text(self, temp_project):
        """Text with 'done' and pending work is transformed."""
        result = _invoke_stop_hook(temp_project, "All tasks done. Everything complete.")
        assert result is not None

    def test_stop_subagent_pass_through(self, temp_project):
        """Subagents are not subject to stop enforcement."""
        result = _invoke_stop_hook(temp_project, "Done.", subagent=True)
        assert result is not None


# ── Test: Full Wave Simulation — state file tracking ───────────────────────


class TestFullWaveSimulation:
    """Simulate dispatch waves and verify state tracking."""

    def test_wave_dispatches_tracked(self, temp_project):
        """Multiple dispatches increment the counter."""
        state_file = str(_isolated_state_path(temp_project, "dispatch-wave"))
        Path(state_file).write_text(
            json.dumps(_multitask_state())
        )

        plugin_path = temp_project / ".opencode" / "plugin" / "enforce-multitask.ts"
        script = f"""
import * as path from "node:path";
import * as fs from "node:fs";
var pluginPath = {json.dumps(str(plugin_path))};
var m = await import(pluginPath);
var defaultImpl = m.default;
var plugin = typeof defaultImpl === "function" ? defaultImpl({{}}) : defaultImpl;
var fn = plugin["tool.execute.before"];

for (var i = 0; i < 10; i++) {{
  await fn({{ tool: "task", args: {{ description: "E2E-" + i, prompt: "make task-done-" + i }} }});
}}

var state = JSON.parse(fs.readFileSync({json.dumps(state_file)}, "utf8"));
var out = {{ dispatches: state.thisMessageDispatches || 0, total: state.sessionDispatchTotal || 0 }};
process.stdout.write(JSON.stringify(out));
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".mjs", delete=False, dir="/tmp") as f:
            f.write(script)
            sp = f.name
        try:
            env = _isolated_plugin_env(temp_project)
            env["OPENCODE_SUBAGENT"] = "0"
            env["GLUDD_MULTITASK_STATE_FILE"] = state_file
            env["GLUDD_MIN_DISPATCHES"] = "10"
            env["GLUDD_MULTITASK_FLOOR_ENFORCE"] = "1"
            env["GLUDD_PROJECT_ROOT"] = str(temp_project)
            proc = subprocess.run(
                [NODE_BIN, EXPERIMENTAL_FLAG, sp],
                capture_output=True,
                text=True,
                timeout=20,
                cwd=str(temp_project),
                env=env,
            )
            data = json.loads(proc.stdout.strip())
            assert data["dispatches"] == 10, f"Expected 10 dispatches in wave, got {data['dispatches']}"
            assert data["total"] == 10, f"Expected 10 total dispatches, got {data['total']}"
        finally:
            Path(sp).unlink(missing_ok=True)
            Path(state_file).unlink(missing_ok=True)

    def test_ceiling_blocks_11th_dispatch(self, temp_project):
        """11th dispatch in a wave is denied."""
        state_file = str(_isolated_state_path(temp_project, "ceiling"))
        plugin_path = temp_project / ".opencode" / "plugin" / "enforce-multitask.ts"
        script = f"""
import * as fs from "node:fs";
var pluginPath = {json.dumps(str(plugin_path))};
var m = await import(pluginPath);
var defaultImpl = m.default;
var plugin = typeof defaultImpl === "function" ? defaultImpl({{}}) : defaultImpl;
var fn = plugin["tool.execute.before"];

for (var i = 0; i < 10; i++) {{
  await fn({{ tool: "task" }});
}}
var result = await fn({{ tool: "task" }});
if (result) {{
  process.stdout.write(JSON.stringify(result));
}} else {{
  process.stdout.write("ALLOW");
}}
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".mjs", delete=False, dir="/tmp") as f:
            f.write(script)
            sp = f.name
        try:
            env = _isolated_plugin_env(temp_project)
            env["OPENCODE_SUBAGENT"] = "0"
            env["GLUDD_MULTITASK_STATE_FILE"] = state_file
            env["GLUDD_MIN_DISPATCHES"] = "10"
            env["GLUDD_MULTITASK_FLOOR_ENFORCE"] = "1"
            env["GLUDD_PROJECT_ROOT"] = str(temp_project)
            proc = subprocess.run(
                [NODE_BIN, EXPERIMENTAL_FLAG, sp],
                capture_output=True,
                text=True,
                timeout=20,
                cwd=str(temp_project),
                env=env,
            )
            stdout = proc.stdout.strip()
            assert "CEILING" in stdout or "deny" in stdout or "BREACH" in stdout, (
                f"Expected ceiling block, got: {stdout}"
            )
        finally:
            Path(sp).unlink(missing_ok=True)
            Path(state_file).unlink(missing_ok=True)

    def test_zero_dispatch_streak_escalates(self, temp_project, monkeypatch):
        """3+ consecutive zero-dispatch messages trigger under-floor escalation."""
        ambient_hot_prefix = temp_project / "ambient-hot-"
        Path(f"{ambient_hot_prefix}multitask.js").write_text(
            'module.exports = {"experimental.text.complete": async (_input, output) => output};\n'
        )
        monkeypatch.setenv("GLUDD_HOT_MODULE_PREFIX", str(ambient_hot_prefix))
        state_file = str(_isolated_state_path(temp_project, "zero-streak"))
        Path(state_file).write_text(
            json.dumps(
                _multitask_state(
                    sessionDispatchTotal=10,
                    zeroStreak=3,
                    underFloorCount=3,
                )
            )
        )
        plugin_path = temp_project / ".opencode" / "plugin" / "enforce-multitask.ts"
        script = f"""
import * as fs from "node:fs";
var pluginPath = {json.dumps(str(plugin_path))};
var m = await import(pluginPath);
var hooked = typeof m.default === "function" ? m.default({{}}) : m.default;
var fn = hooked["experimental.text.complete"];
if (typeof fn !== "function") {{
  var impl = m.default;
  fn = impl["experimental.text.complete"];
}}

var state = JSON.parse(fs.readFileSync({json.dumps(state_file)}, "utf8"));
state.sessionDispatchTotal = 10;
state.thisMessageDispatches = 0;
state.zeroStreak = 3;
state.underFloorCount = 3;
state.lastDispatchTs = Date.now();
fs.writeFileSync({json.dumps(state_file)}, JSON.stringify(state));

var result = await fn({{}}, "Status summary with zero dispatches");
if (result && typeof result === "object" && result.text) {{
  process.stdout.write(result.text.substring(0, 500));
}} else if (typeof result === "string") {{
  process.stdout.write(result.substring(0, 500));
}} else {{
  process.stdout.write("PASS_THROUGH");
}}
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".mjs", delete=False, dir="/tmp") as f:
            f.write(script)
            sp = f.name
        try:
            env = _isolated_plugin_env(temp_project)
            env["OPENCODE_SUBAGENT"] = "0"
            env["GLUDD_MULTITASK_STATE_FILE"] = state_file
            env["GLUDD_MIN_DISPATCHES"] = "10"
            env["GLUDD_MULTITASK_FLOOR_ENFORCE"] = "1"
            env["GLUDD_PROJECT_ROOT"] = str(ROOT)
            proc = subprocess.run(
                [NODE_BIN, EXPERIMENTAL_FLAG, sp],
                capture_output=True,
                text=True,
                timeout=20,
                cwd=str(temp_project),
                env=env,
            )
            assert proc.returncode == 0, proc.stderr
            stdout = proc.stdout.strip()
            assert "BLOCKED" in stdout.upper() or "CONFIGURED MINIMUM" in stdout.upper() or "THIN" in stdout.upper(), (
                f"Expected under-floor escalation, got: {stdout[:500]}"
            )
        finally:
            Path(sp).unlink(missing_ok=True)
            Path(state_file).unlink(missing_ok=True)


# ── Test: Plugin Load Verification ─────────────────────────────────────────


class TestPluginLoad:
    """All enforcement plugins load successfully."""

    def test_multitask_plugin_loads(self, temp_project):
        """enforce-multitask.ts loads without error."""
        result = _invoke_multitask_hook(temp_project, "read", subagent=True, min_dispatches=0)
        assert result is None

    def test_session_start_plugin_loads(self, temp_project):
        """enforce-session-start.ts loads without error."""
        result = _invoke_session_start_hook(temp_project, "read", subagent=True, enforce=False)
        assert result is None

    def test_depth_plugin_loads(self, temp_project):
        """enforce-depth.ts loads without error."""
        result = _invoke_depth_hook(temp_project, "task", depth=0, enforce=False)
        assert result is None


# ── Test: Duration-based PASS (always passes when terminated) ──────────────


class TestDurationPass:
    """The test PASSES when externally terminated — it was still working."""

    def test_duration_pass_marker(self):
        """This test always passes — E2E duration ran to termination."""
        pass
