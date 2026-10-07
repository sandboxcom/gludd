from __future__ import annotations

import contextlib
import json
import os
import time
from typing import Any, cast

from .fixtures import (
    _OPENCODE_DIR,
    LIB_DIR,
    PLUGIN_DIR,
    ROOT,
    _clean_state_files,
    _runtime_state_root,
    _with_open_work,
)
from .runner import (
    _factory_plugin_code,
    _run_ts,
)

# enforce-session-start.ts  —  session-start protocol enforcement
# ---------------------------------------------------------------------------


def _fresh_session_state(state_path: str, **overrides: object) -> dict[str, object]:
    """Write a fresh session state file with started_at=now and return the contents."""
    state = {
        "started_at": int(time.time() * 1000),
        "readsDone": False,
        "dispatches": 0,
        "timeGateReset": False,
        **overrides,
    }
    with open(state_path, "w") as f:
        json.dump(state, f)
    return state


def test_session_start_fresh_no_reads_mutation_denied() -> None:
    """Fresh session (no reads, no dispatches) + non-dispatch tool → denied (throws Error)."""
    state_file = os.path.join("/tmp", f"test-ss-denied-{os.getpid()}.json")
    _fresh_session_state(state_file)
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-session-start.ts')
const plugin = await mod.default({{}})
let result
try {{
  await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
  console.log(JSON.stringify({{allowed: true}}))
}} catch (e) {{
  result = {{permissionDecision: 'deny', message: e.message}}
  console.log(JSON.stringify(result))
}}
"""
    result = _run_ts(code, env_override={"GLUDD_SESSION_STATE": state_file})
    assert result is not None, "Expected deny object from thrown Error"
    assert result.get("permissionDecision") == "deny", f"Expected deny, got: {result}"
    assert "SESSION START PROTOCOL" in result.get("message", ""), f"Message missing PROTOCOL: {result}"
    _clean_state_files(state_file)


def test_session_start_read_tool_always_allowed() -> None:
    """Read/Grep/Glob tools always allowed even in fresh unprimed session."""
    state_file = os.path.join("/tmp", f"test-ss-read-{os.getpid()}.json")
    _fresh_session_state(state_file)
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-session-start.ts')
const plugin = await mod.default({{}})
let result
try {{
  await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
  console.log(JSON.stringify({{allowed: true}}))
}} catch (e) {{
  result = {{permissionDecision: 'deny', message: e.message}}
  console.log(JSON.stringify(result))
}}
"""
    result = _run_ts(code, env_override={"GLUDD_SESSION_STATE": state_file})
    assert result is not None, "Expected output"
    assert result.get("allowed") is True, f"Read tool should be allowed, got: {result}"
    _clean_state_files(state_file)


def test_session_start_subagent_guard() -> None:
    """OPENCODE_SUBAGENT=1 → all tools allowed, enforcement skipped."""
    state_file = os.path.join("/tmp", f"test-ss-subagent-{os.getpid()}.json")
    _fresh_session_state(state_file)
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-session-start.ts')
const plugin = await mod.default({{}})
let result
try {{
  await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
  console.log(JSON.stringify({{allowed: true}}))
}} catch (e) {{
  result = {{permissionDecision: 'deny', message: e.message}}
  console.log(JSON.stringify(result))
}}
"""
    result = _run_ts(
        code,
        env_override={
            "GLUDD_SESSION_STATE": state_file,
            "OPENCODE_SUBAGENT": "1",
        },
    )
    assert result is not None, "Expected output"
    assert result.get("allowed") is True, f"Subagent should bypass enforcement, got: {result}"
    _clean_state_files(state_file)


def test_session_start_env_disable() -> None:
    """GLUDD_SESSION_START_ENFORCE=0 → no blocking (advisory only)."""
    state_file = os.path.join("/tmp", f"test-ss-disable-{os.getpid()}.json")
    _fresh_session_state(state_file)
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-session-start.ts')
const plugin = await mod.default({{}})
let result
try {{
  await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
  console.log(JSON.stringify({{allowed: true}}))
}} catch (e) {{
  result = {{permissionDecision: 'deny', message: e.message}}
  console.log(JSON.stringify(result))
}}
"""
    result = _run_ts(
        code,
        env_override={
            "GLUDD_SESSION_STATE": state_file,
            "GLUDD_SESSION_START_ENFORCE": "0",
        },
    )
    assert result is not None, "Expected output"
    assert result.get("allowed") is True, f"With ENFORCE=0, tool should be allowed, got: {result}"
    _clean_state_files(state_file)


