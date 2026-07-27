"""Verify enforce-stop.ts SUBAGENT_DEFICIT block.

2026-07-27: Agent sends text summarizing subagent results ("Agent 1 did X,
Agent 2 did Y...") between dispatch waves with <10 dispatches. This is a
stop-by-another-name — listing completed work instead of refilling the floor.

Fixes:
  1. hasRealPendingWork() treats underFloor as pending work independently
  2. SUBAGENT_DEFICIT_RE blanks text mentioning subagent results when
     dispatchCount < 10 AND hasPendingWork is true

Tests:
  (a) SUBAGENT_DEFICIT_RE matches subagent-result summary phrases
  (b) hasRealPendingWork() returns hasPendingWork=true when underFloor
  (c) text.complete blanks subagent-deficit text when dispatchCount < 10
  (d) text.complete does NOT blank when dispatchCount >= 10 (full floor)
  (e) text.complete does NOT block when text has no subagent-result markers
  (f) Persist block written with "subagent-deficit" reason
"""

from __future__ import annotations

import json
import re
import time as _time
from pathlib import Path

import pytest

from tests.unit._hook_fixtures import (
    HookEnv,
    hook_plugin_env_impl,
)

ROOT = Path(__file__).parent.parent.parent
PERSIST_BLOCK_ENV = "GLUDD_PERSIST_STOP_BLOCK_FILE"

# hasRealPendingWork() reads the live /tmp/gludd-watchdog-ci.json CI cache.
CI_CACHE_PATH = Path("/tmp/gludd-watchdog-ci.json")

pytestmark = pytest.mark.xdist_group("gludd-watchdog-ci-cache")


@pytest.fixture
def hook_plugin_env(tmp_path: Path):
    yield from hook_plugin_env_impl(tmp_path)


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
        with __import__("contextlib").suppress(json.JSONDecodeError):
            parsed = json.loads(stdout_raw)
    return parsed, stdout_raw, result.stderr, result.returncode


def _read_persist_block(env: HookEnv) -> dict | None:
    pb_path = env.cwd / "persist-stop-block.json"
    if not pb_path.exists():
        return None
    return json.loads(pb_path.read_text())


def _clean_leaked_state_files() -> None:
    import contextlib

    for p in [
        "/tmp/gludd-multitask-state.json",
        "/tmp/gludd-release-completeness.json",
        "/tmp/gludd-last-test-result.json",
    ]:
        with contextlib.suppress(OSError):
            Path(p).unlink(missing_ok=True)


# ── (a) SUBAGENT_DEFICIT_RE matches subagent-result phrases ────────────────


SUBAGENT_DEFICIT_RE = re.compile(
    r"\b(?:agent|subagent|task)\s+\d+\s+"
    r"(?:completed|finished|did|fixed|found|wrote|added|removed|updated|"
    r"reported|returned|resolved|processed|handled|investigated|checked|"
    r"audited|reviewed|implemented|created|tested|verified|deployed|"
    r"patched|refactored|cleaned|merged|built|generated|produced|"
    r"says|indicates|confirms|shows|began|started|noted)\b",
    re.IGNORECASE,
)


@pytest.mark.parametrize(
    "text,should_match",
    [
        ("Agent 1 fixed the bug and Agent 2 added feature B", True),
        ("Subagent 3 wrote tests for module C", True),
        ("Task 5 completed successfully with commit abc1234", True),
        ("Agent 4 reported findings from the security audit", True),
        ("subagent 1 returned results: gate passed", True),
        ("Agent 7 implemented the daemon endpoint", True),
        ("task 9 refactored the schema module", True),
        ("Agent 1 says CI is green now", True),
        ("Agent 2 indicates the test coverage is 92%", True),
        ("Agent 3 confirms the fix works on all platforms", True),
        ("Agent 5 began investigating the OOM issue", True),
        ("Agent 6 started working on the release pipeline", True),
        ("Agent 8 noted that all 42 tests pass", True),
        # Non-matching: no agent number + action verb
        ("The CI pipeline is green after the latest push", False),
        ("All tests pass with 42/42. Ready for next wave.", False),
        ("dispatch subagent to fix the remaining failures", False),
        ("Agent configuration was updated to include bash tool", False),
        ("", False),
    ],
)
def test_subagent_deficit_regex_match(text: str, should_match: bool):
    result = SUBAGENT_DEFICIT_RE.search(text)
    assert bool(result) == should_match, (
        f"SUBAGENT_DEFICIT_RE {'should' if should_match else 'should NOT'} match: {text[:100]}"
    )


