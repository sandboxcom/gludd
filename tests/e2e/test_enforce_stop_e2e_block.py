"""E2E test for enforce-stop.ts: full plugin chain with real filesystem state.

Tests the enforce-stop plugin through the actual hook harness (Node loads plugin
→ executes factory → resolves hooks → invokes with input/output — the closest
available analog to opencode's plugin framework). State is set up via REAL
filesystem entries (TASKS.md, ratchet.yml, CI cache, .gate-status, BUGS.md)
that hasRealPendingWork() reads directly — no pre-seeded JSON state-file bypass.

Tests the FULL sequential flow of an opencode session:
  1. system.transform: injects the MANDATORY PRE-GENERATION GATE when work exists
  2. text.complete: blanks text-only stop-pattern responses
  3. tool.execute.before: denies non-dispatch tools after persist block
  4. Dispatch clears persist block → subsequent tool calls allowed
"""

from __future__ import annotations

import json
import time as _time
from pathlib import Path

import pytest

from tests.unit._hook_fixtures import (
    HookEnv,
    hook_plugin_env_impl,
)

ROOT = Path(__file__).parent.parent.parent
PERSIST_BLOCK_ENV = "GLUDD_PERSIST_STOP_BLOCK_FILE"
CI_CACHE_PATH = Path("/tmp/gludd-watchdog-ci.json")

# CI cache is shared — serialize onto one xdist worker
pytestmark = pytest.mark.xdist_group("gludd-watchdog-ci-cache")


@pytest.fixture
def hook_plugin_env(tmp_path: Path):
    yield from hook_plugin_env_impl(tmp_path)


@pytest.fixture(autouse=True)
def _ci_cache_guard():
    old_ci = CI_CACHE_PATH.read_bytes() if CI_CACHE_PATH.exists() else None
    old_mt = MULTITASK_STATE_PATH.read_bytes() if MULTITASK_STATE_PATH.exists() else None
    try:
        yield
    finally:
        try:
            if old_ci is None:
                CI_CACHE_PATH.unlink(missing_ok=True)
            else:
                CI_CACHE_PATH.write_bytes(old_ci)
        except OSError:
            pass
        try:
            if old_mt is None:
                MULTITASK_STATE_PATH.unlink(missing_ok=True)
            else:
                MULTITASK_STATE_PATH.write_bytes(old_mt)
        except OSError:
            pass


def _seed_ci_cache(status: str, now_ms: int | None = None) -> None:
    CI_CACHE_PATH.write_text(json.dumps({
        "last_ci_check": now_ms or int(_time.time() * 1000),
        "last_ci_status": status,
        "run_id": "000000",
        "head_sha": "000000000",
    }))


def _setup_real_pending_work(env: HookEnv) -> None:
    """Create REAL filesystem state that hasRealPendingWork() reads directly
    from process.cwd() — NO pre-seeded GLUDD_STOP_STATE_FILE JSON."""

    # TASKS.md with unchecked items
    (env.cwd / "TASKS.md").write_text(
        "- [ ] Fix critical auth bug in daemon.py\n"
        "- [ ] Implement multi-tenant isolation\n"
        "- [ ] Write unit tests for event loop\n"
        "- [ ] Audit secrets handling in Worker\n"
        "- [ ] Update API docs for /admin/token\n"
        "- [ ] Fix CI pipeline flake on macOS\n"
        "- [ ] Review PR #142 (game builder)\n"
    )

    # ratchet.yml with entries
    (env.cwd / "config").mkdir(exist_ok=True)
    (env.cwd / "config" / "ratchet.yml").write_text(
        "lint:\n  max_errors: 0\n"
        "typecheck:\n  max_errors: 10\n"
        "coverage:\n  min_pct: 85.0\n"
        "secrets:\n  max_findings: 0\n"
    )

    # .gate-status with a FAIL line
    (env.cwd / ".gate-status").write_text(
        "=== GATE: FAILED ===\n"
        "lint FAIL (12 errors)\n"
        "typecheck OK\n"
        "collect OK\n"
        "test FAIL (3 failures)\n"
    )

    # CI cache: non-SUCCESS status
    _seed_ci_cache("failure")

    # Clear multitask state so underFloor doesn't interfere with exact checks
    MULTITASK_STATE_PATH.write_text(json.dumps({
        "thisMessageDispatches": 10,
        "sessionDispatches": 10,
        "lastDispatchTs": int(_time.time() * 1000),
    }))


