"""Tests that _build_system_prompt wires AgentBehavior into the live prompt (PG-0)."""

from __future__ import annotations

from general_ludd.agents.behavior import AgentBehavior
from general_ludd.execution.engine import _build_system_prompt
from general_ludd.schemas.job import JobSpec


def _minimal_job() -> JobSpec:
    return JobSpec(
        job_id="JOB-PG0",
        playbook="code",
        queue="core",
    )


class TestBuildSystemPromptBehaviorWiring:
    def test_system_prompt_contains_rendered_behavior_when_wired(self):
        """FAILS today: _build_system_prompt ignores the behavior arg.

        After PG-0 lands, the rendered behavior block (which includes the
        'Do NOT pause' line from the completion_policy section of
        BehaviorRenderer.render()) must appear in the returned string.
        """
        job = _minimal_job()
        behavior = AgentBehavior(completion_policy="complete_all")
        result = _build_system_prompt(job, behavior=behavior)
        # BehaviorRenderer.render() emits this exact phrase for completion_policy="complete_all"
        assert "Do NOT pause to ask" in result

    def test_system_prompt_generic_when_no_behavior(self):
        """Old path must still work when no behavior is supplied."""
        job = _minimal_job()
        result = _build_system_prompt(job)
        assert "You are a coding agent" in result
