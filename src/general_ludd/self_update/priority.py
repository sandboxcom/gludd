"""Backlog entry + priority + scheduler hook for self-updates (issue #81).

A self-update is, ultimately, just another unit of work. This module turns a
:class:`~general_ludd.self_update.model.SelfUpdatePlan` into:

  * a computed integer **priority** (higher = sooner), and
  * a **todo spec** (a dict ready for ``TodoRepository.create`` /
    ``POST /api/todos``) that enters the request into the normal backlog, and
  * a :class:`~general_ludd.scheduling.scheduler.WorkItem` describing how the
    request should be scheduled relative to other work.

It deliberately does NOT edit the scheduler. Instead it produces a ``WorkItem``
the existing :class:`~general_ludd.scheduling.scheduler.Scheduler` already
understands, and documents (in :func:`describe_scheduler_hook`) exactly where a
future wiring step would feed these items into ``Scheduler.plan()``.
"""

from __future__ import annotations

from general_ludd.scheduling.scheduler import WorkItem
from general_ludd.schemas.todo import RiskLevel, WorkType
from general_ludd.self_update.model import ApplyTier, SelfUpdatePlan, SelfUpdateRequest

#: Exclusive resource a real-code self-update must serialize on: it can touch
#: the live source tree, so two code-tier self-updates must never run together.
SELF_UPDATE_CODE_RESOURCE = "self_update:code"
#: Exclusive resource a config self-update serialises on (config hot-reload is a
#: single-writer operation against config/).
SELF_UPDATE_CONFIG_RESOURCE = "self_update:config"

#: Base priorities per tier (higher applies sooner). Config edits are cheap +
#: safe so they jump the queue; code changes wait behind review.
_TIER_BASE_PRIORITY: dict[ApplyTier, int] = {
    ApplyTier.CONFIG: 70,
    ApplyTier.SCAFFOLD: 55,
    ApplyTier.CODE: 30,
    ApplyTier.REFUSED: 0,
}

#: Risk level per tier — drives review intensity downstream.
_TIER_RISK: dict[ApplyTier, RiskLevel] = {
    ApplyTier.CONFIG: RiskLevel.LOW,
    ApplyTier.SCAFFOLD: RiskLevel.MEDIUM,
    ApplyTier.CODE: RiskLevel.HIGH,
    ApplyTier.REFUSED: RiskLevel.CRITICAL,
}


def compute_priority(plan: SelfUpdatePlan) -> int:
    """Compute a non-negative backlog priority for ``plan`` (higher = sooner).

    Tier sets the base; classifier confidence nudges it up (a high-confidence
    config tweak should land promptly); needing approval nudges it down (it
    can't run unattended anyway). Always clamped to ``>= 0`` so it satisfies the
    Todo schema's non-negative-priority validator.
    """
    base = _TIER_BASE_PRIORITY.get(plan.apply_tier, 0)
    confidence_bonus = round(plan.confidence * 20)
    approval_penalty = 15 if plan.requires_approval else 0
    return max(0, base + confidence_bonus - approval_penalty)


def to_todo_spec(
    plan: SelfUpdatePlan,
    request: SelfUpdateRequest,
    *,
    project_id: str | None = None,
) -> dict[str, object]:
    """Build a backlog todo spec (``TodoRepository.create`` / API payload).

    The spec carries the routing decision in its tags + description so the
    backlog row is self-describing and an operator can review the plan without
    re-running the classifier.
    """
    priority = compute_priority(plan)
    risk = _TIER_RISK.get(plan.apply_tier, RiskLevel.HIGH)
    title = f"self-update: {plan.subsystem.value}/{plan.change_kind.value}"
    description = (
        f"Self-update request from {request.requested_by!r}.\n"
        f"Request: {request.raw_text.strip()}\n"
        f"Routing: {plan.rationale}\n"
        f"Apply tier: {plan.apply_tier.value} "
        f"(requires_approval={plan.requires_approval}, confidence={plan.confidence}).\n"
        f"Target files: {', '.join(plan.target_files) or '(none resolved)'}"
    )
    tags = [
        "self-update",
        f"subsystem:{plan.subsystem.value}",
        f"tier:{plan.apply_tier.value}",
        f"kind:{plan.change_kind.value}",
    ]
    import json as _json

    spec: dict[str, object] = {
        "title": title,
        "description": description,
        "priority": priority,
        "queue": "self_update",
        "work_type": WorkType.INFRA.value,
        "risk_level": risk.value,
        "tags": _json.dumps(tags),
        "created_by": request.requested_by,
        "approval_policy": "required" if plan.requires_approval else "none",
    }
    if project_id is not None:
        spec["project_id"] = project_id
    return spec


def work_item_for_tier(tier: ApplyTier, item_id: str) -> WorkItem:
    """Build a :class:`WorkItem` for a self-update at the given apply tier.

    This is the tier→resource-label mapping, isolated from
    :class:`SelfUpdatePlan` so the event-loop scheduler branch can call it
    directly when reconstructing a work item from a backlog todo's ``tier:``
    tag (which is all the scheduler has at dispatch time — the full plan is
    not carried on the todo row).

    Resource selection encodes the safety contract directly into the scheduler's
    concurrency model:

      * CODE-tier  -> holds :data:`SELF_UPDATE_CODE_RESOURCE` (serialises with
        every other code-tier self-update; never runs two source edits at once).
      * CONFIG/SCAFFOLD -> holds :data:`SELF_UPDATE_CONFIG_RESOURCE` (config
        hot-reload is single-writer).
      * Anything else (e.g. REFUSED) -> greenfield/no-resource so it never blocks
        real work.
    """
    tier_value = tier.value
    if tier_value == ApplyTier.CODE.value:
        resources = frozenset({SELF_UPDATE_CODE_RESOURCE})
        greenfield = False
    elif tier_value in (ApplyTier.CONFIG.value, ApplyTier.SCAFFOLD.value):
        resources = frozenset({SELF_UPDATE_CONFIG_RESOURCE})
        greenfield = False
    else:
        resources = frozenset()
        greenfield = True
    return WorkItem(id=item_id, resources=resources, is_greenfield=greenfield)


def to_work_item(plan: SelfUpdatePlan, item_id: str) -> WorkItem:
    """Build a :class:`WorkItem` so the existing Scheduler can place this request.

    Thin delegate over :func:`work_item_for_tier` — kept as the plan-facing API
    so intake-side callers don't need to dig the tier out themselves.
    """
    return work_item_for_tier(plan.apply_tier, item_id)


def describe_scheduler_hook() -> str:
    """Document (don't perform) how these items wire into the scheduler.

    Returned as text so it is testable and surfaces in the audit/UI rather than
    living only in a comment. The actual wiring is a DEFERRED step (see the
    package note): a self-update intake would call :func:`to_work_item` for each
    pending self-update todo and pass the resulting items to
    ``Scheduler.plan()`` alongside the rest of the backlog, so config-tier
    updates batch concurrently while code-tier updates serialise on
    ``self_update:code``.
    """
    return (
        "DEFERRED WIRING: feed to_work_item(plan, todo_id) for each pending "
        "self_update-queue todo into Scheduler.plan(items) alongside the normal "
        "backlog WorkItems. CONFIG/SCAFFOLD items share "
        f"{SELF_UPDATE_CONFIG_RESOURCE!r} (single-writer config reload); CODE "
        f"items share {SELF_UPDATE_CODE_RESOURCE!r} (serialise source edits). "
        "Do NOT edit scheduler.py — it already partitions on these resources."
    )
