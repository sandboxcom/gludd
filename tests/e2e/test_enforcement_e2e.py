"""E2E enforcement test: full multi-plugin chain with cumulative decisions.

This is different from test_hook_runtime.py (individual plugin tests) and
test_enforcement_plugin_e2e.py (state-file simulation). This test loads multiple
plugins into a hook chain and sends realistic tool-call payloads through all of
them in sequence, verifying the cumulative decision that emerges from the chain.

Chain order matches opencode.json registration order:
  enforce-make → enforce-floor → enforce-delegate → enforce-stop →
  enforce-session-start → enforce-deadline → enforce-deletion-gate →
  enforce-no-suppressions → enforce-no-wait → enforce-commit-lock →
  enforce-clean-tree → enforce-verified-claims → enforce-multitask →
  enforce-enhancement-ratio
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = ROOT / ".opencode" / "plugin"

PLUGIN_REGISTRATION_ORDER = [
    "enforce-make.ts",
    "enforce-floor.ts",
    "enforce-delegate.ts",
    "enforce-stop.ts",
    "enforce-session-start.ts",
    "enforce-deadline.ts",
    "enforce-deletion-gate.ts",
    "enforce-no-suppressions.ts",
    "enforce-no-wait.ts",
    "enforce-commit-lock.ts",
    "enforce-clean-tree.ts",
    "enforce-verified-claims.ts",
    "enforce-multitask.ts",
    "enforce-enhancement-ratio.ts",
]


# ── helpers ──────────────────────────────────────────────────────────────────

_tmp_counter = 0


def _run_ts(ts_code: str, env_override: dict | None = None, timeout: int = 20):
    """Write TS code to temp file, run via node --experimental-strip-types."""
    global _tmp_counter
    _tmp_counter += 1
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".ts", dir="/tmp", prefix=f"hook_chain_{_tmp_counter}_", delete=False
    ) as f:
        f.write(ts_code)
        tmp = f.name
    try:
        env = os.environ.copy()
        env["OPENCODE_SUBAGENT"] = ""
        if env_override:
            env.update(env_override)
        proc = subprocess.run(
            ["node", "--experimental-strip-types", tmp],
            capture_output=True, text=True, timeout=timeout,
            cwd=str(ROOT), env=env,
        )
        if proc.returncode != 0:
            raise AssertionError(
                f"Node exit {proc.returncode}:\nstderr: {proc.stderr[:800]}\nstdout: {proc.stdout[:400]}"
            )
        stdout = proc.stdout.strip()
        if not stdout:
            return None
        return json.loads(stdout)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _clean_state_files(*paths: str):
    for p in paths:
        try:
            os.unlink(p)
        except OSError:
            pass


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def clean_enforcement_state():
    """Reset all enforcement state files before each test."""
    state_files = [
        "/tmp/gludd-mainthread-streak.json",
        "/tmp/gludd-tool-streak.json",
        "/tmp/gludd-multitask-state.json",
        "/tmp/gludd-floor-override",
        "/tmp/gludd-session-start.json",
        "/tmp/gludd-watchdog-disengage.json",
        "/tmp/gludd-stop-state.json",
        "/tmp/gludd-block-counter.json",
        "/tmp/gludd-task-deadlines.json",
        "/tmp/gludd-task-stale.json",
        "/tmp/gludd-enhancement-ratio.json",
        "/tmp/gludd-force-delegate.json",
        "/tmp/gludd-sonnet-health.json",
    ]
    for f in state_files:
        try:
            os.unlink(f)
        except OSError:
            pass
    try:
        os.unlink("/tmp/gludd-test-tasks-e2e.md")
    except OSError:
        pass
    yield
    for f in state_files:
        try:
            os.unlink(f)
        except OSError:
            pass
    try:
        os.unlink("/tmp/gludd-test-tasks-e2e.md")
    except OSError:
        pass


@pytest.fixture
def tasks_path():
    """Create a temp TASKS.md with unchecked items."""
    p = "/tmp/gludd-test-tasks-e2e.md"
    with open(p, "w") as f:
        f.write("- [ ] test task 1\n- [ ] test task 2\n")
    return p


# ── chain runner ─────────────────────────────────────────────────────────────


def _chain_ts_code(call_code: str, plugins: list[str] | None = None) -> str:
    """Generate TS code that loads plugins in registration order and simulates
    the full opencode hook chain.

    For tool.execute.before: iterates plugins; first deny wins.
    For text.complete: each plugin feeds output into next.
    """
    if plugins is None:
        plugins = PLUGIN_REGISTRATION_ORDER

    import_paths = []
    for p in plugins:
        abs_path = str(PLUGIN_DIR / p)
        import_paths.append(f"'{abs_path}'")

    return f"""\
