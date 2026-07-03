"""Plan-time technical-debt decision applier (SLICE 2).

:mod:`general_ludd.planning.debt_evaluator` (SLICE 1) produces
:class:`DebtFindings`, each carrying a deterministic ``recommendation`` of
``fold_in`` or ``defer``.  This module *applies* those decisions:

* ``defer``  -> a BACKLOG child todo is created via ``todo_repo.create`` (one per
  finding), so the gap is captured as future work instead of expanding the
  current task (feature-creep control).
* ``fold_in`` -> the current :class:`PlanArtifact` is *augmented* in place of the
  original — the gap is appended to the plan's contracts and notes, any in-scope
  ``touched_files`` are merged into ``target_files`` (deduped), and a
  natural-language ``prompt_addendum`` is produced telling the implementing agent
  to address the folded gaps within *this* task.  No todo is created.

The applier is deliberately defensive: each ``create`` is wrapped in
try/except so a single repository failure never aborts the remaining findings,
and the created-todo id is extracted whether ``create`` returns a model object,
a mapping, or a bare id.  ``create`` may be sync or async — both are awaited
correctly.
"""

from __future__ import annotations

import inspect
import json
import logging
from typing import Any

from pydantic import BaseModel

from general_ludd.planning.artifact import PlanArtifact
from general_ludd.planning.debt_evaluator import DebtFindings
from general_ludd.schemas.todo import TodoStatus

logger = logging.getLogger(__name__)

# Deferred tech-debt is real work but strictly lower urgency than the task that
# spawned it — it was split off precisely to avoid feature creep.  A moderate,
# non-zero default keeps it visible in the backlog without preempting primary
# work.  (The self-improve persist path maps "high" -> 10; we sit below that.)
_DEFAULT_DEBT_PRIORITY = 5

_ADDENDUM_HEADER = "### Fold-in scope (address in this task)"


class DebtApplyResult(BaseModel):
    """Outcome of applying a batch of :class:`DebtFindings`."""

    deferred_todo_ids: list[str] = []
    folded_in: int = 0
    prompt_addendum: str = ""
    augmented_plan: PlanArtifact


def _extract_todo_id(created: Any) -> str | None:
    """Best-effort pull of a todo id from whatever ``create`` returned.

    Supports an object with ``.todo_id`` (``TodoModel``), a mapping with a
    ``todo_id`` / ``id`` key, or an object with a bare ``.id``.
    """
    if created is None:
        return None
    todo_id = getattr(created, "todo_id", None)
    if todo_id:
        return str(todo_id)
    if isinstance(created, dict):
        val = created.get("todo_id") or created.get("id")
        return str(val) if val else None
    ident = getattr(created, "id", None)
    return str(ident) if ident else None


async def _maybe_await(value: Any) -> Any:
    """Await *value* if it is awaitable; otherwise return it unchanged."""
    if inspect.isawaitable(value):
        return await value
    return value


async def apply_debt_findings(
    findings: DebtFindings,
    plan: PlanArtifact,
    todo: Any,
    todo_repo: Any,
    *,
    project_id: str | None,
    tag: str = "tech-debt",
    work_type: str = "code",
) -> DebtApplyResult:
    """Apply ``fold_in`` / ``defer`` debt decisions to the plan and backlog.

    ``defer`` findings become BACKLOG child todos (mirroring the self-improve
    persist payload shape); ``fold_in`` findings extend a deep copy of ``plan``
    and contribute to ``prompt_addendum``.  Never raises on a single create
    failure — those are logged and skipped so the rest of the batch still lands.
    """
    augmented_plan = plan.model_copy(deep=True)
    parent_id = getattr(todo, "todo_id", None)

    deferred_ids: list[str] = []
    folded_gaps: list[str] = []

    for finding in findings.findings:
        if finding.recommendation == "fold_in":
            _fold_in(augmented_plan, finding)
            folded_gaps.append(finding.gap)
        else:  # "defer"
            todo_id = await _create_deferred_todo(
                finding,
                todo_repo,
                parent_id=parent_id,
                project_id=project_id,
                tag=tag,
                work_type=work_type,
            )
            if todo_id is not None:
                deferred_ids.append(todo_id)

    prompt_addendum = _build_addendum(folded_gaps)

    return DebtApplyResult(
        deferred_todo_ids=deferred_ids,
        folded_in=len(folded_gaps),
        prompt_addendum=prompt_addendum,
        augmented_plan=augmented_plan,
    )


def _fold_in(plan: PlanArtifact, finding: Any) -> None:
    """Mutate ``plan`` (already a copy) to absorb a fold_in finding."""
    # gap -> contracts
    plan.contracts.append(finding.gap)
    # gap -> notes (one line appended, preserving any existing note)
    note_line = f"Fold-in: {finding.gap}"
    plan.notes = f"{plan.notes}\n{note_line}" if plan.notes else note_line
    # touched_files -> target_files (dedup, preserve order)
    existing = set(plan.target_files)
    for f in finding.touched_files:
        if f not in existing:
            plan.target_files.append(f)
            existing.add(f)


def _build_addendum(gaps: list[str]) -> str:
    if not gaps:
        return ""
    lines = [_ADDENDUM_HEADER, ""]
    lines += [f"- {g}" for g in gaps]
    return "\n".join(lines)


async def _create_deferred_todo(
    finding: Any,
    todo_repo: Any,
    *,
    parent_id: str | None,
    project_id: str | None,
    tag: str,
    work_type: str,
) -> str | None:
    """Create one BACKLOG todo for a deferred finding; non-fatal on failure."""
    description = (finding.why_it_matters + " " + finding.feature_creep_rationale).strip()
    payload: dict[str, Any] = {
        "title": f"[tech-debt] {finding.gap[:120]}",
        "description": description,
        "status": TodoStatus.BACKLOG,
        "work_type": work_type,
        "priority": _DEFAULT_DEBT_PRIORITY,
        "created_by": "debt_evaluator",
        "project_id": project_id,
        "parent_todo_id": parent_id,
        # ``TodoModel.tags`` is a Text column holding a JSON array (the
        # "JSON-in-Text" convention shared by every other create path, e.g.
        # HumanTodoRepository.create). A raw Python list is rejected by the
        # SQLite driver ("type 'list' is not supported") AND poisons the job
        # session's transaction, so serialise to a JSON string here.
        "tags": json.dumps([tag]),
    }
    try:
        created = await _maybe_await(todo_repo.create(payload))
    except Exception:
        logger.warning(
            "failed to create deferred tech-debt todo for gap %r; skipping",
            finding.gap,
            exc_info=True,
        )
        return None
    return _extract_todo_id(created)