def test_session_start_corrupt_state_fail_open() -> None:
    """Corrupt state file → tools allowed (fail-open)."""
    state_file = os.path.join("/tmp", f"test-ss-corrupt-{os.getpid()}.json")
    with open(state_file, "w") as f:
        f.write("not valid json {{{[[[")
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-session-start.ts')
const plugin = await mod.default({{}})
let result
try {{
  await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
  console.log(JSON.stringify({{allowed: true}}))
}} catch (e) {{
  result = {{permissionDecision: 'deny', message: e.message}}
  console.log(JSON.stringify(result))
}}
"""
    result = _run_ts(code, env_override={"GLUDD_SESSION_STATE": state_file})
    assert result is not None, "Expected output (fail-open should not throw)"
    assert result.get("allowed") is True, f"Corrupt state should fail-open, got: {result}"
    _clean_state_files(state_file)


def test_session_start_read_task_file_sets_readsDone() -> None:
    """Reading TASKS.md via the REAL opencode input shape sets readsDone=true.

    opencode passes tool args in param 2 (output), not param 1 (input).
    This is the regression guard for the isTaskFileRead output-arg fix.
    Before the fix, isTaskFileRead only checked input (param 1) for filePath,
    which was always empty — readsDone never got set, so the session-start
    gate stayed permanently active.
    """
    state_file = os.path.join("/tmp", f"test-ss-readtask-{os.getpid()}.json")
    hot_module = "/tmp/gludd-hot-enforce-session-start.js"
    _clean_state_files(state_file, hot_module)
    _fresh_session_state(state_file)
    code = f"""\
const fs = await import('node:fs')
const mod = await import('{PLUGIN_DIR}/enforce-session-start.ts')
const plugin = await mod.default({{}})
await plugin['tool.execute.before'](
  {{tool: 'read'}},
  {{args: {{filePath: '{ROOT}/TASKS.md'}}}}
)
const state = JSON.parse(fs.readFileSync('{state_file}', 'utf8'))
console.log(JSON.stringify({{readsDone: state.readsDone}}))
"""
    result = _run_ts(code, env_override={"GLUDD_SESSION_STATE": state_file})
    assert result is not None, "Expected JSON output from test"
    assert result["readsDone"] is True, (
        f"readsDone must be true after reading TASKS.md via output.args.filePath. Got: {result}"
    )
    _clean_state_files(state_file, hot_module)


def test_session_start_read_bugs_md_sets_readsDone() -> None:
    """Reading BUGS.md via output.args also sets readsDone=true."""
    state_file = os.path.join("/tmp", f"test-ss-readbugs-{os.getpid()}.json")
    hot_module = "/tmp/gludd-hot-enforce-session-start.js"
    _clean_state_files(state_file, hot_module)
    _fresh_session_state(state_file)
    code = f"""\
const fs = await import('node:fs')
const mod = await import('{PLUGIN_DIR}/enforce-session-start.ts')
const plugin = await mod.default({{}})
await plugin['tool.execute.before'](
  {{tool: 'read'}},
  {{args: {{filePath: '{ROOT}/BUGS.md'}}}}
)
const state = JSON.parse(fs.readFileSync('{state_file}', 'utf8'))
console.log(JSON.stringify({{readsDone: state.readsDone}}))
"""
    result = _run_ts(code, env_override={"GLUDD_SESSION_STATE": state_file})
    assert result is not None
    assert result["readsDone"] is True, f"readsDone must be true after reading BUGS.md. Got: {result}"
    _clean_state_files(state_file, hot_module)


def test_session_start_read_non_task_file_does_not_set_readsDone() -> None:
    """Reading a non-task file (e.g. src/foo.py) must NOT set readsDone."""
    state_file = os.path.join("/tmp", f"test-ss-readnontask-{os.getpid()}.json")
    hot_module = "/tmp/gludd-hot-enforce-session-start.js"
    _clean_state_files(state_file, hot_module)
    _fresh_session_state(state_file)
    code = f"""\
const fs = await import('node:fs')
const mod = await import('{PLUGIN_DIR}/enforce-session-start.ts')
const plugin = await mod.default({{}})
await plugin['tool.execute.before'](
  {{tool: 'read'}},
  {{args: {{filePath: '/tmp/random-non-task-file.py'}}}}
)
const state = JSON.parse(fs.readFileSync('{state_file}', 'utf8'))
console.log(JSON.stringify({{readsDone: state.readsDone}}))
"""
    result = _run_ts(code, env_override={"GLUDD_SESSION_STATE": state_file})
    assert result is not None
    assert result["readsDone"] is False, f"readsDone must remain false for non-task files. Got: {result}"
    _clean_state_files(state_file, hot_module)


def _session_start_dispatch_then_bash(
    configured_min: str | None,
) -> dict[str, Any]:
    label = configured_min or "adaptive"
    state_file = os.path.join("/tmp", f"test-ss-dispatch-inc-{label}-{os.getpid()}.json")
    _fresh_session_state(state_file, readsDone=True, dispatches=0)
    code = f"""\