const plugins = [{', '.join(import_paths)}]

const loaded = []
for (const p of plugins) {{
  const mod = await import(p)
  loaded.push(mod.default)
}}

{call_code}
"""


# ── tests ────────────────────────────────────────────────────────────────────


class TestEnforcementChain:
    """Full multi-plugin hook chain tests."""

    # ── 1. Non-make bash is denied ──

    def test_raw_bash_denied_by_make_plugin(self):
        """Direct bash (e.g. 'ls') is denied by enforce-make."""
        code = _chain_ts_code("""\
const results = []
for (const factory of loaded) {
  const plugin = await factory({})
  if (plugin['tool.execute.before']) {
    const r = await plugin['tool.execute.before']({tool: 'bash', command: 'ls -la'}, {})
    if (r) results.push({plugin: factory.name || 'unknown', decision: r.permissionDecision, msg: r.message?.slice(0, 80)})
  }
}
console.log(JSON.stringify(results))
""")
        result = _run_ts(code)
        assert result is not None
        denials = [r for r in result if r.get("decision") == "deny"]
        assert len(denials) >= 1, f"Expected at least 1 denial for raw bash, got: {result}"
        make_denial = [r for r in result if "make" in (r.get("msg") or "").lower()]
        assert len(make_denial) >= 1, f"enforce-make should deny raw bash: {result}"

    def test_make_target_allowed_by_all_plugins(self):
        """'make git-status' is allowed by all plugins (no deny decisions)."""
        code = _chain_ts_code("""\
const results = []
for (const factory of loaded) {
  const plugin = await factory({})
  if (plugin['tool.execute.before']) {
    const r = await plugin['tool.execute.before']({tool: 'bash', command: 'make git-status'}, {})
    if (r) results.push({permissionDecision: r.permissionDecision})
  }
}
console.log(JSON.stringify({denied: results.filter(r => r.permissionDecision === 'deny').length > 0, totalChecks: results.length}))
""")
        result = _run_ts(code)
        assert result is not None
        assert result["denied"] == False, (
            f"make git-status should be allowed by all plugins, got denials"
        )

    def test_make_with_metachar_denied(self):
        """'make test 2>&1' (pipe-like) is denied by enforce-make."""
        code = _chain_ts_code("""\
const results = []
for (const factory of loaded) {
  const plugin = await factory({})
  if (plugin['tool.execute.before']) {
    const r = await plugin['tool.execute.before']({tool: 'bash', command: 'make test 2>&1 | tail'}, {})
    if (r) results.push({plugin: 'enforce-make', decision: r.permissionDecision})
    break
  }
}
console.log(JSON.stringify(results))
""")
        result = _run_ts(code)
        assert result is not None
        denials = [r for r in result if r.get("decision") == "deny"]
        assert len(denials) >= 1, f"Metachar bash should be denied: {result}"

    # ── 3. Subagent context skips enforcement ──

    def test_subagent_bypasses_enforcement(self):
        """All plugins skip enforcement when OPENCODE_SUBAGENT=1."""
        code = _chain_ts_code("""\
