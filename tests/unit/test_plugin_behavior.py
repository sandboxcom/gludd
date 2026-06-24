"""Behavioral tests for the opencode TypeScript plugins.

These tests verify key logic paths in the plugins by reading their source as
text (we cannot execute TypeScript from Python). Each test targets one
load-bearing piece of enforcement so a silent regression (deleted constant,
weakened check, missing function) is caught at gate time.

Covered:
  1. enforce-make.ts  — detectStopPattern() + COMPLETION_SOUNDING list
  2. enforce-delegate.ts — sonnet/total ratio math (projShare < target)
  3. enforce-stop.ts   — NO_WAIT_PATTERNS array size (>= 20)
  4. enforce-floor.ts  — FLOOR/TARGET/CEILING constants == 10/14/16
"""

import re
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
PLUGIN_DIR = ROOT / ".opencode" / "plugin"

ENFORCE_MAKE = PLUGIN_DIR / "enforce-make.ts"
ENFORCE_DELEGATE = PLUGIN_DIR / "enforce-delegate.ts"
ENFORCE_STOP = PLUGIN_DIR / "enforce-stop.ts"
ENFORCE_FLOOR = PLUGIN_DIR / "enforce-floor.ts"
ENFORCE_DEADLINE = PLUGIN_DIR / "enforce-deadline.ts"


# --------------------------------------------------------------------------- #
# 1. enforce-make.ts — stop-pattern detection
# --------------------------------------------------------------------------- #
class TestEnforceMakeStopPattern:
    """detectStopPattern must exist and consult COMPLETION_SOUNDING."""

    def test_detect_stop_pattern_function_exists(self):
        src = ENFORCE_MAKE.read_text()
        assert "function detectStopPattern" in src, (
            "detectStopPattern function missing from enforce-make.ts — "
            "the response.transform stop-pattern guardrail is gone"
        )

    def test_completion_sounding_array_exists(self):
        src = ENFORCE_MAKE.read_text()
        assert "COMPLETION_SOUNDING" in src, (
            "COMPLETION_SOUNDING constant missing — detectStopPattern has no "
            "vocabulary list to match against"
        )

    def test_detect_stop_pattern_uses_completion_sounding(self):
        """The function must actually consult the COMPLETION_SOUNDING list."""
        src = ENFORCE_MAKE.read_text()
        # Confirm the wiring: detectStopPattern references COMPLETION_SOUNDING
        # via .some(p => lower.includes(p)) — the load-bearing check.
        assert re.search(
            r"COMPLETION_SOUNDING\.some\s*\(\s*p\s*=>\s*lower\.includes\s*\(\s*p\s*\)\s*\)",
            src,
        ), (
            "detectStopPattern must call COMPLETION_SOUNDING.some(p => lower.includes(p)) "
            "— without it the function cannot detect completion-sounding responses"
        )

    def test_completion_sounding_has_meaningful_entries(self):
        """The list must contain real completion phrases, not be empty/stubbed."""
        src = ENFORCE_MAKE.read_text()
        # Extract the array body between `const COMPLETION_SOUNDING = [` and the
        # closing `]` (non-greedy across newlines).
        m = re.search(
            r"const\s+COMPLETION_SOUNDING\s*=\s*\[(.*?)\]",
            src,
            re.DOTALL,
        )
        assert m, "Could not locate COMPLETION_SOUNDING array body"
        entries = re.findall(r'"([^"]+)"', m.group(1))
        # Sanity: a handful of known load-bearing phrases must be present.
        required = {"all passed", "all done", "ready for review"}
        found = {e.lower() for e in entries}
        missing = required - found
        assert not missing, (
            f"COMPLETION_SOUNDING lost required entries: {missing}. "
            f"Found {len(entries)} entries: {sorted(found)[:10]}..."
        )
        assert len(entries) >= 10, (
            f"COMPLETION_SOUNDING has only {len(entries)} entries — expected "
            "a broad vocabulary list; was it truncated?"
        )

    def test_detect_stop_pattern_checks_permission_seeking(self):
        """Short completion-sounding text OR explicit permission-seeking flags."""
        src = ENFORCE_MAKE.read_text()
        # The function must check for "want me to" / "should i" / "shall i"
        # as explicit permission-seeking phrases.
        perm_phrases = ['"want me to"', '"should i"', '"shall i"']
        found = [p for p in perm_phrases if p in src]
        assert len(found) >= 2, (
            "detectStopPattern must check for permission-seeking phrases "
            f"(want me to / should i / shall i); only found {found}"
        )


