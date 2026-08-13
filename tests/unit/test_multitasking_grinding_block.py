"""Runtime test: enforce-multitask.ts grinding detection.

Verifies that the CONSECUTIVE-NON-DISPATCH-STREAK block in enforce-multitask.ts
actually denies non-dispatch calls when pending work exists and the consecutive
non-dispatch counter reaches the threshold.

Uses --experimental-strip-types dynamic import to load the real plugin code.
Env vars MUST be set before import() since MIN_DISPATCHES / THRESHOLD are
computed at module-load time.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import ClassVar

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / ".opencode" / "plugin" / "enforce-multitask.ts"
CONFIG = ROOT / ".opencode" / "lib" / "multitask_config.ts"


def _run_test_script(script: str, env_extra: dict | None = None) -> dict:
    """Run a Node.js .mjs script that imports enforce-multitask.

    Returns the parsed JSON stdout, or raw output on parse failure.
    """
    env = {**os.environ, **(env_extra or {})}
    env.pop("GLUDD_PROJECT_ROOT", None)  # unset so script sets it itself
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".mjs", dir="/tmp", prefix="test_grind_", delete=False,
    ) as f:
        f.write(script)
        script_path = f.name

    try:
        result = subprocess.run(
            ["node", "--experimental-strip-types", script_path],
            capture_output=True, text=True, timeout=20,
            env=env, cwd=str(ROOT),
        )
        combined = (result.stdout or "") + "\n" + (result.stderr or "")
        try:
            out = json.loads((result.stdout or "").strip().split("\n")[-1] or "{}")
        except json.JSONDecodeError:
            out: dict = {"_raw": result.stdout, "_stderr": result.stderr, "_exit": result.returncode}
        out["_combined"] = combined
        out["_exit_code"] = result.returncode
        return out
    finally:
        with __import__("contextlib").suppress(OSError):
            os.unlink(script_path)


# ── Static (structural) tests ──────────────────────────────────────────────


class TestGrindingBlockStatic:
    """Source-level assertions that the grinding detection code exists."""

    @pytest.fixture(scope="class")
    def src(self):
        return PLUGIN.read_text()

    @pytest.fixture(scope="class")
    def config_src(self):
        return CONFIG.read_text()

    def test_consecutive_non_dispatch_threshold_constant(self, src):
        """CONSECUTIVE_NON_DISPATCH_THRESHOLD must be defined and defaults to 5."""
        assert "CONSECUTIVE_NON_DISPATCH_THRESHOLD" in src, (
            "CONSECUTIVE_NON_DISPATCH_THRESHOLD constant must exist in plugin source"
        )

    def test_consecutive_non_dispatch_block_exists(self, src):
        """The CONSECUTIVE-NON-DISPATCH-STREAK deny branch must exist."""
        assert "CONSECUTIVE NON-DISPATCH STREAK" in src, (
            "CONSECUTIVE NON-DISPATCH STREAK deny message must exist in plugin source"
        )

    def test_consecutive_counter_incremented(self, src):
        """consecutiveNonDispatch counter must be incremented for non-dispatch calls."""
        assert "consecutiveNonDispatch++" in src or "consecutiveNonDispatch ++" in src, (
            "consecutiveNonDispatch counter must be incremented"
        )

    def test_consecutive_counter_reset_on_dispatch(self, src):
        """consecutiveNonDispatch must reset to 0 inside the dispatch-tool branch."""
        # The reset line exists: _state.consecutiveNonDispatch = 0 (line ~235)
        assert "_state.consecutiveNonDispatch = 0" in src, (
            "consecutiveNonDispatch counter must be reset to 0 in dispatch branch"
        )
        assert "// Reset the consecutive-non-dispatch streak FIRST" in src, (
            "Comment confirming counter reset in dispatch branch must exist"
        )

    def test_env_var_tunable_threshold(self, src, config_src):
        """Threshold must be env-tunable via GLUDD_CONSECUTIVE_NON_DISPATCH_THRESHOLD."""
        assert "CONSECUTIVE_NON_DISPATCH_THRESHOLD" in src
        assert "GLUDD_CONSECUTIVE_NON_DISPATCH_THRESHOLD" in config_src, (
            "CONSECUTIVE_NON_DISPATCH_THRESHOLD must read from env var"
        )

    def test_defaultimpl_is_module_private(self, src):
        """The fallback implementation must not become a loader-visible export."""
        assert "const defaultImpl: HotModule" in src
        assert "export const defaultImpl" not in src

    def test_state_isolation_uses_configurable_file_not_named_reset(
        self, src, config_src,
    ):
        """Tests isolate state through the configured file and a fresh process."""
        assert "MULTITASK_STATE_FILE" in src
        assert "GLUDD_MULTITASK_STATE_FILE" in config_src
        assert "export function resetMultitaskState" not in src


# ── Runtime tests ──────────────────────────────────────────────────────────


class TestGrindingBlockRuntime:
    """Invoke the actual plugin hooks via --experimental-strip-types."""

    SCRIPTS: ClassVar[list[str]] = []  # Track created scripts for cleanup

    def _create_temp_project(self, tasks_content: str) -> str:
        """Create a temp project dir with a TASKS.md. Returns path."""
        tmpdir = Path(tempfile.mkdtemp(prefix="test_grind_proj_", dir="/tmp"))
        (tmpdir / "TASKS.md").write_text(tasks_content)
        return str(tmpdir)

    # ── Helper: build a script that sets env, imports plugin, runs hooks ────

    def _make_base_script(
        self, min_dispatch: int = 2, threshold: int = 3,
        tasks_md: str = "# Tasks\n\n- [ ] Unchecked test task\n",
    ) -> tuple[str, str, str, str]:
        """Build the test script and return (script, tmp_project, state_file, script_path).

        Sets env vars BEFORE dynamic import() so module-level constants pick them up.
        """
        tmp_project = self._create_temp_project(tasks_md)
        pid = os.getpid()
        state_file = f"/tmp/test-multitask-grind-state-{pid}.json"
        script_path = f"/tmp/test_grind_script_{pid}.mjs"

        script = f"""// Set env vars BEFORE importing the module (module-level constants)