const results = []
for (const factory of loaded) {
  const plugin = await factory({})
  if (plugin['tool.execute.before']) {
    const r = await plugin['tool.execute.before']({tool: 'bash', command: 'ls -la'}, {})
    if (r && r.permissionDecision === 'deny') results.push(r.permissionDecision)
  }
}
console.log(JSON.stringify({deniedCount: results.length}))
""")
        result = _run_ts(code, env_override={"OPENCODE_SUBAGENT": "1"})
        assert result is not None
        assert result["deniedCount"] == 0, (
            f"Subagent should bypass all enforcement, got {result['deniedCount']} denials"
        )

    # ── 4. Disengage disables enforcement ──

    def test_disengage_bypasses_floor_and_delegate(self, tasks_path):
        """With disengage active, high mainthread streak does not block edits."""
        sf = "/tmp/gludd-mainthread-streak.json"
        with open(sf, "w") as f:
            json.dump({"count": 5, "ts": int(time.time() * 1000)}, f)
        disengage = "/tmp/gludd-watchdog-disengage.json"
        with open(disengage, "w") as f:
            json.dump({"disengage_until": int(time.time() * 1000) + 600_000}, f)
        code = _chain_ts_code("""\
const results = []
for (const factory of loaded) {
  const plugin = await factory({})
  if (plugin['tool.execute.before']) {
    try {
      const r = await plugin['tool.execute.before']({tool: 'edit'}, {})
      if (r && r.permissionDecision === 'deny') results.push(r.permissionDecision)
    } catch (e) {
      if (e.message?.includes('delegate') || e.message?.includes('mainthread')) {
        results.push('deny')
      }
    }
  }
}
console.log(JSON.stringify({deniedCount: results.length}))
""")
        result = _run_ts(code, env_override={
            "GLUDD_TASKS_MD": tasks_path,
            "GLUDD_LIVE_AGENTS_COUNT": "0",
        })
        assert result is not None
        assert result["deniedCount"] == 0, (
            f"Disengage should bypass enforcements, got {result['deniedCount']} denials"
        )

    def test_disengage_expired_still_enforces(self, tasks_path):
        """Expired disengage does not bypass enforcement."""
        sf = "/tmp/gludd-mainthread-streak.json"
        with open(sf, "w") as f:
            json.dump({"count": 5, "ts": int(time.time() * 1000)}, f)
        disengage = "/tmp/gludd-watchdog-disengage.json"
        with open(disengage, "w") as f:
            json.dump({"disengage_until": int(time.time() * 1000) - 600_000})
        code = _chain_ts_code("""\
const results = []
for (const factory of loaded) {
  const plugin = await factory({})
  if (plugin['tool.execute.before']) {
    try {
      const r = await plugin['tool.execute.before']({tool: 'edit'}, {})
      if (r && r.permissionDecision === 'deny') results.push('deny')
    } catch (e) {
      if (e.message?.includes('delegate') || e.message?.includes('mainthread')) {
        results.push('deny')
      }
    }
  }
}
console.log(JSON.stringify({deniedCount: results.length}))
""")
        result = _run_ts(code, env_override={
            "GLUDD_TASKS_MD": tasks_path,
            "GLUDD_LIVE_AGENTS_COUNT": "0",
        })
        assert result is not None
        assert result["deniedCount"] >= 1, (
            f"Expired disengage should still enforce, got {result['deniedCount']} denials"
        )

    # ── 5. Multi-plugin text.complete dedup ──

    def test_text_complete_chain_no_conflict(self):
        """Multiple text.complete handlers chain without producing duplicate blocks."""
        state_file = os.path.join("/tmp", f"test-tc-chain-{os.getpid()}.json")
        tasks_path = "/tmp/gludd-test-tasks-e2e.md"
        with open(state_file, "w") as f:
            json.dump({
                "ts": int(time.time() * 1000),
                "ratchetEntries": 1,
                "tasksMdUnchecked": True,
                "gateStatusRed": False,
                "repoPending": False,
                "hasLocalWork": True,
                "hasPendingWork": True,
                "ciVerdictPendingOrRed": False,
                "healthScore": 30,
            }, f)

        code = _chain_ts_code("""\