# --------------------------------------------------------------------------- #
# 2. enforce-delegate.ts — model utilization ratio
# --------------------------------------------------------------------------- #
class TestEnforceDelegateModelRatio:
    """The sonnet:non-sonnet ratio enforcer must compute sonnet/total."""

    def test_ratio_calculation_uses_sonnet_over_total(self):
        """projShare = projSonnet / projected.length (sonnet count / total)."""
        src = ENFORCE_DELEGATE.read_text()
        # The load-bearing line: const projShare = projSonnet / projected.length
        assert re.search(
            r"projShare\s*=\s*projSonnet\s*/\s*projected\.length",
            src,
        ), (
            "Model utilization ratio must be computed as projSonnet / projected.length "
            "(sonnet count divided by total window). Missing or altered."
        )

    def test_ratio_compared_against_target_share(self):
        """Block fires when projShare < target (share below threshold)."""
        src = ENFORCE_DELEGATE.read_text()
        assert re.search(
            r"projShare\s*<\s*target\b",
            src,
        ), (
            "Ratio enforcer must compare projShare < target to decide whether "
            "a non-sonnet dispatch would drop the share below threshold"
        )

    def test_target_share_default_is_high(self):
        """Default target_share must be >= 0.67 (sonnet-dominant policy)."""
        src = ENFORCE_DELEGATE.read_text()
        # SONNET_TARGET_DEFAULT = 0.91 — sonnet must dominate.
        m = re.search(r"SONNET_TARGET_DEFAULT\s*=\s*([\d.]+)", src)
        assert m, "SONNET_TARGET_DEFAULT constant not found"
        default = float(m.group(1))
        assert default >= 0.67, (
            f"SONNET_TARGET_DEFAULT={default} is below 0.67 (2:1 sonnet ratio) — "
            "the sonnet-dominant dispatch policy has been weakened"
        )

    def test_sonnet_count_uses_filter(self):
        """projSonnet must be derived by filtering for 'sonnet' entries."""
        src = ENFORCE_DELEGATE.read_text()
        assert re.search(
            r"projSonnet\s*=\s*projected\.filter\s*\(\s*m\s*=>\s*m\s*===\s*[\"']sonnet[\"']\s*\)\.length",
            src,
        ), (
            "projSonnet must be computed as projected.filter(m => m === 'sonnet').length"
        )