const fs = await import('node:fs')
const mod = await import('{PLUGIN_DIR}/enforce-session-start.ts')
const plugin = await mod.default({{}})
// Call 1: task dispatch → should increment dispatches to 1
await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
const state1 = JSON.parse(fs.readFileSync('{state_file}', 'utf8'))
const dp1 = state1.dispatches
// Call 2: bash (non-dispatch, non-read) exercises the configured policy.
let denied = false
let msg = ''
try {{
  await plugin['tool.execute.before']({{tool: 'bash'}}, undefined)
}} catch (e) {{
  denied = true
  msg = e.message
}}
console.log(JSON.stringify({{dp1, denied, hasProtocol: msg.includes('SESSION START PROTOCOL')}}))
"""
    env_override = {"GLUDD_SESSION_STATE": state_file}
    if configured_min is not None:
        env_override["GLUDD_SESSION_START_MIN_DISPATCHES"] = configured_min
    result = _run_ts(code, env_override=env_override)
    _clean_state_files(state_file)
    return cast(dict[str, Any], result)


def test_session_start_dispatch_increment_default_adaptive() -> None:
    """One dispatch is enough by default; no quota-padding denial is emitted."""
    probe_code = _factory_plugin_code(
        "enforce-session-start.ts",
        "tool.execute.before",
        "await plugin['tool.execute.before']({tool: 'read'}, {})",
    )
    assert _run_ts(probe_code, env_override={"OPENCODE_SUBAGENT": "1"}) is None
    result = _session_start_dispatch_then_bash(configured_min=None)
    assert result["dp1"] == 1, f"Expected dispatches=1 after task call, got: {result}"
    assert result["denied"] is False, f"Adaptive default must allow the bash call: {result}"


def test_session_start_explicit_minimum_denies_under_dispatch() -> None:
    """An explicit ten-dispatch minimum denies a mutation after one dispatch."""
    result = _session_start_dispatch_then_bash(configured_min="10")
    assert result["dp1"] == 1, f"Expected dispatches=1 after task call, got: {result}"
    assert result["denied"] is True, f"Configured minimum must deny under-dispatch: {result}"
    assert result["hasProtocol"] is True, f"Deny message missing SESSION START PROTOCOL: {result}"


# ---------------------------------------------------------------------------
# enforce-make.ts  —  bash command enforcement (non-make + metachar blocking)
# ---------------------------------------------------------------------------


def _enforce_make_bash_test(command: str, env_override: dict[str, str] | None = None) -> dict[str, Any]:
    """Run a bash command through enforce-make.ts tool.execute.before.
    Returns {allowed: true} or {permissionDecision: 'deny', message: '...'}.
    """
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-make.ts')
const plugin = await mod.default({{}})
try {{
  const result = await plugin['tool.execute.before'](
    {{tool: 'bash', args: {{command: {json.dumps(command)}}}}}, undefined)
  console.log(JSON.stringify({{allowed: true}}))
}} catch (e) {{
  console.log(JSON.stringify({{permissionDecision: 'deny', message: String(e.message)}}))
}}
"""
    return cast(dict[str, Any], _run_ts(code, env_override=env_override))


def test_make_allows_make_target() -> None:
    """bash 'make lint' → allowed (no deny)."""
    result = _enforce_make_bash_test("make lint")
    assert result is not None
    assert result.get("allowed") is True, f"make lint should be allowed, got: {result}"


def test_make_denies_cd_command() -> None:
    """bash 'cd /tmp' → permissionDecision: 'deny'."""
    result = _enforce_make_bash_test("cd /tmp")
    assert result is not None
    assert result.get("permissionDecision") == "deny", f"cd should be denied, got: {result}"
    assert "does not start with 'make'" in result.get("message", "").lower()


def test_make_denies_python() -> None:
    """bash 'python script.py' → deny."""
    result = _enforce_make_bash_test("python script.py")
    assert result.get("permissionDecision") == "deny", f"python should be denied, got: {result}"


def test_make_denies_pip() -> None:
    """bash 'pip install x' → deny."""
    result = _enforce_make_bash_test("pip install x")
    assert result.get("permissionDecision") == "deny", f"pip should be denied, got: {result}"


def test_make_denies_git() -> None:
    """bash 'git status' → deny."""
    result = _enforce_make_bash_test("git status")
    assert result.get("permissionDecision") == "deny", f"git should be denied, got: {result}"


def test_make_denies_metachar_pipe() -> None:
    """bash 'make test | grep' → deny (pipe not allowed)."""
    result = _enforce_make_bash_test("make test | grep")
    assert result.get("permissionDecision") == "deny", f"pipe should be denied, got: {result}"
    assert "BLOCKED" in result.get("message", "")


def test_make_denies_metachar_semicolon() -> None:
    """bash 'make test; make lint' → deny."""
    result = _enforce_make_bash_test("make test; make lint")
    assert result.get("permissionDecision") == "deny", f"; should be denied, got: {result}"


def test_make_denies_metachar_and() -> None:
    """bash 'make test && make lint' → deny."""
    result = _enforce_make_bash_test("make test && make lint")
    assert result.get("permissionDecision") == "deny", f"&& should be denied, got: {result}"


def test_make_denies_metachar_dollar() -> None:
    """bash 'make $(cat file)' → deny."""
    result = _enforce_make_bash_test("make $(cat file)")
    assert result.get("permissionDecision") == "deny", f"$() should be denied, got: {result}"


def test_make_denies_redirect() -> None:
    """bash 'make test > file' → deny (redirect involves metachar)."""
    result = _enforce_make_bash_test("make test > file")
    assert result.get("permissionDecision") == "deny", f"> redirect should be denied, got: {result}"


def test_make_subagent_guard() -> None:
    """OPENCODE_SUBAGENT=1 → allowed (skip)."""
    result = _enforce_make_bash_test("cd /tmp", env_override={"OPENCODE_SUBAGENT": "1"})
    assert result.get("allowed") is True, f"Subagent should bypass enforcement, got: {result}"