const handlers = []
for (const factory of loaded) {
  const plugin = await factory({})
  if (plugin['experimental.text.complete']) {
    handlers.push(plugin['experimental.text.complete'])
  }
}
// Chain the handlers like opencode does: each handler receives previous output
let output = {text: 'Done. All tasks complete.'}
for (const handler of handlers) {
  try {
    const next = await handler(undefined, output)
    if (next) output = typeof next === 'string' ? {text: next} : next
  } catch (e) {}
}
console.log(JSON.stringify({
  modified: output.text !== 'Done. All tasks complete.',
  blockCount: (output.text || '').includes('BLOCKED') ? 1 : 0,
  textLen: (output.text || '').length,
  hasViolation: (output.text || '').includes('VIOLATION'),
}))
""")
        result = _run_ts(code, env_override={
            "GLUDD_STOP_STATE_FILE": state_file,
            "GLUDD_TASKS_MD": tasks_path,
        })
        assert result is not None
        assert result["modified"] == True, f"Expected text to be modified by stop plugin: {result}"
        assert result["textLen"] < 500, f"Expected terse block message, got {result['textLen']} chars"

    def test_text_complete_no_work_passes_through(self):
        """Text passes through unmodified when no pending work exists."""
        state_file = os.path.join("/tmp", f"test-tc-pass-{os.getpid()}.json")
        with open(state_file, "w") as f:
            json.dump({
                "ts": int(time.time() * 1000),
                "ratchetEntries": 0,
                "tasksMdUnchecked": False,
                "gateStatusRed": False,
                "repoPending": False,
                "hasLocalWork": False,
                "hasPendingWork": False,
                "ciVerdictPendingOrRed": False,
                "healthScore": 100,
            }, f)

        code = _chain_ts_code("""\
const handlers = []
for (const factory of loaded) {
  const plugin = await factory({})
  if (plugin['experimental.text.complete']) {
    handlers.push(plugin['experimental.text.complete'])
  }
}
let output = {text: 'All good. Continuing work.'}
for (const handler of handlers) {
  try {
    const next = await handler(undefined, output)
    if (next) output = typeof next === 'string' ? {text: next} : next
  } catch (e) {}
}
console.log(JSON.stringify({
  passedThrough: output.text === 'All good. Continuing work.',
}))
""")
        result = _run_ts(code, env_override={"GLUDD_STOP_STATE_FILE": state_file})
        assert result is not None
        assert result["passedThrough"] == True, f"Text should pass through: {result}"

    # ── 6. Dispatch resets streak across plugins ──

    def test_dispatch_resets_all_streaks(self):
        """A dispatch (task/agent/workflow) resets streak counters in all plugins."""
        tasks_path = "/tmp/gludd-test-tasks-e2e.md"
        with open(tasks_path, "w") as f:
            f.write("- [ ] task A\n- [ ] task B\n")

        # First, run some non-dispatch calls to build streak
        build_code = _chain_ts_code("""\
const results = []
for (const factory of loaded) {
  const plugin = await factory({})
  if (plugin['tool.execute.before']) {
    try {
      const r = await plugin['tool.execute.before']({tool: 'edit'}, {})
      if (r && r.permissionDecision === 'deny') results.push('deny')
    } catch (e) {
      if (e.message?.includes('deny') || e.message?.includes('mainthread')) {
        results.push('deny')
      }
    }
  }
}
// Now dispatch to reset
for (const factory of loaded) {
  const plugin = await factory({})
  if (plugin['tool.execute.before']) {
    try {
      await plugin['tool.execute.before']({tool: 'task'}, {})
    } catch (e) {}
  }
}
// After dispatch reset, another edit should be allowed (streak=0)
const afterResults = []
for (const factory of loaded) {
  const plugin = await factory({})
  if (plugin['tool.execute.before']) {
    try {
      const r = await plugin['tool.execute.before']({tool: 'edit'}, {})
      if (r && r.permissionDecision === 'deny') afterResults.push('deny')
    } catch (e) {
      if (e.message?.includes('deny') || e.message?.includes('mainthread')) {
        afterResults.push('deny')
      }
    }
  }
}
console.log(JSON.stringify({afterDenials: afterResults.length}))
""")
        result = _run_ts(build_code, env_override={
            "GLUDD_TASKS_MD": tasks_path,
            "GLUDD_LIVE_AGENTS_COUNT": "0",
            "GLUDD_SESSION_STATE": "/tmp/gludd-session-start-null.json",
        })
        assert result is not None
        assert result["afterDenials"] == 0, (
            f"After dispatch reset, edit should be allowed. Got {result['afterDenials']} denials"
        )

    # ── 7. Env var disable works across chain ──

    def test_disable_all_enforcement_env_vars(self):
        """Setting all GLUDD_*_ENFORCE=0 env vars disables blocking."""
        tasks_path = "/tmp/gludd-test-tasks-e2e.md"
        with open(tasks_path, "w") as f:
            f.write("- [ ] test task\n")

        # Pre-condition: make enforcement states look like work exists
        sf = "/tmp/gludd-mainthread-streak.json"
        with open(sf, "w") as f:
            json.dump({"count": 5, "ts": int(time.time() * 1000)}, f)

        code = _chain_ts_code("""\
