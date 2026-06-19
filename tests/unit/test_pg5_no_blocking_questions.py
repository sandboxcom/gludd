"""Tests for PG-5: no-blocking-questions behavior.

Verifies that AgentBehavior.assume_and_proceed, record_assumption,
should_block_on_question, and the corresponding BehaviorRenderer section
all work correctly.
"""

from __future__ import annotations

from general_ludd.agents.behavior import AgentBehavior, BehaviorRenderer

# ── Field defaults ──────────────────────────────────────────────────────────


def test_assume_and_proceed_default_true() -> None:
    """assume_and_proceed must default to True."""
    behavior = AgentBehavior()
    assert behavior.assume_and_proceed is True


def test_assumption_log_default_empty() -> None:
    """assumption_log must start as an empty list."""
    behavior = AgentBehavior()
    assert behavior.assumption_log == []


# ── should_block_on_question ────────────────────────────────────────────────


def test_should_not_block_when_assume_and_proceed_true() -> None:
    """should_block_on_question returns False when assume_and_proceed is True."""
    behavior = AgentBehavior()
    assert behavior.should_block_on_question("Any clarifying question?") is False


def test_should_block_when_assume_and_proceed_false() -> None:
    """should_block_on_question returns True when assume_and_proceed is False."""
    behavior = AgentBehavior(assume_and_proceed=False)
    assert behavior.should_block_on_question("Any clarifying question?") is True


# ── record_assumption ───────────────────────────────────────────────────────


def test_record_assumption_adds_to_log() -> None:
    """record_assumption appends an entry to assumption_log."""
    behavior = AgentBehavior()
    behavior.record_assumption("Which file to use?", "Use config.yaml")
    assert len(behavior.assumption_log) == 1


def test_record_assumption_returns_formatted_string() -> None:
    """record_assumption returns a string containing 'ASSUMPTION:' and 'assumed:'."""
    behavior = AgentBehavior()
    result = behavior.record_assumption("Which env?", "staging")
    assert "ASSUMPTION:" in result
    assert "assumed:" in result


def test_record_assumption_multiple_entries() -> None:
    """Multiple calls to record_assumption accumulate all entries in the log."""
    behavior = AgentBehavior()
    behavior.record_assumption("q1", "a1")
    behavior.record_assumption("q2", "a2")
    behavior.record_assumption("q3", "a3")
    assert len(behavior.assumption_log) == 3


def test_record_assumption_entry_content() -> None:
    """The log entry must contain both the question and assumed answer."""
    behavior = AgentBehavior()
    behavior.record_assumption("Should we use TLS?", "yes")
    entry = behavior.assumption_log[0]
    assert "Should we use TLS?" in entry
    assert "yes" in entry


# ── BehaviorRenderer ────────────────────────────────────────────────────────


def test_renderer_includes_no_blocking_section() -> None:
    """render() includes the no-blocking section when assume_and_proceed is True."""
    renderer = BehaviorRenderer()
    output = renderer.render(AgentBehavior())
    assert "NEVER pause" in output or "No-Blocking" in output


def test_renderer_excludes_no_blocking_when_disabled() -> None:
    """render() omits the no-blocking section when assume_and_proceed is False."""
    renderer = BehaviorRenderer()
    output = renderer.render(AgentBehavior(assume_and_proceed=False))
    assert "No-Blocking-Questions Policy" not in output
    assert "NEVER pause work to ask the user" not in output


# ── Serialisation ───────────────────────────────────────────────────────────


def test_to_dict_includes_assume_and_proceed() -> None:
    """to_dict() must include the 'assume_and_proceed' key."""
    behavior = AgentBehavior()
    d = behavior.to_dict()
    assert "assume_and_proceed" in d
    assert d["assume_and_proceed"] is True
