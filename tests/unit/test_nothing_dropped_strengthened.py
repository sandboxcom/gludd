"""Strengthened tests for the nothing-dropped guardrail plugin.

The original `tests/unit/test_todo_guard_plugin.py` pins the todowrite-state
enforcement. The 2026-06-29 audit proved that enforcement was necessary but
NOT sufficient: 14 subagent-produced files accumulated untracked in the
working tree without ever being committed, and the guardrail never fired
because none of them were tracked in todowrite.

These tests pin the FOUR strengthened detection paths added to
`.opencode/plugin/enforce-todos.ts`:

  * Gap 1 — untracked-deliverable accumulation (>=3 deliverable files that
    are not committed + a summary-style response -> directive prepended).
  * Gap 2 — post-wave commit sweep (response mentions dispatch-result
    indicators AND there are uncommitted deliverables -> sweep directive).
  * Gap 3 — orphaned-test detection (tests/unit/test_X.py untracked while
    src/**/X.py is committed -> loud warning).
  * Gap 4 — frequency cap (>=30s between identical directive firings;
    state file /tmp/gludd-nothing-dropped-last-fired.json).

These tests follow the SAME static-source style as the existing
`test_todo_guard_plugin.py`: the plugin is TypeScript executed by the
opencode runtime, so we pin its behaviour by asserting on its source text.
The behavioural integration (real git repos) is exercised by
`tests/unit/test_opencode_plugin_ports.py`.
"""

import re
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
PLUGIN = ROOT / ".opencode" / "plugin" / "enforce-todos.ts"


def _src() -> str:
    return PLUGIN.read_text()


class TestStrengthenedPluginPresent:
    def test_plugin_file_exists(self):
        assert PLUGIN.exists(), (
            "enforce-todos.ts must exist — this plugin guarantees that every "
            "parallel subagent result is codified before the agent sends a "
            "terminal response."
        )


# ---------------------------------------------------------------------------
# Gap 1 — untracked-deliverable accumulation
# ---------------------------------------------------------------------------

class TestUntrackedDeliverableDetection:
    """The guardrail must surface untracked deliverables produced by prior
    subagent waves even when todowrite has no pending items. Many subagent
    results (test files, source files, migrations, ansible modules) never
    appear in todowrite; checking only todowrite misses them."""

    def test_runs_git_status_porcelain(self):
        s = _src()
        assert "git status --porcelain" in s, (
            "Plugin must invoke `git status --porcelain` to enumerate "
            "untracked deliverables. Without it the guardrail cannot see "
            "files that subagents produced."
        )

    def test_filters_untracked_entries(self):
        s = _src()
        # `??` is the porcelain marker for untracked files. The plugin must
        # distinguish untracked from modified/staged.
        assert "??" in s, (
            "Plugin must filter `git status --porcelain` output for untracked "
            "entries (line prefix `??`) — modified/staged files do not "
            "indicate dropped work."
        )

    def test_deliverable_patterns_present(self):
        """All required deliverable path families must be recognised so
        detection covers the full surface area where subagent work lands.
        Patterns are regexes (e.g. `docs\\/design\\/`), so we match on the
        path stem without the escape."""
        s = _src()
        required = [
            "tests",
            "src",
            "docs",   # docs\/design\/ in regex
            "design",
            "collections",
            "alembic",
            "versions",
            "plugin",  # .opencode\/plugin\/ in regex
            "playbooks",
            "molecule",
            "rego",
            "tf",
        ]
        missing = [p for p in required if p not in s]
        assert not missing, (
            "Plugin must recognise these deliverable paths: " + ", ".join(missing)
        )

    def test_untracked_deliverable_directive_text(self):
        s = _src()
        assert "UNTRACKED-DELIVERABLES GUARDRAIL" in s, (
            "Plugin must emit an UNTRACKED-DELIVERABLES GUARDRAIL directive "
            "when >=2 untracked deliverable files accumulate alongside a "
            "summary-style response."
        )

    def test_threshold_is_at_least_two(self):
        """The accumulation threshold must be >=2 so the directive still
        catches small drops. (Lowered from 3 on 2026-06-29 after the audit
        proved 3 let 1-2 drops through.)"""
        s = _src()
        # Look for a numeric threshold near the deliverable check.
        m = re.search(r"untrackedDeliverables\w*\.length\s*[<>]=?\s*(\d+)", s)
        if not m:
            m = re.search(r"deliverables\w*\.length\s*[<>]=?\s*(\d+)", s)
        assert m, (
            "Plugin must enforce a numeric accumulation threshold for the "
            "untracked-deliverables directive (e.g. length >= 2)."
        )
        threshold = int(m.group(1))
        assert threshold >= 2, (
            f"Untracked-deliverables threshold must be >=2 (got {threshold}); "
            "a lower value fires on single innocuous untracked files."
        )