const results = []
for (const factory of loaded) {
  const plugin = await factory({})
  if (plugin['tool.execute.before']) {
    try {
      const r = await plugin['tool.execute.before']({tool: 'edit'}, {})
      if (r && r.permissionDecision === 'deny') results.push('deny')
    } catch (e) {
      if (e.message?.includes('deny') || e.message?.includes('mainthread')) {
        results.push('deny')
      }
    }
  }
}
console.log(JSON.stringify({deniedCount: results.length}))
""")
        result = _run_ts(code, env_override={
            "GLUDD_FLOOR_ENFORCE": "0",
            "GLUDD_MAINTHREAD_STREAK_ENFORCE": "0",
            "GLUDD_STOP_ENFORCE": "0",
            "GLUDD_SESSION_START_ENFORCE": "0",
            "GLUDD_MULTITASK_FLOOR_ENFORCE": "0",
            "GLUDD_TASK_DEADLINE_ENABLED": "0",
            "GLUDD_TASK_DEADLINE_BLOCK": "0",
            "GLUDD_NO_WAIT_ENFORCE": "0",
            "GLUDD_CLEAN_TREE_ENFORCE": "0",
            "GLUDD_VERIFIED_CLAIMS_ENFORCE": "0",
            "GLUDD_ENHANCEMENT_RATIO_ENFORCE": "0",
            "GLUDD_ENHANCEMENT_RATIO_BLOCK": "0",
            "GLUDD_DELETION_GATE_THRESHOLD": "0",
            "GLUDD_TASKS_MD": tasks_path,
            "GLUDD_LIVE_AGENTS_COUNT": "0",
            "GLUDD_SESSION_STATE": "/tmp/gludd-session-start-null.json",
        })
        assert result is not None
        assert result["deniedCount"] == 0, (
            f"All enforcement disabled should allow edits, got {result['deniedCount']} denials"
        )

    # ── 8. Plugin load order is respected ──

    def test_plugin_registration_order_matches_opencode_json(self):
        """The test plugin list matches opencode.json plugin order."""
        raw = json.loads((ROOT / "opencode.json").read_text())
        registered = [p.split("/")[-1] for p in raw.get("plugin", [])]
        # watchdog.ts is not an enforcement plugin
        registered = [p for p in registered if p not in ("watchdog.ts",)]
        assert registered == PLUGIN_REGISTRATION_ORDER, (
            f"Plugin order mismatch.\nExpected: {PLUGIN_REGISTRATION_ORDER}\nGot: {registered}"
        )

    def test_all_registered_plugins_loadable(self):
        """Every plugin in opencode.json can be imported without error."""
        code = f"""\