# --------------------------------------------------------------------------- #
# 3. enforce-stop.ts — NO_WAIT_PATTERNS array
# --------------------------------------------------------------------------- #
class TestEnforceStopNoWaitPatterns:
    """The deferral-pattern list must be broad enough to catch real stops."""

    def test_no_wait_patterns_array_exists(self):
        src = ENFORCE_STOP.read_text()
        assert "NO_WAIT_PATTERNS" in src, (
            "NO_WAIT_PATTERNS constant missing from enforce-stop.ts — "
            "the no-wait stop guardrail has no pattern list"
        )

    def _extract_no_wait_patterns_body(self) -> str:
        r"""Extract the NO_WAIT_PATTERNS array body.

        Matches up to the closing `\n]` (bracket on its own line) because
        regex character classes inside the patterns (e.g. `[- ]`) contain
        literal `]` chars that would prematurely terminate a non-greedy `.*?\]`.
        """
        src = ENFORCE_STOP.read_text()
        m = re.search(
            r"NO_WAIT_PATTERNS\s*:\s*RegExp\[\]\s*=\s*\[(.*?)\n\]",
            src,
            re.DOTALL,
        )
        assert m, (
            "Could not extract NO_WAIT_PATTERNS array body — is the declaration "
            "(NO_WAIT_PATTERNS: RegExp[] = [...]) intact?"
        )
        return m.group(1)

    def test_no_wait_patterns_has_at_least_20_entries(self):
        """The array must contain >= 20 regex patterns (deferral + constraint-as-stopsign)."""
        body = self._extract_no_wait_patterns_body()
        # Strip comment lines (they contain `/` that would create spurious
        # regex-literal matches in the counting pass).
        body_no_comments = "\n".join(
            line for line in body.splitlines()
            if not line.strip().startswith("//")
        )
        # Each entry ends with /i (case-insensitive flag). Count them.
        count = len(re.findall(r"/[a-z]+\s*,?\s*$", body_no_comments, re.MULTILINE))
        # Fallback: also count by the /i, delimiter pattern (multiple per line).
        if count < 20:
            count = len(re.findall(r"/i\b", body_no_comments))
        assert count >= 20, (
            f"NO_WAIT_PATTERNS has only {count} entries — expected >= 20 "
            "(permission-seek + constraint-as-stopsign + status-report-as-handoff). "
            "The deferral vocabulary was truncated."
        )

    def test_no_wait_patterns_includes_deferral_phrases(self):
        """Must catch the canonical 'want me to' / 'should i' deferrals."""
        body = self._extract_no_wait_patterns_body()
        # Representative phrases that MUST be covered.
        required_substrings = ["want me to", "should i", "let me know", "your call"]
        missing = [s for s in required_substrings if s not in body]
        assert not missing, (
            f"NO_WAIT_PATTERNS missing deferral phrases: {missing}"
        )

    def test_no_wait_patterns_includes_constraint_phrases(self):
        """Must catch constraint-as-stopsign ('isn't possible', 'limitation')."""
        body = self._extract_no_wait_patterns_body()
        required = ["possible", "limitation", "wait"]
        missing = [s for s in required if s not in body]
        assert not missing, (
            f"NO_WAIT_PATTERNS missing constraint-as-stopsign phrases: {missing}"
        )

    def test_detect_no_wait_pattern_function_exists(self):
        src = ENFORCE_STOP.read_text()
        assert "function detectNoWaitPattern" in src, (
            "detectNoWaitPattern function missing — the pattern list is unused"
        )
        # Must apply the patterns via .some(p => p.test(text))
        assert re.search(
            r"NO_WAIT_PATTERNS\.some\s*\(\s*p\s*=>\s*p\.test\s*\(\s*text\s*\)\s*\)",
            src,
        ), "detectNoWaitPattern must call NO_WAIT_PATTERNS.some(p => p.test(text))"