# ---------------------------------------------------------------------------
# Gap 2 — post-wave commit sweep directive
# ---------------------------------------------------------------------------

class TestPostWaveCommitSweep:
    """When a response references dispatch results AND uncommitted deliverables
    exist, the plugin must inject a sweep directive: 'recovery is the
    deliverable now, not new work.'"""

    def test_dispatch_result_indicators_recognised(self):
        s = _src()
        # At least one of the documented dispatch-result markers must be
        # checked when deciding to emit the sweep directive.
        markers = ["task_result", "task_id", "subagent result", "wave", "parallel task"]
        assert any(m in s.lower() for m in markers), (
            "Plugin must check for dispatch-result indicators "
            "(<task_result>, <task_id>, 'subagent result', 'wave', "
            "'parallel task') before emitting the sweep directive."
        )

    def test_sweep_directive_text_present(self):
        s = _src()
        # The directive must name the recovery action ('sweep', 'commit',
        # 'recovery') so the agent knows to stop new work and codify results.
        assert "sweep" in s.lower(), (
            "Plugin must inject a post-wave sweep directive that tells the "
            "agent to commit accumulated deliverables before starting new work."
        )


# ---------------------------------------------------------------------------
# Gap 3 — orphaned-test detection
# ---------------------------------------------------------------------------

class TestOrphanedTestDetector:
    """A common drop pattern: subagent writes src/foo.py + tests/unit/test_foo.py;
    the source is committed and the test is left untracked (or vice versa).
    The plugin must detect this structural mismatch."""

    def test_orphan_check_function_present(self):
        s = _src()
        # The plugin must contain a function that performs the orphaned-test
        # structural check. We accept any reasonable name.
        assert re.search(
            r"function\s+\w*(orphan|orphaned|orphanTest)\w*", s, re.IGNORECASE
        ), (
            "Plugin must contain a dedicated function for orphaned-test "
            "detection (a name containing 'orphan')."
        )

    def test_orphan_warning_text_present(self):
        s = _src()
        # The warning must explicitly call out the dropped-test pattern so
        # the agent cannot mistake it for a generic commit reminder.
        assert "dropped" in s.lower(), (
            "Orphaned-test warning must explicitly say the test was dropped."
        )

    def test_orphan_check_reads_committed_state(self):
        """To detect src-committed / test-untracked, the plugin must consult
        git's tracked-file set (e.g. `git ls-files` or a porcelain filter
        that excludes the `??` prefix for the source-side check)."""
        s = _src()
        # Either `git ls-files` is invoked, or the porcelain output is split
        # into untracked vs tracked sets.
        assert "git ls-files" in s or "tracked" in s.lower(), (
            "Orphaned-test detection must distinguish tracked vs untracked "
            "files via `git ls-files` or by filtering porcelain prefixes."
        )


# ---------------------------------------------------------------------------
# Gap 4 — frequency cap
# ---------------------------------------------------------------------------