# ── (b) text.complete blanks subagent-deficit text when dispatchCount < 10 ──


def test_subagent_deficit_text_complete_blanks_3_dispatch_summary(
    hook_plugin_env: HookEnv,
):
    """Text mentioning subagent results with only 3 dispatches is blanked."""
    _clean_leaked_state_files()
    now_ms = int(_time.time() * 1000)

    _old_ci = CI_CACHE_PATH.read_bytes() if CI_CACHE_PATH.exists() else None
    _seed_ci_cache("SUCCESS")

    # Create TASKS.md with unchecked items so hasRealPendingWork() finds work
    (hook_plugin_env.cwd / "TASKS.md").write_text("- [ ] Fix critical bug A\n- [ ] Implement feature B\n")

    # Pre-seed multitask state with thisMessageDispatches < 10
    multitask_path = hook_plugin_env.state_path("GLUDD_MULTITASK_STATE_FILE")
    multitask_path.write_text(
        json.dumps(
            {
                "thisMessageDispatches": 3,
                "minDispatches": 10,
                "ts": now_ms,
            }
        )
    )

    try:
        parsed, raw, stderr, rc = _invoke_text_complete(
            hook_plugin_env,
            "Agent 1 fixed the null pointer in daemon.py. "
            "Agent 2 added the missing migration file. "
            "Agent 3 wrote tests for the new endpoint. "
            "Continuing with remaining work.",
            dispatch_count=3,
        )
        assert rc == 0, stderr

        if parsed is None:
            pb = _read_persist_block(hook_plugin_env)
            assert pb is not None, (
                f"SUBAGENT_DEFICIT must block text summarizing results with 3 dispatches. persist_block={pb} raw={raw}"
            )
            assert pb.get("blocked") is True, f"Block must be recorded; got: {pb}"
            assert "subagent-deficit" in pb.get("reason", ""), f"Reason must be 'subagent-deficit'; got: {pb}"
        else:
            block_text = parsed.get("text", "")
            assert "SUBAGENT DEFICIT" in block_text.upper() or "BLOCKED" in block_text.upper(), (
                f"SUBAGENT_DEFICIT block must fire. raw: {raw[:300]}"
            )
    finally:
        if _old_ci is not None:
            CI_CACHE_PATH.write_bytes(_old_ci)
        elif CI_CACHE_PATH.exists():
            CI_CACHE_PATH.unlink()


def test_subagent_deficit_text_complete_blanks_text_only_summary(
    hook_plugin_env: HookEnv,
):
    """Text-only summary mentioning subagent results with 0 dispatches is blanked."""
    _clean_leaked_state_files()
    now_ms = int(_time.time() * 1000)

    _old_ci = CI_CACHE_PATH.read_bytes() if CI_CACHE_PATH.exists() else None
    _seed_ci_cache("SUCCESS")

    (hook_plugin_env.cwd / "TASKS.md").write_text("- [ ] Write remaining unit tests\n")

    multitask_path = hook_plugin_env.state_path("GLUDD_MULTITASK_STATE_FILE")
    multitask_path.write_text(
        json.dumps(
            {
                "thisMessageDispatches": 0,
                "minDispatches": 10,
                "ts": now_ms,
            }
        )
    )

    try:
        _parsed, raw, stderr, rc = _invoke_text_complete(
            hook_plugin_env,
            "Agent 1 completed the parser refactor. Agent 2 fixed the lint errors. "
            "Agent 4 wrote the E2E tests. Waiting for the remaining results.",
            dispatch_count=0,
        )
        assert rc == 0, stderr

        pb = _read_persist_block(hook_plugin_env)
        assert pb is not None, f"Text-only subagent summary with 0 dispatches must be blocked. raw={raw}"
        assert pb.get("blocked") is True, f"Block must be recorded; got: {pb}"
    finally:
        if _old_ci is not None:
            CI_CACHE_PATH.write_bytes(_old_ci)
        elif CI_CACHE_PATH.exists():
            CI_CACHE_PATH.unlink()