process.env.GLUDD_MULTITASK_FLOOR_ENFORCE = "1";
process.env.GLUDD_MULTITASK_MIN_DISPATCHES = "{min_dispatch}";
process.env.GLUDD_CONSECUTIVE_NON_DISPATCH_THRESHOLD = "{threshold}";
process.env.GLUDD_PROJECT_ROOT = "{tmp_project}";
process.env.GLUDD_MULTITASK_STATE_FILE = "{state_file}";
// Reset any disengage / subagent markers
process.env.OPENCODE_SUBAGENT = "";

import * as fs from "node:fs";
import * as path from "node:path";

// Clean stale state file from any prior run
try {{ fs.unlinkSync("{state_file}"); }} catch (e) {{}}

// Dynamic import so env vars above take effect before module init
const mod = await import("{PLUGIN}");

const plugin = await mod.default({{}});
const hook = plugin["tool.execute.before"];

async function run() {{
    const results = [];

    // Step 1: dispatch subagents to satisfy the floor
    const r0 = await hook({{ tool: "task" }});
    results.push({{ step: "dispatch-1-task", result: r0 || {{ allow: true }} }});

    const r0b = await hook({{ tool: "agent" }});
    results.push({{ step: "dispatch-2-agent", result: r0b || {{ allow: true }} }});

    // Step 2: consecutive non-dispatch calls
    const r1 = await hook({{ tool: "read" }});
    results.push({{ step: "nondispatch-1-read", result: r1 || {{ allow: true }} }});

    const r2 = await hook({{ tool: "grep" }});
    results.push({{ step: "nondispatch-2-grep", result: r2 || {{ allow: true }} }});

    const r3 = await hook({{ tool: "edit" }});
    results.push({{ step: "nondispatch-3-edit", result: r3 || {{ allow: true }} }});

    return results;
}}