def test_make_disengage_escape() -> None:
    """GLUDD_MAKE_ENFORCE=0 → allowed (disengage)."""
    result = _enforce_make_bash_test("cd /tmp", env_override={"GLUDD_MAKE_ENFORCE": "0"})
    assert result.get("allowed") is True, f"MAKE_ENFORCE=0 should disengage, got: {result}"


# ---------------------------------------------------------------------------
# enforce-make.ts  —  bash command blocking (runtime tests: bare commands)
# ---------------------------------------------------------------------------


def test_make_allows_make_lint() -> None:
    """bash 'make lint' → ALLOWED (starts with 'make')."""
    result = _enforce_make_bash_test("make lint")
    assert result is not None
    assert result.get("allowed") is True, f"make lint should be allowed, got: {result}"


def test_make_denies_python3() -> None:
    """bash 'python3 -c "print(1)"' → DENIED (bare command)."""
    result = _enforce_make_bash_test('python3 -c "print(1)"')
    assert result is not None
    assert result.get("permissionDecision") == "deny", f"python3 should be denied, got: {result}"


def test_make_denies_gh() -> None:
    """bash 'gh --version' → DENIED (non-make binary)."""
    result = _enforce_make_bash_test("gh --version")
    assert result is not None
    assert result.get("permissionDecision") == "deny", f"gh should be denied, got: {result}"


def test_make_denies_cat() -> None:
    """bash 'cat file.txt' → DENIED (non-make command)."""
    result = _enforce_make_bash_test("cat file.txt")
    assert result is not None
    assert result.get("permissionDecision") == "deny", f"cat should be denied, got: {result}"


def test_make_denies_git_status() -> None:
    """bash 'git status' → DENIED (non-make command)."""
    result = _enforce_make_bash_test("git status")
    assert result is not None
    assert result.get("permissionDecision") == "deny", f"git status should be denied, got: {result}"


def test_make_denies_pipe_in_make_args() -> None:
    """bash 'make test | grep FAILED' → DENIED (metacharacter pipe)."""
    result = _enforce_make_bash_test("make test | grep FAILED")
    assert result is not None
    assert result.get("permissionDecision") == "deny", f"pipe should be denied, got: {result}"
    assert "BLOCKED" in result.get("message", "")


def test_make_denies_and_in_make_args() -> None:
    """bash 'make test && make lint' → DENIED (metacharacter &&)."""
    result = _enforce_make_bash_test("make test && make lint")
    assert result is not None
    assert result.get("permissionDecision") == "deny", f"&& should be denied, got: {result}"


# ---------------------------------------------------------------------------
# enforce-additive-task.ts  —  real proxy-hook invocation
# ---------------------------------------------------------------------------


def test_additive_task_runtime_hook_invocation() -> None:
    code = _factory_plugin_code(
        "enforce-additive-task.ts",
        "tool.execute.before",
        "await plugin['tool.execute.before']({tool: 'read', args: {}}, {args: {}})",
    )
    result = _run_ts(code)
    assert result is None or isinstance(result, dict)


# ---------------------------------------------------------------------------
# enforce-anti-essay.ts  —  real proxy-hook invocation
# ---------------------------------------------------------------------------


def test_anti_essay_runtime_hook_invocation() -> None:
    code = _factory_plugin_code(
        "enforce-anti-essay.ts",
        "tool.execute.before",
        "await plugin['tool.execute.before']({tool: 'read', args: {}}, {args: {}})",
    )
    result = _run_ts(code)
    assert result is None or isinstance(result, dict)


def test_anti_essay_blocks_explanation_status_phrase_with_pending_work() -> None:
    """A pending-work explanation phrase is replaced by the enforcement notice."""
    code = _factory_plugin_code(
        "enforce-anti-essay.ts",
        "experimental.text.complete",
        "await plugin['experimental.text.complete']({}, {text: 'Let me explain the current status'})",
    )
    env, tasks_path = _with_open_work({}, "")
    try:
        result = _run_ts(code, env_override=env)
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tasks_path)
    assert isinstance(result, dict)
    assert "ANTI-ESSAY GUARD" in str(result.get("text", ""))
    assert "Let me explain" not in str(result.get("text", ""))


# ---------------------------------------------------------------------------
# enforce-audit.ts  —  real text-complete hook invocation
# ---------------------------------------------------------------------------


def test_audit_runtime_hook_invocation() -> None:
    code = _factory_plugin_code(
        "enforce-audit.ts",
        "experimental.text.complete",
        "await plugin['experimental.text.complete']({}, {text: 'runtime hook smoke'})",
    )
    result = _run_ts(code)
    assert result is None or isinstance(result, dict)


# ---------------------------------------------------------------------------
# enforce-batch-push.ts  —  real proxy-hook invocation
# ---------------------------------------------------------------------------


def test_batch_push_runtime_hook_invocation() -> None:
    code = _factory_plugin_code(
        "enforce-batch-push.ts",
        "tool.execute.before",
        "await plugin['tool.execute.before']({tool: 'read', args: {}}, {args: {}})",
    )
    result = _run_ts(code)
    assert result is None or isinstance(result, dict)