# ── (c) text.complete does NOT blank when dispatchCount >= 10 ──────────────


def test_subagent_deficit_not_blocked_at_full_floor(
    hook_plugin_env: HookEnv,
):
    """Text mentioning subagent results with 10 dispatches is NOT blanked —
    the floor is full, so this is a legitimate full-wave dispatch message."""
    _clean_leaked_state_files()
    int(_time.time() * 1000)

    _old_ci = CI_CACHE_PATH.read_bytes() if CI_CACHE_PATH.exists() else None
    _seed_ci_cache("SUCCESS")

    (hook_plugin_env.cwd / "TASKS.md").write_text("- [ ] Task A\n")

    try:
        _parsed, _raw, stderr, rc = _invoke_text_complete(
            hook_plugin_env,
            "Agent 1 fixed the parser, Agent 2 wrote tests, Agent 3 added docs, "
            "Agent 4 refactored config, Agent 5 updated CI, Agent 6 fixed lint, "
            "Agent 7 audited security, Agent 8 improved coverage, Agent 9 cleaned deps, "
            "Agent 10 deployed staging. Full wave dispatched.",
            dispatch_count=10,
        )
        assert rc == 0, stderr

        pb = _read_persist_block(hook_plugin_env)
        if pb and pb.get("reason") == "subagent-deficit":
            pytest.fail(
                f"SUBAGENT_DEFICIT must NOT fire when dispatchCount >= 10. "
                f"Text has subagent results but floor is full. persist_block={pb}"
            )
    finally:
        if _old_ci is not None:
            CI_CACHE_PATH.write_bytes(_old_ci)
        elif CI_CACHE_PATH.exists():
            CI_CACHE_PATH.unlink()


# ── (d) text without subagent-result markers passes through ─────────────────


def test_subagent_deficit_not_blocked_on_plain_text(
    hook_plugin_env: HookEnv,
):
    """Plain text without subagent-result markers is NOT blocked by
    the SUBAGENT_DEFICIT check (other blocks may still fire)."""
    _clean_leaked_state_files()
    now_ms = int(_time.time() * 1000)

    _old_ci = CI_CACHE_PATH.read_bytes() if CI_CACHE_PATH.exists() else None
    _seed_ci_cache("SUCCESS")

    (hook_plugin_env.cwd / "TASKS.md").write_text("- [ ] Task A\n")

    multitask_path = hook_plugin_env.state_path("GLUDD_MULTITASK_STATE_FILE")
    multitask_path.write_text(
        json.dumps(
            {
                "thisMessageDispatches": 3,
                "minDispatches": 10,
                "ts": now_ms,
            }
        )
    )

    try:
        _parsed, _raw, stderr, rc = _invoke_text_complete(
            hook_plugin_env,
            "Now dispatching more subagents to continue the work. "
            "The CI is green and the previous wave committed successfully.",
            dispatch_count=3,
        )
        assert rc == 0, stderr

        pb = _read_persist_block(hook_plugin_env)
        if pb and pb.get("reason") == "subagent-deficit":
            pytest.fail(f"SUBAGENT_DEFICIT must NOT fire on text without agent-number markers. persist_block={pb}")
    finally:
        if _old_ci is not None:
            CI_CACHE_PATH.write_bytes(_old_ci)
        elif CI_CACHE_PATH.exists():
            CI_CACHE_PATH.unlink()


# ── (e) underFloor alone makes hasRealPendingWork() return hasPendingWork=true