# --------------------------------------------------------------------------- #
# 3b. enforce-stop.ts — CONSTRAINT_AS_STOP_PATTERNS (self-heal guardrail)
# --------------------------------------------------------------------------- #
# Incident (2026-06-23): agent responded "restart opencode one more time" to a
# recoverable state. The first-gen constraint phrases in NO_WAIT_PATTERNS did
# not catch "restart opencode", "can't without", "limitation of", "we must
# restart/wait/stop". A dedicated CONSTRAINT_AS_STOP_PATTERNS group was added
# with its own detector + directive injection. These tests pin its existence.
class TestEnforceStopConstraintPatterns:
    """The constraint-as-stop self-heal group must exist and stay populated."""

    def test_constraint_as_stop_patterns_array_exists(self):
        src = ENFORCE_STOP.read_text()
        assert "CONSTRAINT_AS_STOP_PATTERNS" in src, (
            "CONSTRAINT_AS_STOP_PATTERNS constant missing from enforce-stop.ts — "
            "the constraint-as-stop self-heal guardrail (2026-06-23 incident) "
            "has no pattern list"
        )

    def _extract_constraint_patterns_body(self) -> str:
        """Extract the CONSTRAINT_AS_STOP_PATTERNS array body."""
        src = ENFORCE_STOP.read_text()
        m = re.search(
            r"CONSTRAINT_AS_STOP_PATTERNS\s*:\s*RegExp\[\]\s*=\s*\[(.*?)\n\]",
            src,
            re.DOTALL,
        )
        assert m, (
            "Could not extract CONSTRAINT_AS_STOP_PATTERNS array body — is the "
            "declaration (CONSTRAINT_AS_STOP_PATTERNS: RegExp[] = [...]) intact?"
        )
        return m.group(1)

    def test_constraint_patterns_include_restart_opencode(self):
        """Must catch the incident phrase 'restart opencode'."""
        body = self._extract_constraint_patterns_body()
        assert "restart" in body and "opencode" in body, (
            "CONSTRAINT_AS_STOP_PATTERNS missing 'restart opencode' — the exact "
            "phrase from the 2026-06-23 incident is no longer detected"
        )

    def test_constraint_patterns_include_cannot_without(self):
        """Must catch 'can't/cannot/not possible' + 'without/unless'."""
        body = self._extract_constraint_patterns_body()
        assert "without" in body and "unless" in body, (
            "CONSTRAINT_AS_STOP_PATTERNS missing the 'can't without/unless' "
            "precondition phrasing"
        )

    def test_constraint_patterns_include_limitation_of(self):
        """Must catch '(limitation|constraint) of'."""
        body = self._extract_constraint_patterns_body()
        assert "limitation" in body and "constraint" in body, (
            "CONSTRAINT_AS_STOP_PATTERNS missing '(limitation|constraint) of'"
        )

    def test_constraint_patterns_include_we_must_restart_wait_stop(self):
        """Must catch 'we need/must (to) restart/wait/stop'."""
        body = self._extract_constraint_patterns_body()
        assert "need" in body and "must" in body, (
            "CONSTRAINT_AS_STOP_PATTERNS missing 'we (need|must)( to)? (restart|wait|stop)'"
        )

    def test_constraint_patterns_has_minimum_entries(self):
        """The group must have >= 7 entries (the incident-derived set)."""
        body = self._extract_constraint_patterns_body()
        body_no_comments = "\n".join(
            line for line in body.splitlines()
            if not line.strip().startswith("//")
        )
        # Each regex entry ends with /i, possibly followed by , and whitespace.
        count = len(re.findall(r"/i,?\s*$", body_no_comments, re.MULTILINE))
        if count < 7:
            # Fallback counter: count /i occurrences in the de-commented body.
            count = len(re.findall(r"/i\b", body_no_comments))
        assert count >= 7, (
            f"CONSTRAINT_AS_STOP_PATTERNS has only {count} entries — expected "
            ">= 7 (restart-opencode, can't-without, have-to-wait, "
            "limitation-of, no-way-to, isn't-possible, we-must-restart/wait/stop). "
            "The constraint vocabulary was truncated."
        )

    def test_detect_constraint_as_stop_function_exists(self):
        """The detector must exist and apply the patterns via .some()."""
        src = ENFORCE_STOP.read_text()
        assert "function detectConstraintAsStop" in src, (
            "detectConstraintAsStop function missing — CONSTRAINT_AS_STOP_PATTERNS "
            "is not wired to a detector"
        )
        assert re.search(
            r"CONSTRAINT_AS_STOP_PATTERNS\.some\s*\(\s*p\s*=>\s*p\.test\s*\(\s*text\s*\)\s*\)",
            src,
        ), (
            "detectConstraintAsStop must call "
            "CONSTRAINT_AS_STOP_PATTERNS.some(p => p.test(text))"
        )

    def test_constraint_block_response_function_exists(self):
        """The directive injector must exist and carry the workaround mandate."""
        src = ENFORCE_STOP.read_text()
        assert "function constraintBlockResponse" in src, (
            "constraintBlockResponse function missing — the constraint case has "
            "no distinct directive injection"
        )
        # Must carry the user-mandated directive language.
        assert "CONSTRAINT" in src and "DETECTED" in src, (
            "constraintBlockResponse must emit a 'CONSTRAINT ... DETECTED' header"
        )
        assert "workaround" in src, (
            "constraintBlockResponse must instruct engineering a workaround"
        )
        assert "research task" in src, (
            "constraintBlockResponse must offer dispatching a research task"
        )

    def test_constraint_detector_wired_into_response_transform(self):
        """The response.transform hook must invoke detectConstraintAsStop."""
        src = ENFORCE_STOP.read_text()
        assert re.search(
            r"detectConstraintAsStop\s*\(\s*output\s*\)",
            src,
        ), (
            "detectConstraintAsStop(output) is not called inside "
            "experimental.chat.response.transform — the constraint guardrail "
            "is dead code"
        )