# ---------------------------------------------------------------------------
# enforce-branch-discipline.ts  —  real proxy-hook invocation
# ---------------------------------------------------------------------------


def test_branch_discipline_runtime_hook_invocation() -> None:
    code = _factory_plugin_code(
        "enforce-branch-discipline.ts",
        "tool.execute.before",
        "await plugin['tool.execute.before']({tool: 'read', args: {}}, {args: {}})",
    )
    result = _run_ts(code)
    assert result is None or isinstance(result, dict)


# ---------------------------------------------------------------------------
# enforce-context.ts  —  real proxy-hook invocation
# ---------------------------------------------------------------------------


def test_context_runtime_hook_invocation() -> None:
    code = _factory_plugin_code(
        "enforce-context.ts",
        "tool.execute.before",
        "await plugin['tool.execute.before']({tool: 'read', args: {}}, {args: {}})",
    )
    result = _run_ts(code, env_override={"GLUDD_CONTEXT_ENFORCE": "0"})
    assert result is None or isinstance(result, dict)


# ---------------------------------------------------------------------------
# enforce-deliverable.ts  —  real proxy-hook invocation
# ---------------------------------------------------------------------------


def test_deliverable_runtime_hook_invocation() -> None:
    code = _factory_plugin_code(
        "enforce-deliverable.ts",
        "tool.execute.before",
        "await plugin['tool.execute.before']({tool: 'read', args: {}}, {args: {}})",
    )
    result = _run_ts(code)
    assert result is None or isinstance(result, dict)


# ---------------------------------------------------------------------------
# enforce-depth.ts  —  real proxy-hook invocation
# ---------------------------------------------------------------------------


def test_depth_runtime_hook_invocation() -> None:
    code = _factory_plugin_code(
        "enforce-depth.ts",
        "tool.execute.before",
        "await plugin['tool.execute.before']({tool: 'read', args: {}}, {args: {}})",
    )
    result = _run_ts(code)
    assert result is None or isinstance(result, dict)


# ---------------------------------------------------------------------------
# enforce-directives.ts  —  real proxy-hook invocation
# ---------------------------------------------------------------------------


def test_directives_runtime_hook_invocation() -> None:
    code = _factory_plugin_code(
        "enforce-directives.ts",
        "tool.execute.before",
        "await plugin['tool.execute.before']({tool: 'read', args: {}}, {args: {}})",
    )
    result = _run_ts(code, env_override={"GLUDD_DIRECTIVE_ENFORCE": "0"})
    assert result is None or isinstance(result, dict)


# ---------------------------------------------------------------------------
# enforce-floor-v2.ts  —  real proxy-hook invocation
# ---------------------------------------------------------------------------


def test_floor_v2_runtime_hook_invocation() -> None:
    code = _factory_plugin_code(
        "enforce-floor-v2.ts",
        "tool.execute.before",
        "await plugin['tool.execute.before']({tool: 'read', args: {}}, {args: {}})",
    )
    result = _run_ts(code, env_override={"GLUDD_FLOOR_V2_ENFORCE": "0"})
    assert result is None or isinstance(result, dict)


# ---------------------------------------------------------------------------
# enforce-no-ci-poll.ts  —  real proxy-hook invocation
# ---------------------------------------------------------------------------


def test_no_ci_poll_runtime_hook_invocation() -> None:
    code = _factory_plugin_code(
        "enforce-no-ci-poll.ts",
        "tool.execute.before",
        "await plugin['tool.execute.before']({tool: 'read', args: {}}, {args: {}})",
    )
    result = _run_ts(code, env_override={"GLUDD_NO_CI_POLL_ENFORCE": "0"})
    assert result is None or isinstance(result, dict)


# ---------------------------------------------------------------------------
# enforce-no-suppressions.ts  —  real proxy-hook invocation
# ---------------------------------------------------------------------------


def test_no_suppressions_runtime_hook_invocation() -> None:
    code = _factory_plugin_code(
        "enforce-no-suppressions.ts",
        "tool.execute.before",
        "await plugin['tool.execute.before']({tool: 'read', args: {}}, {args: {}})",
    )
    result = _run_ts(code)
    assert result is None or isinstance(result, dict)


# ---------------------------------------------------------------------------
# enforce-objective.ts  —  real proxy-hook invocation
# ---------------------------------------------------------------------------


def test_objective_runtime_hook_invocation() -> None:
    code = _factory_plugin_code(
        "enforce-objective.ts",
        "tool.execute.before",
        "await plugin['tool.execute.before']({tool: 'read', args: {}}, {args: {}})",
    )
    result = _run_ts(code, env_override={"GLUDD_OBJECTIVE_ENFORCE": "0"})
    assert result is None or isinstance(result, dict)


# ---------------------------------------------------------------------------
# enforce-pipeline-kickoff.ts  —  real proxy-hook invocation
# ---------------------------------------------------------------------------


def test_pipeline_kickoff_runtime_hook_invocation() -> None:
    code = _factory_plugin_code(
        "enforce-pipeline-kickoff.ts",
        "tool.execute.before",
        "await plugin['tool.execute.before']({tool: 'read', args: {}}, {args: {}})",
    )
    result = _run_ts(code, env_override={"GLUDD_PIPELINE_KICKOFF_ENFORCE": "0"})
    assert result is None or isinstance(result, dict)