const results = await run();

// Cleanup
try {{ fs.rmSync("{tmp_project}", {{ recursive: true, force: true }}); }} catch (e) {{}}
try {{ fs.unlinkSync("{state_file}"); }} catch (e) {{}}

console.log(JSON.stringify(results));
"""
        return script, tmp_project, state_file, script_path

    def _run_script(self, script: str, script_path: str) -> list:
        """Write script to file, run as subprocess, parse JSON result list."""
        with open(script_path, "w") as f:
            f.write(script)
        try:
            result = subprocess.run(
                ["node", "--experimental-strip-types", script_path],
                capture_output=True, text=True, timeout=20,
                cwd=str(ROOT),
                env={
                    **os.environ,
                    "GLUDD_MULTITASK_STATE_FILE": "",
                    "GLUDD_PROJECT_ROOT": "",
                    "GLUDD_HOT_MODULE_PREFIX": (
                        f"/tmp/test-grind-no-hot-{os.getpid()}-"
                    ),
                    "OPENCODE_SUBAGENT": "",
                },
            )
            stdout = (result.stdout or "").strip()
            # Find last JSON array in output
            lines = stdout.split("\n")
            json_line = ""
            for line in reversed(lines):
                line = line.strip()
                if line.startswith("[") and line.endswith("]"):
                    json_line = line
                    break
            if not json_line:
                return [{
                    "_error": "no json array found",
                    "_stdout": stdout,
                    "_stderr": result.stderr,
                    "_exit": result.returncode,
                }]
            return json.loads(json_line)
        finally:
            with __import__("contextlib").suppress(OSError):
                os.unlink(script_path)

    def test_consecutive_non_dispatch_blocked_at_threshold(self):
        """Third consecutive non-read non-dispatch call (bash) is denied.

        Setup: MIN_DISPATCHES=2, THRESHOLD=3, pending work in TASKS.md.
        read/grep/glob are excluded from the streak counter (isReadTool guard).
        Uses edit → write → bash to build the streak.

        1. Dispatch 2 subagents → floor satisfied.
        2. Call edit → counter=1, allowed.
        3. Call write → counter=2, allowed.
        4. Call bash → counter=3 ≥ threshold → DENIED with CONSECUTIVE NON-DISPATCH STREAK.
        """
        tmp_project = self._create_temp_project("# Tasks\n\n- [ ] Unchecked test task\n")
        pid = os.getpid()
        state_file = f"/tmp/test-multitask-grind-thresh-{pid}.json"
        script_path = f"/tmp/test_grind_thresh_{pid}.mjs"

        script = f"""process.env.GLUDD_MULTITASK_FLOOR_ENFORCE = "1";
process.env.GLUDD_MULTITASK_MIN_DISPATCHES = "2";
process.env.GLUDD_CONSECUTIVE_NON_DISPATCH_THRESHOLD = "3";
process.env.GLUDD_PROJECT_ROOT = "{tmp_project}";
process.env.GLUDD_MULTITASK_STATE_FILE = "{state_file}";
process.env.OPENCODE_SUBAGENT = "";

import * as fs from "node:fs";
try {{ fs.unlinkSync("{state_file}"); }} catch (e) {{}}

const mod = await import("{PLUGIN}");
const plugin = await mod.default({{}});
const hook = plugin["tool.execute.before"];

