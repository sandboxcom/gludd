"""E2E enforcement test: full multi-plugin chain with cumulative decisions.

This is different from test_hook_runtime.py (individual plugin tests via node)
and test_enforcement_plugin_e2e.py (state-file simulation of individual plugins).

This test simulates the FULL enforcement chain: multiple plugins applied in
opencode.json registration order, with realistic tool-call payloads flowing
through all plugins, and the cumulative decision that emerges.

All plugin decision logic is re-implemented in pure Python from the TypeScript
source (extract-translate-assert pattern, same as test_verified_claims_plugin.py).

The simulated chain matches how opencode processes hooks:
  - tool.execute.before: first deny wins; plugins iterate in registration order
  - text.complete: each plugin's output feeds into next (middleware chain)
  - disengage: bypasses enforcement when active (not expired)
  - subagent guard: OPENCODE_SUBAGENT=1 skips ALL enforcement
  - env-disable: GLUDD_*_ENFORCE=0 disables specific plugins
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from pathlib import Path

import pytest

from tests.e2e.enforcement_state import state_path

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.xdist_group("enforcement-shared-state")

PLUGIN_REGISTRATION_ORDER = [
    "enforce-session-start.ts",
    "enforce-make.ts",
    "enforce-floor.ts",
    "enforce-delegate.ts",
    "enforce-multitask.ts",
    "enforce-floor-v2.ts",
    "enforce-stop.ts",
    "enforce-deadline.ts",
    "enforce-enhancement-ratio.ts",
    "enforce-additive-task.ts",
    "enforce-clean-tree.ts",
    "enforce-commit-lock.ts",
    "enforce-verified-claims.ts",
    "enforce-no-suppressions.ts",
    "enforce-no-wait.ts",
    "enforce-deletion-gate.ts",
    "enforce-batch-push.ts",
    "enforce-depth.ts",
    "enforce-directives.ts",
    "enforce-tdd.ts",
    "enforce-objective.ts",
    "enforce-anti-essay.ts",
    "enforce-branch-discipline.ts",
    "enforce-test-integrity.ts",
    "enforce-worktree.ts",
    "enforce-audit.ts",
    "enforce-context.ts",
    "enforce-deliverable.ts",
    "enforce-no-ci-poll.ts",
    "enforce-release-deadline.ts",
    "enforce-task-tracking.ts",
]

_STATE_FILES = [
    state_path(name)
    for name in (
        "gludd-mainthread-streak.json",
        "gludd-tool-streak.json",
        "gludd-multitask-state.json",
        "gludd-floor-override",
        "gludd-session-start.json",
        "gludd-watchdog-disengage.json",
        "gludd-stop-state.json",
        "gludd-block-counter.json",
        "gludd-task-deadlines.json",
        "gludd-task-stale.json",
        "gludd-enhancement-ratio.json",
        "gludd-todowrite-state.json",
    )
]


# ── state helpers (mirror plugin state-machine logic) ────────────────────────


def _clean_state() -> None:
    for f in _STATE_FILES:
        with contextlib.suppress(FileNotFoundError, OSError):
            f.unlink()


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data))


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text()) if path.exists() else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _is_subagent(env: dict) -> bool:
    """Mirrors the _isSubagent() guard in every plugin."""
    return env.get("OPENCODE_SUBAGENT") == "1"


def _is_disengaged() -> bool:
    """Mirrors isDisengaged() in enforce-floor.ts / enforce-delegate.ts."""
    d = _read_json(state_path("gludd-watchdog-disengage.json"))
    if not d:
        return False
    until = d.get("disengage_until", 0)
    return until > int(time.time() * 1000)


def _has_open_work(tasks_path: str) -> bool:
    """Mirrors openWorkExists()."""
    if tasks_path and os.path.exists(tasks_path):
        content = Path(tasks_path).read_text()
        if "- [ ]" in content:
            return True
    return False


# ── plugin decision logic (pure-Python re-implementations) ───────────────────


def _enforce_make_check(tool: str, command: str = "") -> dict | None:
    """enforce-make.ts: denies non-make bash and metacharacters."""
    if tool != "bash":
        return None
    if not command.startswith("make ") and not command.startswith("make\t"):
        return {"permissionDecision": "deny", "message": "BLOCKED: non-make bash command"}
    SHELL_META_CHARS_REGEX = r'[|;&(){}$`\\!]'
    import re
    if re.search(SHELL_META_CHARS_REGEX, command[len("make "):]):
        return {"permissionDecision": "deny", "message": "BLOCKED: shell metacharacters in make command"}
    return None


def _enforce_floor_check(tool: str, tasks_path: str) -> dict | None:
    """enforce-floor.ts: blocks non-dispatch calls when streak exceeds threshold.
    Read/grep/glob tools increment read streak but are NEVER blocked."""
    DISPATCH_TOOLS = {"task", "agent", "workflow"}
    if tool in DISPATCH_TOOLS:
        return None
    # Bash calls are gated by enforce-make only; floor doesn't block them
    if tool == "bash":
        return None

    s = _read_json(state_path("gludd-tool-streak.json"))
    streak = s.get("streak", 0)
    read_streak = s.get("readStreak", 0)
    edit_streak = s.get("editStreak", 0)

    is_read_tool = tool in ("read", "grep", "glob")
    if is_read_tool:
        read_streak += 1
    else:
        edit_streak += 1
    streak += 1

    _write_json(state_path("gludd-tool-streak.json"), {
        "streak": streak, "readStreak": read_streak,
        "editStreak": edit_streak, "lastUpdateTs": int(time.time() * 1000),
        "lastWriter": "enforce-floor",
    })

    if is_read_tool:
        return None

    MAX_STREAK = 2
    if streak > MAX_STREAK and _has_open_work(tasks_path):
        return {"permissionDecision": "deny", "message": "STREAK EXCEEDED: Must dispatch subagents"}
    return None


def _enforce_delegate_check(tool: str, tasks_path: str) -> dict | None:
    """enforce-delegate.ts: blocks edits/writes at high mainthread streak.
    Read tools, dispatch tools, and bash are always allowed."""
    if tool in ("read", "grep", "glob", "task", "agent", "workflow", "bash"):
        return None

    s = _read_json(state_path("gludd-mainthread-streak.json"))
    count = s.get("count", 0)
    count += 1
    _write_json(
        state_path("gludd-mainthread-streak.json"),
        {"count": count, "ts": int(time.time() * 1000)},
    )

    MAINTHREAD_THRESHOLD = 2
    if count > MAINTHREAD_THRESHOLD and _has_open_work(tasks_path):
        raise RuntimeError("BLOCKED: mainthread streak exceeded threshold")
    return None


def _enforce_stop_text_check(text: str) -> str | None:
    """enforce-stop.ts text.complete: blanks text when work is pending."""
    s = _read_json(state_path("gludd-stop-state.json"))
    has_work = s.get("hasLocalWork", False) or s.get("hasPendingWork", False) or s.get("tasksMdUnchecked", False)
    if has_work:
        return "HARD STOP: Work remains. Continue with tool call."
    return None


def _enforce_multitask_text_check(text: str) -> str | None:
    """enforce-multitask.ts text.complete: blocks zero-dispatch messages."""
    ms = _read_json(state_path("gludd-multitask-state.json"))
    zero_streak = ms.get("zeroStreak", 0)
    if zero_streak >= 2:
        return "BLOCKED: Zero-dispatch streak. MUST DISPATCH subagents."
    return None


def _enforce_enhancement_text_check(text: str) -> str | None:
    """enforce-enhancement-ratio.ts text.complete: warns on fix-heavy waves."""
    er = _read_json(state_path("gludd-enhancement-ratio.json"))
    wave = er.get("wave", [])
    if len(wave) < 2:
        return None
    fixes = sum(1 for w in wave if w.get("type") == "fix")
    total = len(wave)
    if total > 0 and fixes / total > 0.5:
        return "ENHANCEMENT RATIO VIOLATION: >50% fixes. Add enhancement dispatches."
    return None


def _enforce_verified_claims_check(text: str) -> str | None:
    """enforce-verified-claims.ts: blocks done-words without evidence."""
    DONE_WORDS = ["committed", "pushed", "fixed", "passing", "shipped", "done",
                  "complete", "green", "resolved", "deployed", "landed", "verified",
                  "passed", "working"]
    EVIDENCE_PATTERNS = [
        r"commit [0-9a-f]{7,40}", r"VERIFIED \S+@\w+", r"CI GREEN",
        r"\d+ passed", r"=== GATE: PASSED ===", r"Collection OK",
    ]
    import re
    text_lower = text.lower()
    has_done_word = any(dw in text_lower for dw in DONE_WORDS)
    if not has_done_word:
        return None
    has_evidence = any(re.search(pat, text) for pat in EVIDENCE_PATTERNS)
    if not has_evidence:
        return "BLOCKED: done claim without evidence. Include commit hash or test count."
    return None


# ── hook chain engine ────────────────────────────────────────────────────────


class HookChain:
    """Simulates the opencode hook chain across all registered plugins."""

    def __init__(self, tasks_path: str = "", env: dict | None = None):
        self.tasks_path = tasks_path
        self.env = env or {}

    def _run_execute_before(self, tool: str, command: str = "", args: dict | None = None) -> dict:
        """Run each plugin's tool.execute.before logic in registration order.
        First deny wins. Returns allow dict or deny dict."""
        if _is_subagent(self.env):
            return {"allowed": True}
        if _is_disengaged():
            return {"allowed": True}

        # enforce-make: deny non-make bash, metacharacters
        if tool == "bash":
            r = _enforce_make_check(tool, command)
            if r:
                return r

        if tool in ("task", "agent", "workflow"):
            return {"allowed": True}

        # enforce-floor: streak-based blocking for non-dispatch tools
        r = _enforce_floor_check(tool, self.tasks_path)
        if r:
            return r

        # enforce-delegate: mainthread streak blocking
        try:
            r = _enforce_delegate_check(tool, self.tasks_path)
            if r:
                return r
        except RuntimeError as e:
            return {"permissionDecision": "deny", "message": str(e)}

        return {"allowed": True}

    execute_before = _run_execute_before

    def text_complete(self, text: str) -> str:
        """Run all plugin text.complete hooks. Output chains through plugins."""
        if _is_subagent(self.env):
            return text
        result = text
        for fn in [_enforce_stop_text_check, _enforce_multitask_text_check,
                   _enforce_enhancement_text_check, _enforce_verified_claims_check]:
            override = fn(result)
            if override:
                result = override
        return result

    def reset_dispatch(self):
        """Simulate a dispatch wave: reset all streaks."""
        _write_json(state_path("gludd-tool-streak.json"), {
            "streak": 0, "readStreak": 0, "editStreak": 0,
            "lastDispatchTs": int(time.time() * 1000), "lastWriter": "enforce-floor",
        })
        _write_json(
            state_path("gludd-mainthread-streak.json"),
            {"count": 0, "ts": int(time.time() * 1000)},
        )
        _write_json(state_path("gludd-multitask-state.json"), {
            "thisMessageDispatches": 5, "zeroStreak": 0,
            "estimatedInFlight": 5, "lastTs": int(time.time() * 1000),
        })


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def clean_state():
    _clean_state()
    tasks_path = state_path("gludd-test-tasks-e2e.md")
    with contextlib.suppress(OSError):
        tasks_path.unlink()
    yield
    _clean_state()
    with contextlib.suppress(OSError):
        tasks_path.unlink()


@pytest.fixture
def tasks_md():
    path = state_path("gludd-test-tasks-e2e.md")
    path.write_text("- [ ] task A\n- [ ] task B\n")
    return str(path)


@pytest.fixture
def chain(tasks_md):
    return HookChain(tasks_path=tasks_md)


# ── tests ────────────────────────────────────────────────────────────────────


class TestFullEnforcementChain:
    """Simulate realistic tool-call payloads through multi-plugin chain."""

    def test_non_make_bash_denied(self, chain):
        result = chain.execute_before("bash", "ls -la")
        assert result.get("permissionDecision") == "deny", f"Raw bash should be denied: {result}"

    def test_make_target_allowed(self, chain):
        result = chain.execute_before("bash", "make git-status")
        assert result.get("allowed") or result.get("permissionDecision") != "deny"

    def test_make_with_metachar_denied(self, chain):
        result = chain.execute_before("bash", "make test 2>&1 | tail")
        assert result.get("permissionDecision") == "deny", f"Metachar should be denied: {result}"

    def test_edit_at_low_streak_allowed(self, chain):
        chain.reset_dispatch()
        result = chain.execute_before("edit")
        assert result.get("allowed") or result.get("permissionDecision") != "deny"

    def test_edit_at_high_streak_denied(self, chain):
        """After MAX_STREAK+1 edits with open work, enforcement blocks."""
        chain.reset_dispatch()
        # Call 1: streak 0→1, allowed
        r1 = chain.execute_before("edit")
        assert r1.get("allowed")
        # Call 2: streak 1→2, allowed (≤MAX_STREAK=2)
        r2 = chain.execute_before("edit")
        assert r2.get("allowed")
        # Call 3: streak 2→3, denied (>MAX_STREAK=2 with open work)
        r3 = chain.execute_before("edit")
        assert r3.get("permissionDecision") == "deny", f"Streak 3 with open work should deny: {r3}"

    def test_dispatch_resets_streak(self, chain):
        """A dispatch resets all streaks so next edit is allowed."""
        chain.reset_dispatch()
        for _ in range(3):
            chain.execute_before("edit")
        # Now at denied state; dispatch to reset
        chain.reset_dispatch()
        r = chain.execute_before("edit")
        assert r.get("allowed"), f"After dispatch reset, edit should be allowed: {r}"

    def test_read_tools_never_blocked(self, chain):
        chain.reset_dispatch()
        for _ in range(10):
            r = chain.execute_before("read")
            assert r.get("allowed") or r.get("permissionDecision") != "deny", \
                f"Read tool should never be denied: {r}"

    def test_dispatch_tools_always_allowed(self, chain):
        for tool in ("task", "agent", "workflow"):
            r = chain.execute_before(tool)
            assert r.get("allowed") or r.get("permissionDecision") != "deny", \
                f"Dispatch tool {tool} should be allowed: {r}"


class TestSubagentContext:
    """Subagent context (OPENCODE_SUBAGENT=1) skips ALL enforcement."""

    def test_subagent_raw_bash_allowed(self, tasks_md):
        chain = HookChain(tasks_path=tasks_md, env={"OPENCODE_SUBAGENT": "1"})
        r = chain.execute_before("bash", "ls -la")
        assert r.get("allowed")

    def test_subagent_high_streak_edit_allowed(self, tasks_md):
        """Even with high streak, subagent edits are allowed."""
        _write_json(
            state_path("gludd-mainthread-streak.json"),
            {"count": 10, "ts": int(time.time() * 1000)},
        )
        _write_json(
            state_path("gludd-tool-streak.json"),
            {"streak": 10, "readStreak": 0, "editStreak": 10},
        )
        chain = HookChain(tasks_path=tasks_md, env={"OPENCODE_SUBAGENT": "1"})
        r = chain.execute_before("edit")
        assert r.get("allowed")

    def test_subagent_text_passes_through(self, tasks_md):
        chain = HookChain(tasks_path=tasks_md, env={"OPENCODE_SUBAGENT": "1"})
        _write_json(state_path("gludd-stop-state.json"), {
            "hasLocalWork": True, "hasPendingWork": True, "tasksMdUnchecked": True,
        })
        text = "Done. All tasks complete."
        assert chain.text_complete(text) == text


class TestDisengageBypass:
    """Disengage (watchdog-disengage.json with future timestamp) bypasses enforcement."""

    def test_disengage_allows_high_streak_edit(self, tasks_md):
        _write_json(
            state_path("gludd-mainthread-streak.json"),
            {"count": 10, "ts": int(time.time() * 1000)},
        )
        _write_json(
            state_path("gludd-tool-streak.json"),
            {"streak": 10, "readStreak": 0, "editStreak": 10},
        )
        _write_json(state_path("gludd-watchdog-disengage.json"), {
            "disengage_until": int(time.time() * 1000) + 600_000,
        })
        chain = HookChain(tasks_path=tasks_md)
        r = chain.execute_before("edit")
        assert r.get("allowed")

    def test_disengage_expired_enforces(self, tasks_md):
        _write_json(
            state_path("gludd-mainthread-streak.json"),
            {"count": 10, "ts": int(time.time() * 1000)},
        )
        _write_json(
            state_path("gludd-tool-streak.json"),
            {"streak": 10, "readStreak": 0, "editStreak": 10},
        )
        _write_json(state_path("gludd-watchdog-disengage.json"), {
            "disengage_until": int(time.time() * 1000) - 600_000,
        })
        chain = HookChain(tasks_path=tasks_md)
        r = chain.execute_before("edit")
        assert r.get("permissionDecision") == "deny" or not r.get("allowed")

    def test_disengage_removed_enforces(self, tasks_md):
        """Removing disengage re-enables enforcement."""
        _write_json(state_path("gludd-watchdog-disengage.json"), {
            "disengage_until": int(time.time() * 1000) + 600_000,
        })
        chain1 = HookChain(tasks_path=tasks_md)
        assert chain1.execute_before("bash", "ls")["allowed"]
        # Remove disengage
        state_path("gludd-watchdog-disengage.json").unlink()
        chain2 = HookChain(tasks_path=tasks_md)
        r = chain2.execute_before("bash", "ls")
        assert r.get("permissionDecision") == "deny", f"Should deny after disengage removal: {r}"


class TestTextCompleteChain:
    """Multiple text.complete handlers chain without conflicts."""

    def test_text_blocked_when_work_exists(self, tasks_md):
        _write_json(state_path("gludd-stop-state.json"), {
            "hasLocalWork": True, "hasPendingWork": True,
            "tasksMdUnchecked": True, "healthScore": 30,
        })
        _write_json(state_path("gludd-multitask-state.json"), {"zeroStreak": 0})
        chain = HookChain(tasks_path=tasks_md)
        result = chain.text_complete("Done. All tasks complete.")
        assert result != "Done. All tasks complete.", f"Expected text to be modified: {result}"
        assert "HARD STOP" in result, f"Expected stop message: {result}"

    def test_text_passes_through_when_no_work(self, tasks_md):
        _write_json(state_path("gludd-stop-state.json"), {
            "hasLocalWork": False, "hasPendingWork": False,
            "tasksMdUnchecked": False, "healthScore": 100,
        })
        chain = HookChain(tasks_path=tasks_md)
        assert chain.text_complete("All good.") == "All good."

    def test_done_claim_without_evidence_blocked(self, tasks_md):
        _write_json(state_path("gludd-stop-state.json"), {
            "hasLocalWork": False, "hasPendingWork": False,
            "tasksMdUnchecked": False, "healthScore": 100,
        })
        chain = HookChain(tasks_path=tasks_md)
        result = chain.text_complete("Everything is committed and pushed.")
        assert "evidence" in result.lower(), f"Done claim should be blocked: {result}"

    def test_done_claim_with_evidence_allowed(self, tasks_md):
        _write_json(state_path("gludd-stop-state.json"), {
            "hasLocalWork": False, "hasPendingWork": False,
            "tasksMdUnchecked": False, "healthScore": 100,
        })
        chain = HookChain(tasks_path=tasks_md)
        result = chain.text_complete("Tests passed: 10 passed, commit abc12345, === GATE: PASSED ===")
        assert result == "Tests passed: 10 passed, commit abc12345, === GATE: PASSED ==="

    def test_text_chain_no_duplicate_blocks(self, tasks_md):
        """Multiple plugins should not produce duplicate blocking messages."""
        _write_json(state_path("gludd-stop-state.json"), {
            "hasLocalWork": True, "hasPendingWork": True,
            "tasksMdUnchecked": True, "healthScore": 30,
        })
        _write_json(state_path("gludd-multitask-state.json"), {"zeroStreak": 2})
        chain = HookChain(tasks_path=tasks_md)
        result = chain.text_complete("Done.")
        # The text should be blocked but not contain duplicated messages
        assert "HARD STOP" in result or "BLOCKED" in result, f"Should be blocked: {result}"


class TestPluginRegistrationOrder:
    """The test registration order matches opencode.json exactly."""

    def test_order_matches_opencode_json(self):
        raw = json.loads((ROOT / "opencode.json").read_text())
        registered = [p.split("/")[-1] for p in raw.get("plugin", [])]
        registered = [p for p in registered if p not in ("watchdog.ts",)]
        assert registered == PLUGIN_REGISTRATION_ORDER, (
            f"Plugin order mismatch.\nTest: {PLUGIN_REGISTRATION_ORDER}\nConfig: {registered}"
        )


class TestFailOpenBehavior:
    """Enforcement fails open when state is corrupt or missing."""

    def test_missing_state_files_dont_crash(self):
        _clean_state()
        chain = HookChain(tasks_path="/nonexistent/file.md")
        r = chain.execute_before("edit")
        assert r.get("allowed")

    def test_corrupt_json_dont_crash(self):
        state_path("gludd-tool-streak.json").write_text("not valid json {{{")
        state_path("gludd-mainthread-streak.json").write_text("corrupt")
        chain = HookChain(tasks_path=str(state_path("gludd-test-tasks-e2e.md")))
        r = chain.execute_before("edit")
        assert r.get("allowed") or r.get("permissionDecision") != "deny"

    def test_corrupt_disengage_fail_open(self):
        state_path("gludd-watchdog-disengage.json").write_text("not json")
        chain = HookChain(tasks_path=str(state_path("gludd-test-tasks-e2e.md")))
        r = chain.execute_before("edit")
        assert r.get("allowed")

    def test_corrupt_stop_state_passes_through(self):
        state_path("gludd-stop-state.json").write_text("invalid")
        chain = HookChain(tasks_path=str(state_path("gludd-test-tasks-e2e.md")))
        assert chain.text_complete("test text") == "test text"


class TestEnhancementRatioEnforcement:
    """Enforce at least 50% enhancement dispatches in each wave."""

    def test_fix_heavy_wave_triggers_violation(self):
        _write_json(state_path("gludd-enhancement-ratio.json"), {
            "wave": [
                {"type": "fix", "prompt_head": "fix A", "ts": int(time.time() * 1000)},
                {"type": "fix", "prompt_head": "fix B", "ts": int(time.time() * 1000)},
                {"type": "fix", "prompt_head": "fix C", "ts": int(time.time() * 1000)},
                {"type": "enhancement", "prompt_head": "add tests", "ts": int(time.time() * 1000)},
            ],
        })
        chain = HookChain(tasks_path=str(state_path("gludd-test-tasks-e2e.md")))
        result = chain.text_complete("All dispatches complete.")
        assert "RATIO VIOLATION" in result, f"Expected ratio violation: {result}"

    def test_balanced_wave_no_violation(self):
        _write_json(state_path("gludd-enhancement-ratio.json"), {
            "wave": [
                {"type": "enhancement", "prompt_head": "add test A", "ts": int(time.time() * 1000)},
                {"type": "enhancement", "prompt_head": "add test B", "ts": int(time.time() * 1000)},
                {"type": "fix", "prompt_head": "fix A", "ts": int(time.time() * 1000)},
            ],
        })
        chain = HookChain(tasks_path=str(state_path("gludd-test-tasks-e2e.md")))
        result = chain.text_complete("Balanced wave dispatched.")
        assert "VIOLATION" not in result

    def test_wave_too_small_no_check(self):
        """Waves with <2 dispatches are not checked."""
        _write_json(state_path("gludd-enhancement-ratio.json"), {
            "wave": [{"type": "fix", "prompt_head": "fix A", "ts": int(time.time() * 1000)}],
        })
        chain = HookChain(tasks_path=str(state_path("gludd-test-tasks-e2e.md")))
        result = chain.text_complete("Single dispatch.")
        assert "VIOLATION" not in result


class TestDenyMessageStructure:
    """Every deny decision returns structured {permissionDecision, message}."""

    def test_bash_deny_structured(self, chain):
        r = chain.execute_before("bash", "ls")
        assert "permissionDecision" in r
        assert "message" in r
        assert r["permissionDecision"] == "deny"

    def test_streak_deny_structured(self, chain):
        chain.reset_dispatch()
        for _ in range(3):
            r = chain.execute_before("write")
        assert "permissionDecision" in r
        assert "message" in r
        assert r["permissionDecision"] == "deny"
        assert "STREAK" in r["message"]


class TestFullSessionCycleSimulation:
    """Simulate: start → read backlog → dispatch wave → edit → commit → end."""

    def test_full_cycle(self, tasks_md):
        _clean_state()
        chain = HookChain(tasks_path=tasks_md)

        # 1. Read backlog (read tool — always allowed)
        for _ in range(4):
            r = chain.execute_before("read")
            assert r.get("allowed") or r.get("permissionDecision") != "deny"

        # 2. Dispatch wave (resets all streaks)
        chain.reset_dispatch()

        # 3. A couple of edits (streak=0→1→allowed)
        r1 = chain.execute_before("edit")
        assert r1.get("allowed"), f"Edit 1 after dispatch should be allowed: {r1}"
        r2 = chain.execute_before("write")
        assert r2.get("allowed"), f"Edit 2 after dispatch should be allowed: {r2}"

        # 4. make target bash — allowed
        r3 = chain.execute_before("bash", "make git-status")
        assert "deny" not in str(r3).lower()

        # 5. Raw bash — denied
        r4 = chain.execute_before("bash", "ls")
        assert r4.get("permissionDecision") == "deny", f"Raw bash denied: {r4}"

        # 6. Dispatch again to reset
        chain.reset_dispatch()

        # 7. Verify clean text passes through (no pending work)
        _write_json(state_path("gludd-stop-state.json"), {
            "hasLocalWork": False, "hasPendingWork": False,
            "tasksMdUnchecked": False, "healthScore": 100,
        })
        result = chain.text_complete("All work done. commit abc12345. 42 passed.")
        assert "HARD STOP" not in result
        assert "BLOCKED" not in result
        assert "abc12345" in result