# ---------------------------------------------------------------------------
# enforce-release-deadline.ts  —  real proxy-hook invocation
# ---------------------------------------------------------------------------


def test_release_deadline_runtime_hook_invocation() -> None:
    code = _factory_plugin_code(
        "enforce-release-deadline.ts",
        "tool.execute.before",
        "await plugin['tool.execute.before']({tool: 'read', args: {}}, {args: {}})",
    )
    result = _run_ts(code, env_override={"GLUDD_RELEASE_DEADLINE_ENFORCE": "0"})
    assert result is None or isinstance(result, dict)


# ---------------------------------------------------------------------------
# enforce-task-tracking.ts  —  real proxy-hook invocation
# ---------------------------------------------------------------------------


def test_task_tracking_runtime_hook_invocation() -> None:
    code = _factory_plugin_code(
        "enforce-task-tracking.ts",
        "tool.execute.before",
        "await plugin['tool.execute.before']({tool: 'read', args: {}}, {args: {}})",
    )
    result = _run_ts(code, env_override={"GLUDD_TASK_TRACKING_ENFORCE": "0"})
    assert result is None or isinstance(result, dict)


# ---------------------------------------------------------------------------
# enforce-tdd.ts  —  real proxy-hook invocation
# ---------------------------------------------------------------------------


def test_tdd_runtime_hook_invocation() -> None:
    code = _factory_plugin_code(
        "enforce-tdd.ts",
        "tool.execute.before",
        "await plugin['tool.execute.before']({tool: 'read', args: {}}, {args: {}})",
    )
    result = _run_ts(code)
    assert result is None or isinstance(result, dict)


# ---------------------------------------------------------------------------
# enforce-test-integrity.ts  —  real proxy-hook invocation
# ---------------------------------------------------------------------------


def test_test_integrity_runtime_hook_invocation() -> None:
    code = _factory_plugin_code(
        "enforce-test-integrity.ts",
        "tool.execute.before",
        "await plugin['tool.execute.before']({tool: 'read', args: {}}, {args: {}})",
    )
    result = _run_ts(code)
    assert result is None or isinstance(result, dict)


# ---------------------------------------------------------------------------
# enforce-worktree.ts  —  real proxy-hook invocation
# ---------------------------------------------------------------------------


def test_worktree_runtime_hook_invocation() -> None:
    code = _factory_plugin_code(
        "enforce-worktree.ts",
        "tool.execute.before",
        "await plugin['tool.execute.before']({tool: 'read', args: {}}, {args: {}})",
    )
    result = _run_ts(code)
    assert result is None or isinstance(result, dict)


# ---------------------------------------------------------------------------
# watchdog.ts  —  session lifecycle daemon launcher
# ---------------------------------------------------------------------------

WATCHDOG_PATH = str(_OPENCODE_DIR / "plugins" / "watchdog.ts")


def test_watchdog_event_hook_runtime_invocation() -> None:
    """Invoke the real event hook and prove its isolated lifecycle effects."""
    pid_path = _runtime_state_root() / f"gludd-watchdog-hook-{os.getpid()}.pid"
    alive_path = _runtime_state_root() / f"gludd-watchdog-hook-{os.getpid()}.json"
    _clean_state_files(str(pid_path), str(alive_path))
    code = f"""\
const mod = await import('{_OPENCODE_DIR}/plugins/watchdog.ts')
const plugin = await mod.default({{}})
await plugin.event({{event: {{type: 'session.created'}}}})
const fs = await import('node:fs')
const created = fs.existsSync('{pid_path}')
await plugin.event({{event: {{type: 'session.deleted'}}}})
console.log(JSON.stringify({{created, removed: !fs.existsSync('{pid_path}')}}))
"""
    result = _run_ts(
        code,
        env_override={
            "GLUDD_WATCHDOG_PID_FILE": str(pid_path),
            "GLUDD_TASK_WATCHDOG_PID": str(pid_path.with_suffix(".task.pid")),
            "GLUDD_ALIVE_PATH": str(alive_path),
        },
    )
    assert result == {"created": True, "removed": True}
    _clean_state_files(str(pid_path), str(alive_path))


def test_watchdog_plugin_loads_report_alive() -> None:
    """watchdog plugin loads, calls reportAlive on init, and exposes its event hook."""
    alive_path = f"/tmp/gludd-test-alive-{os.getpid()}-1.json"
    _clean_state_files(alive_path)
    code = f"""\
const mod = await import('{WATCHDOG_PATH}')
const plugin = await mod.default({{}})
const keys = Object.keys(plugin)
console.log(JSON.stringify({{ ok: true, keys }}))
"""
    result = _run_ts(code, env_override={"GLUDD_ALIVE_PATH": alive_path})
    assert result["ok"] is True, f"Watchdog plugin load should not throw, got: {result}"
    assert result["keys"] == ["event"], f"Plugin should expose event hook, got keys: {result['keys']}"
    # Verify reportAlive was called on module load
    assert os.path.exists(alive_path), f"Alive file {alive_path} should exist after plugin load"
    with open(alive_path) as f:
        alive = json.load(f)
    assert "watchdog" in alive, f"watchdog key missing from alive file: {alive}"
    assert isinstance(alive["watchdog"]["last_seen"], int)
    _clean_state_files(alive_path)