async function run() {{
    var results = [];

    // Dispatch 2 to satisfy floor
    var r0 = await hook({{ tool: "task" }});
    results.push({{ step: "dispatch-1-task", result: r0 || {{ allow: true }} }});

    var r0b = await hook({{ tool: "agent" }});
    results.push({{ step: "dispatch-2-agent", result: r0b || {{ allow: true }} }});

    // Non-read non-dispatch calls (edit, write, bash — never read/grep/glob)
    var r1 = await hook({{ tool: "edit" }});
    results.push({{ step: "nondispatch-1-edit", result: r1 || {{ allow: true }} }});

    var r2 = await hook({{ tool: "write" }});
    results.push({{ step: "nondispatch-2-write", result: r2 || {{ allow: true }} }});

    var r3 = await hook({{ tool: "bash" }});
    results.push({{ step: "nondispatch-3-bash", result: r3 || {{ allow: true }} }});

    return results;
}}

var results = await run();
try {{ fs.rmSync("{tmp_project}", {{ recursive: true, force: true }}); }} catch (e) {{}}
try {{ fs.unlinkSync("{state_file}"); }} catch (e) {{}}
console.log(JSON.stringify(results));
"""
        with open(script_path, "w") as f:
            f.write(script)
        self.SCRIPTS.append(script_path)

        results = self._run_script(script, script_path)

        assert len(results) >= 5, f"Expected at least 5 results, got {len(results)}: {results}"

        d1 = results[0]
        assert "dispatch-1-task" in d1["step"], f"Bad step name: {d1}"
        assert (d1["result"].get("allow") is True) or (d1["result"] is None), (
            f"dispatch 1 (task) should be allowed, got: {d1}"
        )

        d2 = results[1]
        assert "dispatch-2-agent" in d2["step"], f"Bad step name: {d2}"
        assert (d2["result"].get("allow") is True) or (d2["result"] is None), (
            f"dispatch 2 (agent) should be allowed, got: {d2}"
        )

        nd1 = results[2]
        assert (nd1["result"].get("allow") is True) or (nd1["result"] is None), (
            f"non-dispatch 1 (edit) should be allowed, got: {nd1}"
        )

        nd2 = results[3]
        assert (nd2["result"].get("allow") is True) or (nd2["result"] is None), (
            f"non-dispatch 2 (write) should be allowed, got: {nd2}"
        )

        nd3 = results[4]
        nd3_result = nd3["result"]
        assert nd3_result.get("permissionDecision") == "deny", (
            f"non-dispatch 3 (bash) MUST be denied, got: {nd3}"
        )
        msg = nd3_result.get("message", "")
        assert "CONSECUTIVE NON-DISPATCH STREAK" in msg, (
            f"Deny message must contain 'CONSECUTIVE NON-DISPATCH STREAK', got: {msg[:200]}"
        )

        with __import__("contextlib").suppress(OSError):
            import shutil
            shutil.rmtree(tmp_project, ignore_errors=True)

    def test_consecutive_counter_resets_after_dispatch(self):
        """After hitting threshold, dispatching a subagent resets the counter.

        1. Accumulate 2 non-dispatch calls (edit, write) → counter at 2.
        2. Dispatch a subagent → counter resets to 0 (but message boundary
           fires because this is a dispatch-after-non-dispatch pattern, so
           thisMessageDispatches also resets).
        3. Dispatch a second subagent → floor re-satisfied (thisMessageDispatches=2).
        4. Make 3 more non-dispatch calls (edit, write, bash) → 3rd is DENIED
           with CONSECUTIVE NON-DISPATCH STREAK.
        """
        _script, tmp_project, state_file, script_path = self._make_base_script(
            min_dispatch=2, threshold=3,
        )
        reset_script = f"""process.env.GLUDD_MULTITASK_FLOOR_ENFORCE = "1";
process.env.GLUDD_MULTITASK_MIN_DISPATCHES = "2";
process.env.GLUDD_CONSECUTIVE_NON_DISPATCH_THRESHOLD = "3";
process.env.GLUDD_PROJECT_ROOT = "{tmp_project}";
process.env.GLUDD_MULTITASK_STATE_FILE = "{state_file}";
process.env.OPENCODE_SUBAGENT = "";

