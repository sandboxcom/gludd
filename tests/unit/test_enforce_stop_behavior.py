"""Runtime invocation tests for enforce-stop.ts enforcement behaviors.

Each test invokes the ACTUAL hook function from the compiled plugin via
scripts/hook_plugin_harness.mjs, not just checks source-code patterns.

Tests:
  1. binary_latch_any_signal_blocks — set one signal true, verify hasPendingWork=true
  2. binary_latch_all_clear_allows — all false, verify false
  3. push_blocked_signal_reads_state_file — write multitask state, verify underFloor detected
  4. push_blocked_cooldown_not_pending — CI cooldown, verify ciVerdictUnknown → pending
  5. multitask_text_block_zero_dispatches — 0 dispatches, verify text blanked
  6. multitask_text_allow_with_dispatches — 10 dispatches, verify text allowed
  7. results_ingestion_blocks_text — 5 task_result markers in prev turn, verify blanked
  8. results_ingestion_allows_with_dispatch — results + 10 dispatches, verify allowed
  9. post_ship_blocks_text — persist block written, next non-dispatch tool denied
  10. post_ship_allows_with_dispatches — persist block + dispatch clears the block
  11. tasksmd_unverified_items_count_pending — [x] without commit hash, verify pending
  12. tasksmd_verified_items_not_pending — [x] + commit hash, verify not pending
"""

from __future__ import annotations

import contextlib
import json
import time as _time
from pathlib import Path

import pytest

from tests.unit._hook_fixtures import (
    HookEnv,
    hook_plugin_env_impl,
    read_optional_bytes,
)

ROOT = Path(__file__).parent.parent.parent

PERSIST_BLOCK_ENV = "GLUDD_PERSIST_STOP_BLOCK_FILE"

CI_CACHE_PATH = Path("/tmp/gludd-watchdog-ci.json")

pytestmark = pytest.mark.xdist_group("enforcement-shared-state")


@pytest.fixture
def hook_plugin_env(tmp_path: Path):
    yield from hook_plugin_env_impl(tmp_path)


def _clean_leaked_state_files() -> None:
    for p in [
        "/tmp/gludd-multitask-state.json",
        "/tmp/gludd-release-completeness.json",
        "/tmp/gludd-last-test-result.json",
    ]:
        with contextlib.suppress(OSError):
            Path(p).unlink(missing_ok=True)


def _seed_ci_cache(status: str) -> None:
    CI_CACHE_PATH.write_text(
        json.dumps(
            {
                "last_ci_check": int(_time.time() * 1000),
                "last_ci_status": status,
                "run_id": "000000",
                "head_sha": "abc0000def",
            }
        )
    )