def test_watchdog_plugin_loads_no_error() -> None:
    """Watchdog plugin loads without error even with no event hook surface."""
    code = f"""\
const mod = await import('{WATCHDOG_PATH}')
const plugin = await mod.default({{}})
console.log(JSON.stringify({{ ok: true, isObject: typeof plugin === 'object' }}))
"""
    result = _run_ts(code)
    assert result["ok"] is True, f"Watchdog should load without error, got: {result}"
    assert result["isObject"] is True


def test_watchdog_plugin_subagent_context() -> None:
    """OPENCODE_SUBAGENT=1: watchdog plugin still loads (it's infra, not enforcement)."""
    alive_path = f"/tmp/gludd-test-alive-{os.getpid()}-2.json"
    _clean_state_files(alive_path)
    code = f"""\
const mod = await import('{WATCHDOG_PATH}')
const plugin = await mod.default({{}})
console.log(JSON.stringify({{ ok: true, isObject: typeof plugin === 'object' }}))
"""
    result = _run_ts(code, env_override={"OPENCODE_SUBAGENT": "1", "GLUDD_ALIVE_PATH": alive_path})
    assert result["ok"] is True, f"Watchdog should load even in subagent context, got: {result}"
    # Verify alive file was still written (reportAlive runs on module load)
    assert os.path.exists(alive_path), "Watchdog must report alive even as subagent"
    _clean_state_files(alive_path)


def test_watchdog_plugin_env_disabled() -> None:
    """GLUDD_WATCHDOG_ENABLED=0: plugin still loads (reportAlive happens on import)."""
    alive_path = f"/tmp/gludd-test-alive-{os.getpid()}-3.json"
    _clean_state_files(alive_path)
    code = f"""\
const mod = await import('{WATCHDOG_PATH}')
const plugin = await mod.default({{}})
console.log(JSON.stringify({{ ok: true }}))
"""
    result = _run_ts(code, env_override={"GLUDD_WATCHDOG_ENABLED": "0", "GLUDD_ALIVE_PATH": alive_path})
    assert result["ok"] is True, f"Disabled watchdog should load without error, got: {result}"
    # The plugin itself doesn't check GLUDD_WATCHDOG_ENABLED (the daemon does)
    # reportAlive is called on module load regardless
    _clean_state_files(alive_path)


def test_watchdog_plugin_loads_with_corrupt_pid_file() -> None:
    """Corrupt PID file does not crash watchdog plugin load (fail-open)."""
    pid_file = os.environ.get("GLUDD_WATCHDOG_PID_FILE", ".gate-logs/watchdog.pid")
    os.makedirs(os.path.dirname(pid_file) or ".", exist_ok=True)
    with open(pid_file, "w") as f:
        f.write("not-a-valid-pid-99999999999999999")
    try:
        code = f"""\
const mod = await import('{WATCHDOG_PATH}')
const plugin = await mod.default({{}})
console.log(JSON.stringify({{ ok: true }}))
"""
        result = _run_ts(code)
        assert result["ok"] is True, f"Corrupt PID file must not crash plugin load, got: {result}"
    finally:
        _clean_state_files(pid_file)


# ---------------------------------------------------------------------------
# enforce-commit-lock.ts  —  commit serialization via lock file
# ---------------------------------------------------------------------------


def test_commit_lock_allowed_no_lock() -> None:
    """Commit target allowed when no lock file exists (tryAcquire succeeds)."""
    lock_path = f"/tmp/gludd-commit-lock-test-a-{os.getpid()}"
    _clean_state_files(lock_path)
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-commit-lock.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'bash', command: 'make ship-commit MSG=test'}})
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(code, env_override={"GLUDD_COMMIT_LOCK_PATH": lock_path})
    assert result is None or result.get("allowed") is True, f"Expected allowed, got: {result}"
    _clean_state_files(lock_path)


def test_commit_lock_fresh_lock_denies() -> None:
    """Fresh lock file (<5 min) blocks another commit with deny + DENY_MESSAGE."""
    lock_path = f"/tmp/gludd-commit-lock-test-d-{os.getpid()}"
    _clean_state_files(lock_path)
    with open(lock_path, "w") as f:
        f.write(str(os.getpid()))
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-commit-lock.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'bash', command: 'make git-commit MSG=test'}})
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(code, env_override={"GLUDD_COMMIT_LOCK_PATH": lock_path})
    assert result is not None, "Expected deny object, got None (allowed)"
    assert result.get("permissionDecision") == "deny", f"Expected deny, got: {result}"
    assert "COMMIT-LOCK" in result.get("message", ""), f"Message missing COMMIT-LOCK: {result}"
    _clean_state_files(lock_path)