const plugins = {json.dumps(PLUGIN_REGISTRATION_ORDER)}
const results = []
for (const p of plugins) {{
  try {{
    await import('{PLUGIN_DIR}/' + p)
    results.push({{plugin: p, loaded: true}})
  }} catch (e) {{
    results.push({{plugin: p, loaded: false, error: e.message.slice(0, 100)}})
  }}
}}
console.log(JSON.stringify(results))
"""
        result = _run_ts(code)
        assert result is not None
        failed = [r for r in result if not r["loaded"]]
        assert len(failed) == 0, f"Some plugins failed to load: {failed}"

    # ── 9. No-suppression plugin in chain ──

    def test_no_suppression_blocks_in_chain(self):
        """enforce-no-suppressions blocks #noqa edits within the full chain."""
        code = _chain_ts_code("""\
const results = []
for (const factory of loaded) {
  const plugin = await factory({})
  if (plugin['tool.execute.before']) {
    const r = await plugin['tool.execute.before']({tool: 'edit', args: {file_path: 'src/foo.py', old_string: '# noqa'}})
    if (r && r.permissionDecision === 'deny') {
      results.push({decision: r.permissionDecision, reason: r.message?.slice(0, 50) || r.reason})
    }
  }
}
console.log(JSON.stringify(results))
""")
        result = _run_ts(code)
        assert result is not None
        suppressions = [r for r in result if "forbidden" in (r.get("reason") or "").lower()]
        assert len(suppressions) >= 1, f"Expected #noqa to be blocked: {result}"

    # ── 10. Enhancement ratio waved in chain ──

    def test_enhancement_ratio_fires_in_chain(self):
        """enforce-enhancement-ratio blocks fix-only waves in the chain."""
        ratio_state = os.path.join("/tmp", f"test-ratio-chain-{os.getpid()}.json")
        _clean_state_files(ratio_state)

        code = _chain_ts_code("""\
const results = []
for (const factory of loaded) {
  const plugin = await factory({})
  if (plugin['tool.execute.before']) {
    // 1st: fix (allowed, wave < 2)
    const r1 = await plugin['tool.execute.before']({tool: 'task', args: {prompt: 'fix bug A'}})
    if (r1 && r1.permissionDecision === 'deny') results.push({slot: 1, denied: true})
    // 2nd: fix (denied, 100% fixes in wave=2)
    const r2 = await plugin['tool.execute.before']({tool: 'task', args: {prompt: 'fix bug B'}})
    if (r2 && r2.permissionDecision === 'deny') results.push({slot: 2, denied: true, msg: r2.message?.slice(0, 100)})
  }
}
console.log(JSON.stringify(results))
""")
        result = _run_ts(code, env_override={
            "GLUDD_ENHANCEMENT_RATIO_STATE": ratio_state,
        })
        assert result is not None
        assert len(result) >= 1, f"Expected denial on fix-only wave: {result}"
        assert any("ENHANCEMENT" in r.get("msg", "") for r in result), (
            f"Denial should mention ENHANCEMENT: {result}"
        )
        _clean_state_files(ratio_state)

    # ── 11. Fail-open: corrupt state in chain ──

    def test_corrupt_state_does_not_crash_chain(self):
        """Corrupt state files cause plugins to fail-open, not crash."""
        with open("/tmp/gludd-mainthread-streak.json", "w") as f:
            f.write("not json {{{")
        with open("/tmp/gludd-tool-streak.json", "w") as f:
            f.write("corrupted {{{")

        code = _chain_ts_code("""\
const results = []
let crashedCount = 0
for (const factory of loaded) {
  const plugin = await factory({})
  if (plugin['tool.execute.before']) {
    try {
      const r = await plugin['tool.execute.before']({tool: 'edit'}, {})
      if (r && r.permissionDecision === 'deny') results.push('deny')
    } catch (e) {
      // Plugin threw — record but continue
      if (!e.message?.includes('corrupt')) {
        crashedCount++
      }
    }
  }
}
console.log(JSON.stringify({denied: results.length > 0, crashed: crashedCount}))
""")
        result = _run_ts(code)
        assert result is not None
        assert result["crashed"] == 0, (
            f"Corrupt state should not crash plugins (fail-open): crashes={result['crashed']}"
        )


