"""Shared model-invocation helper for the generation path.

Both the worker HTTP path (``worker/app.py:/jobs/execute``) and the daemon's
in-process Ansible runner path (``event_loop/loop.py:_dispatch_execute_job``)
must invoke the model the SAME way for a generation work type, then feed the
generated text into the playbook vars/extravars and the job result. This module
is the single source of that logic so the two surfaces cannot drift.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from general_ludd.models.gateway import ModelGateway

logger = logging.getLogger(__name__)

# Work types whose execute job is a model-driven generation task. For these the
# caller invokes the ModelGateway and feeds the generated output into the
# playbook (and the job result) before running the playbook.
_GENERATION_WORK_TYPES: frozenset[str] = frozenset(
    {"code", "bug_fix", "test", "refactor", "docs", "prompt", "analysis", "security"}
)


def is_generation_work_type(work_type: str | None) -> bool:
    """True when ``work_type`` is a model-driven generation task."""
    return work_type in _GENERATION_WORK_TYPES


def invoke_model_for_generation(
    gateway: ModelGateway,
    *,
    job_id: str,
    work_type: str | None,
    model_profile: str | None,
    prompt_text: str | None,
    skill_body: str | None,
    budget_guard: Any = None,
) -> str | None:
    """Call the model for a generation job. Returns the generated text or None.

    Mirrors the original ``worker/app.py:_invoke_gateway_for_job`` exactly:
    the system turn is the skill body, the user turn is the task prompt, both
    are bounded to the model's token window via AgentCapabilities, and any
    model/transport error is swallowed (logged) so the playbook still runs.
    """
    if not prompt_text:
        logger.info(
            "Generation job %s has no prompt_text; skipping model call", job_id
        )
        return None
    if budget_guard is not None and hasattr(budget_guard, "check_all_limits"):
        try:
            verdict = budget_guard.check_all_limits(estimated_cost=0.0)
        except Exception as exc:
            logger.warning("Budget pre-check raised for job %s: %s", job_id, exc)
            return None
        if not isinstance(verdict, dict) or not verdict.get("allowed", False):
            reason = (
                verdict.get("reason", "budget exhausted")
                if isinstance(verdict, dict)
                else "non-dict"
            )
            logger.warning("Budget denied for job %s: %s", job_id, reason)
            return None
    profile_id = model_profile or "default"
    # Bound the prompt to the model's token window via the shared agent
    # capabilities bundle (ContextCompactor + TokenWindowManager). The system
    # turn is the skill body; the user turn is the task prompt.
    from general_ludd.agents.capabilities import AgentCapabilities

    caps = AgentCapabilities(primary_profile=profile_id)
    system_prompt = skill_body or ""
    history = [{"role": "user", "content": prompt_text}]
    messages = [
        m for m in caps.prepare_messages(system_prompt, history)
        if m["content"].strip()
    ]
    try:
        response = gateway.call_model(profile_id, messages=messages)
        return response.content
    except Exception as exc:
        logger.warning(
            "Model call failed for job %s (profile=%s): %s",
            job_id,
            profile_id,
            exc,
        )
        return None