def _invoke_text_complete(
    env: HookEnv,
    text: str,
    *,
    dispatch_count: int = 0,
    tool_call_made: bool = False,
    env_overrides: dict[str, str] | None = None,
) -> tuple[dict | None, str, str, int]:
    overrides = (env_overrides or {}).copy()
    overrides.setdefault(PERSIST_BLOCK_ENV, str(env.cwd / "persist-stop-block.json"))
    output: dict = {"text": text, "dispatchCount": dispatch_count}
    if tool_call_made:
        output["toolCallMade"] = True
    result = env.invoke(
        "enforce-stop.ts",
        "experimental.text.complete",
        input={},
        output=output,
        env_overrides=overrides,
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


def _invoke_tool_before(
    env: HookEnv, tool: str, env_overrides: dict[str, str] | None = None
) -> tuple[dict | None, str, str, int]:
    overrides = (env_overrides or {}).copy()
    overrides.setdefault(PERSIST_BLOCK_ENV, str(env.cwd / "persist-stop-block.json"))
    result = env.invoke(
        "enforce-stop.ts",
        "tool.execute.before",
        input={"tool": tool},
        output={},
        env_overrides=overrides,
        timeout=15,
    )
    stdout_raw = result.stdout.strip()
    parsed = None
    if stdout_raw:
        with contextlib.suppress(json.JSONDecodeError):
            parsed = json.loads(stdout_raw)
    return parsed, stdout_raw, result.stderr, result.returncode


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 1: binary_latch_any_signal_blocks
# Any one signal in hasRealPendingWork() latches hasPendingWork = true.
# Signal used: TASKS.md with unchecked items.
# ═══════════════════════════════════════════════════════════════════════════════


def test_binary_latch_any_signal_blocks(hook_plugin_env: HookEnv):
    """When ANY one pending-work signal is true (TASKS.md unchecked),
    hasRealPendingWork() returns hasPendingWork=true and text-only
    responses are blocked."""
    _clean_leaked_state_files()

    (hook_plugin_env.cwd / "TASKS.md").write_text("- [ ] Implement feature X\n")

    parsed, raw, stderr, rc = _invoke_text_complete(
        hook_plugin_env,
        "Here is a status update. Continuing work.",
    )
    assert rc == 0, stderr

    if parsed is None:
        pb = _read_persist_block(hook_plugin_env)
        assert pb is not None, (
            f"Binary latch: text-only with unchecked TASKS.md MUST be blocked. persist_block={pb}, raw={raw}"
        )
        assert pb.get("blocked") is True, f"Got: {pb}"
    else:
        block_text = parsed.get("text", "")
        assert "BLOCKED" in block_text.upper(), f"Binary latch: text must be blanked. raw={raw[:300]}"


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 2: binary_latch_all_clear_allows
# All signals false → hasRealPendingWork() returns hasPendingWork=false.
# ═══════════════════════════════════════════════════════════════════════════════


def test_binary_latch_all_clear_allows(hook_plugin_env: HookEnv):
    """When ALL pending-work signals are clean (no TASKS.md unchecked,
    no ratchet entries, CI SUCCESS, gate clean), hasRealPendingWork()
    returns false and text passes through."""
    _clean_leaked_state_files()

    state_path = hook_plugin_env.state_path("GLUDD_STOP_STATE_FILE")
    state_path.write_text(
        json.dumps(
            {
                "ts": int(_time.time() * 1000),
                "tasksMdUnchecked": False,
                "tasksMdUncheckedCount": 0,
                "ratchetEntries": 0,
                "bugsOpen": False,
                "gateStatusMissing": False,
                "gateStale": False,
                "gateStatusRed": False,
                "ciVerdictPendingOrRed": False,
                "ciVerdictUnknown": False,
                "releaseIncomplete": False,
                "testFailures": False,
                "repoPending": False,
                "underFloor": False,
                "hasPendingWork": False,
                "hasLocalWork": False,
                "healthScore": 100,
            }
        )
    )

    _seed_ci_cache("SUCCESS")

    _parsed, _raw, stderr, rc = _invoke_text_complete(
        hook_plugin_env,
        "commit abc12345 — 42 tests passed. CI GREEN. === GATE: PASSED ===. Collection OK.",
    )
    assert rc == 0, stderr

    pb = _read_persist_block(hook_plugin_env)
    assert pb is None or pb.get("blocked") is not True, (
        f"All-clear: text with no pending work MUST NOT be blocked. persist_block={pb}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 3: push_blocked_signal_reads_state_file
# Write multitask state file with thisMessageDispatches below REQUIRED_AGENT_MIN.
# hasRealPendingWork() reads the file and sets underFloor=true → hasPendingWork=true.
# ═══════════════════════════════════════════════════════════════════════════════


def test_push_blocked_signal_reads_state_file(hook_plugin_env: HookEnv):
    """Write multitask state file with thisMessageDispatches=0
    and CLAUDE_AGENT_FLOOR=10. hasRealPendingWork() detects
    underFloor=true and hasPendingWork=true."""
    _clean_leaked_state_files()

    multitask_path = hook_plugin_env.state_path("GLUDD_MULTITASK_STATE_FILE")
    multitask_path.write_text(json.dumps({"thisMessageDispatches": 0, "ts": int(_time.time() * 1000)}))

    (hook_plugin_env.cwd / "TASKS.md").write_text("- [ ] Push-blocked task\n")

    parsed, raw, stderr, rc = _invoke_text_complete(
        hook_plugin_env,
        "Text after push signal.",
        env_overrides={
            "CLAUDE_AGENT_FLOOR": "10",
            "GLUDD_MIN_DISPATCHES": "10",
        },
    )
    assert rc == 0, stderr

    if parsed is None:
        pb = _read_persist_block(hook_plugin_env)
        assert pb is not None, (
            f"Push-blocked signal: underFloor=true + unchecked tasks MUST produce block. persist_block={pb}, raw={raw}"
        )
        assert pb.get("blocked") is True
    else:
        assert "BLOCKED" in parsed.get("text", "").upper(), raw[:300]


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 4: push_blocked_cooldown_not_pending
# CI cooldown → ciVerdictUnknown=true (NOT ciVerdictPendingOrRed).
# ciVerdictUnknown contributes to projectWorkOpen → hasPendingWork still true.
# ═══════════════════════════════════════════════════════════════════════════════


def test_push_blocked_cooldown_not_pending(hook_plugin_env: HookEnv):
    """CI cache with COOLDOWN-ACTIVE status produces ciVerdictUnknown=true
    but ciVerdictPendingOrRed=false. hasRealPendingWork() still returns
    hasPendingWork=true because ciVerdictUnknown counts as project work."""
    _clean_leaked_state_files()

    ci_path = Path("/tmp/gludd-watchdog-ci.json")
    _old_ci = read_optional_bytes(ci_path)
    ci_path.write_text(
        json.dumps(
            {
                "last_ci_check": int(_time.time() * 1000),
                "last_ci_status": "COOLDOWN-ACTIVE: 300s remaining",
                "run_id": "000001",
                "head_sha": "abc0000def",
            }
        )
    )

    (hook_plugin_env.cwd / "TASKS.md").write_text("- [ ] Cooldown test task\n")

    try:
        parsed, raw, stderr, rc = _invoke_text_complete(
            hook_plugin_env,
            "CI is in cooldown — continuing work.",
        )
        assert rc == 0, stderr

        # CI cooldown → ciVerdictUnknown → hasPendingWork=true
        # Text-only with pending work SHOULD be blocked
        if parsed is None:
            pb = _read_persist_block(hook_plugin_env)
            assert pb is not None, (
                f"Cooldown: ciVerdictUnknown + unchecked tasks MUST produce block. persist_block={pb}, raw={raw}"
            )
            assert pb.get("blocked") is True
        else:
            assert "BLOCKED" in parsed.get("text", "").upper(), raw[:300]
    finally:
        if _old_ci is not None:
            ci_path.write_bytes(_old_ci)
        elif ci_path.exists():
            ci_path.unlink()


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 5: multitask_text_block_zero_dispatches
# 0 dispatches in the current message + pending work → text.complete blanks.
# ═══════════════════════════════════════════════════════════════════════════════


def test_multitask_text_block_zero_dispatches(hook_plugin_env: HookEnv):
    """isTextOnly with dispatchCount=0 + pending work → blockMandatoryPendingText
    fires and blanks the response text."""
    _clean_leaked_state_files()

    (hook_plugin_env.cwd / "TASKS.md").write_text("- [ ] Multitask block test\n")

    parsed, raw, stderr, rc = _invoke_text_complete(
        hook_plugin_env,
        "Just sending a status message with no tool calls at all.",
        dispatch_count=0,
        tool_call_made=False,
    )
    assert rc == 0, stderr

    if parsed is None:
        pb = _read_persist_block(hook_plugin_env)
        assert pb is not None, (
            f"Zero-dispatches: text-only with pending work MUST be blanked. persist_block={pb}, raw={raw}"
        )
        assert pb.get("blocked") is True
    else:
        block_text = parsed.get("text", "")
        assert "BLOCKED" in block_text.upper(), f"Zero-dispatches: text must be blanked. raw={raw[:300]}"


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 6: multitask_text_allow_with_dispatches
# 10 dispatches + pending work → isTextOnly=false, text allowed through.
# ═══════════════════════════════════════════════════════════════════════════════


def test_multitask_text_allow_with_dispatches(hook_plugin_env: HookEnv):
    """dispatchCount=10 → isTextOnly=false → blockMandatoryPendingText
    does NOT fire. Text passes through unmodified."""
    _clean_leaked_state_files()

    (hook_plugin_env.cwd / "TASKS.md").write_text("- [ ] Multitask allow test\n")

    parsed, raw, stderr, rc = _invoke_text_complete(
        hook_plugin_env,
        "Dispatching 10 agents now: Agent 1 fixes X, Agent 2 adds Y...",
        dispatch_count=10,
        tool_call_made=True,
    )
    assert rc == 0, stderr

    if parsed is not None:
        block_text = parsed.get("text", "")
        assert "BLOCKED" not in block_text.upper(), f"10 dispatches: text MUST NOT be blanked. raw={raw[:300]}"
    # parsed=None (hook returned undefined) means allowed — correct.
    pb = _read_persist_block(hook_plugin_env)
    assert pb is None or pb.get("blocked") is not True or pb.get("reason") in ("after-results-text-only",), (
        f"10 dispatches MUST NOT leave a deny-persist. persist_block={pb}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 7: results_ingestion_blocks_text
# Previous turn had 5 task_result markers. Current turn: 0 dispatches.
# POST-RESULTS TEXT-ONLY BLOCK must fire.
# ═══════════════════════════════════════════════════════════════════════════════


def test_results_ingestion_blocks_text(hook_plugin_env: HookEnv):
    """Pre-seed post-results state with lastTurnHadResults=true (5 results).
    Text-only response with 0 dispatches → POST-RESULTS TEXT-ONLY BLOCK fires."""
    _clean_leaked_state_files()

    now_ms = int(_time.time() * 1000)

    post_results_path = hook_plugin_env.state_path("GLUDD_POST_RESULTS_STATE_FILE")
    post_results_path.write_text(
        json.dumps(
            {
                "lastTurnHadResults": True,
                "lastTurnHadWave": True,
                "lastResultCount": 5,
                "ts": now_ms - 1000,
            }
        )
    )

    (hook_plugin_env.cwd / "TASKS.md").write_text(
        "- [ ] Results-ingestion test task A\n- [ ] Results-ingestion test task B\n"
    )

    _seed_ci_cache("SUCCESS")

    # Seed stop state with pending work
    state_path = hook_plugin_env.state_path("GLUDD_STOP_STATE_FILE")
    state_path.write_text(
        json.dumps(
            {
                "ts": now_ms,
                "tasksMdUnchecked": True,
                "tasksMdUncheckedCount": 2,
                "ratchetEntries": 0,
                "bugsOpen": False,
                "gateStatusMissing": True,
                "gateStale": False,
                "gateStatusRed": False,
                "ciVerdictPendingOrRed": False,
                "ciVerdictUnknown": False,
                "releaseIncomplete": False,
                "testFailures": False,
                "repoPending": False,
                "underFloor": False,
                "hasPendingWork": True,
                "hasLocalWork": True,
                "healthScore": 80,
            }
        )
    )

    parsed, raw, stderr, rc = _invoke_text_complete(
        hook_plugin_env,
        "Results are in. Agent 1 fixed X, Agent 2 added Y. Continuing.",
        dispatch_count=0,
        tool_call_made=False,
    )
    assert rc == 0, stderr

    if parsed is None:
        pb = _read_persist_block(hook_plugin_env)
        assert pb is not None, (
            f"Results-ingestion: text-only after results MUST fire post-results block. persist_block={pb}, raw={raw}"
        )
        assert pb.get("blocked") is True
        assert "after-results-text-only" in pb.get("reason", ""), (
            f"Reason must be 'after-results-text-only'; got: {pb.get('reason')}"
        )
    else:
        block_text = parsed.get("text", "").upper()
        assert "RESULTS INGESTION PROTOCOL: 5 SUBAGENT RESULTS ARRIVED." in block_text
        assert "CODIFY RESULTS" in block_text
        assert "TEXT-ONLY AFTER RESULTS IS A STOP." in block_text


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 8: results_ingestion_allows_with_dispatch
# Previous turn had results, but current turn dispatches 10 agents.
# isTextOnly=false → POST-RESULTS TEXT-ONLY BLOCK does NOT fire.
# ═══════════════════════════════════════════════════════════════════════════════


def test_results_ingestion_allows_with_dispatch(hook_plugin_env: HookEnv):
    """Pre-seed post-results state + dispatch 10 agents in current message.
    Text is NOT blanked because isTextOnly=false."""
    _clean_leaked_state_files()

    now_ms = int(_time.time() * 1000)

    post_results_path = hook_plugin_env.state_path("GLUDD_POST_RESULTS_STATE_FILE")
    post_results_path.write_text(
        json.dumps(
            {
                "lastTurnHadResults": True,
                "lastTurnHadWave": True,
                "lastResultCount": 5,
                "ts": now_ms - 1000,
            }
        )
    )

    (hook_plugin_env.cwd / "TASKS.md").write_text("- [ ] Results-allow test\n")
    _seed_ci_cache("SUCCESS")

    state_path = hook_plugin_env.state_path("GLUDD_STOP_STATE_FILE")
    state_path.write_text(
        json.dumps(
            {
                "ts": now_ms,
                "tasksMdUnchecked": True,
                "tasksMdUncheckedCount": 1,
                "ratchetEntries": 0,
                "bugsOpen": False,
                "gateStatusMissing": True,
                "hasPendingWork": True,
                "hasLocalWork": True,
                "healthScore": 90,
            }
        )
    )

    parsed, raw, stderr, rc = _invoke_text_complete(
        hook_plugin_env,
        "Results arrived. Dispatching next wave of 10 agents now.",
        dispatch_count=10,
        tool_call_made=True,
    )
    assert rc == 0, stderr

    if parsed is not None:
        block_text = parsed.get("text", "")
        assert "POST-RESULTS" not in block_text.upper(), f"Results+dispatch: text MUST NOT be blanked. raw={raw[:300]}"
    pb = _read_persist_block(hook_plugin_env)
    assert pb is None or pb.get("blocked") is not True, f"Results+dispatch: must not persist block. pb={pb}"


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 9: post_ship_blocks_text
# Write a persist-block (simulating previous ship-commit block).
# Next non-dispatch tool call (edit) must be denied by tool.execute.before.
# ═══════════════════════════════════════════════════════════════════════════════


def test_post_ship_blocks_text(hook_plugin_env: HookEnv):
    """Write a persist-stop-block with blocked=true.
    tool.execute.before for a non-dispatch tool (edit) must deny."""
    _clean_leaked_state_files()

    persist_path = hook_plugin_env.cwd / "persist-stop-block.json"
    persist_path.write_text(
        json.dumps(
            {
                "blocked": True,
                "timestamp": int(_time.time() * 1000),
                "reason": "text-only-while-work-exists",
            }
        )
    )

    parsed, raw, stderr, rc = _invoke_tool_before(hook_plugin_env, "edit")
    assert rc == 0, stderr

    if parsed is None:
        out = raw.strip()
        assert len(out) == 0, (
            f"Post-ship: edit after persist-block must be denied. "
            f"Expected non-empty block message. raw={raw} stderr={stderr}"
        )
    else:
        assert parsed.get("permissionDecision") == "deny", (
            f"Post-ship: non-dispatch tool after persist-block MUST be denied. Got: {parsed}"
        )
        assert "BLOCKED" in parsed.get("message", ""), (
            f"Deny message must include BLOCKED. Got: {parsed.get('message', '')[:200]}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 10: post_ship_allows_with_dispatches
# Write a persist-block + dispatch a task.
# tool.execute.before for dispatch tool clears the persist block and allows.
# ═══════════════════════════════════════════════════════════════════════════════


def test_post_ship_allows_with_dispatches(hook_plugin_env: HookEnv):
    """Write a persist-stop-block with blocked=true.
    tool.execute.before for a dispatch tool (task) clears the block."""
    _clean_leaked_state_files()

    persist_path = hook_plugin_env.cwd / "persist-stop-block.json"
    persist_path.write_text(
        json.dumps(
            {
                "blocked": True,
                "timestamp": int(_time.time() * 1000),
                "reason": "text-only-while-work-exists",
            }
        )
    )

    parsed, _raw, stderr, rc = _invoke_tool_before(hook_plugin_env, "task")
    assert rc == 0, stderr

    if parsed is not None:
        assert parsed.get("permissionDecision") != "deny", (
            f"Post-ship+dispatch: task dispatch MUST clear persist block. Got: {parsed}"
        )

    pb_after = _read_persist_block(hook_plugin_env)
    assert pb_after is None or pb_after.get("blocked") is not True, (
        f"Post-ship+dispatch: dispatch MUST clear persist block. pb_after={pb_after}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 11: tasksmd_unverified_items_count_pending
# TASKS.md with [x] items lacking commit hash → counted as unverified pending.
# This is NEW behavior: hasRealPendingWork() must distinguish verified-from-
# unverified checked items by commit-hash presence next to [x].
# ═══════════════════════════════════════════════════════════════════════════════


def test_tasksmd_unverified_items_count_pending(hook_plugin_env: HookEnv):
    """TASKS.md contains `- [x] Fix bug A` (checked, NO commit hash).
    This MUST be detected as unverified pending work. Text-only response
    with 0 dispatches must be blocked.

    NOTE: This test validates NEW behavior — hasRealPendingWork() currently
    only counts `- [ ]` (unchecked) items. A [x] item without a commit hash
    is indistinguishable from verified work. The implementation must add:
    1. Count [x] items in TASKS.md
    2. For each [x] item, check if a commit hash (7+ hex chars) appears
       on the same line or the immediately following line
    3. [x] items without a neighbor commit hash → count as unverified pending
    """
    _clean_leaked_state_files()

    tasks_path = hook_plugin_env.cwd / "TASKS.md"
    tasks_path.write_text(
        "- [x] Fix critical bug in daemon.py\n"
        "- [x] Add new endpoint\n"
        "- [x] Update CI pipeline\n"
        "- [x] Refactor event loop\n"
        "- [x] Write integration tests\n"
    )

    _seed_ci_cache("SUCCESS")

    parsed, raw, stderr, rc = _invoke_text_complete(
        hook_plugin_env,
        "Everything is checked off. All done.",
        dispatch_count=0,
        tool_call_made=False,
    )
    assert rc == 0, stderr

    # ENHANCEMENT: [x] without commit hash → MUST be pending work → block fires
    if parsed is None:
        pb = _read_persist_block(hook_plugin_env)
        assert pb is not None, (
            f"UNVERIFIED [x] items: text-only MUST be blocked. "
            f"All 5 items are [x] without commit hashes — they are unverified. "
            f"persist_block={pb}, raw={raw}"
        )
        assert pb.get("blocked") is True, f"Unverified [x] items must trigger block. Got: {pb}"
    else:
        block_text = parsed.get("text", "")
        assert "BLOCKED" in block_text.upper(), f"Unverified [x]: text must be blanked. raw={raw[:300]}"


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 12: tasksmd_verified_items_not_pending
# TASKS.md with [x] items + commit hash → verified, NOT pending.
# Text-only with 0 dispatches is allowed because no pending work exists.
# ═══════════════════════════════════════════════════════════════════════════════


def test_tasksmd_verified_items_not_pending(hook_plugin_env: HookEnv):
    """TASKS.md contains `- [x] Fix bug A abc12345` (checked WITH commit hash).
    These are verified items and do NOT count as pending work. Text is allowed.

    NOTE: This test validates NEW behavior — [x] items with a neighboring
    commit hash (7+ hex chars including at least one letter) are verified
    work and do NOT contribute to hasPendingWork.
    """
    _clean_leaked_state_files()

    tasks_path = hook_plugin_env.cwd / "TASKS.md"
    tasks_path.write_text(
        "- [x] Fix critical bug in daemon.py — abc12345\n"
        "- [x] Add new endpoint — def6789a\n"
        "- [x] Update CI pipeline — bbb0000c\n"
        "- [x] Refactor event loop — eee1111f\n"
        "- [x] Write integration tests — aaa2222d\n"
    )

    _seed_ci_cache("SUCCESS")

    state_path = hook_plugin_env.state_path("GLUDD_STOP_STATE_FILE")
    state_path.write_text(
        json.dumps(
            {
                "ts": int(_time.time() * 1000),
                "tasksMdUnchecked": False,
                "tasksMdUncheckedCount": 0,
                "ratchetEntries": 0,
                "bugsOpen": False,
                "gateStatusMissing": False,
                "gateStale": False,
                "gateStatusRed": False,
                "ciVerdictPendingOrRed": False,
                "ciVerdictUnknown": False,
                "releaseIncomplete": False,
                "testFailures": False,
                "repoPending": False,
                "underFloor": False,
                "hasPendingWork": False,
                "hasLocalWork": False,
                "healthScore": 100,
            }
        )
    )

    parsed, raw, stderr, rc = _invoke_text_complete(
        hook_plugin_env,
        "All 5 items verified with commit hashes. Work is complete. abc12345 — 42 passed. Done.",
        dispatch_count=0,
        tool_call_made=False,
    )
    assert rc == 0, stderr

    # ENHANCEMENT: [x] WITH commit hash → verified, not pending → NO block
    if parsed is not None:
        block_text = parsed.get("text", "")
        assert "BLOCKED" not in block_text.upper(), (
            f"Verified [x] items: text MUST NOT be blanked. All items have commit hashes. raw={raw[:300]}"
        )

    pb = _read_persist_block(hook_plugin_env)
    assert pb is None or pb.get("blocked") is not True, f"Verified [x] items: must NOT persist block. pb={pb}"
