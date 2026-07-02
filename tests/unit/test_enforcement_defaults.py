"""Pin the default enforcement state of the multitasking guardrails.

The three core multitasking guardrails (floor, session-start, no-wait) must be
ON BY DEFAULT — opt-out via the corresponding `*=0` env var. They must NOT be
flipped to opt-in (`*=1`) without an explicit user directive, because the
default-on behaviour is what prevents the agent from serializing work.

`force-delegate` is intentionally OPT-IN — it is an aggressive grind guard that
would wedge legitimate single-file edit sessions if it defaulted to on. This
test pins that asymmetry so a future "let's be consistent and make them all
default-on/off" change is caught loudly.
"""

from pathlib import Path

ROOT = Path(__file__).parent.parent.parent

FLOOR_PLUGIN = ROOT / ".opencode" / "plugin" / "enforce-floor.ts"
SESSION_START_PLUGIN = ROOT / ".opencode" / "plugin" / "enforce-session-start.ts"
STOP_PLUGIN = ROOT / ".opencode" / "plugin" / "enforce-stop.ts"
DELEGATE_PLUGIN = ROOT / ".opencode" / "plugin" / "enforce-delegate.ts"


class TestEnforcementDefaultsOn:
    """The multitasking guardrails must default to ON (opt-out via =0)."""

    def test_floor_enforcement_default_is_on(self) -> None:
        src = FLOOR_PLUGIN.read_text()
        # Find the GLUDD_FLOOR_ENFORCE line.
        line = next(
            (
                src_line
                for src_line in src.splitlines()
                if "GLUDD_FLOOR_ENFORCE" in src_line and "process.env" in src_line
            ),
            None,
        )
        assert line is not None, "GLUDD_FLOOR_ENFORCE env read not found in enforce-floor.ts"
        assert '!== "0"' in line, (
            "GLUDD_FLOOR_ENFORCE must default ON (opt-out via =0). "
            "Found line did not use the opt-OUT `!== \"0\"` pattern — do not flip "
            "the floor guardrail to opt-in. See AGENTS.md 'Minimum 10 Subagents'."
        )
        assert '=== "1"' not in line, (
            "GLUDD_FLOOR_ENFORCE must NOT be opt-in. The floor guardrail is the "
            "load-bearing default-on enforcement; flipping it to opt-in is a regression."
        )

    def test_session_start_enforcement_default_is_on(self) -> None:
        src = SESSION_START_PLUGIN.read_text()
        line = next(
            (
                ln
                for ln in src.splitlines()
                if "GLUDD_SESSION_START_ENFORCE" in ln and "process.env" in ln
            ),
            None,
        )
        assert line is not None, (
            "GLUDD_SESSION_START_ENFORCE env read not found in enforce-session-start.ts"
        )
        assert '!== "0"' in line, (
            "GLUDD_SESSION_START_ENFORCE must default ON (opt-out via =0). "
            "Found line did not use the opt-OUT `!== \"0\"` pattern — the session-start "
            "gate is what prevents prose-first session opens; do not make it opt-in."
        )
        assert '=== "1"' not in line, (
            "GLUDD_SESSION_START_ENFORCE must NOT be opt-in."
        )

    def test_no_wait_enforcement_default_is_on(self) -> None:
        src = STOP_PLUGIN.read_text()
        line = next(
            (
                ln
                for ln in src.splitlines()
                if "GLUDD_NO_WAIT_ENFORCE" in ln and "process.env" in ln
            ),
            None,
        )
        assert line is not None, "GLUDD_NO_WAIT_ENFORCE env read not found in enforce-stop.ts"
        assert '!== "0"' in line, (
            "GLUDD_NO_WAIT_ENFORCE must default ON (opt-out via =0) — this has been "
            "the contract since 2026-06-22. Found line did not use the opt-OUT "
            "`!== \"0\"` pattern; do not regress to advisory-by-default."
        )
        assert '=== "1"' not in line, (
            "GLUDD_NO_WAIT_ENFORCE must NOT be opt-in."
        )

    def test_force_delegate_remains_opt_in(self) -> None:
        src = DELEGATE_PLUGIN.read_text()
        line = next(
            (
                ln
                for ln in src.splitlines()
                if "GLUDD_FORCE_DELEGATE" in ln and "process.env" in ln
            ),
            None,
        )
        assert line is not None, "GLUDD_FORCE_DELEGATE env read not found in enforce-delegate.ts"
        assert '=== "1"' in line, (
            "GLUDD_FORCE_DELEGATE must remain OPT-IN (`=== \"1\"`). It is an aggressive "
            "grind guard that would wedge legitimate single-file edit sessions if it "
            "defaulted to on. Do not 'be consistent' and flip it to default-on."
        )
        assert '!== "0"' not in line, (
            "GLUDD_FORCE_DELEGATE must NOT use the opt-OUT pattern — it is intentionally opt-in."
        )

    def test_agent_floor_constant_is_10(self) -> None:
        src = FLOOR_PLUGIN.read_text()
        # 2026-07-01 refactor: the FLOOR default moved from an inline
        # `parseInt(process.env.CLAUDE_AGENT_FLOOR || "10")` to a
        # `_tunable("/tmp/gludd-floor-override", "CLAUDE_AGENT_FLOOR", "10")`
        # helper (same default, adds a /tmp override read). Accept either the
        # _tunable form or the legacy parseInt form; the env-var name + "10"
        # default are the load-bearing parts.
        line = next(
            (
                src_line
                for src_line in src.splitlines()
                if "CLAUDE_AGENT_FLOOR" in src_line
                and ("_tunable" in src_line or "parseInt" in src_line)
            ),
            None,
        )
        assert line is not None, (
            "CLAUDE_AGENT_FLOOR default line not found in enforce-floor.ts "
            "(expected _tunable(..., \"CLAUDE_AGENT_FLOOR\", \"10\") or a parseInt form)"
        )
        assert '"10"' in line, (
            "CLAUDE_AGENT_FLOOR default must be \"10\" (per the 2026-06-22 user mandate "
            "raising the floor from 6 to 10). The floor must not be silently lowered."
        )