MULTITASK_STATE_PATH = Path("/tmp/gludd-multitask-state.json")


def _setup_clean_state(env: HookEnv) -> None:
    """No pending work — clean filesystem state."""
    now_ms = int(_time.time() * 1000)
    _seed_ci_cache("SUCCESS", now_ms=now_ms)
    # Clear multitask-state so underFloor doesn't trigger
    MULTITASK_STATE_PATH.write_text(json.dumps({
        "thisMessageDispatches": 10,
        "sessionDispatches": 10,
        "lastDispatchTs": now_ms,
    }))
    # Write a clean .gate-status
    (env.cwd / ".gate-status").write_text(
        "=== GATE: PASSED ===\n"
        "lint OK\n"
        "typecheck OK\n"
        "collect OK\n"
        "test OK (42 passed)\n"
    )
    # Ensure no TASKS.md (or empty)
    tasks = env.cwd / "TASKS.md"
    if tasks.exists():
        tasks.unlink()
    # Ensure no ratchet.yml
    ratchet = env.cwd / "config" / "ratchet.yml"
    if ratchet.exists():
        ratchet.unlink()
    # Ensure no BUGS.md
    bugs = env.cwd / "BUGS.md"
    if bugs.exists():
        bugs.unlink()


# ── Harness helpers ──────────────────────────────────────────────────────────

def _invoke_text_complete(env: HookEnv, text: str, **overrides) -> tuple[dict | None, str, str, int]:
    import contextlib
    env_overrides = dict(overrides)
    env_overrides.setdefault(PERSIST_BLOCK_ENV, str(env.cwd / "persist-stop-block.json"))
    result = env.invoke(
        "enforce-stop.ts", "experimental.text.complete",
        input={}, output={"text": text},
        env_overrides=env_overrides, timeout=15,
    )
    stdout_raw = result.stdout.strip()
    parsed = None
    if stdout_raw:
        with contextlib.suppress(json.JSONDecodeError):
            parsed = json.loads(stdout_raw)
    return parsed, stdout_raw, result.stderr, result.returncode


def _invoke_system_transform(env: HookEnv, output_text: str) -> tuple[str | None, str, int]:
    result = env.invoke(
        "enforce-stop.ts", "experimental.chat.system.transform",
        input={}, output=output_text, timeout=15,
    )
    stdout_raw = result.stdout.strip()
    # The harness JSON-encodes the return value — if it's a string, decode it.
    if stdout_raw:
        try:
            decoded = json.loads(stdout_raw)
            if isinstance(decoded, str):
                return decoded, result.stderr, result.returncode
        except json.JSONDecodeError:
            pass
        return stdout_raw, result.stderr, result.returncode
    return None, result.stderr, result.returncode


def _invoke_tool_before(env: HookEnv, tool: str) -> tuple[dict | None, str, str, int]:
    import contextlib
    result = env.invoke(
        "enforce-stop.ts", "tool.execute.before",
        input={"tool": tool}, output={},
        env_overrides={PERSIST_BLOCK_ENV: str(env.cwd / "persist-stop-block.json")},
        timeout=15,
    )
    stdout_raw = result.stdout.strip()
    parsed = None
    if stdout_raw:
        with contextlib.suppress(json.JSONDecodeError):
            parsed = json.loads(stdout_raw)
    return parsed, stdout_raw, result.stderr, result.returncode


def _read_persist_block(env: HookEnv) -> dict | None:
    pb_path = env.cwd / "persist-stop-block.json"
    if not pb_path.exists():
        return None
    return json.loads(pb_path.read_text())


# ═══════════════════════════════════════════════════════════════════════════════
# 1: system.transform — injects PRE-GENERATION GATE when pending work exists
# ═══════════════════════════════════════════════════════════════════════════════