# --------------------------------------------------------------------------- #
# 4. enforce-floor.ts — floor/target/ceiling constants
# --------------------------------------------------------------------------- #
class TestEnforceFloorConstants:
    """The three band constants must be 10/14/16 (user directive 2026-06-22)."""

    def test_floor_constant_is_10(self):
        src = ENFORCE_FLOOR.read_text()
        m = re.search(
            r"const\s+FLOOR\s*=\s*parseInt\s*\(\s*process\.env\.CLAUDE_AGENT_FLOOR\s*\|\|\s*[\"'](\d+)[\"']",
            src,
        )
        assert m, (
            "FLOOR constant declaration not found — expected "
            "parseInt(process.env.CLAUDE_AGENT_FLOOR || \"10\", 10)"
        )
        assert m.group(1) == "10", (
            f"FLOOR default is {m.group(1)}, expected 10 "
            "(user directive 2026-06-22 raised the floor from 6 to 10)"
        )

    def test_target_constant_is_14(self):
        src = ENFORCE_FLOOR.read_text()
        m = re.search(
            r"const\s+TARGET\s*=\s*parseInt\s*\(\s*process\.env\.CLAUDE_AGENT_TARGET\s*\|\|\s*[\"'](\d+)[\"']",
            src,
        )
        assert m, (
            "TARGET constant declaration not found — expected "
            "parseInt(process.env.CLAUDE_AGENT_TARGET || \"14\", 10)"
        )
        assert m.group(1) == "14", (
            f"TARGET default is {m.group(1)}, expected 14"
        )

    def test_ceiling_constant_is_16(self):
        src = ENFORCE_FLOOR.read_text()
        m = re.search(
            r"const\s+CEILING\s*=\s*parseInt\s*\(\s*process\.env\.CLAUDE_AGENT_CEILING\s*\|\|\s*[\"'](\d+)[\"']",
            src,
        )
        assert m, (
            "CEILING constant declaration not found — expected "
            "parseInt(process.env.CLAUDE_AGENT_CEILING || \"16\", 10)"
        )
        assert m.group(1) == "16", (
            f"CEILING default is {m.group(1)}, expected 16"
        )

    def test_all_three_constants_present(self):
        """FLOOR, TARGET, and CEILING must all be declared (bands intact)."""
        src = ENFORCE_FLOOR.read_text()
        for name in ("FLOOR", "TARGET", "CEILING"):
            assert re.search(rf"const\s+{name}\s*=", src), (
                f"{name} constant missing from enforce-floor.ts"
            )

    def test_floor_breaches_dispatch_upward(self):
        """Below floor must instruct dispatch toward TARGET (not just warn)."""
        src = ENFORCE_FLOOR.read_text()
        assert "active < FLOOR" in src or re.search(r"active\s*<\s*FLOOR", src), (
            "Floor-breach branch (active < FLOOR) missing — the guardrail "
            "cannot detect an under-staffed pool"
        )
        # The breach message must reference TARGET (dispatch UP to target).
        assert "TARGET" in src, (
            "Floor-breach response must reference TARGET to direct refilling"
        )

    def test_ceiling_breach_stops_dispatch(self):
        """Above ceiling must instruct the model to STOP adding agents."""
        src = ENFORCE_FLOOR.read_text()
        assert re.search(r"active\s*>\s*CEILING", src), (
            "Ceiling-breach branch (active > CEILING) missing — the guardrail "
            "cannot detect an over-staffed pool (disk/overload risk)"
        )