import * as fs from "node:fs";
try {{ fs.unlinkSync("{state_file}"); }} catch (e) {{}}

const mod = await import("{PLUGIN}");
const plugin = await mod.default({{}});
const hook = plugin["tool.execute.before"];

async function run() {{
    var results = [];

    // Initial dispatches — floor requirement
    await hook({{ tool: "task" }});
    await hook({{ tool: "agent" }});

    // Accumulate 2 non-read non-dispatch calls (counter → 2)
    await hook({{ tool: "edit" }});
    await hook({{ tool: "write" }});

    // Dispatch a subagent — resets counter to 0. Pattern-change boundary
    // also fires (dispatch after non-dispatch), resetting thisMessageDispatches.
    await hook({{ tool: "task" }});

    // Re-satisfy the floor: need a second dispatch so thisMessageDispatches
    // reaches MIN_DISPATCHES again. Without this, the next non-dispatch call
    // would hit UNDER-FLOOR instead of the streak.
    var refillDispatch = await hook({{ tool: "agent" }});
    results.push({{ step: "refill-dispatch", result: refillDispatch || {{ allow: true }} }});

    // Now make 3 new non-read non-dispatch calls
    var r1 = await hook({{ tool: "edit" }});
    results.push({{ step: "post-reset-edit-1", result: r1 || {{ allow: true }} }});

    var r2 = await hook({{ tool: "write" }});
    results.push({{ step: "post-reset-write-2", result: r2 || {{ allow: true }} }});

    var r3 = await hook({{ tool: "bash" }});
    results.push({{ step: "post-reset-bash-3", result: r3 || {{ allow: true }} }});

    return results;
}}

var results = await run();
try {{ fs.rmSync("{tmp_project}", {{ recursive: true, force: true }}); }} catch (e) {{}}
try {{ fs.unlinkSync("{state_file}"); }} catch (e) {{}}
console.log(JSON.stringify(results));
"""
        with open(script_path, "w") as f:
            f.write(reset_script)
        self.SCRIPTS.append(script_path)

        results = self._run_script(reset_script, script_path)

        assert len(results) >= 4, f"Expected at least 4 results, got {len(results)}: {results}"

        refill = results[0]
        assert refill["step"] == "refill-dispatch"
        assert (refill["result"].get("allow") is True) or (refill["result"] is None), (
            f"Refill dispatch should be allowed, got: {refill}"
        )

        assert (results[1]["result"].get("allow") is True) or (results[1]["result"] is None), (
            f"post-reset edit 1 should be allowed, got: {results[1]}"
        )
        assert (results[2]["result"].get("allow") is True) or (results[2]["result"] is None), (
            f"post-reset write 2 should be allowed, got: {results[2]}"
        )

        nd3 = results[3]
        nd3_result = nd3["result"]
        assert nd3_result.get("permissionDecision") == "deny", (
            f"post-reset bash 3 MUST be denied, got: {nd3}"
        )
        assert "CONSECUTIVE NON-DISPATCH STREAK" in nd3_result.get("message", ""), (
            f"Deny must contain 'CONSECUTIVE NON-DISPATCH STREAK', got: {nd3_result.get('message', '')[:200]}"
        )

        with __import__("contextlib").suppress(OSError):
            import shutil
            shutil.rmtree(tmp_project, ignore_errors=True)

    def test_no_deny_when_no_pending_work(self):
        """When TASKS.md has NO unchecked items, grinding should NOT be blocked."""
        tmp_project = self._create_temp_project("# Tasks\n\n- [x] All done\n")  # Checked item = no pending
        pid = os.getpid()
        state_file = f"/tmp/test-grind-no-work-{pid}.json"
        script_path = f"/tmp/test_grind_no_work_{pid}.mjs"

        script = f"""process.env.GLUDD_MULTITASK_FLOOR_ENFORCE = "1";