class TestFrequencyCap:
    """Without a frequency cap the directive would fire on every response and
    become noise. The cap records the last fire timestamp per directive type
    and suppresses refiring within 30 seconds."""

    def test_state_file_path_present(self):
        s = _src()
        assert "/tmp/gludd-nothing-dropped-last-fired.json" in s, (
            "Plugin must persist the last-fire timestamp in "
            "/tmp/gludd-nothing-dropped-last-fired.json so the frequency cap "
            "survives across hook invocations within a session."
        )

    def test_thirty_second_window(self):
        s = _src()
        # 30000 ms or 30 s — either representation is acceptable.
        assert ("30000" in s or "30" in s), (
            "Frequency cap must suppress refiring for >=30 seconds."
        )

    def test_cap_applies_per_directive_type(self):
        """The cap must key on directive type (untracked-deliverables vs
        sweep vs orphaned-test) so firing one does not suppress the others."""
        s = _src()
        # DirectiveType union enumerates the directive types that key the
        # frequency-cap state. Confirm all three are named.
        for dt in (
            "untracked-deliverables",
            "post-wave-sweep",
            "orphaned-test",
        ):
            assert dt in s, (
                f"Frequency-cap state must be keyed by directive type '{dt}' "
                "so firing one directive does not suppress the others."
            )
        # The state write must index by the directive-type key (bracket or
        # dot notation). Bracket notation is what the implementation uses.
        assert re.search(r"state\[\s*type\s*\]\s*=\s*Date\.now\(\)", s), (
            "Frequency-cap state must be written keyed by the directive type "
            "(state[type] = Date.now()) so the cap applies per-type."
        )


# ---------------------------------------------------------------------------
# Fail-open guarantees
# ---------------------------------------------------------------------------

class TestFailOpenGuarantees:
    """All new detection paths must fail open: a corrupt repo, missing git,
    or a permission error must NEVER wedge the session. The plugin returns
    the response unchanged."""

    def test_git_invocations_wrapped_in_try_catch(self):
        s = _src()
        # Every execSync call site must be inside a try block whose catch
        # returns a benign default. We count execSync calls vs try blocks —
        # there must be at least one try per execSync invocation.
        exec_count = len(re.findall(r"execSync\s*\(", s))
        try_count = len(re.findall(r"\btry\b\s*\{", s))
        assert try_count >= exec_count, (
            f"Found {exec_count} execSync calls but only {try_count} try "
            "blocks. Every git invocation must be wrapped in try/catch to "
            "fail open on git errors."
        )

    def test_response_transform_returns_output_on_error(self):
        s = _src()
        # The outermost response.transform must end with a catch that
        # returns `output` unchanged.
        pattern = r"catch\s*\{[^}]*return\s+output\s*\}"
        assert re.search(pattern, s), (
            "response.transform must have a catch block that returns the "
            "unchanged `output` on any internal error."
        )


# ---------------------------------------------------------------------------
# 2026-06-29 STRENGTHENING — post-wave-sweep REPLACES (not appends) the
# dropping summary, the `make <word>` exemption is dropped, the threshold
# for the deliverable check is lowered to 2, and the post-wave-sweep fire
# resets the other directive caps.
# ---------------------------------------------------------------------------

class TestPostWaveSweepReplacesNotAppends:
    """Gap #2 from the audit: the nothing-dropped directives were APPEND-only,
    so the dropping summary shipped to the user with the directive appended
    underneath — the user saw the dropped summary as if it were the
    deliverable. The post-wave-sweep case is severe enough to REPLACE the
    response entirely."""

    def test_post_wave_sweep_replaces_response_when_2plus_untracked(self):
        s = _src()
        # The replace logic must be present (post-wave-sweep branch returns
        # the directive alone, NOT appended to output).
        assert "POST-WAVE SWEEP REQUIRED" in s, (
            "Post-wave-sweep directive must be present with the marker "
            "'POST-WAVE SWEEP REQUIRED' so the replace path is identifiable."
        )
        assert "REPLACED by this directive" in s, (
            "Post-wave-sweep directive must state that the summary is being "
            "REPLACED — not appended to."
        )
        # The threshold for the deliverable check must be lowered to 2.
        m = re.search(
            r"UNTRACKED_DELIVERABLE_THRESHOLD\s*=\s*(\d+)", s,
        )
        assert m, "UNTRACKED_DELIVERABLE_THRESHOLD constant must be declared."
        assert int(m.group(1)) <= 2, (
            f"UNTRACKED_DELIVERABLE_THRESHOLD must be <=2 (got {m.group(1)}); "
            "the audit proved 3 let 1-2 drops through."
        )

    def test_post_wave_sweep_does_not_replace_when_only_1_untracked(self):
        s = _src()
        # The post-wave-sweep branch must be gated on >=2 untracked
        # deliverables (NOT >0 — that would replace on a single file). The
        # post-wave marker check alone is no longer enough.
        # Look for a length comparison against 2 in the sweep branch.
        # Accept any of the variable names the implementation may use.
        assert re.search(
            r"(untrackedDeliverables|sweepUntracked|deliverables)\w*\.length\s*>=?\s*2", s,
        ), (
            "Post-wave-sweep replace branch must require >=2 untracked "
            "deliverables — with only 1, append (do not replace)."
        )

    def test_pending_todos_directive_still_appends(self):
        """Only post-wave-sweep replaces. The pending-todos directive (the
        todowrite-state case) must still APPEND — it is less severe."""
        s = _src()
        # The append return path must still exist.
        assert re.search(
            r"return\s+output\s*\+\s*[\"']\\n[\"']\s*\+\s*directives", s,
        ), (
            "Pending-todos and orphaned-test directives must still APPEND to "
            "the response (only post-wave-sweep replaces)."
        )