def test_commit_lock_stale_break_allows() -> None:
    """Stale lock (>STALE_THRESHOLD_MS) is broken and commit allowed."""
    lock_path = f"/tmp/gludd-commit-lock-test-s-{os.getpid()}"
    _clean_state_files(lock_path)
    with open(lock_path, "w") as f:
        f.write("stale")
    six_min_ago = time.time() - 360
    os.utime(lock_path, (six_min_ago, six_min_ago))
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-commit-lock.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'bash', command: 'make repo-commit MSG=test'}})
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(code, env_override={"GLUDD_COMMIT_LOCK_PATH": lock_path})
    assert result is None or result.get("allowed") is True, f"Stale lock should allow, got: {result}"
    _clean_state_files(lock_path)


def test_commit_lock_non_commit_allowed() -> None:
    """Non-commit bash command passes through without lock check."""
    lock_path = f"/tmp/gludd-commit-lock-test-nc-{os.getpid()}"
    _clean_state_files(lock_path)
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-commit-lock.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'bash', command: 'make test-unit'}})
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(code, env_override={"GLUDD_COMMIT_LOCK_PATH": lock_path})
    assert result is None or result.get("allowed") is True, f"Non-commit should pass through, got: {result}"
    _clean_state_files(lock_path)


def test_commit_lock_subagent_guard() -> None:
    """OPENCODE_SUBAGENT=1 skips enforcement even with lock present."""
    lock_path = f"/tmp/gludd-commit-lock-test-sub-{os.getpid()}"
    _clean_state_files(lock_path)
    with open(lock_path, "w") as f:
        f.write("locked")
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-commit-lock.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'bash', command: 'make ship-commit MSG=test'}})
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(
        code,
        env_override={
            "GLUDD_COMMIT_LOCK_PATH": lock_path,
            "OPENCODE_SUBAGENT": "1",
        },
    )
    assert result is None or result.get("allowed") is True, f"Subagent should skip, got: {result}"
    _clean_state_files(lock_path)


def test_commit_lock_env_disable() -> None:
    """GLUDD_COMMIT_LOCK_ENFORCE=0 disables lock enforcement entirely."""
    lock_path = f"/tmp/gludd-commit-lock-test-dis-{os.getpid()}"
    _clean_state_files(lock_path)
    with open(lock_path, "w") as f:
        f.write("locked")
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-commit-lock.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'bash', command: 'make ship-commit MSG=test'}})
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(
        code,
        env_override={
            "GLUDD_COMMIT_LOCK_PATH": lock_path,
            "GLUDD_COMMIT_LOCK_ENFORCE": "0",
        },
    )
    assert result is None or result.get("allowed") is True, f"Disabled should allow, got: {result}"
    _clean_state_files(lock_path)


def test_commit_lock_after_releases_lock() -> None:
    """execute.after hook releases the lock file acquired by execute.before."""
    lock_path = f"/tmp/gludd-commit-lock-test-af-{os.getpid()}"
    _clean_state_files(lock_path)
    code = f"""\
const fs = await import('node:fs')
const mod = await import('{PLUGIN_DIR}/enforce-commit-lock.ts')
const plugin = await mod.default({{}})
const beforeResult = await plugin['tool.execute.before']({{tool: 'bash', command: 'make ship-commit MSG=test'}})
const lockBefore = fs.existsSync('{lock_path}')
await plugin['tool.execute.after']({{tool: 'bash', command: 'make ship-commit MSG=test'}})
const lockAfter = fs.existsSync('{lock_path}')
console.log(JSON.stringify({{beforeOk: beforeResult === undefined, lockBefore, lockAfter: !lockAfter}}))
"""
    result = _run_ts(code, env_override={"GLUDD_COMMIT_LOCK_PATH": lock_path})
    assert result["beforeOk"] is True, "Before hook should allow"
    assert result["lockBefore"] is True, "Lock must exist after before hook"
    assert result["lockAfter"] is True, "Lock must be removed after after hook"
    _clean_state_files(lock_path)


def test_commit_lock_is_commit_command() -> None:
    """isCommitCommand matches commit targets, rejects non-commit and non-make."""
    code = f"""\
const mod = await import('{LIB_DIR}/plugin_test_exports.ts')
console.log(JSON.stringify({{
    ship: mod.isCommitCommand('make ship-commit MSG=test'),
    nonCommit: mod.isCommitCommand('make test-unit'),
    gitCommit: mod.isCommitCommand('make git-commit MSG=test'),
    commitNoVerify: mod.isCommitCommand('make commit-no-verify MSG=test'),
    gitAmend: mod.isCommitCommand('make git-amend-msg MSG=fix'),
    notMake: mod.isCommitCommand('git commit'),
    testAndCommit: mod.isCommitCommand('make test-and-commit MSG=test'),
    repoCommit: mod.isCommitCommand('make repo-commit MSG=test'),
    empty: mod.isCommitCommand(''),
    noTarget: mod.isCommitCommand('make '),
}}))
"""
    result = _run_ts(code)
    assert result["ship"] is True
    assert result["nonCommit"] is False
    assert result["gitCommit"] is True
    assert result["commitNoVerify"] is True
    assert result["gitAmend"] is True
    assert result["notMake"] is False
    assert result["testAndCommit"] is True
    assert result["repoCommit"] is True
    assert result["empty"] is False
    assert result["noTarget"] is False