process.env.GLUDD_MULTITASK_MIN_DISPATCHES = "2";
process.env.GLUDD_CONSECUTIVE_NON_DISPATCH_THRESHOLD = "3";
process.env.GLUDD_PROJECT_ROOT = "{tmp_project}";
process.env.GLUDD_MULTITASK_STATE_FILE = "{state_file}";
process.env.GLUDD_CI_CACHE_PATH = "{tmp_project}/ci.json";
process.env.GLUDD_STOP_STATE_PATH = "{tmp_project}/stop.json";
process.env.GLUDD_RELEASE_COMPLETENESS_FILE = "{tmp_project}/release.json";
process.env.GLUDD_TODOWRITE_STATE_PATH = "{tmp_project}/todo.json";
process.env.OPENCODE_SUBAGENT = "";

import * as fs from "node:fs";
try {{ fs.unlinkSync("{state_file}"); }} catch (e) {{}}

const mod = await import("{PLUGIN}");
const plugin = await mod.default({{}});
const hook = plugin["tool.execute.before"];

async function run() {{
    var results = [];
    await hook({{ tool: "task" }});
    await hook({{ tool: "agent" }});

    // Make 5 consecutive non-dispatch calls — ALL should be allowed
    // because TASKS.md has no unchecked items
    for (var i = 0; i < 5; i++) {{
        var r = await hook({{ tool: "edit" }});
        results.push(r || {{ allow: true }});
    }}
    return results;
}}

var results = await run();
try {{ fs.rmSync("{tmp_project}", {{ recursive: true, force: true }}); }} catch (e) {{}}
try {{ fs.unlinkSync("{state_file}"); }} catch (e) {{}}
console.log(JSON.stringify(results));
"""
        with open(script_path, "w") as f:
            f.write(script)
        self.SCRIPTS.append(script_path)

        results = self._run_script(script, script_path)

        assert len(results) == 5, f"Expected 5 results, got {len(results)}"
        for i, r in enumerate(results):
            is_denied = (isinstance(r, dict) and r.get("permissionDecision") == "deny")
            assert not is_denied, (
                f"Call {i} should be allowed (no pending work). Got: {r}"
            )

        with __import__("contextlib").suppress(OSError):
            import shutil
            shutil.rmtree(tmp_project, ignore_errors=True)

    def test_threshold_env_override_takes_effect(self):
        """Setting CONSECUTIVE_NON_DISPATCH_THRESHOLD=2 blocks on the 2nd
        non-read non-dispatch call. Uses edit→write (not read/grep) since
        read tools are excluded from the counter."""
        tmp_project = self._create_temp_project("# Tasks\n\n- [ ] Unchecked test task\n")
        pid = os.getpid()
        state_file = f"/tmp/test-multitask-grind-t2-{pid}.json"
        script_path = f"/tmp/test_grind_t2_{pid}.mjs"

        script = f"""process.env.GLUDD_MULTITASK_FLOOR_ENFORCE = "1";
process.env.GLUDD_MULTITASK_MIN_DISPATCHES = "2";
process.env.GLUDD_CONSECUTIVE_NON_DISPATCH_THRESHOLD = "2";
process.env.GLUDD_PROJECT_ROOT = "{tmp_project}";
process.env.GLUDD_MULTITASK_STATE_FILE = "{state_file}";
process.env.OPENCODE_SUBAGENT = "";

import * as fs from "node:fs";
try {{ fs.unlinkSync("{state_file}"); }} catch (e) {{}}

const mod = await import("{PLUGIN}");
const plugin = await mod.default({{}});
const hook = plugin["tool.execute.before"];

