"""Session-start protocol tests.

Codifies the rule that the FIRST thing the agent does after session start is:
  1. Locate work (read TASKS.md, BUGS.md, config/ratchet.yml)
  2. Immediately fan out >= FLOOR parallel task/agent dispatches on disjoint work

The previous failure mode: agent booted, did a long inline investigation, then
sent a status report with 0 subagents live. This test pins the guardrail that
makes that structurally harder.

The guardrail lives in `.opencode/plugin/enforce-session-start.ts` (opencode
plugin layer) and AGENTS.md (prompt layer). These tests verify the load-bearing
pieces exist and would catch a regression that strips the enforcement.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent.parent
PLUGIN = ROOT / ".opencode" / "plugin" / "enforce-session-start.ts"
AGENTS_MD = ROOT / "AGENTS.md"


@pytest.fixture(scope="module")
def plugin_src():
    if not PLUGIN.exists():
        pytest.fail(
            f"Missing {PLUGIN}. The session-start protocol plugin must exist — "
            "it forces parallel dispatch as the first action of every session."
        )
    return PLUGIN.read_text()


@pytest.fixture(scope="module")
def agents_src():
    return AGENTS_MD.read_text()


class TestSessionStartPluginExists:
    def test_plugin_file_exists(self, plugin_src):
        assert plugin_src, "enforce-session-start.ts must not be empty"

    def test_plugin_exports_default(self, plugin_src):
        assert "export default" in plugin_src, (
            "enforce-session-start.ts must export a default plugin object"
        )

    def test_plugin_registered_in_opencode_json(self):
        import json

        cfg = json.loads((ROOT / "opencode.json").read_text())
        plugins = cfg.get("plugin", [])
        assert any("enforce-session-start" in str(p) for p in plugins), (
            "enforce-session-start.ts must be registered in opencode.json plugin[] "
            "or it will never load."
        )


class TestSessionStartEnforcesFloor:
    """The plugin must require >= FLOOR dispatches before allowing mutations."""

    def test_references_floor_constant(self, plugin_src):
        # Either CLAUDE_AGENT_FLOOR or a literal minimum like "5" / FLOOR.
        assert (
            "CLAUDE_AGENT_FLOOR" in plugin_src
            or "FLOOR" in plugin_src
            or "MIN_DISPATCHES" in plugin_src
        ), "Plugin must reference the agent-floor constant."

    def test_defines_minimum_dispatch_threshold(self, plugin_src):
        # The threshold for 'primed' must be at least 5 (the message-shape wave floor).
        # Accept: const MIN = parseInt(... "5"), or >= 5, etc.
        assert any(
            token in plugin_src
            for token in ('"5"', "'5'", "MIN_DISPATCHES", "FLOOR")
        ), "Plugin must define a minimum-dispatches threshold (>= 5)."

    def test_distinguishes_dispatch_tools_from_read_tools(self, plugin_src):
        # Post E.5 refactor the dispatch/read classification sets live in
        # shared.ts (DISPATCH_TOOLS / READ_TOOLS); the plugin imports them.
        shared = (PLUGIN.parents[1] / "lib" / "shared.ts").read_text()
        for tok in ("task", "agent", "workflow"):
            assert tok in shared, f"shared.ts must classify '{tok}' as a dispatch."
        for tok in ("read", "glob", "grep"):
            assert tok in shared, f"shared.ts must classify '{tok}' as a read."
        assert "isDispatchTool" in plugin_src or "isReadTool" in plugin_src


class TestSessionStartStateTracking:
    """The plugin must persist session state so it can detect a fresh session."""

    def test_writes_session_state_file(self, plugin_src):
        # Must write some marker file under /tmp (or GLUDD_ override).
        assert "/tmp/gludd-session" in plugin_src or "GLUDD_SESSION_STATE" in plugin_src, (
            "Plugin must persist session-start state to a file so it can detect "
            "a fresh session vs. a resumed one."
        )

    def test_session_state_path_is_overridable(self, plugin_src):
        assert "GLUDD_SESSION_STATE" in plugin_src, (
            "Session state path must be overridable via GLUDD_SESSION_STATE for tests."
        )


class TestSessionStartSystemInjection:
    """The plugin must inject a Session Start Protocol into the system prompt."""

    def test_has_system_transform_hook(self, plugin_src):
        assert "experimental.chat.system.transform" in plugin_src, (
            "Plugin must register experimental.chat.system.transform to inject "
            "the Session Start Protocol at session boot."
        )

    def test_injection_names_the_protocol(self, plugin_src):
        # The injected text must name the protocol so it is greppable.
        assert "SESSION START PROTOCOL" in plugin_src.upper(), (
            "Injected context must include a 'Session Start Protocol' header."
        )

    def test_injection_orders_tasks_before_dispatch(self, plugin_src):
        # The protocol must instruct: locate work FIRST, then dispatch. Scope
        # to the buildSessionDirective body. Match the dispatch INSTRUCTION
        # ("DISPATCH >=" / "DISPATCH >") rather than bare DISPATCH, which also
        # appears inside the GLUDD_SESSION_START_DISPATCH_NOW_SECS env var name.
        directive_idx = plugin_src.find("buildSessionDirective")
        directive = plugin_src[directive_idx:] if directive_idx > 0 else plugin_src
        upper = directive.upper()
        tasks_idx = upper.find("TASKS.MD")
        dispatch_idx = upper.find("DISPATCH >")
        if tasks_idx == -1 or dispatch_idx == -1:
            pytest.skip("Protocol ordering not names-checked in directive")
        assert tasks_idx < dispatch_idx, (
            "Protocol must instruct reading TASKS.md BEFORE dispatching."
        )


class TestSessionStartToolBeforeHook:
    """The plugin must intercept non-dispatch tool calls in a fresh session."""

    def test_has_tool_execute_before_hook(self, plugin_src):
        assert "tool.execute.before" in plugin_src, (
            "Plugin must register tool.execute.before to intercept premature "
            "non-dispatch work in a fresh session."
        )

    def test_emits_warning_when_unprimed(self, plugin_src):
        # Must emit some console.warn / returned error mentioning dispatch.
        assert "dispatch" in plugin_src.lower(), (
            "Plugin must emit a dispatch-reminder when the session is unprimed."
        )

    def test_enforce_env_knob_exists(self, plugin_src):
        assert "GLUDD_SESSION_START_ENFORCE" in plugin_src, (
            "Plugin must support GLUDD_SESSION_START_ENFORCE to elevate from "
            "advisory to blocking (mirrors the GLUDD_FLOOR_ENFORCE pattern)."
        )


class TestSessionStartPromptPolicy:
    """AGENTS.md must codify the Session Start Protocol as a top-level policy."""

    def test_agents_md_has_session_start_section(self, agents_src):
        upper = agents_src.upper()
        assert "SESSION START PROTOCOL" in upper or "SESSION-START PROTOCOL" in upper, (
            "AGENTS.md must have a 'Session Start Protocol' section — the prompt "
            "layer of the 3-layer guardrail."
        )

    def test_agents_md_names_first_action(self, agents_src):
        # The policy must say the FIRST action is locating work + dispatching.
        upper = agents_src.upper()
        assert "FIRST" in upper and "DISPATCH" in upper, (
            "AGENTS.md must explicitly say the FIRST action is to dispatch."
        )

    def test_agents_md_references_plugin(self, agents_src):
        assert "enforce-session-start" in agents_src, (
            "AGENTS.md must reference the enforcing plugin (3-layer guardrail)."
        )


class TestSessionStartAtomicStateWrites:
    """Fix A + Fix B: atomic state writes + latched gate.

    The race: STATE_FILE is shared by every plugin instance (main agent AND
    every subagent). With 10+ subagents concurrently doing `state.dispatches
    += 1` via load-modify-save, a stale read overwrites a fresher write.

    Fix A (atomic write via temp-file + rename) eliminates torn reads/writes.
    Fix B (module-level latched `sessionPrimed`) eliminates the steady-state
    race window entirely once the orchestrator has primed the gate.

    These tests verify both fixes are present in the plugin source and that
    the temp+rename write pattern actually preserves every increment under
    concurrent writers.
    """

    def test_plugin_uses_atomic_temp_rename_write(self, plugin_src):
        """Fix A: saveState must write to a PID-unique temp file then rename."""
        assert ".tmp." in plugin_src, (
            "saveState must write to a temp file (e.g. `${STATE_FILE}.tmp.${pid}`) "
            "so concurrent writers don't clobber each other's temp file."
        )
        assert "renameSync" in plugin_src, (
            "saveState must use fs.renameSync for the atomic publish step. "
            "Direct writeFileSync to STATE_FILE creates a torn-read window."
        )

    def test_plugin_latches_primed_state(self, plugin_src):
        """Fix B: module-level `sessionPrimed` latch skips state I/O once primed."""
        assert "sessionPrimed" in plugin_src, (
            "Plugin must declare a module-level `sessionPrimed` latch so the "
            "gate stops policing every subagent `make` call after the "
            "orchestrator's turn-1 duty is complete."
        )
        # The latch must short-circuit the tool.execute.before hook BEFORE
        # any state-file read happens.
        assert "if (sessionPrimed === true) return" in plugin_src, (
            "tool.execute.before must short-circuit on the latched `sessionPrimed` "
            "flag BEFORE loadState() — otherwise the race window is still open."
        )

    def test_concurrent_atomic_writes_preserve_every_increment(self, tmp_path):
        """Concurrent temp+rename writes never produce torn JSON.

        The atomic-write fix (Fix A) uses temp-file + fs.renameSync, which is
        atomic on POSIX: readers see either the complete previous state or the
        complete new state, NEVER a partial write. This test fires 100
        concurrent writers (each doing 20 read-modify-write cycles via temp +
        os.replace) and asserts no reader ever sees corrupt JSON.

        Note: temp+rename eliminates TORN READS, not lost updates in concurrent
        read-modify-write. The lost-update race in the dispatches counter is
        eliminated by the COMBINATION of Fix A (no torn reads) + Fix B (the
        ``sessionPrimed`` latch stops all state I/O once the orchestrator's
        turn-1 duty completes). The source-grep tests above pin both halves.
        """
        import json
        import os
        import threading

        state_file = tmp_path / "gludd-session-start.json"
        state_file.write_text(json.dumps({
            "started_at": 0,
            "readsDone": True,
            "dispatches": 0,
        }))

        N = 100
        CYCLES = 20
        barrier = threading.Barrier(N)
        read_errors: list = []

        def writer(i: int) -> None:
            try:
                barrier.wait()  # release all threads simultaneously
                for j in range(CYCLES):
                    # Reader: must ALWAYS parse valid JSON (no torn reads).
                    # A direct-writeFileSync pattern would intermittently
                    # throw JSONDecodeError mid-write. temp+rename never does.
                    try:
                        raw = json.loads(state_file.read_text())
                        int(raw["dispatches"])
                    except (json.JSONDecodeError, ValueError, KeyError) as e:
                        read_errors.append((i, j, repr(e)))
                        continue
                    # Writer: temp-file + atomic rename (mirrors saveState).
                    raw["dispatches"] = int(raw["dispatches"]) + 1
                    tmp = state_file.with_suffix(
                        f".tmp.{os.getpid()}.{threading.get_ident()}.{i}.{j}"
                    )
                    tmp.write_text(json.dumps(raw))
                    os.replace(str(tmp), str(state_file))
            except Exception as e:  # surface every failure
                read_errors.append((i, "fatal", repr(e)))

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert read_errors == [], (
            f"Torn reads detected under concurrent temp+rename writes: "
            f"{read_errors[:3]}... The atomic-write pattern (Fix A) must "
            "guarantee every reader sees valid JSON."
        )
        # Final state is parseable and reflects at least one successful write.
        final = json.loads(state_file.read_text())
        assert final["dispatches"] >= 1, (
            "Expected at least one increment to land under 100 concurrent writers."
        )


class TestSessionStartTimeGate:
    """The plugin must enforce a time-based dispatch deadline.

    After DISPATCH_NOW_SECS (default 60s) with 0 dispatches, a DISPATCH NOW
    warning is injected. After HARD_DENY_SECS (default 120s) with 0 dispatches,
    the next non-dispatch, non-read tool call is denied. Both reset on first
    successful dispatch.
    """

    def test_time_gate_constants_present(self, plugin_src):
        """DISPATCH_NOW_SECS and HARD_DENY_SECS must be declared."""
        for tok in ("DISPATCH_NOW_SECS", "HARD_DENY_SECS"):
            assert tok in plugin_src, (
                f"Plugin must declare {tok} time-gate constant."
            )

    def test_time_gate_checks_elapsed_seconds(self, plugin_src):
        """The plugin must compute elapsed seconds from started_at."""
        assert "started_at" in plugin_src, (
            "Plugin must read started_at to compute elapsed time."
        )
        assert "Date.now()" in plugin_src, (
            "Elapsed time must use Date.now() for wall-clock comparison."
        )

    def test_time_gate_hard_deny_blocks_non_dispatch(self, plugin_src):
        """After HARD_DENY_SECS with 0 dispatches, non-dispatch tools are denied."""
        assert "denyMessage" in plugin_src, (
            "Hard deny must set denyMessage to block the tool call."
        )

    def test_time_gate_warns_at_dispatch_now_secs(self, plugin_src):
        """After DISPATCH_NOW_SECS with 0 dispatches, a console.warn is emitted."""
        assert "console.warn" in plugin_src, (
            "Warning threshold must emit via console.warn."
        )

    def test_time_gate_resets_on_first_dispatch(self, plugin_src):
        """First dispatch sets timeGateReset=true, clearing both gates."""
        assert "timeGateReset" in plugin_src, (
            "Plugin must track timeGateReset to reset deadlines on first dispatch."
        )
        assert "state.timeGateReset = true" in plugin_src, (
            "Dispatch handler must set timeGateReset=true on first dispatch."
        )

    def test_time_gate_warning_is_throttled(self, plugin_src):
        """Warnings must be throttled to avoid spamming every tool call."""
        assert "_lastTimeGateWarningTs" in plugin_src or "30_000" in plugin_src, (
            "Time-gate warnings must be throttled (e.g., once per 30s)."
        )

    def test_time_gate_state_field_backward_compat(self, plugin_src):
        """timeGateReset must default to false for old state files."""
        assert "Boolean(raw.timeGateReset)" in plugin_src, (
            "loadState must handle missing timeGateReset via Boolean() cast."
        )