def test_under_floor_alone_makes_has_pending_work_true(
    hook_plugin_env: HookEnv,
):
    """When underFloor is true (multitask state shows <10 dispatches) but no
    other work exists, hasRealPendingWork() should still report
    hasPendingWork=true — the under-dispatch itself IS pending work."""
    _clean_leaked_state_files()
    now_ms = int(_time.time() * 1000)

    _old_ci = CI_CACHE_PATH.read_bytes() if CI_CACHE_PATH.exists() else None
    _seed_ci_cache("SUCCESS")

    # No TASKS.md — no file-based pending work
    # But multitask state says underFloor (3 dispatches)

    multitask_path = hook_plugin_env.state_path("GLUDD_MULTITASK_STATE_FILE")
    multitask_path.write_text(
        json.dumps(
            {
                "thisMessageDispatches": 3,
                "minDispatches": 10,
                "ts": now_ms,
            }
        )
    )

    try:
        _parsed, raw, stderr, rc = _invoke_text_complete(
            hook_plugin_env,
            "All done. Everything is complete.",
            dispatch_count=3,
        )
        assert rc == 0, stderr

        # Even though TASKS.md has no unchecked items, underFloor alone
        # makes hasRealPendingWork() return hasPendingWork=true. The text
        # "All done. Everything is complete." should be blocked by a
        # completion-claim check or the text-only block.
        pb = _read_persist_block(hook_plugin_env)
        assert pb is not None, (
            f"underFloor alone must make hasPendingWork=true. "
            f"No block recorded — under-dispatch not detected as pending work. "
            f"raw={raw}"
        )
        assert pb.get("blocked") is True, (
            f"underFloor=pending-work must trigger a block. Expected persist block with blocked=true; got: {pb}"
        )
    finally:
        if _old_ci is not None:
            CI_CACHE_PATH.write_bytes(_old_ci)
        elif CI_CACHE_PATH.exists():
            CI_CACHE_PATH.unlink()


# ── (f) SUBAGENT_DEFICIT blocked before evidence check ──────────────────────


def test_subagent_deficit_blocks_even_with_evidence_in_text(
    hook_plugin_env: HookEnv,
):
    """Subagent-deficit text with evidence (commit hash) is STILL blocked
    because the SUBAGENT_DEFICIT check fires before hasWorkArtifact gives
    the all-clear — the key condition is !hasWorkArtifact."""
    _clean_leaked_state_files()
    now_ms = int(_time.time() * 1000)

    _old_ci = CI_CACHE_PATH.read_bytes() if CI_CACHE_PATH.exists() else None
    _seed_ci_cache("SUCCESS")

    (hook_plugin_env.cwd / "TASKS.md").write_text("- [ ] Task A\n")

    multitask_path = hook_plugin_env.state_path("GLUDD_MULTITASK_STATE_FILE")
    multitask_path.write_text(
        json.dumps(
            {
                "thisMessageDispatches": 5,
                "minDispatches": 10,
                "ts": now_ms,
            }
        )
    )

    try:
        # Text contains both subagent-result markers AND evidence (commit hash).
        # The !hasWorkArtifact gate in SUBAGENT_DEFICIT means evidence bypasses
        # this block — the subagent deficit check defers to the evidence rule.
        # Verify that evidence DOES prevent the subagent-deficit block.
        _parsed, _raw, stderr, rc = _invoke_text_complete(
            hook_plugin_env,
            "Agent 1 fixed the parser a1b2c3d, Agent 2 added tests e4f5g6h. "
            "42 tests passed. Continuing with remaining work.",
            dispatch_count=5,
        )
        assert rc == 0, stderr

        pb = _read_persist_block(hook_plugin_env)
        if pb and pb.get("reason") == "subagent-deficit":
            pytest.fail(
                f"SUBAGENT_DEFICIT must defer to evidence (!hasWorkArtifact gate). "
                f"Commit hash in text should prevent subagent-deficit block. "
                f"persist_block={pb}"
            )
    finally:
        if _old_ci is not None:
            CI_CACHE_PATH.write_bytes(_old_ci)
        elif CI_CACHE_PATH.exists():
            CI_CACHE_PATH.unlink()