async function run() {{
    var results = [];

    // Dispatch 2 to satisfy floor
    await hook({{ tool: "task" }});
    await hook({{ tool: "agent" }});

    // Non-read non-dispatch calls (edit, write, bash — never read/grep/glob)
    var r1 = await hook({{ tool: "edit" }});
    results.push({{ step: "nondispatch-1-edit", result: r1 || {{ allow: true }} }});

    var r2 = await hook({{ tool: "write" }});
    results.push({{ step: "nondispatch-2-write", result: r2 || {{ allow: true }} }});

    return results;
}}

var results = await run();
try {{ fs.rmSync("{tmp_project}", {{ recursive: true, force: true }}); }} catch (e) {{}}
try {{ fs.unlinkSync("{state_file}"); }} catch (e) {{}}
console.log(JSON.stringify(results));
"""
        with open(script_path, "w") as f:
            f.write(script)
        self.SCRIPTS.append(script_path)

        results = self._run_script(script, script_path)

        assert len(results) >= 2, f"Expected at least 2 results, got {len(results)}"

        nd1 = results[0]
        assert (nd1["result"].get("allow") is True) or (nd1["result"] is None), (
            f"non-dispatch 1 (edit) should be allowed, got: {nd1}"
        )

        nd2 = results[1]
        nd2_result = nd2["result"]
        assert nd2_result.get("permissionDecision") == "deny", (
            f"non-dispatch 2 (write) MUST be denied at threshold=2, got: {nd2}"
        )
        assert "CONSECUTIVE NON-DISPATCH STREAK" in nd2_result.get("message", ""), (
            "Deny must contain 'CONSECUTIVE NON-DISPATCH STREAK'"
        )

        with __import__("contextlib").suppress(OSError):
            import shutil
            shutil.rmtree(tmp_project, ignore_errors=True)

    def test_multitask_enforce_disabled_allows_grinding(self):
        """When MULTITASK_FLOOR_ENFORCE=0, ALL non-dispatch calls are allowed."""
        tmp_project = self._create_temp_project("# Tasks\n\n- [ ] Unchecked task\n")
        pid = os.getpid()
        state_file = f"/tmp/test-grind-enforce-off-{pid}.json"
        script_path = f"/tmp/test_grind_enforce_off_{pid}.mjs"

        script = f"""// ENFORCE DISABLED
process.env.GLUDD_MULTITASK_FLOOR_ENFORCE = "0";
process.env.GLUDD_CONSECUTIVE_NON_DISPATCH_THRESHOLD = "3";
process.env.GLUDD_PROJECT_ROOT = "{tmp_project}";
process.env.GLUDD_MULTITASK_STATE_FILE = "{state_file}";
process.env.OPENCODE_SUBAGENT = "";

import * as fs from "node:fs";
try {{ fs.unlinkSync("{state_file}"); }} catch (e) {{}}

const mod = await import("{PLUGIN}");
const plugin = await mod.default({{}});
const hook = plugin["tool.execute.before"];

async function run() {{
    var results = [];
    // No dispatches needed — enforcement off
    for (var i = 0; i < 5; i++) {{
        var r = await hook({{ tool: "edit" }});
        results.push(r || {{ allow: true }});
    }}
    return results;
}}

var results = await run();
try {{ fs.rmSync("{tmp_project}", {{ recursive: true, force: true }}); }} catch (e) {{}}
try {{ fs.unlinkSync("{state_file}"); }} catch (e) {{}}
console.log(JSON.stringify(results));
"""
        with open(script_path, "w") as f:
            f.write(script)
        self.SCRIPTS.append(script_path)

        results = self._run_script(script, script_path)

        assert len(results) == 5, f"Expected 5 results, got {len(results)}"
        for i, r in enumerate(results):
            is_denied = (isinstance(r, dict) and r.get("permissionDecision") == "deny")
            assert not is_denied, (
                f"Call {i} should be allowed (enforcement off). Got: {r}"
            )

        with __import__("contextlib").suppress(OSError):
            import shutil
            shutil.rmtree(tmp_project, ignore_errors=True)