class TestSystemTransformWithRealState:
    """system.transform reads hasRealPendingWork() from the real CWD filesystem."""

    def test_system_transform_injects_gate_when_work_exists(
        self, hook_plugin_env: HookEnv,
    ):
        _setup_real_pending_work(hook_plugin_env)

        transformed, stderr, rc = _invoke_system_transform(
            hook_plugin_env, "normal system prompt content",
        )
        assert rc == 0, stderr
        assert transformed is not None, "Must return transformed output"
        assert "MANDATORY PRE-GENERATION GATE" in transformed, (
            f"Must inject gate when work exists. Got: {transformed[:300]}"
        )
        assert "PENDING WORK EXISTS" in transformed

    def test_system_transform_passes_through_when_no_work(
        self, hook_plugin_env: HookEnv,
    ):
        _setup_clean_state(hook_plugin_env)

        original = "normal system prompt content"
        transformed, stderr, rc = _invoke_system_transform(hook_plugin_env, original)
        assert rc == 0, stderr
        assert transformed is not None
        assert "[orchestration] No pending work" in transformed
        assert original in transformed

    def test_system_transform_preserves_subagent_result_markers(
        self, hook_plugin_env: HookEnv,
    ):
        _setup_real_pending_work(hook_plugin_env)

        original = "task_id: abc-123\nsubagent result: completed successfully\nexit code: 0"
        transformed, stderr, rc = _invoke_system_transform(hook_plugin_env, original)
        assert rc == 0, stderr
        # Subagent result markers: transform returns output unchanged
        assert transformed == original, (
            f"Subagent results must pass through unchanged. Got: {transformed[:200]}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 2: text.complete — blocks text-only with real filesystem state
# ═══════════════════════════════════════════════════════════════════════════════


class TestTextCompleteBlocksWithRealState:
    """text.complete reads hasRealPendingWork() from real files, not pre-seeded JSON."""

    def test_status_summary_with_real_pending_work_blocked(
        self, hook_plugin_env: HookEnv,
    ):
        _setup_real_pending_work(hook_plugin_env)

        parsed, raw, stderr, rc = _invoke_text_complete(
            hook_plugin_env,
            "Here's the final status of the session. Everything committed. "
            "Continuing with remaining items.",
        )
        assert rc == 0, stderr

        if parsed is None:
            pb = _read_persist_block(hook_plugin_env)
            assert pb is not None, (
                f"Status summary with real work MUST block. raw={raw}"
            )
            assert pb.get("blocked") is True, f"Got: {pb}"
        else:
            assert "BLOCKED" in parsed.get("text", "").upper(), (
                f"Must be blocked. Got: {raw[:300]}"
            )

    def test_all_done_short_text_with_real_work_blocked(
        self, hook_plugin_env: HookEnv,
    ):
        _setup_real_pending_work(hook_plugin_env)

        _parsed, raw, stderr, rc = _invoke_text_complete(
            hook_plugin_env, "All done.",
        )
        assert rc == 0, stderr

        pb = _read_persist_block(hook_plugin_env)
        assert pb is not None, f"Short 'All done.' with real work MUST block. raw={raw}"
        assert pb.get("blocked") is True, f"Got: {pb}"

    def test_qa_response_with_real_work_blocked(
        self, hook_plugin_env: HookEnv,
    ):
        _setup_real_pending_work(hook_plugin_env)

        parsed, raw, stderr, rc = _invoke_text_complete(
            hook_plugin_env,
            "Here is a summary of what was completed in this session:\n"
            "- Fixed auth bug\n- Added feature\n"
            "Everything committed and merged.",
        )
        assert rc == 0, stderr

        if parsed is None:
            pb = _read_persist_block(hook_plugin_env)
            assert pb is not None, f"QA response with real work MUST block. raw={raw}"
            assert pb.get("blocked") is True
        else:
            assert "BLOCKED" in parsed.get("text", "").upper()

    def test_completion_smell_with_real_work_blocked(
        self, hook_plugin_env: HookEnv,
    ):
        _setup_real_pending_work(hook_plugin_env)

        parsed, raw, stderr, rc = _invoke_text_complete(
            hook_plugin_env,
            "The refactoring is done. All tests passing. Build is green. "
            "Ready to merge.",
        )
        assert rc == 0, stderr

        if parsed is None:
            pb = _read_persist_block(hook_plugin_env)
            assert pb is not None, f"Completion smell with real work MUST block. raw={raw}"
            assert pb.get("blocked") is True
        else:
            assert "BLOCKED" in parsed.get("text", "").upper()

    def test_text_with_evidence_and_no_status_summary_still_blocks(
        self, hook_plugin_env: HookEnv,
    ):
        """Text with structured evidence (commit hash, pass count) and NO status summary
        phrasing still blocks when hasRealPendingWork detects work."""
        _setup_real_pending_work(hook_plugin_env)

        parsed, raw, stderr, rc = _invoke_text_complete(
            hook_plugin_env,
            "commit abc1234f — 42 tests passed. CI GREEN. "
            "=== GATE: PASSED ===. Collection OK.",
        )
        assert rc == 0, stderr
        pb = _read_persist_block(hook_plugin_env)
        text = parsed.get("text", "") if parsed else ""
        assert "TEXT-ONLY RESPONSE BLOCKED" in text or (
            pb is not None and pb.get("blocked") is True
        ), f"Text with evidence must still block when work exists. raw={raw} pb={pb}"

    def test_plain_text_with_real_work_blocked(
        self, hook_plugin_env: HookEnv,
    ):
        """Any text-only response without evidence when real work exists must be blocked
        by the 'text-only-while-work-exists' path."""
        _setup_real_pending_work(hook_plugin_env)

        parsed, raw, stderr, rc = _invoke_text_complete(
            hook_plugin_env,
            "Here is a normal progress update. The build is compiling. "
            "I will check the results and continue.",
        )
        assert rc == 0, stderr

        if parsed is None:
            pb = _read_persist_block(hook_plugin_env)
            assert pb is not None, (
                f"Plain text with real work MUST block via text-only-while-work-exists. raw={raw}"
            )
            assert pb.get("blocked") is True
        else:
            assert "BLOCKED" in parsed.get("text", "").upper()

    def test_clean_state_allows_text_through(self, hook_plugin_env: HookEnv):
        """When NO real pending work exists, text passes through even with
        completion-adjacent words (as long as evidence is present or the
        completion test doesn't fire)."""
        _setup_clean_state(hook_plugin_env)

        _parsed, _raw, stderr, rc = _invoke_text_complete(
            hook_plugin_env,
            "commit abc1234f — 42 tests passed. All done. "
            "CI GREEN. === GATE: PASSED ===. Collection OK.",
        )
        assert rc == 0, stderr
        pb = _read_persist_block(hook_plugin_env)
        assert pb is None or pb.get("blocked") is not True, (
            f"Clean state must allow text through. persist_block={pb}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 3: tool.execute.before — persist block carry-forward and dispatch clear
# ═══════════════════════════════════════════════════════════════════════════════


class TestPersistBlockFlowWithRealState:
    """The full session flow: text.complete blocks → persist block written →
    tool.execute.before denies non-dispatch → dispatch clears."""

    def test_full_block_deny_clear_flow(self, hook_plugin_env: HookEnv):
        _setup_real_pending_work(hook_plugin_env)

        # Step 1: text.complete blocks a status summary
        _parsed, raw, stderr, rc = _invoke_text_complete(
            hook_plugin_env,
            "Here's the final session status. Everything is complete. Ready to merge.",
        )
        assert rc == 0, stderr
        pb = _read_persist_block(hook_plugin_env)
        assert pb is not None, f"Step 1 failed: persist block not written. raw={raw}"
        assert pb.get("blocked") is True, f"Step 1: {pb}"

        # Step 2: tool.execute.before denies a Write call
        parsed_tb, _raw_tb, stderr_tb, rc_tb = _invoke_tool_before(
            hook_plugin_env, "write",
        )
        assert rc_tb == 0, stderr_tb
        if parsed_tb is not None:
            assert parsed_tb.get("permissionDecision") == "deny", (
                f"Step 2 failed: write must be denied. Got: {parsed_tb}"
            )

        # Step 3: tool.execute.before allows a Task dispatch
        parsed_tb2, _raw_tb2, stderr_tb2, rc_tb2 = _invoke_tool_before(
            hook_plugin_env, "task",
        )
        assert rc_tb2 == 0, stderr_tb2
        if parsed_tb2 is not None:
            assert parsed_tb2.get("permissionDecision") != "deny", (
                f"Step 3 failed: dispatch must be allowed. Got: {parsed_tb2}"
            )

        # Step 4: persist block is cleared
        pb_after = _read_persist_block(hook_plugin_env)
        assert pb_after is None or pb_after.get("blocked") is not True, (
            f"Step 4 failed: persist block not cleared. Got: {pb_after}"
        )

        # Step 5: subsequent Write is now allowed
        parsed_tb3, _raw_tb3, stderr_tb3, rc_tb3 = _invoke_tool_before(
            hook_plugin_env, "write",
        )
        assert rc_tb3 == 0, stderr_tb3
        if parsed_tb3 is not None:
            assert parsed_tb3.get("permissionDecision") != "deny", (
                f"Step 5 failed: write after dispatch must be allowed. Got: {parsed_tb3}"
            )

    def test_persist_block_denies_edit_write_and_bash(self, hook_plugin_env: HookEnv):
        _setup_real_pending_work(hook_plugin_env)

        # Write persist block
        _invoke_text_complete(
            hook_plugin_env, "All done. Everything complete.",
        )
        pb = _read_persist_block(hook_plugin_env)
        assert pb is not None and pb.get("blocked") is True

        for blocked_tool in ("edit", "write", "bash"):
            parsed, _raw, stderr, rc = _invoke_tool_before(hook_plugin_env, blocked_tool)
            assert rc == 0, stderr
            if parsed is not None:
                assert parsed.get("permissionDecision") == "deny", (
                    f"Tool '{blocked_tool}' must be denied when persist block active. "
                    f"Got: {parsed}"
                )

    def test_persist_block_allows_task_agent_workflow(self, hook_plugin_env: HookEnv):
        _setup_real_pending_work(hook_plugin_env)

        _invoke_text_complete(
            hook_plugin_env, "All done. Everything complete.",
        )
        pb = _read_persist_block(hook_plugin_env)
        assert pb is not None and pb.get("blocked") is True

        for dispatch_tool in ("task", "agent", "workflow"):
            parsed, _raw, stderr, rc = _invoke_tool_before(
                hook_plugin_env, dispatch_tool,
            )
            assert rc == 0, stderr
            if parsed is not None:
                assert parsed.get("permissionDecision") != "deny", (
                    f"Dispatch tool '{dispatch_tool}' must be allowed even with "
                    f"persist block active. Got: {parsed}"
                )


# ═══════════════════════════════════════════════════════════════════════════════
# 4: End-to-end session simulation — multi-hook chain
# ═══════════════════════════════════════════════════════════════════════════════


class TestE2ESessionSimulation:
    """Simulates a real opencode session: system.transform → text.complete → tool.before."""

    def test_session_with_work_rejects_text_only_chain(
        self, hook_plugin_env: HookEnv,
    ):
        """Full chain: work exists → system.transform injects gate →
        text.complete blocks summary → tool.execute.before enforces."""
        _setup_real_pending_work(hook_plugin_env)

        # 1. system.transform: gate injected
        transformed, _stderr_sys, rc_sys = _invoke_system_transform(
            hook_plugin_env, "system prompt base",
        )
        assert rc_sys == 0
        assert "MANDATORY PRE-GENERATION GATE" in (transformed or "")
        assert "7 unchecked TASKS.md items" in (transformed or "")

        # 2. text.complete: summary blocked
        _parsed, raw_tc, _stderr_tc, rc_tc = _invoke_text_complete(
            hook_plugin_env,
            "Session summary: fixed auth bug, added feature, everything committed.",
        )
        assert rc_tc == 0
        pb = _read_persist_block(hook_plugin_env)
        assert pb is not None, f"Chain step 2 failed. raw={raw_tc}"
        assert pb.get("blocked") is True

        # 3. tool.execute.before: edit denied
        parsed_tb, _raw_tb, _stderr_tb, rc_tb = _invoke_tool_before(
            hook_plugin_env, "edit",
        )
        assert rc_tb == 0
        if parsed_tb:
            assert parsed_tb.get("permissionDecision") == "deny"

        # 4. Dispatch clears
        _invoke_tool_before(hook_plugin_env, "task")
        pb_after = _read_persist_block(hook_plugin_env)
        assert pb_after is None or pb_after.get("blocked") is not True

    def test_session_without_work_proceeds_normally(
        self, hook_plugin_env: HookEnv,
    ):
        """Full chain with NO work: everything passes through normally."""
        _setup_clean_state(hook_plugin_env)

        # 1. system.transform: no gate
        transformed, _stderr_sys, rc_sys = _invoke_system_transform(
            hook_plugin_env, "system prompt base",
        )
        assert rc_sys == 0
        assert "No pending work" in (transformed or "")

        # 2. text.complete: text passes through
        _parsed, _raw, _stderr_tc, rc_tc = _invoke_text_complete(
            hook_plugin_env,
            "commit abc1234f — 42 passed. CI GREEN. All done.",
        )
        assert rc_tc == 0
        pb = _read_persist_block(hook_plugin_env)
        assert pb is None or pb.get("blocked") is not True, (
            f"No work = no block. persist_block={pb}"
        )

        # 3. tool.execute.before: allowed
        parsed_tb, _raw_tb, _stderr_tb, rc_tb = _invoke_tool_before(
            hook_plugin_env, "edit",
        )
        assert rc_tb == 0
        if parsed_tb:
            assert parsed_tb.get("permissionDecision") != "deny"


# ═══════════════════════════════════════════════════════════════════════════════
# 5: Guardrails — subagent context, env disable, fail-open
# ═══════════════════════════════════════════════════════════════════════════════


class TestGuardrailsWithRealState:
    """Enforcement guardrails work correctly even with real filesystem state."""

    def test_subagent_context_bypasses_all_enforcement(
        self, hook_plugin_env: HookEnv,
    ):
        _setup_real_pending_work(hook_plugin_env)

        parsed, _raw, stderr, rc = _invoke_text_complete(
            hook_plugin_env,
            "All done. Everything complete.",
            OPENCODE_SUBAGENT="1",
        )
        assert rc == 0, stderr
        assert parsed is not None, "Subagent must return output object unchanged"
        assert "All done" in parsed.get("text", "")

    def test_env_disable_does_not_bypass_text_complete(
        self, hook_plugin_env: HookEnv,
    ):
        _setup_real_pending_work(hook_plugin_env)

        parsed, raw, stderr, rc = _invoke_text_complete(
            hook_plugin_env,
            "All done. Everything complete.",
            GLUDD_STOP_ENFORCE="0",
        )
        assert rc == 0, stderr
        if parsed is None:
            assert "BLOCKED" in raw.upper(), (
                f"GLUDD_STOP_ENFORCE=0 must not bypass text.complete. Raw: {raw[:300]!r}"
            )
        else:
            assert "BLOCKED" in parsed.get("text", "").upper(), (
                f"GLUDD_STOP_ENFORCE=0 must not bypass text.complete. Got: {parsed}"
            )

    def test_disengage_does_not_bypass_real_pending_work_block(
        self, hook_plugin_env: HookEnv,
    ):
        """Even when disengaged, text-only responses with real pending work must
        still be blocked. Disengage only skips heuristic checks (COMPLETION_SMELL,
        COMPLETION_WORDS)."""
        _setup_real_pending_work(hook_plugin_env)

        # Write disengage signal active (future)
        now_ms = int(_time.time() * 1000)
        block_counter_path = hook_plugin_env.state_path("GLUDD_BLOCK_COUNTER_FILE")
        block_counter_path.write_text(json.dumps({
            "consecutiveBlocks": 0,
            "totalBlocks": 0,
            "lastBlockTs": 0,
            "disengageUntil": now_ms + 300_000,
        }))

        parsed, raw, stderr, rc = _invoke_text_complete(
            hook_plugin_env,
            "Here is a status update. Continuing with remaining work. "
            "Everything looks good.",
        )
        assert rc == 0, stderr

        # Disengage should NOT prevent the text-only-while-work-exists block
        # or the status-summary block
        if parsed is None:
            pb = _read_persist_block(hook_plugin_env)
            assert pb is not None, (
                f"Disengage must NOT bypass real pending work block. "
                f"raw={raw}"
            )
            assert pb.get("blocked") is True, f"Got: {pb}"
        else:
            assert "BLOCKED" in parsed.get("text", "").upper(), (
                f"Disengage must NOT bypass. Response: {raw[:300]}"
            )