class TestEnforcementChainIntegration:
    """Integration scenarios: multiple plugins acting on a realistic session."""

    def test_full_session_cycle(self):
        """Simulate: start → read backlog → dispatch wave → edit → commit → end."""
        tasks_path = "/tmp/gludd-test-tasks-e2e.md"
        with open(tasks_path, "w") as f:
            f.write("- [ ] feature X\n- [ ] feature Y\n")

        session_state = "/tmp/gludd-session-start-null.json"
        with open(session_state, "w") as f:
            json.dump({}, f)

        code = _chain_ts_code("""\
// Simulate a full session cycle through the hook chain
const allPluginFactories = loaded

async function runThroughHook(tool, args) {
  for (const factory of allPluginFactories) {
    const plugin = await factory({})
    if (plugin['tool.execute.before']) {
      try {
        const r = await plugin['tool.execute.before']({tool, args: args || {}}, {})
        if (r && r.permissionDecision === 'deny') return {denied: true, tool, msg: r.message?.slice(0, 80)}
      } catch (e) {
        if (e.message?.includes('deny') || e.message?.includes('blocked') || e.message?.includes('mainthread') || e.message?.includes('protocol') || e.message?.includes('SESSION')) {
          return {denied: true, tool, msg: e.message.slice(0, 80)}
        }
      }
    }
  }
  return {denied: false, tool}
}

const results = []

// 1. Read backlog (read tool — always allowed)
results.push(await runThroughHook('read', {file_path: 'TASKS.md'}))

// 2. Dispatch wave (task tool — always allowed, resets streaks)
results.push(await runThroughHook('agent'))
results.push(await runThroughHook('agent'))
results.push(await runThroughHook('agent'))

// 3. After dispatch, a single edit should be allowed (streak reset)
results.push(await runThroughHook('edit'))

// 4. Another edit — streak=1, should still be allowed
results.push(await runThroughHook('edit'))

// 5. make target bash — allowed
results.push(await runThroughHook('bash', {command: 'make git-status'}))

// 6. Raw bash — denied
results.push(await runThroughHook('bash', {command: 'ls'}))

console.log(JSON.stringify(results))
""")
        result = _run_ts(code, env_override={
            "GLUDD_TASKS_MD": tasks_path,
            "GLUDD_SESSION_STATE": session_state,
            "GLUDD_LIVE_AGENTS_COUNT": "0",
        })
        assert result is not None, f"Chain execution failed"
        # Verify sequence behavior:
        # - reads are allowed
        read_result = next((r for r in result if r["tool"] == "read"), None)
        assert read_result is not None
        assert read_result["denied"] == False, f"Read should be allowed: {read_result}"

        # - make git-status is allowed
        make_result = next((r for r in result if r.get("tool") == "bash" and
                           r.get("denied") == False), None)
        assert make_result is not None, f"make git-status should be allowed: {result}"

        # - raw bash (ls) is denied
        raw_bash = next((r for r in result if r.get("tool") == "bash" and
                        r.get("denied") == True), None)
        assert raw_bash is not None, f"Raw bash should be denied: {result}"


class TestEnforcementErrorsAreObservable:
    """Verify enforcement errors surface observable failure information."""

    def test_deny_messages_are_structured(self):
        """Deny decisions return structured {permissionDecision, message}."""
        code = _chain_ts_code("""\
const results = []
for (const factory of loaded) {
  const plugin = await factory({})
  if (plugin['tool.execute.before']) {
    const r = await plugin['tool.execute.before']({tool: 'bash', command: 'echo $HOME'}, {})
    if (r) results.push({
      hasDecision: 'permissionDecision' in r,
      hasMessage: 'message' in r,
      decision: r.permissionDecision,
    })
  }
}
console.log(JSON.stringify(results))
""")
        result = _run_ts(code)
        assert result is not None
        assert len(result) >= 1, "Expected at least one plugin to check bash"
        for r in result:
            assert r["hasDecision"] == True, f"Deny should have permissionDecision: {r}"
            assert r["hasMessage"] == True, f"Deny should have message: {r}"

    def test_errors_do_not_propagate_across_plugins(self):
        """A deny from one plugin does not prevent later plugins from checking."""
        code = _chain_ts_code("""\
const allChecks = []
for (const factory of loaded) {
  const plugin = await factory({})
  if (plugin['tool.execute.before']) {
    try {
      const r = await plugin['tool.execute.before']({tool: 'edit'}, {})
      allChecks.push({checked: true, denied: r?.permissionDecision === 'deny'})
    } catch (e) {
      allChecks.push({checked: true, thrown: true, msg: e.message?.slice(0, 60)})
    }
  }
}
console.log(JSON.stringify({checkCount: allChecks.length}))
""")
        result = _run_ts(code)
        assert result is not None
        assert result["checkCount"] >= 3, (
            f"Expected multiple plugins to run, got {result['checkCount']} checks"
        )