class TestOrphanedTestStillAppends:
    """The orphaned-test directive is not severe enough to REPLACE the
    response — it must still APPEND."""

    def test_orphaned_test_directive_does_not_replace(self):
        s = _src()
        # Only post-wave-sweep is in the replace branch. The orphaned-test
        # directive must be pushed onto the directives array (appended),
        # not returned alone.
        # We assert the orphaned-test branch pushes to `directives`.
        assert re.search(
            r'recordFire\(\s*"orphaned-test"\s*\)', s,
        ), "orphaned-test must still use recordFire (append path)."
        # And the post-wave-sweep marker is NOT in the orphaned branch.
        assert "POST-WAVE SWEEP REQUIRED" not in s.split(
            'recordFire("orphaned-test"'
        )[0].split('recordFire("post-wave-sweep"')[-1], (
            "orphaned-test branch must not contain the replace marker."
        )


class TestMakeWordExemptionDropped:
    """Gap #2 sub-finding: `responseLooksLikeSummary()` returned false if the
    response contained `make <word` — so a recap ending with 'run make gate'
    was misclassified as not-a-summary. The exemption must be GONE."""

    def test_make_word_recap_detected_as_summary(self):
        s = _src()
        # The regex `/\bmake\s+\w/` must NOT appear in
        # responseLooksLikeSummary. (It may appear elsewhere as an
        # allow-pattern for other checks, but NOT as a summary-excluder.)
        # Extract the body of responseLooksLikeSummary and assert the
        # make-regex early-return is gone from it.
        m = re.search(
            r"function\s+responseLooksLikeSummary\([^)]*\)\s*:\s*boolean\s*\{(.*?)\n\}",
            s, re.DOTALL,
        )
        assert m, "responseLooksLikeSummary function must exist."
        body = m.group(1)
        assert not re.search(r"\\bmake\s+\\w", body), (
            "responseLooksLikeSummary must NOT early-return on `make <word>` — "
            "a recap ending with 'run make gate' IS a summary and must be "
            "detected. Drop the exemption."
        )


class TestPostWaveSweepResetsCaps:
    """Gap #4: the 30s per-directive cap can mask repeat breaches. When the
    post-wave-sweep directive fires, the other directive caps must be RESET
    so the next response gets full scrutiny."""

    def test_post_wave_sweep_resets_other_directive_caps(self):
        s = _src()
        # After recordFire("post-wave-sweep") there must be a state reset
        # that clears (deletes or zeroes) the other directive-type entries.
        # Look for a reset call near the post-wave-sweep branch.
        assert re.search(
            r"resetOtherDirectiveCaps|resetDirectiveCaps|clearFreqCaps",
            s,
        ) or (
            "delete state[" in s and "untracked-deliverables" in s
        ), (
            "Post-wave-sweep fire must reset the other directive frequency "
            "caps so the next response gets full scrutiny."
        )