# --------------------------------------------------------------------------- #
# 5. enforce-deadline.ts — subagent task wall-clock timeout enforcement
# --------------------------------------------------------------------------- #
class TestEnforceDeadlinePlugin:
    """The 5-minute subagent task limit is now mechanically enforced."""

    def test_plugin_file_exists(self):
        assert ENFORCE_DEADLINE.exists(), (
            "enforce-deadline.ts missing — the wall-clock task timeout has no "
            "mechanical enforcement"
        )

    def test_references_timeout_env_var(self):
        src = ENFORCE_DEADLINE.read_text()
        assert "GLUDD_TASK_TIMEOUT_MS" in src, (
            "enforce-deadline.ts must reference GLUDD_TASK_TIMEOUT_MS env var"
        )

    def test_default_timeout_is_5_minutes(self):
        """Default timeout must be 300000 ms (5 min) per AGENTS.md."""
        src = ENFORCE_DEADLINE.read_text()
        m = re.search(
            r'GLUDD_TASK_TIMEOUT_MS\s*\|\|\s*["\'](\d+)["\']',
            src,
        )
        assert m, "GLUDD_TASK_TIMEOUT_MS default literal not found"
        assert m.group(1) == "300000", (
            f"Default timeout is {m.group(1)}ms, expected 300000 (5 min)"
        )

    def test_records_dispatch_timestamps(self):
        """On task dispatch the plugin must record Date.now() in state file."""
        src = ENFORCE_DEADLINE.read_text()
        assert re.search(r"\[id\]\s*=\s*Date\.now\(\)", src), (
            "Plugin must record dispatch timestamp via d[id] = Date.now()"
        )
        assert "GLUDD_TASK_DEADLINE_STATE" in src, (
            "State file path env var not referenced"
        )

    def test_warns_on_deadline_exceeded(self):
        """Over-time tasks must emit a TASK DEADLINE EXCEEDED console.warn."""
        src = ENFORCE_DEADLINE.read_text()
        assert "console.warn" in src, (
            "Plugin must emit console.warn to surface the breach"
        )
        assert "TASK DEADLINE EXCEEDED" in src, (
            "Warning must carry the load-bearing 'TASK DEADLINE EXCEEDED' header"
        )
        assert re.search(r"elapsed\s*>\s*TASK_TIMEOUT_MS", src), (
            "Deadline check must compare elapsed > TASK_TIMEOUT_MS"
        )

    def test_cleans_up_on_task_completion(self):
        """tool.execute.after must remove the task id from the state file."""
        src = ENFORCE_DEADLINE.read_text()
        assert "tool.execute.after" in src, (
            "Plugin must hook tool.execute.after to clean up completed tasks"
        )
        assert "delete d[id]" in src, (
            "tool.execute.after must delete the completed task from the state file"
        )

    def test_fail_open_wraps_enforcement(self):
        """Plugin must fail-open — an internal error never wedges the session."""
        src = ENFORCE_DEADLINE.read_text()
        assert re.search(r"catch\s*\{[^}]*fail open[^}]*\}", src), (
            "Plugin must wrap enforcement in try/catch with 'fail open' comment"
        )

    def test_dispatch_tools_covered(self):
        """Must hook task/agent/workflow dispatch tools."""
        src = ENFORCE_DEADLINE.read_text()
        for t in ("task", "agent", "workflow"):
            assert f'"{t}"' in src, f"Dispatch tool '{t}' not covered by deadline plugin"
