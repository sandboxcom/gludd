"""Event loop for the agentic harness."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import queue as _stdqueue
import random
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from general_ludd.agents.hibernation import (
    AgentEnvironmentSnapshot,
    DispatchState,
)
from general_ludd.compaction.aggressive import level_at as _level_at
from general_ludd.controllers.compaction_aggressiveness import (
    AccuracySample,
    CompactionAggressivenessController,
)
from general_ludd.controllers.floor import FloorController
from general_ludd.controllers.load_scrape import LoadSnapshot
from general_ludd.controllers.pid import LoadController
from general_ludd.db.models import TaskDecisionModel
from general_ludd.db.repository import (
    AuditEventRepository,
    ConcurrencyError,
    TaskReturnRepository,
    TodoRepository,
    VariableNamespaceRepository,
)
from general_ludd.db.tenant import reset_tenant as _reset_tenant
from general_ludd.db.tenant import set_tenant as _set_tenant
from general_ludd.event_loop.lease import reclaim_expired_leases, release_lease
from general_ludd.event_loop.loop_handlers import EventLoopHandlers
from general_ludd.execution.graph_checkpointer import TickCheckpointer
from general_ludd.execution.human_gate import HumanGate
from general_ludd.execution.situation_store import BadCallSituationStore
from general_ludd.execution.tool_auditor import ToolCallAuditor
from general_ludd.mcp.client import MCPClient
from general_ludd.mcp.registry import MCPToolRegistry
from general_ludd.models.job_invocation import (
    invoke_model_for_generation,
    is_generation_work_type,
)
from general_ludd.observability.timing import default_tracker
from general_ludd.planning.debt_evaluator import (
    DebtEvaluator,
    make_debt_evaluate_fn,
)
from general_ludd.reload.self_improve import SelfImprovementWorkflow
from general_ludd.rules.engine import Rule, apply_rule_actions, evaluate_rules
from general_ludd.schemas.benchmark import TaskType
from general_ludd.schemas.job import JobSpec
from general_ludd.schemas.queue import Queue
from general_ludd.schemas.task_decision import TaskDecision
from general_ludd.schemas.task_return import TaskReturn, TaskReturnStatus
from general_ludd.schemas.todo import Todo, TodoStatus

if TYPE_CHECKING:
    # TYPE_CHECKING-only: avoids a runtime import cycle and keeps the drain
    # hook decoupled from the IPC layer at import time. ``WriteQueue`` is only
    # used as a type annotation on the ``inbound_queue`` kwarg.
    from general_ludd.ipc.queue import WriteQueue

logger = logging.getLogger(__name__)


class _FileClaimConflict(Exception):
    """Raised when a todo's git-delivery would clobber a file held by another
    live worker (#31 multi-agent safety).

    Treated by the completed-work push path exactly like any other delivery
    failure: the work id is left OUT of the pushed ledger so the commit is
    retried on a later tick (after the conflicting worker releases its claim),
    rather than two todos committing the same file simultaneously.
    """


PHASE_ORDER = [
    "load_config_snapshot",
    "claim_unreviewed_task_returns",
    "dispatch_return_review_jobs",
    "evaluate_pid_controllers",
    "refill_task_buckets",
    "run_scheduler",
    "sdlc_gate",
    "claim_runnable_todos",
    "evaluate_rules",
    "dispatch_execute_jobs",
    "reconcile_completed_decisions",
    "refresh_model_performance",
    "check_compute_utilization",
    "check_service_credits",
    "flush_spend_ledger",
    "remediate_blocked_tasks",
    "consolidate_memory",
    "self_improve",
    "poll_issue_sources",
    "service_discovery",
    "reap_expired_sts_tokens",
    "purge_old_task_decisions",
    "emit_tick_metrics",
]

# E10 (PERF-1): index of the dispatch phase in PHASE_ORDER.  The tick session
# is committed + closed BEFORE this phase so the dispatch gather (up to ~30 min)
# does not hold the DB writer lock.  A fresh session is opened for the
# post-dispatch phases.
DISPATCH_PHASE_INDEX = PHASE_ORDER.index("dispatch_execute_jobs")


# Two-phase generation (keystone): Phase 1 (``invoke_model_for_generation``) is
# tool-free BY DESIGN (CA-T9) — it asks the model for text and never binds tools.
# Phase 2 runs the fully-built ``ToolCallLoop`` for work types whose execution
# genuinely needs autonomous tool use (the model decides which MCP tools to call,
# the loop executes them, feeds results back, and iterates). This frozenset is the
# (tunable) gate for which work types get Phase 2; it now includes code-generation
# work types (code, bug_fix, refactor, feature, test) so the model can iterate on
# code based on test failures — with safety guards (budget check, token limit,
# adversarial scan, per-iteration timeout, work-type-specific max iterations).
# Phase 2 additionally requires both an MCP client and a model gateway to be
# wired; absent either, the tick falls through to Phase-1-only output.
_TOOL_USE_WORK_TYPES: frozenset[str] = frozenset(
    {"analysis", "audit", "code", "bug_fix", "refactor", "feature", "test"}
)

_CODE_WORK_TYPES: frozenset[str] = frozenset({"code", "bug_fix", "refactor", "feature", "test"})


def _safe_str(obj: Any, attr: str, default: str | None = None) -> str | None:
    val = getattr(obj, attr, default)
    return val if isinstance(val, str) else default


def _format_acceptance_criteria(raw_ac: str | None) -> str:
    if not raw_ac:
        return ""
    try:
        parsed = json.loads(raw_ac)
    except (json.JSONDecodeError, TypeError):
        return raw_ac
    if not isinstance(parsed, list):
        return raw_ac
    if not parsed:
        return ""
    return "\n".join(f"- {c}" for c in parsed)


def _self_update_work_item_from_todo(todo: Any, todo_id: str) -> Any:
    """Build a Scheduler ``WorkItem`` for a ``self_update``-queue todo.

    The intake half of the pipeline (:func:`priority.to_todo_spec`) writes a
    ``tier:<value>`` tag onto the backlog row. This inverts that: it digs the
    apply tier back out of the todo's tags and feeds it through
    :func:`priority.work_item_for_tier` so code-tier self-updates serialise on
    ``self_update:code`` and config-tier on ``self_update:config`` — without
    round-tripping the original :class:`SelfUpdatePlan` (which is not carried
    on the todo row).

    Fail-closed: an unknown / missing tier becomes a greenfield work item
    (empty resources) so a malformed tag never blocks real work. Mirrors
    ``ApplyTier.REFUSED`` handling in :func:`priority.work_item_for_tier`.
    """
    from general_ludd.self_update.model import ApplyTier
    from general_ludd.self_update.priority import work_item_for_tier

    tags = getattr(todo, "tags", None) or []
    tier_value = ""
    for tag in tags:
        if isinstance(tag, str) and tag.startswith("tier:"):
            tier_value = tag.split(":", 1)[1].strip()
            break
    try:
        tier = ApplyTier(tier_value)
    except ValueError:
        tier = ApplyTier.REFUSED
    return work_item_for_tier(tier, todo_id)


def _resolve_prompt_text_static(
    prompt_registry: Any,
    prompt_profile: str | None,
    **kwargs: object,
) -> str | None:
    project_templates_dir: object = kwargs.pop("project_templates_dir", None)
    if not prompt_profile:
        return None
    if project_templates_dir is not None:
        from pathlib import Path as _Path

        tmpl_path = _Path(str(project_templates_dir)) / prompt_profile
        if tmpl_path.is_file():
            try:
                from jinja2 import FileSystemLoader as _FSL
                from jinja2.sandbox import SandboxedEnvironment as _Env

                env = _Env(loader=_FSL(str(project_templates_dir)), autoescape=True)
                tmpl = env.get_template(prompt_profile)
                return tmpl.render(**kwargs)
            except Exception:
                logger.debug(
                    "Jinja project-template render failed for profile %r; falling through to registry render",
                    prompt_profile,
                    exc_info=True,
                )
    if prompt_registry is None:
        return None
    try:
        result: str = prompt_registry.render(prompt_profile, **kwargs)
        return result
    except Exception:
        logger.warning(
            "Registry render failed for prompt profile %r; returning no prompt text",
            prompt_profile,
            exc_info=True,
        )
        return None


_WORK_TYPE_TASK_TYPE_MAP: dict[str, str] = {
    "bug_fix": "bug_fix",
    "code": "feature",
    "test": "test_write",
    "review": "code_review",
    "refactor": "refactor",
    "docs": "documentation",
    "infra": "feature",
    "prompt": "feature",
    "analysis": "feature",
    "audit": "feature",
    "release": "feature",
    "dependency": "feature",
    "security": "security_fix",
    "model": "feature",
    "unknown": "feature",
    "model_decision": "feature",
    "langgraph_generate": "feature",
    "enforcement_gate": "feature",
    "enforcement_gate_push_guard": "feature",
    "enforcement_gate_check": "feature",
}


def _work_type_to_task_type(work_type: str) -> TaskType:
    mapped = _WORK_TYPE_TASK_TYPE_MAP.get(work_type, "feature")
    try:
        return TaskType(mapped)
    except ValueError:
        return TaskType.FEATURE


_WORK_TYPE_PLAYBOOK_MAP: dict[str, str] = {
    "code": "validate_task.yml",
    "test": "molecule_test.yml",
    "analysis": "gap_analysis.yml",
    "audit": "log_audit.yml",
    "prompt": "prompt_eval.yml",
    "self_improve": "self_improve_harness.yml",
    "dependency": "dependency_update.yml",
    "review": "return_review.yml",
    "docs": "noop.yml",
    "infra": "noop.yml",
    "security": "noop.yml",
    "model": "noop.yml",
    "release": "noop.yml",
    "model_decision": "langgraph_decide.yml",
    "langgraph_generate": "langchain_generate.yml",
    "enforcement_gate": "enforcement_gate.yml",
    "enforcement_gate_push_guard": "enforcement_gate.yml",
    "enforcement_gate_check": "enforcement_gate.yml",
}


def _playbook_for_work_type(
    work_type: str,
    default: str = "noop.yml",
    *,
    project_id: str | None = None,
    workspaces: dict[str, Any] | None = None,
) -> str:
    ws = workspaces.get(project_id) if workspaces and project_id else None
    if ws is not None and hasattr(ws, "playbooks_dir"):
        from pathlib import Path as _Path

        pb_path = _Path(ws.playbooks_dir) / f"{work_type}.yml"
        if pb_path.is_file():
            return str(pb_path)
    return _WORK_TYPE_PLAYBOOK_MAP.get(work_type, default)


_RESOURCE_BASE_COST: dict[str, float] = {
    "low_resource": 0.05,
    "medium_resource": 0.25,
    "high_resource": 1.0,
}


def _compute_todo_estimate(todo: object) -> float:
    resource_profile: str = getattr(todo, "resource_profile", "low_resource") or "low_resource"
    confidence: float | None = getattr(todo, "confidence", None)
    base_cost = _RESOURCE_BASE_COST.get(resource_profile, 0.05)
    effective_confidence = 0.5 if confidence is None else float(confidence)
    return round(base_cost * (1.5 - effective_confidence), 4)


class EventLoop(EventLoopHandlers):
    def __init__(
        self,
        worker_base_url: str = "http://localhost:8000",
        config: dict[str, Any] | None = None,
        session: AsyncSession | async_sessionmaker[AsyncSession] | None = None,
        http_client: Any | None = None,
        todo_repo: TodoRepository | None = None,
        task_return_repo: TaskReturnRepository | None = None,
        budget_guard: Any | None = None,
        mcp_client: MCPClient | None = None,
        mcp_tool_registry: MCPToolRegistry | None = None,
        runner: Any | None = None,
        event_bus: Any | None = None,
        project_manager: Any | None = None,
        prompt_registry: Any | None = None,
        audit_repo: Any | None = None,
        skill_registry: Any | None = None,
        variable_repo: Any | None = None,
        adaptive_router: Any | None = None,
        daemon_state: dict[str, Any] | None = None,
        project_secrets_manager: Any | None = None,
        project_workspace: Any | None = None,
        self_improve_interval: int = 0,
        reviewer: Any | None = None,
        consensus_reviewer: Any | None = None,
        langgraph_reviewer: Any | None = None,
        model_gateway: Any | None = None,
        dispatcher: Any | None = None,
        loc_ledger: Any | None = None,
        pause_controller: Any | None = None,
        spend_limiter: Any | None = None,
        file_claim_registry: Any | None = None,
        ansible_env_updater: Any | None = None,
        model_perf_repo: Any | None = None,
        model_performance_interval: int = 10,
        deployment_health_router: Any | None = None,
        memory_repo: Any = None,
        procedural_memory: Any = None,
        semantic_memory: Any = None,
        consolidation_interval_ticks: int = 100,
        sandbox_executor: Any | None = None,
        sandbox_config: Any | None = None,
        sandbox_attestation_store: Any | None = None,
        sandbox_profile: Any | None = None,
        run_recorder: Any | None = None,
        prompt_variant_selector: Any | None = None,
        checkpointer: TickCheckpointer | None = None,
        utilization_tracker: Any | None = None,
        deployment_manager: Any | None = None,
        floor_controller: FloorController | None = None,
        issue_ingestor: Any | None = None,
        compaction_controller: CompactionAggressivenessController | None = None,
        infra_tracker: Any | None = None,
        credit_tracker: Any | None = None,
        ephemeral_account_manager: Any | None = None,
        inbound_queue: WriteQueue | None = None,
        checkpoint_manager: Any | None = None,
        service_discovery: Any | None = None,
    ) -> None:
        self.worker_base_url = worker_base_url
        self.config = config or {}
        self._daemon_state = daemon_state
        self._run_recorder = run_recorder
        self._prompt_variant_selector = prompt_variant_selector
        self._checkpointer = checkpointer
        self._utilization_tracker = utilization_tracker
        self._deployment_manager = deployment_manager
        self._project_secrets_manager = project_secrets_manager
        self._project_workspace = project_workspace
        self._self_improve_interval = self_improve_interval
        self._reviewer = reviewer
        self._consensus_reviewer = consensus_reviewer
        self._langgraph_reviewer = langgraph_reviewer
        self._model_gateway = model_gateway
        self._mock_gateway: Any = None
        self._dispatcher = dispatcher
        self._spend_limiter = spend_limiter  # may be overwritten post-construction by the daemon
        self._pause_controller = pause_controller
        self._floor_controller = floor_controller
        self._compaction_controller = compaction_controller
        self._infra_tracker = infra_tracker
        self._credit_tracker = credit_tracker
        # Ephemeral cloud account lifecycle: when set, completed tasks that
        # carry an ``ephemeral_account_id`` stamp trigger account teardown via
        # ``maybe_delete_ephemeral_after_task``. None = ephemeral mode off.
        self._ephemeral_account_manager = ephemeral_account_manager
        # B3.1.5: dispatch-lifecycle checkpoint manager. When set, the
        # event loop writes a checkpoint at three boundaries in
        # _dispatch_execute_job (pre-model, per-tool-iter, clear-on-persist)
        # and on boot re-runs any dispatch whose checkpoint survived a
        # writer crash. None = checkpoint/resume disabled (back-compat).
        self._checkpoint_manager = checkpoint_manager
        # Compaction feedback loop: accumulated accuracy samples across ticks.
        self._compaction_passed = 0
        self._compaction_total = 0
        self._compaction_level: int | None = None  # lazy-init from config on first dispatch
        self._compaction_disabled: bool = False
        self._stuck_timeout_minutes = 15
        self._max_retries = 3
        if isinstance(session, async_sessionmaker):
            self._session_factory: async_sessionmaker[AsyncSession] | None = session
            self.session: AsyncSession | None = None
        else:
            self._session_factory = None
            self.session = session
        self._http_client = http_client
        self._runner = runner
        self._active_session: AsyncSession | None = self.session
        live_session = self.session
        self._todo_repo = todo_repo or (TodoRepository(live_session) if live_session else None)
        self._task_return_repo = task_return_repo or (TaskReturnRepository(live_session) if live_session else None)
        self._budget_guard = budget_guard
        self._mcp_client = mcp_client
        self._mcp_tool_registry = mcp_tool_registry
        self._running = False
        self._wake_event = asyncio.Event()
        # A manual wake/tick can overlap the daemon's run_forever task.  The
        # loop stores tick-scoped repositories on ``self``, so overlapping
        # ticks would otherwise replace one another's AsyncSession owners and
        # close a session while it is still provisioning a connection.
        self._tick_lock = asyncio.Lock()
        self._total_ticks = 0
        self._tick_state: dict[str, Any] = {}
        self._active_traces: dict[str, Any] = {}
        self._benchmark_recorder: Any = None
        # A3: fire-and-forget background tasks (benchmark / trace writes).
        # A strong reference is held here so a still-running task is never
        # garbage-collected mid-flight. Mutations use the idiomatic
        # add + add_done_callback(discard) pattern (both ops individually atomic
        # under the GIL); the only COMPOUND access — the shutdown drain, which
        # snapshots → cancels → awaits — is serialised by _bg_tasks_lock so a
        # concurrent discard during the drain can never raise "set changed size
        # during iteration".
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._bg_tasks_lock = asyncio.Lock()
        self._observability_enabled: bool = bool(adaptive_router)
        self._tick_metrics: dict[str, Any] = {}
        # Reconcile idempotency ledgers (defects F1/F2/F5):
        #   _applied_decisions  — decision ids whose status transition has already
        #     been applied; re-applying the same decision on a later tick/re-run is
        #     a no-op (F1: non-idempotent decision re-apply).
        #   _pushed_work        — todo (work) ids whose completed work has already
        #     been pushed; guarantees the push fires exactly once and is never
        #     duplicated across ticks (F2: completed-work push lost/double).
        #   _push_retry_count   — per-todo consecutive push failure counter (#53);
        #     after _MAX_PUSH_RETRIES failures the todo is transitioned to BLOCKED
        #     instead of retrying every tick forever (F5: file-claim livelock).
        self._applied_decisions: OrderedDict[str, None] = OrderedDict()
        self._pushed_work: OrderedDict[str, None] = OrderedDict()
        self._push_retry_count: dict[str, int] = {}
        max_to_thread = self.config.get("event_loop", {}).get("max_to_thread_concurrency", 32)
        self._to_thread_semaphore = asyncio.Semaphore(max_to_thread)
        max_gather = self.config.get("event_loop", {}).get("max_gather_concurrency", 20)
        self._dispatch_semaphore = asyncio.Semaphore(max_gather)
        self._config_snapshot: dict[str, Any] = {}
        # M14 (W3.14): single project selected per tick, shared across all phases.
        # Reset to None at the end of every tick (see tick() finally block).
        self._tick_project_id: str | None = None
        self._event_bus = event_bus
        self._project_manager = project_manager
        self._prompt_registry = prompt_registry
        self._audit_repo = audit_repo or (AuditEventRepository(live_session) if live_session else None)
        self._skill_registry = skill_registry
        self._variable_repo = variable_repo or (VariableNamespaceRepository(live_session) if live_session else None)
        if event_bus is not None:
            event_bus.subscribe("config_reloaded", self._on_config_reloaded)
        self._adaptive_router = adaptive_router
        # LocLedger (accounting.ledger): the event loop records a per-commit
        # lines-of-code delta here after every successful commit so the
        # accounting router can report cumulative loc_changed per project.
        self._loc_ledger = loc_ledger
        # #31 (multi-agent safety): the shared FileClaimRegistry (also stored on
        # app.state._file_claims and surfaced via /api/coordination + /api/facts).
        # The git-delivery path (_try_commit_completed_work) claims a todo's
        # affected files here BEFORE committing and releases them after, so two
        # concurrent todos can never write+commit the same file in the same tick.
        self._file_claims = file_claim_registry
        # Ansible collections env rebuild callback (daemon startup wires this to
        # a closure that updates app.state._ansible_env + the runner's
        # _default_env). Invoked from _select_tick_project_id when the active
        # project changes so playbook invocations pick up the new project's
        # .gludd/collections/ on the next run.
        self._ansible_env_updater = ansible_env_updater
        self._last_ansible_env_project_id: str | None = None
        self._model_perf_repo: Any = model_perf_repo
        self._model_performance_interval: int = model_performance_interval
        self._deployment_health_router: Any = deployment_health_router
        self._memory_repo: Any = memory_repo
        self._procedural_memory: Any = procedural_memory
        self._semantic_memory: Any = semantic_memory
        self._consolidation_interval_ticks: int = consolidation_interval_ticks
        self._consolidation_tick_counter: int = 0
        self._sandbox_executor = sandbox_executor
        self._sandbox_config = sandbox_config
        self._sandbox_attestation_store = sandbox_attestation_store
        self._sandbox_profile = sandbox_profile
        # Task #48: plan-time technical-debt evaluator (config-gated at the
        # dispatch seam; default OFF). Wired to the model gateway when present;
        # a None gateway leaves the evaluator on its deterministic structural
        # fallback so this is never a hard dependency.
        self._debt_evaluator = DebtEvaluator(
            make_debt_evaluate_fn(self._model_gateway) if self._model_gateway else None
        )
        self._human_gate = HumanGate(config=self.config)
        self._issue_ingestor: Any = issue_ingestor
        self._issue_poll_interval_ticks: int = 300
        self._issue_poll_tick_counter: int = 0
        # B3.1.3 Slice 5: inbound WriteQueue drain hook. When set, run_forever
        # empties this queue between ticks (non-blocking) and applies each
        # Envelope inside its own DB session. None = drain disabled (no-op).
        self._inbound_queue: WriteQueue | None = inbound_queue
        self._service_discovery = service_discovery
        self._service_discovery_last_run: float = 0.0

    def _track_background_task(self, task: asyncio.Task[None]) -> None:
        """Register a fire-and-forget task so its reference is held until done.

        A3: keeps a strong reference in ``self._background_tasks`` (so the task
        is never GC'd while running) and arms a done-callback that discards it on
        completion (so the set drains to empty and never leaks). ``set.add`` and
        ``set.discard`` are each atomic under the GIL, and ``add_done_callback``
        fires immediately if the task is already complete — so this is safe to
        call from any coroutine without a lock. The lock guards only the COMPOUND
        shutdown drain in :meth:`_drain_background_tasks`.
        """
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _drain_background_tasks(self, *, cancel: bool = True) -> None:
        """Cancel + await all tracked background tasks (used on shutdown).

        A3: iterates a SNAPSHOT (``list(self._background_tasks)``) taken under
        ``_bg_tasks_lock`` so a concurrent done-callback ``discard`` cannot mutate
        the set while we read it — the read-modify-await sequence here is the only
        compound access to the set and is the one that genuinely needs the lock.
        Cancellation is best-effort: each task's done-callback removes it from the
        live set as it settles, so after the gather the set drains to empty.
        """
        async with self._bg_tasks_lock:
            pending = list(self._background_tasks)
        if not pending:
            return
        if cancel:
            for task in pending:
                if not task.done():
                    task.cancel()
        # gather a snapshot (never the live set) with return_exceptions so a
        # cancelled/failed background task can't propagate into shutdown.
        await asyncio.gather(*pending, return_exceptions=True)
        # A done-callback is scheduled by asyncio after the gather callback
        # resumes. Remove the drained snapshot synchronously as well, so callers
        # can rely on the shutdown postcondition before the next loop turn.
        async with self._bg_tasks_lock:
            self._background_tasks.difference_update(pending)

    async def _append_message_queue_section(
        self, prompt_text: str | None, todo: Any, project_id: str | None
    ) -> str | None:
        """PART 4: tell a dispatched agent the MQ + facts are available.

        Gated behind config flag ``message_queue_prompt`` (default off) so prompts
        without MQ context are byte-for-byte unchanged. The agent "role" is the
        todo's assigned_agent, falling back to its work_type.
        """
        if not self.config.get("message_queue_prompt"):
            return prompt_text
        role = _safe_str(todo, "assigned_agent") or _safe_str(todo, "work_type") or "agent"
        unread = 0
        senders: list[str] = []
        factory = self._session_factory
        if factory is not None:
            try:
                from general_ludd.db.repository import AgentMessageRepository

                async with factory() as session:
                    repo = AgentMessageRepository(session)
                    msgs = await repo.inbox(role, unread_only=True, project_id=project_id)
                    unread = len(msgs)
                    senders = [m.sender for m in msgs]
            except Exception:
                logger.warning(
                    "MQ inbox lookup failed for role %r (project %s): falling back to empty inbox",
                    role,
                    project_id,
                    exc_info=True,
                )
                unread = 0
                senders = []
        from general_ludd.prompts.registry import render_message_queue_section

        section = render_message_queue_section(role=role, unread_count=unread, senders=senders, enabled=True)
        if not section:
            return prompt_text
        return f"{prompt_text}\n\n{section}" if prompt_text else section

    async def _build_memory_section(self, prompt_text: str | None, todo: Any) -> str | None:
        if self._memory_repo is None:
            return prompt_text
        assigned_agent = _safe_str(todo, "assigned_agent") or _safe_str(todo, "work_type") or "agent"
        try:
            records = await self._memory_repo.list_by_namespace(
                agent_id=assigned_agent,
                namespace="default",
                limit=50,
            )
        except Exception:
            logger.warning(
                "Memory lookup failed for agent %r; skipping memory section",
                assigned_agent,
                exc_info=True,
            )
            return prompt_text
        if not records:
            return prompt_text
        lines = ["## Agent Memory"]
        for r in records:
            lines.append(f"- **{r.key}**: {r.value}")
        section = "\n".join(lines)
        return f"{prompt_text}\n\n{section}" if prompt_text else section

    async def _resolve_adaptive_prompt(
        self, todo: Any, default_model_profile: str = "default"
    ) -> tuple[str | None, str | None, Any | None]:
        if self._adaptive_router is None:
            logger.warning("_adaptive_router not initialized; skipping adaptive prompt routing")
            return None, None, None
        work_type = _safe_str(todo, "work_type", "feature") or "feature"
        task_type = _work_type_to_task_type(work_type)
        default_prompt = _safe_str(todo, "prompt_profile")
        decision = await self._adaptive_router.route(
            task_type=task_type,
            default_prompt_profile=default_prompt,
            default_model_profile=default_model_profile,
        )
        return (decision.selected_prompt_profile_id, decision.selected_model_profile_id, decision)

    def _resolve_skill_body(self, todo: Any) -> str | None:
        if self._skill_registry is None:
            return None
        title = _safe_str(todo, "title") or ""
        matched = self._skill_registry.match_trigger(title)
        if matched:
            body: str | None = matched[0].body
            return body
        return None

    async def _load_shared_vars(self, project_id: str | None) -> dict[str, str] | None:
        if self._variable_repo is None or self._active_session is None:
            return None
        return await self._variable_repo.load_vars_for_project(project_id)

    def _on_config_reloaded(self, event: Any) -> None:
        payload = getattr(event, "payload", {}) or {}
        scope = payload.get("scope", "")
        logger.info("EventLoop received config reload event, scope=%s", scope)
        self._config_snapshot = dict(self.config)

    async def _reap_stuck_todos(self) -> None:
        """Requeue ACTIVE todos whose worker is genuinely gone.

        A liveness signal is required before reaping: an ACTIVE todo is only
        "stuck" if its bucket lease has *expired* (or never existed). A todo that
        is still executing holds a live (unexpired) lease — its ``updated_at`` is
        frozen at claim time with no heartbeat, so ``updated_at`` ALONE is not a
        liveness clock and must never be used to reap live work.

        ``version`` is NOT a retry counter (it is bumped by every write), so it is
        no longer conflated with attempts. A genuinely stale todo is simply
        requeued for another attempt.
        """
        if self._active_session is None or self._todo_repo is None:
            return
        try:
            from general_ludd.db.models import BucketLeaseModel, TodoModel

            now = datetime.now(UTC)
            cutoff = now - timedelta(minutes=self._stuck_timeout_minutes)
            stmt = (
                select(TodoModel)
                .where(TodoModel.status == TodoStatus.ACTIVE.value)
                .where(TodoModel.updated_at < cutoff)
            )
            result = await self._active_session.execute(stmt)
            candidates = list(result.scalars().all())
            if not candidates:
                return

            # Batch-fetch all live leases for candidate todos in one query
            # instead of per-todo N+1 lookups. Build bucket_keys from
            # queue:todo_id pairs, then query BucketLeaseModel with IN.
            bucket_keys = [f"{_safe_str(t, 'queue', 'core')}:{_safe_str(t, 'todo_id', '')}" for t in candidates]
            live_lease_stmt = (
                select(BucketLeaseModel.bucket_key)
                .where(BucketLeaseModel.bucket_key.in_(bucket_keys))
                .where(BucketLeaseModel.expires_at > now)
            )
            live_lease_result = await self._active_session.execute(live_lease_stmt)
            live_bucket_keys: set[str] = set(live_lease_result.scalars().all())

            reaped = 0
            for todo in candidates:
                queue = _safe_str(todo, "queue", "core") or "core"
                todo_id = _safe_str(todo, "todo_id", "") or ""
                bucket_key = f"{queue}:{todo_id}"
                if bucket_key in live_bucket_keys:
                    # Worker is still heartbeating (lease alive) -> do NOT reap.
                    continue
                # Guarded compare-and-set: transition ACTIVE->QUEUED only if the
                # row is STILL active at the version we read. A concurrent writer
                # (claim, reconcile, manual edit) that moved the row makes the CAS
                # affect zero rows -> ConcurrencyError, treated as a lost race and
                # skipped. This mirrors claim_runnable()/transition()'s version +
                # status guard so the reaper can never silently clobber a
                # concurrent status write (the check-then-act race this method
                # previously had when it assigned the ORM attribute directly).
                try:
                    await self._todo_repo.transition(todo.todo_id, TodoStatus.QUEUED, todo.version)
                except ConcurrencyError as exc:
                    logger.info(
                        "Reaper lost version race for todo %s: %s — skipping",
                        todo.todo_id,
                        exc,
                    )
                    continue
                reaped += 1
                self._tick_state.setdefault("reaped_todo_ids", set()).add(todo.todo_id)
            if reaped:
                logger.info("Reaped %d stuck ACTIVE todos (no live lease)", reaped)
        except Exception as exc:
            logger.warning("Stuck-todo reaper failed: %s", exc)

    async def tick(self) -> dict[str, Any]:
        """Run one complete tick with exclusive ownership of tick state."""
        async with self._tick_lock:
            return await self._tick_once()

    async def _tick_once(self) -> dict[str, Any]:
        self._tick_state = {}
        self._total_ticks += 1
        tick_id = f"tick_{self._total_ticks}"
        self._tick_metrics = {
            "total_ticks": self._total_ticks,
            "phases_completed": 0,
            "tick_duration_ms": 0.0,
            "returns_reviewed": 0,
            "todos_dispatched": 0,
            "decisions_applied": 0,
            "leases_reclaimed": 0,
        }
        if self._checkpointer is not None:
            previous = self._checkpointer.get("last_tick")
            if previous:
                self._tick_state = previous.get("_tick_state", {})
                for key in previous.get("_applied_decision_keys", []):
                    if key not in self._applied_decisions:
                        self._applied_decisions[key] = None
                for key in previous.get("_pushed_work_keys", []):
                    if key not in self._pushed_work:
                        self._pushed_work[key] = None
                self._push_retry_count = previous.get("_push_retry_count", self._push_retry_count)
        # M14 (W3.14): select ONE project per tick before phases run; reset after.
        self._tick_project_id = self._select_tick_project_id()
        # C.3: propagate tenant context into thread-pool workers so sessions
        # created inside asyncio.to_thread carry the project_id filter.
        _tenant_token = _set_tenant(self._tick_project_id)
        start = time.monotonic()
        try:
            needs_own_session = self.session is None and self._session_factory is not None
            if needs_own_session:
                assert self._session_factory is not None
                async with self._session_factory() as session:
                    self._active_session = session
                    self._todo_repo = TodoRepository(session)
                    self._task_return_repo = TaskReturnRepository(session)
                    self._audit_repo = AuditEventRepository(session)
                    self._variable_repo = VariableNamespaceRepository(session)
                    # E10 (PERF-1): commit + close the tick session BEFORE the
                    # dispatch gather so the DB writer lock is released during
                    # the potentially-30-minute dispatch window.
                    await self._run_phase_range(0, DISPATCH_PHASE_INDEX)
                    await self._commit_tick_session(session)
                    self._clear_repos()
                # Dispatch phase: NO tick session held.  Isolated per-job
                # sessions are opened inside _dispatch_execute_job_isolated.
                await self._run_phase_range(DISPATCH_PHASE_INDEX, DISPATCH_PHASE_INDEX + 1)
                assert self._session_factory is not None
                for phase_idx in range(DISPATCH_PHASE_INDEX + 1, len(PHASE_ORDER)):
                    async with self._session_factory() as session:
                        self._active_session = session
                        self._todo_repo = TodoRepository(session)
                        self._task_return_repo = TaskReturnRepository(session)
                        self._audit_repo = AuditEventRepository(session)
                        self._variable_repo = VariableNamespaceRepository(session)
                        await self._run_phase_range(phase_idx, phase_idx + 1)
                        await self._commit_tick_session(session)
                    self._clear_repos()
            else:
                if self.session is not None:
                    self._active_session = self.session
                    self._todo_repo = self._todo_repo or TodoRepository(self.session)
                    self._task_return_repo = self._task_return_repo or TaskReturnRepository(self.session)
                    self._audit_repo = self._audit_repo or AuditEventRepository(self.session)
                    self._variable_repo = self._variable_repo or VariableNamespaceRepository(self.session)
                await self._run_phases()
                self._active_session = None
        finally:
            # M14 (W3.14): always reset tick-scoped project selection after the tick.
            self._tick_project_id = None
            # C.3: clear tenant context after tick so thread workers in
            # subsequent ticks do not inherit a stale project_id.
            _reset_tenant(_tenant_token)
        elapsed = time.monotonic() - start
        self._tick_metrics["tick_duration_ms"] = elapsed * 1000
        if self._daemon_state is not None:
            self._daemon_state["tick_metrics"] = dict(self._tick_metrics)
        if self._checkpointer is not None:
            state = {
                "_tick_state": dict(self._tick_state),
                "_applied_decision_keys": list(self._applied_decisions.keys()),
                "_pushed_work_keys": list(self._pushed_work.keys()),
                "_push_retry_count": dict(self._push_retry_count),
            }
            self._checkpointer.put(tick_id, state)
            self._checkpointer.put("last_tick", state)
        return self._tick_metrics

    async def _run_phases(self) -> None:
        await self._run_phase_range(0, len(PHASE_ORDER))

    async def _run_phase_range(self, start: int, end: int) -> None:
        for phase_name in PHASE_ORDER[start:end]:
            phase_fn = getattr(self, f"_phase_{phase_name}")
            try:
                logger.info("Phase started: %s", phase_name)
                await phase_fn()
                logger.info("Phase completed: %s", phase_name)
                self._tick_metrics["phases_completed"] += 1
            except Exception as exc:
                logger.error("Phase %s raised %s: %s", phase_name, type(exc).__name__, exc)

    def _clear_repos(self) -> None:
        self._active_session = None
        self._todo_repo = None
        self._task_return_repo = None
        self._audit_repo = None
        self._variable_repo = None

    async def _commit_tick_session(self, session: Any) -> None:
        try:
            await session.commit()
        except Exception as exc:
            logger.error(
                "Failed to commit tick session (writes lost): %s",
                exc,
                exc_info=True,
            )
            with contextlib.suppress(Exception):
                await session.rollback()

    async def _bounded_to_thread(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        async with self._to_thread_semaphore:
            return await asyncio.to_thread(fn, *args, **kwargs)

    async def run_forever(self, interval: float = 1.0) -> None:
        self._running = True
        # B3.1.5: re-hydrate crash-interrupted dispatches before the tick
        # loop starts so a writer restart does not lose in-flight work.
        with contextlib.suppress(Exception):
            await self._resume_interrupted_dispatches()
        try:
            while self._running:
                await self.tick()
                if self._inbound_queue is not None:
                    await self._drain_inbound_queue()
                if not self._running:
                    break
                try:
                    await asyncio.wait_for(self._wake_event.wait(), timeout=interval)
                except TimeoutError:
                    pass
                finally:
                    self._wake_event.clear()
        except Exception as exc:
            logger.error("EventLoop run_forever exited with error: %s", exc)
            raise
        finally:
            logger.error("EventLoop run_forever stopped; no further ticks will occur")

    async def _drain_inbound_queue(self) -> None:
        """B3.1.3 Slice 5: drain the inbound :class:`WriteQueue` between ticks.

        Non-blocking: pulls every currently-buffered envelope via
        :meth:`WriteQueue.get_nowait` and returns the moment the queue is
        empty (``queue.Empty``). Each envelope is applied inside its own
        freshly-opened DB session so a failure in one envelope rolls back
        ONLY that envelope's writes; subsequent envelopes still commit.

        In no-DB mode (``self._session_factory is None``) envelopes are
        dropped (logged) — the queue still empties, so the producer is never
        blocked by a consumer that has no DB to apply to.
        """
        if self._inbound_queue is None:
            return
        while True:
            try:
                envelope = self._inbound_queue.get_nowait()
            except _stdqueue.Empty:
                return
            if self._session_factory is None:
                logger.warning(
                    "Dropping inbound envelope (no DB session factory): topic=%s payload_keys=%s",
                    envelope.topic,
                    list(envelope.payload.keys()),
                )
                continue
            try:
                async with self._session_factory() as session:
                    try:
                        await self._apply_envelope(envelope, session)
                        await session.commit()
                    except Exception:
                        logger.exception(
                            "Failed to apply inbound envelope: topic=%s",
                            envelope.topic,
                        )
                        with contextlib.suppress(Exception):
                            await session.rollback()
            except Exception:
                # The session factory itself blew up (rare); log + continue so
                # one bad envelope does not kill the drain loop.
                logger.exception(
                    "Session factory error during inbound drain: topic=%s",
                    envelope.topic,
                )

    async def _apply_envelope(self, envelope: Any, session: Any) -> None:
        """B3.1.3 Slice 5 STUB envelope handler.

        Routes by ``envelope.topic`` to the appropriate repository method.
        This is a deliberately thin stub — real per-topic handlers (full
        upsert semantics, validation, audit-event emission) land in later
        slices. Today only ``todo.upsert`` is routed; unknown topics are
        logged and otherwise ignored so the drain never crashes on a
        future-introduced topic it doesn't yet know about.
        """
        topic = envelope.topic
        payload = envelope.payload
        if topic == "todo.upsert":
            if self._todo_repo is not None:
                await self._todo_repo.create(payload)
        else:
            logger.info(
                "No stub handler for inbound envelope topic=%s; ignoring",
                topic,
            )
        logger.info("Applied inbound envelope: topic=%s", topic)

    def wake(self) -> None:
        """Interrupt the idle interval so newly available work is claimed now."""
        self._wake_event.set()

    def stop(self) -> None:
        self._running = False
        self.wake()

    async def shutdown(self) -> None:
        """Stop ticking and drain all in-flight background tasks.

        A3: cancels + awaits the fire-and-forget benchmark/trace tasks so the
        process does not exit with running tasks (which would emit
        "Task was destroyed but it is pending" warnings and could lose telemetry
        writes mid-flush). Safe to call concurrently with task creation — the
        drain reads a snapshot under ``_bg_tasks_lock``.
        """
        self._running = False
        self.wake()
        await self._drain_background_tasks()

    def get_available_tools(self) -> list[str]:
        if self._mcp_tool_registry is None:
            return []
        return self._mcp_tool_registry.tool_names()

    async def _phase_load_config_snapshot(self) -> None:
        import copy

        self._config_snapshot = copy.deepcopy(self.config)
        if self._variable_repo is not None and self._active_session is not None:
            shared_vars = await self._variable_repo.load_vars_for_project(None)
            if shared_vars:
                self._config_snapshot["shared_vars"] = shared_vars

    def _select_tick_project_id(self) -> str | None:
        """M14 (W3.14): select ONE project per tick and return its id.

        Called once at the start of tick(); the result is stored in
        self._tick_project_id and shared across all phases within that tick.
        All phases must read self._tick_project_id directly — never call
        select_project() independently inside a phase.
        """
        if self._project_manager is None:
            return None
        project = self._project_manager.select_project()
        project_id = project.project_id if project is not None else None
        # Rebuild the Ansible collections env when the active project changes
        # so the next playbook run uses the new project's .gludd/collections/.
        if project_id != self._last_ansible_env_project_id:
            self._rebuild_ansible_env_for_project(project_id)
            self._last_ansible_env_project_id = project_id
        return project_id

    def _resolve_project_root_for_collections(self, project_id: str | None) -> str | None:
        """Resolve the directory whose ``.gludd/collections/`` holds the
        project-local Ansible content.

        Looks up the per-project workspace (``self._project_workspace``) and
        returns its ``repo_dir`` (where the project repo — and its
        ``.gludd/`` — is cloned). Falls back to the workspace ``root`` when
        ``repo_dir`` is unavailable. Returns ``None`` when no project is
        selected or no workspace is registered — the resolver then yields
        bundled-only.
        """
        if project_id is None:
            return None
        workspaces = self._project_workspace if isinstance(self._project_workspace, dict) else None
        if not workspaces or project_id not in workspaces:
            return None
        ws = workspaces[project_id]
        # repo_dir is where the project repo (with its .gludd/) lives.
        repo_dir = getattr(ws, "repo_dir", None)
        if repo_dir is not None:
            try:
                return str(repo_dir)
            except Exception:
                pass
        # Fallback: workspace root (test/standalone setups may host .gludd/
        # directly at the workspace root rather than under repo/).
        root = getattr(ws, "root", None)
        return str(root) if root is not None else None

    def _rebuild_ansible_env_for_project(self, project_id: str | None) -> None:
        """Re-resolve Ansible collections paths for a new active project.

        Called when ``_select_tick_project_id`` detects the active project has
        changed. Resolves the new project's root, re-runs the 3-tier resolver,
        and invokes the daemon-supplied ``_ansible_env_updater`` callback so
        ``app.state._ansible_env`` / ``app.state._collections_paths`` and the
        adapter's ``_default_env`` are all rebound. Also updates the local
        adapter directly when no callback is wired (test/standalone use).
        """
        from general_ludd.ansible.paths import (
            resolve_collections_paths,
            to_ansible_env,
        )

        project_root = self._resolve_project_root_for_collections(project_id)
        entries = resolve_collections_paths(project_root)
        env = to_ansible_env(entries)
        if self._ansible_env_updater is not None:
            try:
                self._ansible_env_updater(entries, env)
            except Exception as exc:
                logger.warning(
                    "ansible_env_updater callback failed for project %s: %s",
                    project_id,
                    exc,
                )
            if self._runner is not None:
                with contextlib.suppress(Exception):
                    self._runner._default_env = dict(env)
        logger.info(
            "Rebuilt Ansible collections env for project %s (%d tier(s))",
            project_id,
            len(entries),
        )

    def _estimated_dispatch_cost(self, item_count: int) -> float:
        budget_cfg = self.config.get("budget", {}) if isinstance(self.config, dict) else {}
        per_job = budget_cfg.get("per_dispatch_usd", 0.01) if isinstance(budget_cfg, dict) else 0.01
        try:
            return float(per_job) * max(0, int(item_count))
        except (TypeError, ValueError):
            return 0.0

    async def _phase_claim_unreviewed_task_returns(self) -> None:
        if self._task_return_repo is None:
            return
        project_id = self._tick_project_id
        claimed = await self._task_return_repo.claim_unreviewed(project_id=project_id)
        self._tick_state["claimed_returns"] = claimed

    async def _phase_dispatch_return_review_jobs(self) -> None:
        claimed = self._tick_state.get("claimed_returns", [])
        if self._budget_guard is not None:
            check = self._budget_guard.check_all_limits(estimated_cost=self._estimated_dispatch_cost(len(claimed)))
            if not check["allowed"]:
                logger.warning("Budget exceeded, skipping return review dispatch: %s", check["reason"])
                self._tick_metrics["returns_reviewed"] = 0
                return
        for tr in claimed:
            await self._dispatch_review_job(tr)
        self._tick_metrics["returns_reviewed"] = len(claimed)

    async def _release_review_claim(self, tr: Any, reason: str = "") -> None:
        # S2: release a review claim back to 'created' so the task return
        # does not strand at 'claimed_for_review' forever. Optionally
        # block the associated todo when the release is due to a failure.
        if self._active_session is not None:
            with contextlib.suppress(Exception):
                tr.status = "created"
            if hasattr(tr, "updated_at"):
                with contextlib.suppress(Exception):
                    tr.updated_at = datetime.now(UTC)
            with contextlib.suppress(Exception):
                await self._active_session.flush()
            logger.warning(
                "Released review claim for return %s: %s",
                getattr(tr, "return_id", "?"),
                reason or "unknown",
            )

    async def _dispatch_review_job(self, tr: Any) -> None:
        # H4 (W3.2): when a gateway-backed reviewer is wired, review in-process
        # and route the decision through apply_decision. Failure escalates the
        # todo — it is never silently marked complete / passed through.
        #
        # G11: consensus review is an alternative path gated behind the
        # ``consensus_review.enabled`` config flag. When enabled AND a
        # ConsensusReviewer is wired, the multi-agent debate replaces the
        # single-model reviewer for this tick.
        _has_standard = self._reviewer is not None
        _has_consensus = (
            isinstance(self.config, dict)
            and bool(self.config.get("consensus_review", {}).get("enabled", False))
            and self._consensus_reviewer is not None
        )
        review_cfg = self.config.get("review", {}) if isinstance(self.config, dict) else {}
        if (_has_standard or _has_consensus) and self._active_session is not None and self._todo_repo is not None:
            in_process_timeout = float(review_cfg.get("in_process_timeout", 600.0))
            try:
                await asyncio.wait_for(
                    self._review_in_process(tr),
                    timeout=in_process_timeout,
                )
            except TimeoutError:
                await self._release_review_claim(
                    tr,
                    f"in-process review timeout after {in_process_timeout:.0f}s",
                )
            return
        project_id_val = getattr(tr, "project_id", None)
        if not isinstance(project_id_val, str):
            project_id_val = None
        job = JobSpec(
            job_id=f"REVIEW-{tr.return_id}",
            return_id=tr.return_id,
            todo_id=tr.todo_id,
            playbook="return_review.yml",
            queue=_safe_str(tr, "queue", "model") or "model",
            work_type="review",
            resource_profile="ai_heavy",
            plan_artifact=_safe_str(tr, "plan_artifact"),
            project_id=project_id_val,
        )
        review_cfg = self.config.get("review", {}) if isinstance(self.config, dict) else {}
        if self._runner is not None:
            dirs = self._runner.prepare_job_dirs(job.job_id)
            self._runner.write_vars(
                job.job_id,
                job_vars={
                    "job_id": job.job_id,
                    "todo_id": job.todo_id,
                    "return_id": job.return_id,
                    "queue": job.queue,
                    "work_type": job.work_type,
                },
                shared_vars=None,
            )
            review_playbook_timeout = float(review_cfg.get("playbook_timeout", 600.0))
            try:
                runner_result = await asyncio.wait_for(
                    self._bounded_to_thread(
                        self._runner.run_playbook,
                        playbook_name="return_review.yml",
                        private_data_dir=dirs["root"],
                    ),
                    timeout=review_playbook_timeout,
                )
                if runner_result:
                    rc = runner_result.get("rc", 0)
                    if rc != 0:
                        await self._release_review_claim(
                            tr,
                            f"playbook rc={rc} (job_id={job.job_id})",
                        )
                    else:
                        logger.info(
                            "Review playbook completed for return %s (job_id=%s)",
                            getattr(tr, "return_id", "?"),
                            job.job_id,
                        )
            except TimeoutError:
                await self._release_review_claim(
                    tr,
                    f"playbook timeout after {review_playbook_timeout:.0f}s (job_id={job.job_id})",
                )
            except Exception:
                await self._release_review_claim(
                    tr,
                    f"playbook failed (job_id={job.job_id})",
                )
            return
        if self._http_client is None:
            return
        review_http_timeout = float(review_cfg.get("http_timeout", 600.0))
        try:
            resp = await asyncio.wait_for(
                self._http_client.post(
                    f"{self.worker_base_url}/jobs/return-review",
                    json=job.model_dump(mode="json"),
                ),
                timeout=review_http_timeout,
            )
            status = getattr(resp, "status_code", 200)
            if status >= 400:
                await self._release_review_claim(
                    tr,
                    f"HTTP {status} from worker (job_id={job.job_id})",
                )
            else:
                await self._persist_review_response(tr, resp)
        except TimeoutError:
            await self._release_review_claim(
                tr,
                f"HTTP timeout after {review_http_timeout:.0f}s (job_id={job.job_id})",
            )

    def _resolve_repo_root(self, project_id: str | None) -> str | None:
        """Resolve the repository root for a project.

        Lookup priority:
          1. Per-project workspace repo_dir — when self._project_workspace is a
             dict of {pid: ProjectWorkspace} and the workspace's repo_dir is a
             real directory on disk (populated by materialize_project_workspace /
             git-clone at startup).
          2. self.config["repo_root"] — operator-configured fallback (also used
             as the daemon-level default set at startup for single-project use).
          3. None — completion_verifier will fail-closed (unverifiable ≠ verified).

        Does NOT fall through to os.getcwd() here; that default belongs at the
        config-injection site (daemon.py) so the intent is explicit and not
        silently inherited from the process cwd.
        """
        from pathlib import Path as _Path

        workspaces = self._project_workspace if isinstance(self._project_workspace, dict) else None
        if workspaces is not None and project_id is not None:
            ws = workspaces.get(project_id)
            if ws is not None and hasattr(ws, "repo_dir"):
                try:
                    rdir = _Path(str(ws.repo_dir))
                    if rdir.is_dir():
                        return str(rdir)
                except Exception:
                    pass
        return self.config.get("repo_root") if isinstance(self.config, dict) else None

    async def _review_in_process(self, tr: Any) -> None:
        from general_ludd.review.decision_applier import apply_decision

        review_cfg = self.config.get("review", {}) if isinstance(self.config, dict) else {}
        consensus_cfg = self.config.get("consensus_review", {}) if isinstance(self.config, dict) else {}
        if review_cfg.get("use_langgraph") and self._langgraph_reviewer is not None:
            effective_reviewer = self._langgraph_reviewer
        elif consensus_cfg.get("enabled", False) and self._consensus_reviewer is not None:
            effective_reviewer = self._consensus_reviewer
        else:
            assert self._reviewer is not None
            effective_reviewer = self._reviewer

        return_id = getattr(tr, "return_id", "")
        todo_id = getattr(tr, "todo_id", None)
        task_return = TaskReturn(
            return_id=return_id,
            todo_id=todo_id,
            job_id=getattr(tr, "job_id", None) or f"JOB-{return_id}",
            playbook=getattr(tr, "playbook", None) or "noop.yml",
            queue=_safe_str(tr, "queue", "model") or "model",
            work_type=_safe_str(tr, "work_type", "review") or "review",
            exit_code=int(getattr(tr, "exit_code", 0) or 0),
            result_summary=_safe_str(tr, "result_summary", "") or "",
        )
        try:
            decision = await self._bounded_to_thread(
                effective_reviewer.review_return,
                task_return,
                candidate_todos=[],
                artifacts=[],
            )
        except Exception as exc:
            # Reviewer itself failed — escalate, never silent pass/complete.
            logger.error("Reviewer raised for return %s: %s", return_id, exc)
            decision = TaskDecision(
                return_id=return_id,
                matched_todo_id=todo_id,
                decision="manual_hold",
                confidence=0.0,
                audit_notes=[f"Reviewer error: {exc}"],
            )
        assert self._todo_repo is not None
        assert self._active_session is not None
        try:
            _review_project_id = getattr(tr, "project_id", None) or None
            await apply_decision(
                decision,
                self._todo_repo,
                self._active_session,
                repo_root=self._resolve_repo_root(_review_project_id),
            )
            await self._active_session.flush()
        except Exception as exc:
            logger.error(
                "apply_decision failed for return %s (decision=%s): %s",
                return_id,
                getattr(decision, "decision", "?"),
                exc,
            )
            return
        if self._audit_repo is not None:
            try:
                await self._audit_repo.create(
                    event_type="return_reviewed",
                    entity_type="task_return",
                    entity_id=return_id,
                    project_id=getattr(tr, "project_id", None),
                    details=json.dumps(
                        {
                            "decision": decision.decision,
                            "confidence": decision.confidence,
                            "matched_todo_id": decision.matched_todo_id,
                        }
                    ),
                )
            except Exception:
                # Audit trail is best-effort; a write failure must not abort the
                # review (the decision is already applied). Log so a broken audit
                # sink is visible.
                logger.warning(
                    "Audit write failed for return_reviewed event %s",
                    return_id,
                    exc_info=True,
                )
        logger.info("In-process review for return %s -> %s", return_id, decision.decision)
        # Compaction feedback loop: feed review outcome into the adaptive controller
        # so compaction aggressiveness auto-tunes from accuracy signal.
        if self._compaction_controller is not None:
            _success = decision.decision == "complete"
            self._compaction_passed += 1 if _success else 0
            self._compaction_total += 1
            _sample = AccuracySample(
                passed=self._compaction_passed,
                total=self._compaction_total,
            )
            if self._compaction_level is None:
                _cfg = self.config.get("compaction", {}) if isinstance(self.config, dict) else {}
                _cfg_level = _cfg.get("level", 1) if _cfg.get("enabled") else 0
                self._compaction_level = _cfg_level
            _next = self._compaction_controller.compute(self._compaction_level, _sample)
            if _next != self._compaction_level:
                logger.info(
                    "Compaction level adjusted: %d -> %d (passed=%d total=%d)",
                    self._compaction_level,
                    _next,
                    self._compaction_passed,
                    self._compaction_total,
                )
                self._compaction_level = _next
            self._compaction_disabled = self._compaction_controller.disable_signaled(self._compaction_level, _sample)
            if self._compaction_disabled:
                logger.warning(
                    "Compaction disabled by adaptive controller (level=%d, passed=%d total=%d, rate=%.2f)",
                    self._compaction_level,
                    self._compaction_passed,
                    self._compaction_total,
                    _sample.rate or 0.0,
                )

    async def _persist_review_response(self, tr: Any, resp: Any) -> None:
        if self._task_return_repo is None:
            return
        try:
            body = getattr(resp, "json", None)
            if callable(body):
                data = await body()
            elif isinstance(resp, dict):
                data = resp
            else:
                data = getattr(resp, "body", None)
                if data is not None:
                    import json as _json

                    data = _json.loads(data)
                else:
                    return
            if not isinstance(data, dict):
                return
            decision = data.get("decision")
            if decision and self._active_session is not None:
                dm = TaskDecisionModel(
                    return_id=tr.return_id,
                    project_id=getattr(tr, "project_id", None),
                    matched_todo_id=getattr(tr, "todo_id", None),
                    decision=str(decision),
                    confidence=float(data.get("confidence", 0.0)),
                    evidence_refs=json.dumps(data.get("evidence_refs", [])),
                    audit_notes=json.dumps(data.get("audit_notes", [])),
                )
                self._active_session.add(dm)
                await self._active_session.flush()
                logger.info("Persisted decision for return %s: %s", tr.return_id, decision)
        except Exception as exc:
            logger.warning("Failed to persist review response for %s: %s", getattr(tr, "return_id", "?"), exc)

    async def _phase_evaluate_pid_controllers(self) -> None:
        queues_data = self._config_snapshot.get("queues", [])
        if not queues_data:
            return
        try:
            import psutil

            load_1, load_5, load_10 = psutil.getloadavg() if hasattr(psutil, "getloadavg") else (0.0, 0.0, 0.0)
            cpu_count = psutil.cpu_count(logical=True) or 1
            cpu_pct = psutil.cpu_percent(interval=0)
            mem = psutil.virtual_memory()
            disk = psutil.disk_usage("/")
            disk_free_pct = 100 - (disk.used / disk.total * 100) if disk.total > 0 else 100.0
            controller = LoadController(cpu_count=cpu_count)
            queues = [Queue(**q) if isinstance(q, dict) else q for q in queues_data]
            active_jobs = 0
            if self._todo_repo is not None and self._active_session is not None:
                try:
                    active_jobs = await self._todo_repo.count_active()
                except Exception:
                    # Best-effort load signal: a count failure falls back to 0
                    # (treated as idle). Log at debug so a broken query is visible
                    # without changing the fall-through behaviour.
                    logger.debug(
                        "count_active failed; using active_jobs=0",
                        exc_info=True,
                    )
            snapshot = LoadSnapshot(
                loadavg_1m=load_1,
                loadavg_5m=load_5,
                loadavg_10m=load_10,
                logical_cpu_count=cpu_count,
                cpu_percent=cpu_pct,
                memory_available_percent=mem.percent,
                disk_free_percent=disk_free_pct,
                active_jobs=active_jobs,
            )
            outputs = controller.evaluate_snapshot(snapshot, queues)
            self._tick_state["pid_outputs"] = outputs
        except Exception as exc:
            logger.debug("PID evaluation skipped: %s", exc)

    async def _phase_evaluate_rules(self) -> None:
        raw_rules = self.config.get("rules", [])
        rules = [r if isinstance(r, Rule) else Rule(**r) for r in raw_rules]
        # W4c: evaluate rules against the LIVE claimed todos for this tick (set by
        # _phase_claim_runnable_todos, which now runs first per PHASE_ORDER), not the
        # static self.config["todos"] — which is absent at runtime, so rules never fired.
        todos_ctx = self._tick_state.get("claimed_todos", [])
        all_results: list[dict[str, Any]] = []
        for todo_ctx in todos_ctx:
            context = {"todo": todo_ctx}
            actions = evaluate_rules(rules, context)
            if actions:
                all_results.append(
                    {
                        "todo_id": _safe_str(todo_ctx, "todo_id", ""),
                        "actions": [
                            {"rule_id": a.rule_id, "action_type": a.action_type, "params": a.params} for a in actions
                        ],
                    }
                )
        self._tick_state["rule_evaluation_results"] = all_results

    async def _phase_refill_task_buckets(self) -> None:
        if self._active_session is not None:
            reclaimed = await reclaim_expired_leases(self._active_session)
            self._tick_metrics["leases_reclaimed"] = reclaimed
        if self._todo_repo is not None and self._active_session is not None:
            await self._reap_stuck_todos()

    async def _phase_run_scheduler(self) -> None:
        """Promote due SCHEDULED todos (cron + one-shot) on each tick.

        DEFECT 0 fix: ``TodoScheduler`` was previously defined but never invoked,
        so SCHEDULED todos were never promoted and the cron feature was dead
        end-to-end. This phase runs BEFORE ``claim_runnable_todos`` in
        ``PHASE_ORDER`` so a todo promoted SCHEDULED→QUEUED (one-shot) or a freshly
        spawned cron child clone is claimable in the same tick. Uses the
        tick-scoped repo/session; ``tick()`` commits once after ``_run_phases``.
        """
        if self._todo_repo is None or self._active_session is None:
            return
        from general_ludd.event_loop.scheduler import TodoScheduler

        promoted, spawned = await TodoScheduler(self._todo_repo).tick()
        self._tick_metrics["scheduled_promoted"] = promoted
        self._tick_metrics["scheduled_spawned"] = spawned

    async def _phase_sdlc_gate(self) -> None:
        if not isinstance(self.config, dict):
            return
        sdlc_cfg = self.config.get("ai_sdlc", {})
        if not sdlc_cfg:
            return
        enforce = sdlc_cfg.get("enforce", False)
        stages = sdlc_cfg.get("pipeline_stages", {})
        results: dict[str, Any] = {
            "enforce": enforce,
            "stages_checked": 0,
            "stages_blocked": 0,
            "stage_results": {},
        }
        for stage_name, stage_spec in stages.items():
            if not isinstance(stage_spec, dict):
                continue
            entry_gates = stage_spec.get("entry_gates", {})
            exit_gates = stage_spec.get("exit_gates", {})
            entry_checks: list[str] = []
            exit_checks: list[str] = []
            stage_result: dict[str, object] = {
                "entry_passed": True,
                "exit_passed": True,
                "entry_checks": entry_checks,
                "exit_checks": exit_checks,
            }
            required_artifact_dir = stage_spec.get("artifact_dir")
            if required_artifact_dir:
                artifact_path = Path(required_artifact_dir)
                if not artifact_path.exists():
                    stage_result["entry_passed"] = False
                    entry_checks.append(f"artifact_dir missing: {required_artifact_dir}")
            for gate_name, gate_spec in entry_gates.items():
                if isinstance(gate_spec, dict) and gate_spec.get("required"):
                    stage_result["entry_passed"] = False
                    entry_checks.append(f"entry gate unsatisfied: {gate_name}")
            for gate_name, gate_spec in exit_gates.items():
                if isinstance(gate_spec, dict) and gate_spec.get("required"):
                    stage_result["exit_passed"] = False
                    exit_checks.append(f"exit gate unsatisfied: {gate_name}")
            if not stage_result["entry_passed"] or not stage_result["exit_passed"]:
                results["stages_blocked"] += 1
            results["stages_checked"] += 1
            results["stage_results"][stage_name] = stage_result
        self._tick_state["sdlc_gate_results"] = results
        if enforce and results["stages_blocked"] > 0:
            logger.warning(
                "SDLC gate: %d/%d stages blocked (enforce=true)",
                results["stages_blocked"],
                results["stages_checked"],
            )

    async def _phase_claim_runnable_todos(self) -> None:
        if self._todo_repo is None:
            return
        if (
            self._pause_controller is not None
            and self._tick_project_id is not None
            and self._pause_controller.is_paused("project", self._tick_project_id)
        ):
            logger.info("Project %s is paused — skipping claim", self._tick_project_id)
            self._tick_state["claimed_todos"] = []
            return
        project_id = self._tick_project_id
        if project_id is None and self._project_manager is not None:
            logger.warning("Claim skipped: no active project selected")
            self._tick_state["claimed_todos"] = []
            return

        # C21: compute the effective claim limit BEFORE the CAS claim so
        # todos are never marked ACTIVE beyond the system's dispatch capacity.
        # Floor cap and PID cap are evaluated here instead of releasing
        # excess ACTIVE todos back to QUEUED after the fact.
        effective_limit = 10
        currently_active = 0
        if self._todo_repo is not None and self._active_session is not None:
            try:
                currently_active = await self._todo_repo.count_active()
            except Exception:
                currently_active = 0

        if self._floor_controller is not None:
            floor_max = self._floor_controller.get_max_active()
            floor_claimable = max(0, floor_max - currently_active)
            effective_limit = min(effective_limit, floor_claimable)

        pid_outputs = self._tick_state.get("pid_outputs")
        if pid_outputs is not None and hasattr(pid_outputs, "desired_total_active_buckets"):
            pid_desired = pid_outputs.desired_total_active_buckets
            pid_claimable = max(0, pid_desired - currently_active)
            effective_limit = min(effective_limit, pid_claimable)

        if effective_limit <= 0:
            logger.debug(
                "Claim skipped: effective_limit=%d (floor=%s, pid=%s, active=%d)",
                effective_limit,
                self._floor_controller.get_max_active() if self._floor_controller else "none",
                getattr(pid_outputs, "desired_total_active_buckets", "none") if pid_outputs else "none",
                currently_active,
            )
            self._tick_state["claimed_todos"] = []
            return

        claimed = await self._todo_repo.claim_runnable(limit=effective_limit, project_id=project_id)
        # A stale todo requeued during the refill phase must not be reclaimed
        # immediately in the same tick.  Leave it queued for the next worker
        # tick, releasing the transient claim lease as part of the rollback.
        reaped_ids = self._tick_state.get("reaped_todo_ids", set())
        if reaped_ids:
            retained: list[Any] = []
            holder = f"tick-{self._total_ticks}"
            for todo in claimed:
                if todo.todo_id not in reaped_ids:
                    retained.append(todo)
                    continue
                try:
                    await self._todo_repo.transition(
                        todo.todo_id,
                        TodoStatus.QUEUED,
                        todo.version,
                        project_id=project_id,
                    )
                    queue = _safe_str(todo, "queue", "core") or "core"
                    if self._active_session is not None:
                        await release_lease(
                            self._active_session,
                            f"{queue}:{todo.todo_id}",
                            holder_id=holder,
                        )
                except Exception as exc:
                    logger.warning(
                        "Failed to defer reaped todo %s to next tick: %s",
                        todo.todo_id,
                        exc,
                    )
            claimed = retained
        self._tick_state["claimed_todos"] = claimed
        # ── Resource estimation: set estimated_cost_usd per claimed todo ──
        if claimed and self._active_session is not None:
            for todo in claimed:
                todo.estimated_cost_usd = _compute_todo_estimate(todo)
        # H15 (W2.5): record a bucket lease per claimed todo so a crashed tick's
        # work can be reclaimed once the lease expires.
        if claimed and self._active_session is not None:
            from general_ludd.event_loop.lease import acquire_leases_batch

            holder = f"tick-{self._total_ticks}"
            bucket_keys = []
            for todo in claimed:
                bucket_key = _safe_str(todo, "queue", "core") or "core"
                todo_id = _safe_str(todo, "todo_id", "") or ""
                bucket_keys.append(f"{bucket_key}:{todo_id}")
            try:
                await acquire_leases_batch(
                    self._active_session,
                    bucket_keys,
                    holder_id=holder,
                    project_id=project_id,
                )
            except Exception as exc:
                logger.warning(
                    "Batch lease acquisition failed for %d todos: %s",
                    len(bucket_keys),
                    exc,
                    exc_info=True,
                )

    async def _trim_claimed_to_pid_cap(self, claimed: list[Any]) -> list[Any]:
        pid_outputs = self._tick_state.get("pid_outputs")
        desired = getattr(pid_outputs, "desired_total_active_buckets", None)
        if not isinstance(desired, int) or len(claimed) <= desired:
            return claimed
        if self._active_session is None or self._todo_repo is None:
            return claimed

        keep_count = max(0, desired)
        kept = list(claimed[:keep_count])
        released = list(claimed[keep_count:])
        for todo in released:
            queue = _safe_str(todo, "queue", "core") or "core"
            todo_id = _safe_str(todo, "todo_id", "") or ""
            version = getattr(todo, "version", None)
            if todo_id and isinstance(version, int):
                try:
                    await self._todo_repo.transition(todo_id, TodoStatus.QUEUED, version)
                except Exception as exc:
                    logger.warning(
                        "PID cap release failed to requeue todo %s: %s",
                        todo_id,
                        exc,
                    )
                    continue
            if todo_id:
                try:
                    await release_lease(self._active_session, f"{queue}:{todo_id}")
                except Exception as exc:
                    logger.warning(
                        "PID cap release failed to delete lease for todo %s: %s",
                        todo_id,
                        exc,
                    )
        self._tick_state["claimed_todos"] = kept
        return kept

    async def _phase_dispatch_execute_jobs(self) -> None:
        claimed = list(self._tick_state.get("claimed_todos", []))
        claimed = await self._trim_claimed_to_pid_cap(claimed)
        if self._budget_guard is not None:
            check = self._budget_guard.check_all_limits(estimated_cost=self._estimated_dispatch_cost(len(claimed)))
            if not check["allowed"]:
                logger.warning("Budget exceeded, skipping execute dispatch: %s", check["reason"])
                self._tick_metrics["todos_dispatched"] = 0
                return

        # W(#23): wire Scheduler.plan() to determine concurrency-safe batches.
        # Each batch may run concurrently (asyncio.gather) when a session_factory
        # is available — each gathered coroutine opens its OWN async session so
        # SQLAlchemy's "no concurrent flush on one session" rule is never violated.
        # Falls back to sequential dispatch when no session_factory (e.g. tests
        # that pass a bare session=...).
        dispatch_count = await self._dispatch_jobs_via_scheduler(claimed)
        self._tick_metrics["todos_dispatched"] = dispatch_count

    async def _dispatch_jobs_via_scheduler(self, todos: list[Any]) -> int:
        """Dispatch todos ordered + grouped by Scheduler.plan().

        Concurrent batches: if self._session_factory is set, each job in a batch
        opens its own async session (session-per-coroutine) and is gathered with
        all other jobs in the same batch.  This is safe because:
          - load_shared_vars is a pure READ (no write contention)
          - persist_task_return writes to an independent row per job (no flush collision)

        Sequential fallback: if no session_factory (bare-session tests, no-DB mode),
        jobs run sequentially in scheduler-plan order.
        """
        from general_ludd.scheduling.scheduler import CycleError, Scheduler, WorkItem

        if not todos:
            return 0

        # Build WorkItems.  Default each todo to its own exclusive resource
        # (its todo_id) so nothing falsely parallelizes unless we can determine
        # otherwise.  Todos sharing the same queue share a resource label so the
        # scheduler correctly serializes queue-exclusive operations.
        items: list[WorkItem] = []
        for todo in todos:
            todo_id = str(_safe_str(todo, "todo_id", "") or id(todo))
            queue = _safe_str(todo, "queue", "core") or "core"
            if queue == "self_update":
                # Phase-2 Step 5: self_update-queue todos serialise on the
                # tier-specific resource label (self_update:code for CODE,
                # self_update:config for CONFIG/SCAFFOLD) so two code-tier
                # source edits never run concurrently. Tier is reconstructed
                # from the todo's `tier:` tag written by priority.to_todo_spec.
                items.append(_self_update_work_item_from_todo(todo, todo_id))
                continue
            # Use the todo_id as a unique resource so each job is self-exclusive
            # by default; add queue as a shared resource only when queue-exclusive
            # serialization is explicitly needed (opt-in via config).
            queue_exclusive = self._config_snapshot.get("scheduler_queue_exclusive", False)
            resources: frozenset[str] = (
                frozenset({f"queue:{queue}", f"todo:{todo_id}"}) if queue_exclusive else frozenset({f"todo:{todo_id}"})
            )
            items.append(WorkItem(id=todo_id, resources=resources))

        # Build id→todo map for lookup.
        todo_map: dict[str, Any] = {}
        for todo in todos:
            tid = str(_safe_str(todo, "todo_id", "") or id(todo))
            todo_map[tid] = todo

        try:
            batches = Scheduler().plan(items)
        except (CycleError, ValueError) as exc:
            # Fail-closed on scheduler error: fall back to sequential.
            logger.warning("Scheduler.plan() failed (%s); falling back to sequential", exc)
            batches = [[str(_safe_str(t, "todo_id", "") or id(t))] for t in todos]

        dispatch_count = 0
        can_concurrent = self._session_factory is not None

        for batch_ids in batches:
            batch_todos = [todo_map[bid] for bid in batch_ids if bid in todo_map]
            if not batch_todos:
                continue

            if can_concurrent and len(batch_todos) > 1:
                # Concurrent: each coroutine opens its own session.
                logger.info(
                    "Scheduler batch: %d jobs concurrent (session-per-coroutine, max_concurrent=%d)",
                    len(batch_todos),
                    self._dispatch_semaphore._value,
                )
                tasks = [asyncio.ensure_future(self._dispatch_with_semaphore(t)) for t in batch_todos]
                batch_timeout = min(300.0 * len(batch_todos), 1800.0)
                try:
                    results = await asyncio.wait_for(
                        asyncio.gather(*tasks, return_exceptions=True),
                        timeout=batch_timeout,
                    )
                except TimeoutError:
                    logger.error(
                        "Concurrent dispatch batch timed out after %.0fs; cancelling %d pending job(s)",
                        batch_timeout,
                        sum(1 for t in tasks if not t.done()),
                    )
                    for t in tasks:
                        if not t.done():
                            t.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    continue
                for res in results:
                    if isinstance(res, Exception):
                        logger.error("Concurrent job dispatch raised: %s", res)
                    else:
                        dispatch_count += 1
            elif can_concurrent and len(batch_todos) == 1:
                try:
                    async with self._dispatch_semaphore:
                        await self._dispatch_execute_job_isolated(batch_todos[0])
                    dispatch_count += 1
                except Exception as exc:
                    logger.error("Job dispatch raised: %s", exc)
            else:
                # Sequential fallback (no session_factory).
                for todo in batch_todos:
                    try:
                        await self._dispatch_execute_job(todo)
                        dispatch_count += 1
                    except Exception as exc:
                        logger.error("Sequential job dispatch raised: %s", exc)

        return dispatch_count

    async def _dispatch_with_semaphore(self, todo: Any) -> None:
        async with self._dispatch_semaphore:
            await self._dispatch_execute_job_isolated(todo)

    async def _dispatch_execute_job_isolated(self, todo: Any) -> None:
        """Dispatch a single execute job using its OWN async session.

        Opens a fresh session from self._session_factory for every DB interaction
        (load_shared_vars, persist_task_return) so this coroutine is safe to run
        concurrently with other _dispatch_execute_job_isolated calls in the same
        asyncio.gather() batch — no shared _active_session is touched.

        # Sandbox wiring: BEFORE the agent's first tool call we resolve its
        # PermissionSpec (from config — a per-queue default) and ask the host's
        # SandboxBackend to apply it. After the job completes (or raises) the
        # backend's release() runs in the finally block. The whole lifecycle is
        # wrapped in try/except so a sandbox bug never wedges the daemon — a
        # failure logs loudly + dispatches with a "no sandbox" warning.
        """
        assert self._session_factory is not None

        sandbox_handle = await self._sandbox_apply_for_todo(todo)
        try:
            async with self._session_factory() as job_session:
                if sandbox_handle is not None and self._sandbox_executor is not None:
                    self._sandbox_executor.execute(
                        f"dispatch:{getattr(todo, 'todo_id', '?')}:{_safe_str(todo, 'work_type', 'unknown')}",
                        workdir=self._resolve_repo_root(getattr(todo, "project_id", None)),
                    )
                job_variable_repo = VariableNamespaceRepository(job_session)
                job_task_return_repo = TaskReturnRepository(job_session)
                await self._dispatch_execute_job(
                    todo,
                    _variable_repo_override=job_variable_repo,
                    _task_return_repo_override=job_task_return_repo,
                    _session_override=job_session,
                )
                try:
                    await job_session.commit()
                except Exception as exc:
                    logger.warning(
                        "Failed to commit isolated job session for %s: %s",
                        getattr(todo, "todo_id", "?"),
                        exc,
                    )
        finally:
            if sandbox_handle is not None:
                await self._sandbox_release(sandbox_handle)

    async def _sandbox_apply_for_todo(self, todo: Any) -> Any | None:
        """Resolve this todo's PermissionSpec and apply the host sandbox.

        Production daemon wiring supplies a durable attestation store and uses
        the fail-closed admission path.  The legacy fail-open behavior remains
        only for explicitly unwired EventLoop instances used by older callers.
        Returns a pinned dispatch lease or a legacy opaque handle.
        """
        if self._sandbox_attestation_store is not None:
            return await self._sandbox_admit_for_todo(todo)
        try:
            from general_ludd.security.sandboxes import SandboxTarget, detect

            backend = detect.auto()
            if backend is None:
                logger.warning(
                    "No sandbox backend for todo %s — dispatching UNSANDBOXED",
                    _safe_str(todo, "todo_id", "?"),
                )
                return None
            spec = self._resolve_permission_spec(todo)
            if spec is None:
                logger.info(
                    "No PermissionSpec resolved for todo %s — skipping sandbox",
                    _safe_str(todo, "todo_id", "?"),
                )
                return None
            target = SandboxTarget(
                directory=self._resolve_repo_root(getattr(todo, "project_id", None)),
            )
            handle = await self._bounded_to_thread(backend.apply, spec, target)
            findings = await self._bounded_to_thread(backend.verify, spec, handle)
            fails = [f for f in findings if f.severity == "fail"]
            if fails or not handle.applied:
                logger.error(
                    "Sandbox verify reported %d fail / %d warn findings for todo "
                    "%s (handle.applied=%s) — proceeding UNSANDBOXED: %s",
                    len(fails),
                    sum(1 for f in findings if f.severity == "warn"),
                    _safe_str(todo, "todo_id", "?"),
                    handle.applied,
                    [f.message for f in fails],
                )
            else:
                logger.info(
                    "Sandbox applied for todo %s via %s (token=%s, %d ok findings)",
                    _safe_str(todo, "todo_id", "?"),
                    getattr(backend, "name", "?"),
                    handle.token,
                    sum(1 for f in findings if f.severity == "ok"),
                )
            return handle
        except Exception as exc:
            logger.error(
                "Sandbox apply raised for todo %s — dispatching UNSANDBOXED: %s",
                _safe_str(todo, "todo_id", "?"),
                exc,
                exc_info=True,
            )
            return None

    async def _sandbox_admit_for_todo(self, todo: Any) -> Any:
        """Apply, independently observe, durably attest, then admit one todo.

        This is the production fail-closed path.  The resolved profile is a
        frozen value supplied at daemon construction, so this dispatch remains
        pinned to one policy hash while a replacement worker/profile is prepared
        and promoted.  Legacy test-only loops without a durable store retain the
        older helper above until their caller explicitly wires admission.
        """

        from general_ludd.security.sandboxes import SandboxTarget, detect
        from general_ludd.security.sandboxes.attestation import (
            RuntimeSandboxObservation,
        )
        from general_ludd.security.sandboxes.dispatch import (
            DurableSandboxDispatchGuard,
            SandboxDispatchIdentity,
            SandboxDispatchLease,
            unavailable_observation,
        )

        resolved = self._sandbox_profile
        if resolved is None:
            raise RuntimeError("sandbox dispatch denied: no resolved sandbox profile")
        store = self._sandbox_attestation_store
        if store is None:
            raise RuntimeError("sandbox dispatch denied: no durable attestation store")

        project_id = (_safe_str(todo, "project_id", "") or "").strip()
        work_item_id = (_safe_str(todo, "todo_id", "") or "").strip()
        if not project_id or not work_item_id:
            raise RuntimeError("sandbox dispatch denied: tenant/project and work-item identity are required")
        identity = SandboxDispatchIdentity(
            project_id=project_id,
            work_item_id=work_item_id,
            agent_id="event-loop",
            tenant_id=project_id,
            correlation_id=f"sandbox:{work_item_id}",
        )
        guard = DurableSandboxDispatchGuard(
            resolved=resolved,
            store=store,
        )
        if self._sandbox_executor is None:
            await guard.deny_unavailable(identity)
        backend = detect.auto()
        if backend is None:
            await guard.deny_unavailable(identity)

        backend_name = str(getattr(backend, "name", ""))
        spec = self._resolve_permission_spec(todo)
        if spec is None:
            await guard.deny_unavailable(identity, backend=backend_name)
        target = SandboxTarget(directory=self._resolve_repo_root(project_id))

        try:
            handle = await self._bounded_to_thread(backend.apply, spec, target)
        except Exception:
            logger.exception(
                "Sandbox apply failed for todo %s; denying dispatch",
                work_item_id,
            )
            await guard.deny_unavailable(identity, backend=backend_name)

        try:
            findings = await self._bounded_to_thread(backend.verify, spec, handle)
            observe_runtime = getattr(backend, "observe_runtime", None)
            if not callable(observe_runtime):
                observation = unavailable_observation(
                    resolved,
                    backend=backend_name,
                )
            else:
                raw_observation = await self._bounded_to_thread(
                    observe_runtime,
                    spec,
                    handle,
                    resolved,
                )
                observation = RuntimeSandboxObservation.model_validate(raw_observation)

            verification_failed = any(getattr(finding, "severity", None) == "fail" for finding in findings)
            if (
                verification_failed
                or not bool(getattr(handle, "applied", False))
                or observation.backend != backend_name
            ):
                observation = observation.model_copy(update={"applied": False})
        except Exception:
            logger.exception(
                "Sandbox observation failed for todo %s; denying dispatch",
                work_item_id,
            )
            await self._sandbox_release_backend(backend, handle)
            await guard.deny_unavailable(identity, backend=backend_name)

        try:
            attestation = await guard.attest(identity, observation)
        except BaseException:
            await self._sandbox_release_backend(backend, handle)
            raise

        logger.info(
            "Sandbox dispatch admitted for todo %s under %s via %s (attestation=%d)",
            work_item_id,
            attestation.policy_version,
            attestation.effective_backend,
            attestation.sequence,
        )
        return SandboxDispatchLease(
            backend=backend,
            handle=handle,
            attestation=attestation,
        )

    async def _sandbox_release_backend(self, backend: Any, handle: Any) -> None:
        """Release the exact backend selected for this admission attempt."""

        try:
            await self._bounded_to_thread(backend.release, handle)
        except Exception as exc:
            logger.warning(
                "Sandbox release raised (token=%s): %s",
                getattr(handle, "token", "?"),
                exc,
            )

    async def _sandbox_release(self, handle: Any) -> None:
        """Release a sandbox handle; best-effort + fail-open."""
        try:
            from general_ludd.security.sandboxes import detect
            from general_ludd.security.sandboxes.dispatch import SandboxDispatchLease

            if isinstance(handle, SandboxDispatchLease):
                await self._sandbox_release_backend(handle.backend, handle.handle)
                return

            backend = detect.auto()
            if backend is None or handle is None:
                return
            await self._bounded_to_thread(backend.release, handle)
        except Exception as exc:
            logger.warning(
                "Sandbox release raised (token=%s): %s",
                getattr(handle, "token", "?"),
                exc,
            )

    def _resolve_permission_spec(self, todo: Any) -> Any | None:
        """Resolve a PermissionSpec for this todo from config.

        Effective scope = ``human ∩ agent ∩ requested``:

        1. ``agent_spec`` — the per-queue ``permission_spec`` block in
           ``self.config`` (``queues[].permission_spec``).
        2. ``human_spec`` — the active human user's spec. Resolved from
           ``self._human_spec`` (set per-session by the daemon) or the
           default human role from ``self.config['default_human_role']``
           (default ``human-operator``). See
           :func:`general_ludd.security.permissions.default_human_spec`.
        3. The two are intersected via
           :meth:`PermissionSpecParser.intersection`. The result is what
           the dispatched subagent actually runs with — never more.

        Returns ``None`` if the security module is unavailable or no queue
        spec is configured (matching pre-existing unscoped behavior).
        """
        try:
            from general_ludd.security.permissions import (
                Capability,
                PermissionSpec,
                PermissionSpecParser,
                default_human_spec,
            )
        except Exception:
            return None
        queue = _safe_str(todo, "queue", "core") or "core"
        queues_cfg = self.config.get("queues", []) if isinstance(self.config, dict) else []
        spec_cfg: dict[str, Any] | None = None
        for q in queues_cfg:
            if isinstance(q, dict) and q.get("name") == queue:
                spec_cfg = q.get("permission_spec")
                break
        if spec_cfg is None:
            return None
        caps = [
            Capability(
                resource=str(c.get("resource", "file:repo")),
                actions=list(c.get("actions") or []),
                constraints=dict(c.get("constraints") or {}),
            )
            for c in (spec_cfg.get("capabilities") or [])
        ]
        denied = [
            Capability(
                resource=str(c.get("resource", "file:repo")),
                actions=list(c.get("actions") or []),
                constraints=dict(c.get("constraints") or {}),
            )
            for c in (spec_cfg.get("denied") or [])
        ]
        agent_spec = PermissionSpec(
            agent_type=f"{queue}-{getattr(todo, 'todo_id', 'agent')}",
            capabilities=caps,
            denied=denied,
        )

        human_spec = getattr(self, "_human_spec", None)
        if human_spec is None and isinstance(self.config, dict):
            role = self.config.get("default_human_role") or "human-operator"
            try:
                human_spec = default_human_spec(role)
            except Exception:
                human_spec = None
        if human_spec is None:
            return agent_spec
        return PermissionSpecParser.intersection(human_spec, agent_spec)

    async def _resolve_human_input_for_todo(self, todo_id: str) -> str | None:
        """Return the resolution text of the most-recently-resolved HumanTodo
        that names this todo as its parent (``parent_agent_todo_id``).

        Injected into the next dispatch's extravars as ``human_input`` so the
        agent receives the human's response to a blocker it raised. Returns
        ``None`` when no resolved human-todo exists for this parent (the
        common case — most todos never block on a human).

        Only terminal-and-done human-todos are surfaced (dismissed todos
        cancel the parent agent todo, so it never re-dispatches).

        E12: replaced the Python-side filter-over-all-rows pattern with a
        SQL-side WHERE parent_agent_todo_id=? AND status='done' LIMIT 1 query
        via HumanTodoRepository.get_done_for_parent.
        """
        factory = self._session_factory
        if factory is None:
            return None
        try:
            from general_ludd.db.repository import HumanTodoRepository
        except Exception:
            return None
        try:
            async with factory() as session:
                repo = HumanTodoRepository(session)
                top = await repo.get_done_for_parent(todo_id)
                if top is None:
                    return None
                return top.human_resolution
        except Exception as exc:
            logger.warning(
                "could not resolve human_input for todo %s: %s",
                todo_id,
                exc,
            )
            return None

    def _get_rule_overrides_for_todo(self, todo: Any) -> dict[str, Any]:
        results = self._tick_state.get("rule_evaluation_results", [])
        for result in results:
            if not isinstance(result, dict):
                continue
            tid = result.get("todo_id", "")
            actual_tid = _safe_str(todo, "todo_id", "")
            if tid == actual_tid:
                actions = result.get("actions", [])
                if actions:
                    return apply_rule_actions(actions)
        return {}

    async def _dispatch_execute_job(
        self,
        todo: Any,
        *,
        _variable_repo_override: VariableNamespaceRepository | None = None,
        _task_return_repo_override: TaskReturnRepository | None = None,
        _session_override: AsyncSession | None = None,
    ) -> None:
        """Dispatch a single execute job.

        The ``_*_override`` keyword arguments are used by
        :meth:`_dispatch_execute_job_isolated` to inject per-coroutine repos/sessions
        so this method remains safe when called concurrently via asyncio.gather.
        When called without overrides (sequential path) it falls back to the shared
        instance attributes as before.

        Phase-2 Step 6: a ``self_update``-queue todo is short-circuited to
        :meth:`_apply_self_update_code` — it never reaches the Ansible/HTTP
        execute path (those todos arm a code hot-rotation + reload, not a
        playbook run).
        """
        _dispatch_start = time.monotonic()
        # SpendLimiter pre-call gate: atomically check + record the projected
        # cost via try_charge().  The previous would_exceed()-only check was
        # non-mutating — it never recorded spend, so the rolling window stayed
        # at zero and the soft cap could never trip (bug: inert limiter).
        # try_charge() does the check and the record in one locked step so:
        #   * Every accepted dispatch is charged against the window immediately.
        #   * Concurrent dispatches cannot both observe the same headroom and
        #     both commit (check-and-record is atomic under the limiter's lock).
        # A None limiter (no cap configured) leaves dispatch unchanged.
        limiter = self._spend_limiter
        if limiter is not None:
            projected = self._estimated_dispatch_cost(1)
            accepted = limiter.try_charge(projected, kind="token")
            if not accepted:
                logger.warning(
                    "SpendLimiter: deferring dispatch for todo %s — projected=%.6f window_spend=%.6f remaining=%.6f",
                    _safe_str(todo, "todo_id", "?"),
                    projected,
                    limiter.window_spend(),
                    limiter.remaining(),
                )
                return
        if _safe_str(todo, "queue") == "self_update":
            await self._apply_self_update_code(todo, _session_override=_session_override)
            return
        # Resolve which repos/session to use: per-job overrides (concurrent path)
        # or the shared tick-level ones (sequential fallback path).
        eff_variable_repo = _variable_repo_override if _variable_repo_override is not None else self._variable_repo
        eff_task_return_repo = (
            _task_return_repo_override if _task_return_repo_override is not None else self._task_return_repo
        )
        eff_session = _session_override if _session_override is not None else self._active_session

        budget_context: dict[str, Any] = {}
        if self._mcp_tool_registry is not None:
            budget_context["mcp_tools"] = self._mcp_tool_registry.tool_names()
        default_playbook = self._config_snapshot.get("default_playbook", "noop.yml")
        work_type = _safe_str(todo, "work_type", "code") or "code"
        project_id_val = todo.project_id if hasattr(todo, "project_id") and isinstance(todo.project_id, str) else None
        workspaces = self._project_workspace if isinstance(self._project_workspace, dict) else None
        ws = workspaces.get(project_id_val) if workspaces and project_id_val else None
        playbook = _playbook_for_work_type(
            work_type,
            default_playbook,
            project_id=project_id_val,
            workspaces=workspaces,
        )
        rule_overrides = self._get_rule_overrides_for_todo(todo)
        adaptive_prompt_id, adaptive_model_id, routing_decision = await self._resolve_adaptive_prompt(todo)
        if routing_decision is not None and not routing_decision.fallback:
            resolved_prompt_profile = adaptive_prompt_id or _safe_str(todo, "prompt_profile")
            resolved_model_profile = adaptive_model_id or _safe_str(todo, "model_profile")
        else:
            resolved_prompt_profile = _safe_str(todo, "prompt_profile")
            resolved_model_profile = _safe_str(todo, "model_profile")
        resolved_model_profile = rule_overrides.get("model_profile") or resolved_model_profile
        resolved_prompt_profile = rule_overrides.get("prompt_profile") or resolved_prompt_profile
        task_context = {
            "todo_title": _safe_str(todo, "title") or "",
            "todo_description": _safe_str(todo, "description") or "",
            "work_type": work_type,
            "queue": _safe_str(todo, "queue") or "core",
            "priority": str(getattr(todo, "priority", "medium") or "medium"),
            "acceptance_criteria": _format_acceptance_criteria(_safe_str(todo, "acceptance_criteria")),
            "definition_of_done": _safe_str(todo, "definition_of_done") or "",
        }
        project_templates_dir = (
            str(ws.templates_dir) if ws and hasattr(ws, "templates_dir") and ws.templates_dir.is_dir() else None
        )
        prompt_text = _resolve_prompt_text_static(
            self._prompt_registry,
            resolved_prompt_profile,
            project_templates_dir=project_templates_dir,
            **task_context,
        )
        # Fallback: the prompt_profile path above is PRIMARY, but a generation
        # todo submitted via POST /api/todos has no prompt_profile, so
        # _resolve_prompt_text_static returns None and the model would never be
        # called (silent no-op). If no profile resolved a prompt but the todo
        # carries a title/description, synthesize a minimal task prompt so the
        # model IS invoked. This only fires when the primary path produced
        # nothing; a real prompt_profile still wins.
        if not prompt_text:
            _fallback_title = task_context.get("todo_title") or ""
            _fallback_desc = task_context.get("todo_description") or ""
            _synthesized = f"Task: {_fallback_title}\n\n{_fallback_desc}".strip()
            if _synthesized:
                prompt_text = _synthesized
                logger.info(
                    "EventLoop: no prompt_profile resolved for todo %s; "
                    "synthesized fallback prompt from title/description",
                    getattr(todo, "todo_id", "?"),
                )
        prompt_text = await self._append_message_queue_section(
            prompt_text,
            todo,
            project_id_val,
        )
        # B3.1.5 checkpoint — pre-model boundary. After claim + prompt
        # resolution, before the model is called. A crash here resumes with
        # the preserved prompt_text so the model invocation is not lost.
        # Wrapped: checkpoint failure MUST NOT abort dispatch (best-effort).
        _todo_id = _safe_str(todo, "todo_id", "") or ""
        if self._checkpoint_manager is not None and _todo_id:
            with contextlib.suppress(Exception):
                self._checkpoint_manager.checkpoint(
                    AgentEnvironmentSnapshot(
                        task_id=_todo_id,
                        agent_name=_safe_str(todo, "agent_name", "event_loop") or "event_loop",
                        dispatch_state=DispatchState(
                            todo_id=_todo_id,
                            resolved_model_profile=resolved_model_profile,
                            resolved_prompt_profile=resolved_prompt_profile,
                            prompt_text=prompt_text or "",
                            phase_marker="pre_model",
                        ),
                    ),
                    phase="pre_model",
                )
        # Task #48: plan-time technical-debt evaluation (config-gated, default
        # OFF, NON-FATAL). When ``debt_eval.enabled`` is truthy, evaluate the
        # planned change for forward-looking scope gaps: ``fold_in`` gaps are
        # appended to the prompt so THIS task closes them; ``defer`` gaps become
        # BACKLOG child todos on the job session. The default-OFF path is a
        # no-op — dispatch is byte-for-byte unchanged when the flag is absent —
        # and any failure here is swallowed so execution always proceeds.
        debt_cfg = self.config.get("debt_eval", {}) if isinstance(self.config, dict) else {}
        if isinstance(debt_cfg, dict) and debt_cfg.get("enabled", False):
            try:
                from general_ludd.planning.artifact import PlanArtifact
                from general_ludd.planning.debt_applier import apply_debt_findings

                plan = PlanArtifact.from_todo(todo)
                goal = _safe_str(todo, "title") or _safe_str(todo, "description") or plan.todo_id
                findings = await self._bounded_to_thread(self._debt_evaluator.evaluate, plan, goal)
                repo = TodoRepository(eff_session) if eff_session is not None else self._todo_repo
                if repo is not None:
                    result = await apply_debt_findings(findings, plan, todo, repo, project_id=project_id_val)
                    if result.prompt_addendum:
                        prompt_text = (
                            f"{prompt_text}\n\n{result.prompt_addendum}" if prompt_text else result.prompt_addendum
                        )
            except Exception:
                logger.warning(
                    "debt-eval hook failed for todo %s; proceeding with dispatch",
                    getattr(todo, "todo_id", "?"),
                    exc_info=True,
                )
        skill_body = self._resolve_skill_body(todo)
        prompt_text = await self._build_memory_section(prompt_text, todo)
        # Load shared vars via the effective (possibly per-job) repo.
        shared_vars: dict[str, str] | None = None
        if eff_variable_repo is not None:
            try:
                shared_vars = await eff_variable_repo.load_vars_for_project(project_id_val)
            except Exception as exc:
                logger.warning("load_shared_vars failed for todo %s: %s", getattr(todo, "todo_id", "?"), exc)
        job_id = f"EXEC-{todo.todo_id}"
        ab_variant: dict[str, Any] | None = None
        if self._prompt_variant_selector is not None:
            ab_cfg = self.config.get("prompt_ab_testing", {}) if isinstance(self.config, dict) else {}
            if isinstance(ab_cfg, dict) and ab_cfg.get("enabled", False):
                with contextlib.suppress(Exception):
                    ab_variant = self._prompt_variant_selector.select(
                        template_name=resolved_prompt_profile,
                    )
                    if ab_variant is not None:
                        dispatched_profile = resolved_prompt_profile
                        variant_letter = ab_variant["variant"]
                        if dispatched_profile and variant_letter:
                            suffix = f".variant_{variant_letter.lower()}"
                            if not dispatched_profile.endswith(suffix):
                                base, _sep, ext = dispatched_profile.rpartition(".")
                                if base:
                                    resolved_prompt_profile = f"{base}{suffix}.{ext}"
                                else:
                                    resolved_prompt_profile = dispatched_profile + suffix
        if self._run_recorder is not None:
            with contextlib.suppress(Exception):
                event = {
                    "type": "dispatch_started",
                    "timestamp": datetime.now(UTC).isoformat(),
                    "todo_id": todo.todo_id,
                    "work_type": work_type,
                    "model_profile": resolved_model_profile,
                    "prompt_profile": resolved_prompt_profile,
                    "project_id": project_id_val,
                }
                if ab_variant is not None:
                    event["ab_variant"] = ab_variant
                self._run_recorder.record(job_id, event)
        if self._runner is not None:
            if ws is not None and hasattr(ws, "private_data_dir"):
                import os as _os

                job_dir = _os.path.join(str(ws.private_data_dir), job_id)
                _os.makedirs(job_dir, exist_ok=True)
                _os.makedirs(_os.path.join(job_dir, "env"), exist_ok=True)
                pdd = str(ws.private_data_dir)
            else:
                # prepare_job_dirs does blocking os.makedirs; offload so it does
                # not stall the daemon's single asyncio event loop (AB).
                dirs = await self._bounded_to_thread(self._runner.prepare_job_dirs, job_id)
                pdd = dirs["root"]
            # C1 (W3.x): invoke the model for a generation work type the SAME
            # way the worker HTTP path does, then feed the generated text into
            # the playbook vars so the runner path is not a no-op generator.
            _model_call_start = time.monotonic()
            _model_call_success = False
            _model_call_error: str | None = None
            _eff_gateway = self._mock_gateway if self._mock_gateway is not None else self._model_gateway
            if _eff_gateway is not None and is_generation_work_type(_safe_str(todo, "work_type", "code") or "code"):
                # Deployment health check: before calling the model, verify
                # the targeted deployment is healthy. If unhealthy, the
                # self-healing router picks the first healthy fallback.
                _deployment_id = resolved_model_profile or "default"
                if self._deployment_health_router is not None:
                    _routed = self._deployment_health_router.check_and_route(
                        _deployment_id,
                    )
                    if _routed is not None and _routed != _deployment_id:
                        resolved_model_profile = _routed
                        _deployment_id = _routed
                    elif _routed is None:
                        logger.warning(
                            "Deployment %s is unhealthy with no healthy fallback; proceeding with original profile",
                            _deployment_id,
                        )
                _call_start = time.monotonic()
                # #56: opt-in SLM context-compaction on the generation path.
                # When the adaptive compaction controller is wired, use its
                # dynamically-tuned level; otherwise fall back to static config.
                if self._compaction_controller is not None:
                    if self._compaction_level is None:
                        _compaction_cfg = self.config.get("compaction", {}) if isinstance(self.config, dict) else {}
                        _cfg_level = _compaction_cfg.get("level", 1) if _compaction_cfg.get("enabled") else 0
                        self._compaction_level = _cfg_level
                    _use_slm_compaction = not self._compaction_disabled and self._compaction_level > 0
                    _compaction_level = _level_at(self._compaction_level) if _use_slm_compaction else None
                else:
                    _compaction_cfg = self.config.get("compaction", {}) if isinstance(self.config, dict) else {}
                    _use_slm_compaction = bool(_compaction_cfg.get("enabled", False))
                    _compaction_level = _level_at(_compaction_cfg.get("level", 1)) if _use_slm_compaction else None
                try:
                    from general_ludd.scheduling.scheduler import ComputeSchedulingHint

                    _sched_hint = ComputeSchedulingHint.for_work_type(_safe_str(todo, "work_type", "code") or "code")
                    model_response, model_tool_calls = await self._bounded_to_thread(
                        invoke_model_for_generation,
                        _eff_gateway,
                        job_id=job_id,
                        work_type=_safe_str(todo, "work_type", "code") or "code",
                        model_profile=resolved_model_profile,
                        prompt_text=prompt_text,
                        skill_body=skill_body,
                        budget_guard=self._budget_guard,
                        # S-1 (task #25): scope this job's secret resolution to
                        # its project so credential/api-base aliases resolve
                        # through the project's ProjectSecretsManager (isolation),
                        # not the shared base resolver. None → base behavior.
                        project_id=project_id_val,
                        use_slm_compaction=_use_slm_compaction,
                        compaction_level=_compaction_level,
                        scheduling_hint=_sched_hint,
                    )
                    _model_call_success = model_response is not None
                    if self._run_recorder is not None:
                        with contextlib.suppress(Exception):
                            self._run_recorder.record(
                                job_id,
                                {
                                    "type": "model_generation",
                                    "timestamp": datetime.now(UTC).isoformat(),
                                    "success": _model_call_success,
                                    "model_profile": resolved_model_profile,
                                    "input_tokens": len(prompt_text or "") // 4,
                                    "output_tokens": len(model_response or "") // 4,
                                },
                            )
                except Exception as _exc:
                    model_response = None
                    model_tool_calls = None
                    _model_call_error = str(_exc)
                    logger.warning(
                        "Model call raised in EventLoop for job %s: %s",
                        job_id,
                        _exc,
                    )
            else:
                model_response = None
                model_tool_calls = None
            _model_call_duration = time.monotonic() - _model_call_start
            # Feed the model-call duration into the shared clock-time tracker so an
            # anomalously-slow model call (e.g. a call that usually takes ~2s now
            # taking ~30s) is detectable vs its learned per-profile baseline. Only
            # record when a model call ACTUALLY happened: non-generation todos leave
            # model_response None, and recording that phantom ~0s sample would drag
            # the per-profile latency baseline toward zero, causing false
            # is_anomalous "slow" warnings and artificially short StallWatchdog
            # deadlines.
            # Token capture was RETIRED from this path: it only covered the daemon
            # generation branch and missed the worker / ToolCallLoop / reviewer /
            # SLM / langgraph paths. It now lives at the gateway billing chokepoint
            # (ModelGateway._invoke_and_bill) with real usage_metadata counts, for
            # complete coverage across every call path.
            if model_response is not None:
                default_tracker().check_then_record(
                    f"model:{resolved_model_profile or 'default'}", _model_call_duration
                )
            # Record deployment health after each model call so the
            # self-healing router learns which deployments are degraded.
            if self._deployment_health_router is not None:
                _health_id = resolved_model_profile or "default"
                _health_checker = self._deployment_health_router.health_checker
                if _model_call_success:
                    _health_checker.record_success(_health_id)
                elif _model_call_error:
                    _health_checker.record_failure(
                        _health_id,
                        _model_call_error,
                        kind="error",
                    )
            # Record the model call in the performance repository.
            if self._model_perf_repo is not None:
                _input_tokens = len(prompt_text or "") // 4
                _output_tokens = len(model_response or "") // 4
                _profile_id = resolved_model_profile or "default"
                _profile_obj: Any = None
                if _eff_gateway is not None:
                    _profile_obj = _eff_gateway.get_profile(_profile_id)
                _cost_usd = 0.0
                if _profile_obj is not None and hasattr(_profile_obj, "cost_per_input_token"):
                    _cost_usd = (
                        _input_tokens * _profile_obj.cost_per_input_token
                        + _output_tokens * _profile_obj.cost_per_output_token
                    )
                _provider = getattr(_profile_obj, "provider", "") if _profile_obj else ""
                _model_name = getattr(_profile_obj, "model_name", "") if _profile_obj else ""
                try:
                    await self._model_perf_repo.record_call(
                        service=_provider or "unknown",
                        model_name=_model_name or _profile_id,
                        model_profile_id=_profile_id,
                        task_type="generation",
                        work_type=_safe_str(todo, "work_type", "code") or "code",
                        success=_model_call_success,
                        input_tokens=_input_tokens,
                        output_tokens=_output_tokens,
                        cost_usd=_cost_usd,
                        duration_ms=_model_call_duration * 1000,
                        todo_id=_safe_str(todo, "todo_id", ""),
                        job_id=job_id,
                        error_message=_model_call_error,
                    )
                except Exception as _rec_exc:
                    logger.debug(
                        "Model perf recording failed for %s: %s",
                        job_id,
                        _rec_exc,
                    )
            if model_response is not None:
                from general_ludd.dispatch.dynamic_dispatcher import (
                    structured_tool_calls_to_calls,
                )
                from general_ludd.routers.dispatch import MAX_CALLS_PER_REQUEST

                # Dispatch the model's STRUCTURED tool_calls directly. The legacy
                # path re-parsed the TEXT (parse_tool_calls(model_response)) which
                # cannot recover the structured calls, so model-driven tool actions
                # were silently discarded on this path.
                calls = structured_tool_calls_to_calls(model_tool_calls)
                if len(calls) > MAX_CALLS_PER_REQUEST:
                    logger.error(
                        "EventLoop: model returned %d tool calls which exceeds cap %d — denying all (job %s)",
                        len(calls),
                        MAX_CALLS_PER_REQUEST,
                        job_id,
                    )
                elif calls:
                    if self._dispatcher is None:
                        logger.warning(
                            "EventLoop: model returned %d tool call(s) but no dispatcher "
                            "is wired — skipping dispatch (job %s)",
                            len(calls),
                            job_id,
                        )
                    else:
                        results = await self._dispatcher.dispatch_all(calls)
                        ok_count = sum(1 for r in results if r.ok)
                        err_count = len(results) - ok_count
                        logger.info(
                            "EventLoop: dispatched %d tool call(s): %d ok, %d error (job %s)",
                            len(results),
                            ok_count,
                            err_count,
                            job_id,
                        )
                        if eff_variable_repo is not None:
                            for r in results:
                                if r.ok:
                                    try:
                                        await eff_variable_repo.set_var(
                                            namespace="tool_results",
                                            key=f"tool_result:{r.name}",
                                            value=str(r.output),
                                        )
                                    except Exception:
                                        # Best-effort persistence of a tool result;
                                        # the dispatch already succeeded. Keep
                                        # swallowing per-result so one bad write
                                        # does not abort the rest, but log it.
                                        logger.debug(
                                            "Failed to persist tool_result for %s (job %s)",
                                            r.name,
                                            job_id,
                                            exc_info=True,
                                        )
                        if self._run_recorder is not None:
                            with contextlib.suppress(Exception):
                                self._run_recorder.record(
                                    job_id,
                                    {
                                        "type": "tool_calls_dispatched",
                                        "timestamp": datetime.now(UTC).isoformat(),
                                        "total": len(results),
                                        "ok": ok_count,
                                        "error_count": err_count,
                                        "calls": [r.to_dict() for r in results],
                                    },
                                )
            # Phase 2 (keystone): autonomous tool use via the ToolCallLoop.
            #
            # Phase 1 above is tool-free by design (CA-T9): it produces text and,
            # if the model emitted STRUCTURED tool_calls, those are dispatched
            # once. Phase 2 is the genuine agentic loop — for tool-requiring work
            # types it binds the live MCP tools (list_tools -> tools=), lets the
            # model choose+call them, executes via the MCP client, feeds results
            # back, and iterates until the model stops requesting tools. This is
            # what makes autonomous tool action FUNCTIONAL (not merely
            # dispatch-wired) end to end.
            #
            # Gated so it runs for tool-requiring work types (analysis, audit,
            # code, bug_fix, refactor, feature, test) — code work types now get
            # the iterative tool loop so they can react to test failures. Still
            # gated on MCP client + model gateway presence. Wrapped so any
            # failure logs + falls through without breaking the tick.
            if work_type in _TOOL_USE_WORK_TYPES and self._mcp_client is not None and self._model_gateway is not None:
                try:
                    _use_langgraph = (
                        self.config.get("use_langgraph_tool_loop", False) if isinstance(self.config, dict) else False
                    )

                    tool_loop: Any = None
                    if _use_langgraph:
                        from general_ludd.execution.langgraph_agent import LangGraphAgentLoop

                        auditor = ToolCallAuditor()
                        _adversarial_detector = None
                        if self._daemon_state is not None:
                            _adversarial_detector = self._daemon_state.get("_adversarial_detector")
                        _max_total_tokens = (
                            self.config.get("tool_loop", {}).get("max_total_tokens", None)
                            if isinstance(self.config, dict)
                            else None
                        )
                        tool_loop = LangGraphAgentLoop(
                            model_gateway=self._model_gateway,
                            mcp_client=self._mcp_client,
                            mcp_registry=self._mcp_tool_registry,
                            role="event_loop",
                            tool_auditor=auditor,
                            budget_guard=self._budget_guard,
                            adversarial_detector=_adversarial_detector,
                            max_total_tokens=_max_total_tokens,
                        )
                        logger.debug("EventLoop: using LangGraphAgentLoop for Phase-2")
                    else:
                        from general_ludd.execution.tool_loop import ToolCallLoop

                        # SLICE 2 (task #56): opt-in SLM context-compaction on the
                        # ITERATIVE tool loop. When the adaptive controller is wired,
                        # use its level; otherwise fall back to static config.
                        _tl_level = None
                        _tl_summarize_fn = None
                        if self._compaction_controller is not None:
                            _use_tl = not self._compaction_disabled and (self._compaction_level or 0) > 0
                            if _use_tl:
                                from general_ludd.compaction.slm import (
                                    make_slm_summarize_fn,
                                )

                                _tl_level = _level_at(self._compaction_level or 1)
                                _tl_summarize_fn = make_slm_summarize_fn(self._model_gateway, "compactor")
                        else:
                            _tl_compaction_cfg = (
                                self.config.get("compaction", {}) if isinstance(self.config, dict) else {}
                            )
                            if bool(_tl_compaction_cfg.get("enabled", False)):
                                from general_ludd.compaction.slm import (
                                    make_slm_summarize_fn,
                                )

                                _tl_level = _level_at(_tl_compaction_cfg.get("level", 1))
                                _tl_summarize_fn = make_slm_summarize_fn(self._model_gateway, "compactor")

                        auditor = ToolCallAuditor()
                        store = BadCallSituationStore()
                        _adversarial_detector = None
                        if self._daemon_state is not None:
                            _adversarial_detector = self._daemon_state.get("_adversarial_detector")
                        _code_max_iters = (
                            self.config.get("tool_loop", {}).get("code_max_iterations", 5)
                            if isinstance(self.config, dict)
                            else 5
                        )
                        _analysis_max_iters = (
                            self.config.get("tool_loop", {}).get("analysis_max_iterations", 10)
                            if isinstance(self.config, dict)
                            else 10
                        )
                        _max_total_tokens = (
                            self.config.get("tool_loop", {}).get("max_total_tokens", None)
                            if isinstance(self.config, dict)
                            else None
                        )
                        _per_iteration_timeout = (
                            self.config.get("tool_loop", {}).get("per_iteration_timeout_seconds", None)
                            if isinstance(self.config, dict)
                            else None
                        )
                        tool_loop = ToolCallLoop(
                            model_gateway=self._model_gateway,
                            mcp_client=self._mcp_client,
                            mcp_registry=self._mcp_tool_registry,
                            # Per-role capability gate: the event-loop turn handler
                            # drives MCP tool use under the built-in "event_loop"
                            # role, which grants the "mcp" dispatch kind (see
                            # capability_lattice._BUILTIN) so normal operation is
                            # unaffected. Supplying the role activates the fail-closed
                            # gate in run_with_tools instead of leaving it ungated.
                            role="event_loop",
                            compaction_level=_tl_level,
                            summarize_fn=_tl_summarize_fn,
                            tool_auditor=auditor,
                            situation_store=store,
                            budget_guard=self._budget_guard,
                            adversarial_detector=_adversarial_detector,
                            max_total_tokens=_max_total_tokens,
                            per_iteration_timeout=_per_iteration_timeout,
                            work_type_max_iterations={
                                "analysis": _analysis_max_iters,
                                "audit": _analysis_max_iters,
                                "code": _code_max_iters,
                                "bug_fix": _code_max_iters,
                                "refactor": _code_max_iters,
                                "feature": _code_max_iters,
                                "test": _code_max_iters,
                            },
                        )
                    # Use the Phase-1 generated text as additional context so the
                    # tool-driven phase REFINES the analysis rather than starting
                    # blind; fall back to the raw prompt when Phase 1 produced
                    # nothing.
                    phase2_user = prompt_text or ""
                    if model_response:
                        phase2_user = (
                            f"{phase2_user}\n\nInitial analysis (refine using the available tools):\n{model_response}"
                        ).strip()
                    phase2_job = JobSpec(
                        job_id=job_id,
                        todo_id=_safe_str(todo, "todo_id", "") or "",
                        playbook=playbook,
                        queue=_safe_str(todo, "queue", "core") or "core",
                        work_type=work_type,
                        resource_profile=_safe_str(todo, "resource_profile", "low_resource") or "low_resource",
                        model_profile=resolved_model_profile,
                        prompt_profile=resolved_prompt_profile,
                        prompt_text=phase2_user,
                        skill_body=skill_body,
                        project_id=project_id_val,
                    )
                    tool_result = await tool_loop.run_with_tools(
                        phase2_job,
                        skill_body or "",
                        phase2_user,
                    )
                    logger.info(
                        "EventLoop: Phase-2 ToolCallLoop completed for %s job %s (work_type=%s)",
                        _safe_str(todo, "todo_id", "?"),
                        job_id,
                        work_type,
                    )
                    if self._run_recorder is not None:
                        with contextlib.suppress(Exception):
                            self._run_recorder.record(
                                job_id,
                                {
                                    "type": "tool_loop_completed",
                                    "timestamp": datetime.now(UTC).isoformat(),
                                    "output": str(tool_result) if tool_result else None,
                                },
                            )
                    # Persist the tool-loop output alongside the Phase-1 dispatch
                    # results so the autonomous tool action is recorded on the job
                    # trace (mirrors how dispatch_all results are persisted above).
                    if eff_variable_repo is not None and tool_result:
                        try:
                            await eff_variable_repo.set_var(
                                namespace="tool_results",
                                key=f"tool_loop_result:{job_id}",
                                value=str(tool_result),
                            )
                        except Exception:
                            logger.debug(
                                "Failed to persist Phase-2 tool_loop_result (job %s)",
                                job_id,
                                exc_info=True,
                            )
                except Exception:
                    # Phase 2 is additive: a tool-loop failure must NOT break the
                    # tick. Log loudly and fall through to the playbook run with
                    # the Phase-1 output intact.
                    logger.warning(
                        "EventLoop: Phase-2 ToolCallLoop failed for job %s "
                        "(work_type=%s) — falling through to Phase-1 output",
                        job_id,
                        work_type,
                        exc_info=True,
                    )
            if model_response is not None and self._benchmark_recorder is not None:
                try:
                    from general_ludd.event_loop.benchmark import record_job_benchmark

                    _bt = asyncio.create_task(
                        record_job_benchmark(
                            self._benchmark_recorder,
                            model_profile=resolved_model_profile,
                            prompt_profile=resolved_prompt_profile,
                            work_type=work_type,
                            success=True,
                            input_tokens=len(prompt_text or "") // 4,
                        )
                    )
                    self._track_background_task(_bt)
                except Exception:
                    # Best-effort fire-and-forget benchmark write: a scheduling
                    # failure must never abort dispatch. Keep swallowing but make
                    # a persistently-dead recorder visible at debug.
                    logger.debug(
                        "Benchmark recorder scheduling failed (non-fatal)",
                        exc_info=True,
                    )
                # Trace-buffer feed (additive): the DB benchmark write above is
                # preserved, but the trace→recorder→RecentTracesBuffer chain was
                # never exercised, so /api/traces always reported count 0. Build a
                # genuine ExecutionTrace with one completed span around the model
                # generation (reusing the data already gathered above) and feed it
                # through AutoBenchmarkRecorder.record_from_trace so the in-process
                # recent-traces buffer reflects actually-captured telemetry.
                try:
                    from general_ludd.observability.tracer import ExecutionTrace

                    _input_tokens = len(prompt_text or "") // 4
                    _output_tokens = len(model_response or "") // 4
                    _trace = ExecutionTrace(
                        todo_id=_safe_str(todo, "todo_id", "") or "",
                        work_type=work_type,
                        # Tenant attribution (task #19): carry the project this
                        # todo belongs to so the trace is visible to
                        # project-scoped /api/traces callers. Without it every
                        # real trace records project_id=None and is EXCLUDED by
                        # the tenant-boundary filter in
                        # RecentTracesBuffer.recent()/snapshot(). None stays
                        # valid for genuinely project-less traces.
                        project_id=project_id_val,
                    )
                    _span = _trace.start_span(name="model_generation", phase="generate")
                    _span.complete(
                        status="success",
                        input_tokens=_input_tokens,
                        output_tokens=_output_tokens,
                        model_profile_id=resolved_model_profile,
                        prompt_profile_id=resolved_prompt_profile,
                    )
                    self._active_traces[_trace.trace_id] = _trace
                    # Prune the trace dict to prevent unbounded growth (MED audit
                    # finding): once we exceed the cap, evict the oldest entries.
                    if len(self._active_traces) > self._MAX_ACTIVE_TRACES:
                        _evict = list(self._active_traces)[: len(self._active_traces) - self._MAX_ACTIVE_TRACES // 2]
                        for _k in _evict:
                            self._active_traces.pop(_k, None)
                    _tbt = asyncio.create_task(
                        self._benchmark_recorder.record_from_trace(
                            _trace,
                            success=True,
                        )
                    )
                    self._track_background_task(_tbt)
                except Exception:
                    # Best-effort trace-buffer feed (telemetry only): a failure
                    # here must never abort dispatch. Keep swallowing but log at
                    # debug so a broken trace pipeline is observable.
                    logger.debug(
                        "Trace-buffer feed failed (non-fatal)",
                        exc_info=True,
                    )
            # write_vars does blocking os.makedirs + yaml file write + os.chmod;
            # offload so it does not stall the daemon's single asyncio loop (AB).
            # Flow 4: surface the most recent resolved HumanTodo's resolution
            # text as ``human_input`` so the agent receives the human's answer
            # to a blocker it raised. None when no human-todo resolved for
            # this todo (the common case).
            _human_input: str | None = await self._resolve_human_input_for_todo(todo.todo_id)
            await self._bounded_to_thread(
                self._runner.write_vars,
                job_id,
                job_vars={
                    "job_id": job_id,
                    "todo_id": todo.todo_id,
                    "queue": _safe_str(todo, "queue", "core"),
                    "work_type": _safe_str(todo, "work_type", "unknown"),
                    "model_profile": resolved_model_profile,
                    "prompt_profile": resolved_prompt_profile,
                    "prompt_text": prompt_text,
                    "skill_body": skill_body,
                    "model_response": model_response,
                    "playbook": playbook,
                    **budget_context,
                    **({"human_input": _human_input} if _human_input else {}),
                },
                shared_vars=shared_vars,
            )
            runner_env: dict[str, str] = {}
            if ws is not None and hasattr(ws, "roles_dir") and ws.roles_dir.is_dir():
                runner_env["ANSIBLE_ROLES_PATH"] = str(ws.roles_dir)
            if ws is not None and hasattr(ws, "templates_dir") and ws.templates_dir.is_dir():
                runner_env["GLUDD_TEMPLATES_DIR"] = str(ws.templates_dir)
            # M9 (W3.3): run_playbook is a blocking I/O call; wrap in
            # asyncio.to_thread so the event loop stays responsive during
            # long playbook executions and CancelledError propagates cleanly.
            await self._bounded_to_thread(
                self._runner.run_playbook,
                playbook_name=playbook,
                private_data_dir=pdd,
                env=runner_env,
            )
            if self._run_recorder is not None:
                with contextlib.suppress(Exception):
                    self._run_recorder.record(
                        job_id,
                        {
                            "type": "dispatch_completed",
                            "timestamp": datetime.now(UTC).isoformat(),
                            "success": True,
                            "playbook": playbook,
                        },
                    )
            if (
                self._prompt_variant_selector is not None
                and self._prompt_variant_selector.variant_metrics is not None
                and ab_variant is not None
            ):
                with contextlib.suppress(Exception):
                    _latency_ms = (time.monotonic() - _dispatch_start) * 1000.0
                    _success = _model_call_success if model_response is not None else True
                    self._prompt_variant_selector.record_outcome(
                        success=_success,
                        latency_ms=_latency_ms,
                    )
            return
        if self._http_client is None:
            return
        roles_path = str(ws.roles_dir) if ws and hasattr(ws, "roles_dir") and ws.roles_dir.is_dir() else None
        tpl_dir = str(ws.templates_dir) if ws and hasattr(ws, "templates_dir") and ws.templates_dir.is_dir() else None
        job = JobSpec(
            job_id=f"EXEC-{todo.todo_id}",
            todo_id=todo.todo_id,
            playbook=playbook,
            queue=_safe_str(todo, "queue", "core") or "core",
            work_type=_safe_str(todo, "work_type", "unknown") or "unknown",
            resource_profile=_safe_str(todo, "resource_profile", "low_resource") or "low_resource",
            model_profile=resolved_model_profile,
            prompt_profile=resolved_prompt_profile,
            plan_artifact=_safe_str(todo, "plan_artifact"),
            prompt_text=prompt_text,
            skill_body=skill_body,
            budget_context=budget_context,
            project_id=project_id_val,
            artifact_dir=str(ws.artifacts_dir) if ws and hasattr(ws, "artifacts_dir") else None,
            vars_namespace_refs=list(shared_vars.keys()) if shared_vars else [],
            ansible_roles_path=roles_path,
            templates_dir=tpl_dir,
            human_input=await self._resolve_human_input_for_todo(todo.todo_id),
        )
        resp = await self._http_client.post(
            f"{self.worker_base_url}/jobs/execute",
            json=job.model_dump(mode="json"),
        )
        await self._persist_task_return(
            todo,
            job,
            resp,
            _task_return_repo_override=eff_task_return_repo,
            _session_override=eff_session,
        )
        if self._run_recorder is not None:
            with contextlib.suppress(Exception):
                self._run_recorder.record(
                    job_id,
                    {
                        "type": "dispatch_completed",
                        "timestamp": datetime.now(UTC).isoformat(),
                        "success": True,
                        "playbook": playbook,
                    },
                )
        # B3.1.5 checkpoint — clear-on-persist boundary. The dispatch
        # committed (persist_task_return wrote the task return), so the
        # checkpoint is no longer actionable and MUST be removed so the
        # next boot does not re-run a completed dispatch.
        if self._checkpoint_manager is not None:
            todo_id = _safe_str(todo, "todo_id", "")
            if todo_id:
                with contextlib.suppress(Exception):
                    self._checkpoint_manager.clear(todo_id)

    async def _resume_interrupted_dispatches(self) -> None:
        """B3.1.5: hydrate crash-interrupted dispatches on boot, re-enqueue.

        Called once from :meth:`run_forever` before the tick loop starts. For
        every checkpoint found on disk whose todo is NOT already COMPLETED,
        mark it resumed (emits the observability event) and re-enqueue the
        todo via the standard in-process inbound queue if wired. If no queue
        is wired, the resume is logged only — a single-process EventLoop has
        no separate worker to re-dispatch onto, and the standard tick will
        pick the todo up by its normal scheduling path once its status allows.

        Best-effort end to end: any failure here is logged and the loop
        continues — a corrupt checkpoint must never prevent boot.
        """
        if self._checkpoint_manager is None:
            return
        try:
            interrupted = self._checkpoint_manager.list_interrupted()
        except Exception:
            logger.exception("B3.1.5: list_interrupted raised; skipping resume")
            return
        if not interrupted:
            return
        # Resolve statuses in one batch if a todo_repo is wired; otherwise
        # assume all are actionable (the common test path).
        statuses: dict[str, str] = {}
        if self._todo_repo is not None:
            for snap in interrupted:
                try:
                    todo = await self._todo_repo.get_by_id(snap.task_id)
                    if todo is not None:
                        statuses[snap.task_id] = str(getattr(todo, "status", "PENDING"))
                except Exception:
                    logger.debug(
                        "B3.1.5: status lookup failed for %s; assuming PENDING",
                        snap.task_id,
                        exc_info=True,
                    )
        actionable = self._checkpoint_manager.filter_actionable_sync(
            interrupted,
            statuses=statuses,
        )
        for snap in actionable:
            phase = snap.dispatch_state.phase_marker if snap.dispatch_state is not None else "pre_model"
            self._checkpoint_manager.mark_resumed(snap.task_id, phase=phase)
            logger.info(
                "B3.1.5: resumed dispatch for todo %s from phase=%s",
                snap.task_id,
                phase,
            )

    def _make_daemon_health_probe(self) -> Callable[[], bool]:
        """Build an in-process health probe for a code-tier hot-rotation.

        Returns a callable suitable for
        :meth:`SelfImprovementWorkflow.set_code_target`'s ``health_check``. The
        probe reads ``self._daemon_state`` (the dict shared with ``app.state``)
        for a ``_degraded`` flag — the same flag the daemon sets on startup
        failure (``daemon.py:1216``). This is intentionally NOT an HTTP
        ``/readyz`` call: a reload-induced regression must be observable from
        the same process without a network round-trip, mirroring the contract
        documented in :mod:`reload.hot_reloader` (``health_check`` returns False
        when ``app.state._degraded`` is set).

        When ``self._daemon_state`` is None the health of the reload CANNOT be
        observed, so the probe FAILS CLOSED (returns False): an unverifiable
        code reload is rolled back by ``reload_code_module`` rather than kept.
        This is the recoverability guarantee — "if it can't be shown to work,
        roll it back." Set ``self_improve.allow_unverified_reload: true`` to opt
        back into the legacy fail-OPEN behavior (e.g. an embedded EventLoop with
        no daemon health surface that still wants reloads to land).
        """
        state = self._daemon_state
        si_cfg = self.config.get("self_improve", {}) if isinstance(self.config, dict) else {}
        allow_unverified = bool(si_cfg.get("allow_unverified_reload", False)) if isinstance(si_cfg, dict) else False

        def _probe() -> bool:
            if state is None:
                # Cannot verify health → fail CLOSED (roll back) unless the
                # operator explicitly opted into unverified reloads.
                return allow_unverified
            # Dict-style access (the EventLoop holds the shared dict) and
            # attribute-style access (a Starlette ``app.state``-like object may
            # be wired in tests) are both acceptable surfaces.
            degraded: object = state.get("_degraded") if isinstance(state, dict) else getattr(state, "_degraded", None)
            return not bool(degraded)

        return _probe

    async def _apply_self_update_code(
        self,
        todo: Any,
        *,
        _session_override: AsyncSession | None = None,
    ) -> None:
        """Phase-2 Step 6: arm ``set_code_target`` + ``reload_if_needed``.

        Reads the ``module:<name>`` and ``candidate:<path>`` tags the intake
        half (``routers/self_update.py``) writes onto a code-tier self-update
        todo, arms a real leaf-module hot-rotation on a
        :class:`SelfImprovementWorkflow`, fires ``reload_if_needed``, and
        transitions the todo COMPLETE or FAILED based on the reload verdict.

        Fail-closed at every stage:

          * A missing ``module:`` or ``candidate:`` tag → todo → FAILED, no
            reload attempted. The intake half MUST arm both for a code-tier
            apply; their absence means the request was malformed and running a
            partial reload would be unsafe.
          * A reload verdict that is not ``"success"`` → todo → FAILED. This
            includes ``"no_op"`` (the ReloadManager fallback that performs no
            real swap — BUG#2): a self-update that touched nothing cannot be
            marked COMPLETE, or an approved code-tier request would silently
            no-op forever. Only an actual hot-rotation whose health gate
            passed counts as applied.

        The todo transition uses a guarded compare-and-set (version check) via
        :meth:`TodoRepository.transition`; a lost version race logs and swallows
        rather than crashing the tick — the todo's persisted state is the
        authoritative record either way.
        """
        from general_ludd.reload.self_improve import ApplyResult
        from general_ludd.schemas.todo import TodoStatus

        todo_id = _safe_str(todo, "todo_id", "") or ""
        version = int(getattr(todo, "version", 1) or 1)

        # Per-call todo_repo: the isolated dispatch path passes its own session
        # via _session_override; the sequential path reuses the tick-level
        # self._todo_repo (already bound to the tick session).
        if _session_override is not None:
            todo_repo: TodoRepository | None = TodoRepository(_session_override)
        else:
            todo_repo = self._todo_repo

        # Resolve the module + candidate tags. Missing either is fail-closed.
        tags = getattr(todo, "tags", None) or []
        module_name: str | None = None
        candidate_path: str | None = None
        base_path: str | None = None
        for tag in tags:
            if not isinstance(tag, str):
                continue
            if module_name is None and tag.startswith("module:"):
                module_name = tag.split(":", 1)[1].strip()
            elif candidate_path is None and tag.startswith("candidate:"):
                candidate_path = tag.split(":", 1)[1].strip()
            elif base_path is None and tag.startswith("base:"):
                # Optional anti-clobber base snapshot the candidate was generated
                # against; when present, activates the 3-way merge so a concurrent
                # edit to the live module is refused rather than clobbered.
                base_path = tag.split(":", 1)[1].strip()

        if not module_name or not candidate_path:
            logger.error(
                "Step 6: self_update todo %s missing module/candidate tags (module=%r, candidate=%r) — failing closed",
                todo_id,
                module_name,
                candidate_path,
            )
            await self._transition_self_update_todo(
                todo_repo,
                todo_id,
                TodoStatus.FAILED,
                version,
            )
            return

        workflow = SelfImprovementWorkflow()
        workflow.set_code_target(
            module_name=module_name,
            candidate_source_path=candidate_path,
            health_check=self._make_daemon_health_probe(),
            base_source_path=base_path,
        )

        # Build an ApplyResult that requests the reload. The workflow's
        # reload_if_needed gates on apply_result.reload_needed; we do NOT bypass
        # validation here — a code-tier candidate must already have been
        # validated upstream by the time it reaches the backlog (the intake half
        # can refuse to enqueue unvalidated code-tier work). Marking
        # validation_passed=True asserts "the candidate was vetted before
        # enqueue"; the live health gate inside reload_if_needed is the second
        # line of defense.
        apply_result = ApplyResult(
            todo_id=todo_id,
            applied=True,
            reload_needed=True,
            validation_passed=True,
        )

        try:
            reload_result = workflow.reload_if_needed(apply_result)
        except Exception as exc:
            logger.error(
                "Step 6: reload_if_needed raised for todo %s (module=%s): %s",
                todo_id,
                module_name,
                exc,
            )
            await self._transition_self_update_todo(
                todo_repo,
                todo_id,
                TodoStatus.FAILED,
                version,
            )
            return

        reload_status = getattr(reload_result, "status", "")
        reload_ok = reload_status == "success"
        logger.info(
            "Step 6: self_update todo %s reload verdict=%s ok=%s (module=%s)",
            todo_id,
            reload_status,
            reload_ok,
            module_name,
        )

        target_status = TodoStatus.COMPLETE if reload_ok else TodoStatus.FAILED
        await self._transition_self_update_todo(
            todo_repo,
            todo_id,
            target_status,
            version,
        )

        if self._daemon_state is not None and isinstance(self._daemon_state, dict):
            # P3 (unbounded ledger growth): this audit list grows by one entry per
            # successful self_update reload and was NEVER pruned — over a long-lived
            # daemon it accumulates without bound. Bound it to the last
            # ``_MAX_SELF_UPDATE_APPLIES`` entries. It stays a plain ``list`` (NOT a
            # deque) so it remains JSON-serialisable and index-/slice-able for the
            # daemon consumers that surface it via app.state; we trim from the FRONT
            # so the most recent applies (the ones an operator cares about) survive.
            applies = self._daemon_state.setdefault("self_update_applies", [])
            applies.append(
                {
                    "todo_id": todo_id,
                    "module": module_name,
                    "candidate": candidate_path,
                    "verdict": reload_status,
                    "ok": reload_ok,
                }
            )
            if len(applies) > self._MAX_SELF_UPDATE_APPLIES:
                del applies[: len(applies) - self._MAX_SELF_UPDATE_APPLIES]

    async def _transition_self_update_todo(
        self,
        todo_repo: TodoRepository | None,
        todo_id: str,
        target: TodoStatus,
        expected_version: int,
    ) -> None:
        """Guarded CAS transition of a self_update todo; never raises into the tick.

        A lost version race (``ConcurrencyError``) or a missing todo row is
        logged and swallowed: the persisted row is the source of truth, and a
        concurrent writer already moved the state — re-applying would clobber
        it. This mirrors the reconcile phase's CAS discipline.
        """
        if todo_repo is None:
            logger.warning(
                "Step 6: no todo_repo available to transition %s -> %s",
                todo_id,
                target.value,
            )
            return
        try:
            await todo_repo.transition(todo_id, target, expected_version)
        except ConcurrencyError as exc:
            logger.info(
                "Step 6: lost version race transitioning %s -> %s: %s",
                todo_id,
                target.value,
                exc,
            )
        except Exception as exc:
            logger.error(
                "Step 6: transition failed for %s -> %s: %s",
                todo_id,
                target.value,
                exc,
            )

    async def _dispatch_validate_job(self, todo: Any) -> None:
        if self._http_client is None:
            return
        job = JobSpec(
            job_id=f"VALIDATE-{todo.todo_id}",
            todo_id=todo.todo_id,
            playbook="validate_task.yml",
            queue=_safe_str(todo, "queue", "core") or "core",
            work_type=_safe_str(todo, "work_type", "unknown") or "unknown",
            project_id=getattr(todo, "project_id", None),
        )
        resp = await self._http_client.post(
            f"{self.worker_base_url}/jobs/validate",
            json=job.model_dump(mode="json"),
        )
        logger.info("Validation dispatch for todo %s: status=%s", todo.todo_id, getattr(resp, "status_code", None))

    async def _persist_task_return(
        self,
        todo: Any,
        job: JobSpec,
        resp: Any,
        *,
        _task_return_repo_override: TaskReturnRepository | None = None,
        _session_override: AsyncSession | None = None,
    ) -> None:
        eff_repo = _task_return_repo_override if _task_return_repo_override is not None else self._task_return_repo
        eff_session = _session_override if _session_override is not None else self._active_session
        if eff_repo is None:
            return
        try:
            body = getattr(resp, "json", None)
            if callable(body):
                data = await body()
            elif isinstance(resp, dict):
                data = resp
            else:
                return
            if not isinstance(data, dict):
                return
            await eff_repo.create(
                data={
                    "return_id": data.get("return_id", f"RET-{job.job_id}"),
                    "todo_id": todo.todo_id,
                    "job_id": job.job_id,
                    "playbook": job.playbook,
                    "queue": job.queue,
                    "exit_code": data.get("exit_code", 0),
                    "result_summary": data.get("result_summary", ""),
                    "project_id": job.project_id,
                }
            )
            if eff_session is not None:
                await eff_session.flush()
            logger.info("Persisted TaskReturn for todo %s", todo.todo_id)
        except Exception as exc:
            logger.warning("Failed to persist task return for %s: %s", todo.todo_id, exc)

    @staticmethod
    def _decision_id(d: Any) -> str:
        """Stable identity for a TaskDecision row.

        Prefer the persisted primary key; fall back to (return_id, matched_todo_id)
        so a decision with no surrogate id is still de-dupable. This id keys the
        F1 idempotency ledger so the same decision can never be applied twice.
        """
        raw_id = getattr(d, "id", None)
        if raw_id is not None:
            return f"id:{raw_id}"
        return f"ret:{getattr(d, 'return_id', '')}:todo:{getattr(d, 'matched_todo_id', '')}"

    async def _phase_reconcile_completed_decisions(self) -> None:
        if self._active_session is None or self._todo_repo is None:
            return
        stmt = select(TaskDecisionModel).order_by(TaskDecisionModel.created_at.desc()).limit(50)
        project_id = self._tick_project_id
        if project_id is not None:
            stmt = stmt.where(TaskDecisionModel.project_id == project_id)
        result = await self._active_session.execute(stmt)
        decisions = list(result.scalars().all())
        todo_ids = [d.matched_todo_id for d in decisions if d.matched_todo_id]
        todo_map: dict[str, Any] = {}
        if todo_ids and self._todo_repo is not None:
            fetched_todos = await self._todo_repo.get_by_ids(todo_ids, project_id=project_id)
            if isinstance(fetched_todos, Mapping):
                todo_map = dict(fetched_todos)
            elif hasattr(self._todo_repo, "get_by_id"):
                for todo_id in todo_ids:
                    todo = await self._todo_repo.get_by_id(todo_id, project_id=project_id)
                    if todo is not None:
                        todo_map[todo_id] = todo
        reconciled = 0
        push_failures = 0
        for d in decisions:
            if not d.matched_todo_id:
                continue
            decision_id = self._decision_id(d)
            already_applied = decision_id in self._applied_decisions
            if already_applied:
                # F1: idempotent re-apply — never transition the same decision
                # twice. BUT a `complete` decision whose earlier push FAILED
                # (F3) is still status=COMPLETE-but-unpushed; that push must be
                # retried independently of the apply ledger (keyed on work id),
                # or the work would be silently lost.
                if d.decision == "complete" and d.matched_todo_id not in self._pushed_work:
                    todo = todo_map.get(d.matched_todo_id)
                    if todo is not None and await self._attempt_completed_push(todo):
                        push_failures += 1
                continue
            todo = todo_map.get(d.matched_todo_id)
            if todo is None or todo.status != TodoStatus.REVIEWING_RETURN.value:
                continue
            new_status = self._decision_to_status(d.decision)
            if new_status is None:
                continue
            # Layer 2: gate COMPLETE transitions behind evidence verification.
            if d.decision == "complete":
                from general_ludd.review.completion_verifier import verify_completion

                try:
                    _ev_refs: list[str] = json.loads(getattr(d, "evidence_refs", None) or "[]")
                    _audit_notes: list[str] = json.loads(getattr(d, "audit_notes", None) or "[]")
                    _schema_dec = TaskDecision(
                        return_id=getattr(d, "return_id", "") or "",
                        matched_todo_id=d.matched_todo_id,
                        decision=d.decision,
                        confidence=float(getattr(d, "confidence", 0.0) or 0.0),
                        evidence_refs=_ev_refs,
                        audit_notes=_audit_notes,
                    )
                except (ValueError, TypeError) as exc:
                    # Malformed ORM JSON / schema build for THIS row must never
                    # abort the whole reconcile phase (the loop processes up to 50
                    # decisions). Fail-safe: skip this one row — do NOT mark it
                    # applied, do NOT transition it to COMPLETE on bad evidence; it
                    # stays REVIEWING_RETURN for a later tick. Mirrors the
                    # per-iteration resilience used elsewhere in this loop.
                    logger.warning(
                        "Reconcile: skipping decision %s for todo %s — could not parse/build evidence for gating: %s",
                        decision_id,
                        d.matched_todo_id,
                        exc,
                    )
                    continue
                _decision_project_id = getattr(d, "project_id", None) or getattr(todo, "project_id", None) or None
                _repo_root = self._resolve_repo_root(_decision_project_id)
                _verified = await self._bounded_to_thread(verify_completion, _schema_dec, None, _repo_root)
                if _verified.decision != "complete":
                    logger.warning(
                        "Reconcile: evidence gate downgraded %s from complete to %s for todo %s",
                        decision_id,
                        _verified.decision,
                        d.matched_todo_id,
                    )
                    new_status = self._decision_to_status(_verified.decision)
                    if new_status is None:
                        continue
            # D2: gate a still-COMPLETE decision behind the EXTERNAL target
            # project's own quality gate when it declares one via project.yml.
            # This mirrors the gate in decision_applier.py (apply_decision) so
            # the reconcile phase applies the same quality-gate policy as the
            # review phase. Fail-safe: any error resolving/running the gate
            # leaves the decision unchanged.
            if d.decision == "complete" and _repo_root is not None:
                _ws = Path(_repo_root)
                if (_ws / "project.yml").is_file():
                    from general_ludd.quality.project_gate import run_project_gate

                    try:
                        _gate_report = await self._bounded_to_thread(run_project_gate, str(_ws))
                    except Exception as exc:
                        logger.warning(
                            "Reconcile: project gate errored for decision %s "
                            "(repo_root=%s): %s — leaving decision unchanged "
                            "(fail-safe)",
                            decision_id,
                            _repo_root,
                            exc,
                        )
                    else:
                        if isinstance(_gate_report, dict) and not _gate_report.get("passed"):
                            _checks = _gate_report.get("checks")
                            _lines: list[str] = []
                            if isinstance(_checks, list):
                                for _c in _checks:
                                    if isinstance(_c, dict) and not _c.get("passed", True):
                                        _s = _c.get("summary")
                                        if isinstance(_s, str) and _s:
                                            _lines.append(_s)
                                        else:
                                            _lines.append(f"{_c.get('name', 'check')}: FAIL")
                            _fail_summary = "; ".join(_lines) if _lines else "project gate FAILED"
                            logger.warning(
                                "Reconcile: project gate FAILED for decision %s "
                                "— downgrading complete -> needs_more_work: %s",
                                decision_id,
                                _fail_summary,
                            )
                            new_status = TodoStatus.NEEDS_MORE_WORK
            # Human-in-the-loop gate: when config ``review.human_in_the_loop`` is
            # enabled and the decision confidence is below the configured
            # threshold, pause via LangGraph interrupt() for human approval.
            # Falls back to existing HumanTodo polling when LangGraph is absent.
            if d.decision == "complete" and self._human_gate.should_interrupt(
                float(getattr(d, "confidence", 0.0) or 0.0)
            ):
                _gate_decision = await self._human_gate.await_approval(
                    thread_id=decision_id,
                    message=f"Review decision {decision_id} for todo {todo.todo_id}",
                    decision_id=decision_id,
                    todo_id=todo.todo_id,
                    confidence=float(getattr(d, "confidence", 0.0) or 0.0),
                )
                if _gate_decision is not None:
                    if _gate_decision.lower() in ("denied", "needs_more_work"):
                        new_status = TodoStatus.NEEDS_MORE_WORK
                    elif _gate_decision.lower() == "approved":
                        pass  # Keep new_status as COMPLETE
            # F6: version race. We transition with the version we just read; the
            # repository performs a guarded compare-and-set on (version, status).
            # If a CONCURRENT reconcile already moved the row, the CAS affects
            # zero rows and raises ConcurrencyError — we treat this stale
            # reconcile as a lost race: do NOT mark it applied, do NOT push, and
            # never overwrite the newer state. It simply skips this tick.
            try:
                await self._todo_repo.transition(todo.todo_id, new_status, todo.version)
            except ConcurrencyError as exc:
                logger.info(
                    "Reconcile lost version race for todo %s (decision %s): %s — skipping stale reconcile",
                    todo.todo_id,
                    decision_id,
                    exc,
                )
                continue
            # Transition committed: this decision is now applied exactly once.
            # P3: bounded LRU insert — evicts the oldest id (FIFO) past the cap.
            self._ledger_add(self._applied_decisions, decision_id)
            reconciled += 1
            # AutoMemory: record an episodic memory for this task outcome.
            self._track_background_task(asyncio.create_task(self._auto_record_episode(todo, new_status, d)))
            if (
                new_status == TodoStatus.COMPLETE
                and d.decision == "complete"
                and await self._attempt_completed_push(todo)
            ):
                push_failures += 1
            # Ephemeral account cleanup: when a task carrying an
            # ``ephemeral_account_id`` stamp is marked COMPLETE and the
            # policy says delete-after-use, tear the account down now so a
            # finished job cannot leave a live credential behind. Best-effort
            # — a provider failure is logged, not raised (the reconcile phase
            # must not abort because cleanup hiccupped on one account).
            if new_status == TodoStatus.COMPLETE and self._ephemeral_account_manager is not None:
                self._track_background_task(asyncio.create_task(self._maybe_cleanup_ephemeral(todo)))
            if self._audit_repo is not None:
                try:
                    from general_ludd.db.models import AuditEventType

                    await self._audit_repo.record_typed(
                        AuditEventType.TODO_STATUS_CHANGED,
                        entity_type="todo",
                        entity_id=todo.todo_id,
                        project_id=todo.project_id,
                        details={
                            "old": todo.status,
                            "new": new_status.value,
                            "decision": d.decision,
                        },
                    )
                except Exception:
                    # Audit trail is best-effort; the transition is already
                    # applied. Log so a broken audit sink is visible without
                    # aborting reconcile.
                    logger.warning(
                        "Audit write failed for todo status change %s",
                        todo.todo_id,
                        exc_info=True,
                    )
        self._tick_metrics["decisions_applied"] = reconciled
        self._tick_metrics["push_failures"] = push_failures

    async def _attempt_completed_push(self, todo: Any) -> bool:
        """Push completed work exactly once; return True if this attempt FAILED.

        F2: deduped by work (todo) id — a todo already pushed is a no-op, so the
        push fires exactly once across ticks (never twice).
        F3: a commit/push failure is surfaced (returned True + error log), and
        the work id is left OUT of the ledger so the push is RETRIED on a later
        tick rather than silently leaving status=COMPLETE-but-unpushed.
        #53: retry limit — after _MAX_PUSH_RETRIES consecutive failures the
        todo is transitioned to BLOCKED instead of retrying every tick forever.
        Exponential backoff spreads retries apart so a persistent conflict
        doesn't burn every tick.
        """
        tid = todo.todo_id
        if tid in self._pushed_work:
            logger.debug(
                "Completed work %s already pushed — skipping duplicate push",
                tid,
            )
            return False

        # #53: if we already escaped the livelock for this todo (BLOCKED or
        # BLOCKED_ON_HUMAN), don't retry.
        todo_status = _safe_str(todo, "status", "")
        if todo_status in (TodoStatus.BLOCKED.value, TodoStatus.BLOCKED_ON_HUMAN.value):
            logger.debug(
                "#53: todo %s is %s — push skipped (livelock already escaped)",
                tid,
                todo_status,
            )
            return False

        # D10 (#53): exponential backoff with jitter + per-todo hash offset.
        # Two todos at the same retry_count that use only tick % window
        # always check the same tick — they collide deterministically.
        # Per-todo offset (hash(tid) % window) spreads their check ticks
        # apart, making deterministic collision structurally impossible.
        # The jittered sleep after still provides runtime de-sync.
        retry_count = self._push_retry_count.get(tid, 0)
        if retry_count > 1:
            window = 2 ** min(retry_count, 6)  # cap at 64-tick window
            tick = self._total_ticks
            offset = abs(hash(tid)) % window
            if (tick + offset) % window != 0:
                logger.debug(
                    "#53: backoff skip for %s (retry %d, tick %d, window %d, offset %d)",
                    tid,
                    retry_count,
                    tick,
                    window,
                    offset,
                )
                return True  # signal failure so ledger skips marking pushed
            # D10: jittered backoff to break retry-phase alignment
            jitter_sec = random.uniform(0.1, min(5.0, 0.5 * (2**retry_count)))
            logger.debug(
                "#53: jittered backoff for %s (retry %d, %.2fs)",
                tid,
                retry_count,
                jitter_sec,
            )
            await asyncio.sleep(jitter_sec)

        try:
            await self._try_commit_completed_work(todo)
        except Exception as exc:
            # #53: increment retry counter; escape to BLOCKED at the limit.
            self._push_retry_count[tid] = retry_count + 1
            new_retry = self._push_retry_count[tid]
            if new_retry > self._MAX_PUSH_RETRIES:
                logger.error(
                    "#53: livelock ESCAPE — todo %s failed push %d times "
                    "(max %d). Transitioning to BLOCKED. Last error: %s",
                    tid,
                    new_retry,
                    self._MAX_PUSH_RETRIES,
                    exc,
                )
                await self._escape_push_livelock(todo, new_retry, exc)
                return False
            logger.error(
                "Reconcile: completed-work push FAILED for %s "
                "(state diverged COMPLETE vs unpushed) — will retry "
                "(attempt %d/%d): %s",
                tid,
                new_retry,
                self._MAX_PUSH_RETRIES,
                exc,
            )
            return True

        # Success: clear retry counter + record push.
        self._push_retry_count.pop(tid, None)
        self._ledger_add(self._pushed_work, tid)
        return False

    async def _escape_push_livelock(
        self,
        todo: Any,
        retry_count: int,
        last_error: Exception,
    ) -> None:
        """#53: transition the todo to BLOCKED after exhausting push retries.

        Best-effort: a failure here (no repo, version mismatch, etc.) is logged
        but never propagated — the todo may stay stuck, but the retry loop has
        already been broken by returning False from _attempt_completed_push.
        """
        tid = todo.todo_id
        try:
            if self._todo_repo is not None:
                todo_version = getattr(todo, "version", None)
                if todo_version is not None:
                    await self._todo_repo.transition(
                        tid,
                        TodoStatus.BLOCKED,
                        todo_version,
                    )
                    logger.info(
                        "#53: todo %s transitioned to BLOCKED after %d failed push attempts",
                        tid,
                        retry_count,
                    )
                else:
                    logger.warning(
                        "#53: cannot transition %s — no version on todo object",
                        tid,
                    )
            else:
                logger.warning(
                    "#53: cannot transition %s — no TodoRepository wired",
                    tid,
                )
        except Exception as transition_exc:
            logger.error(
                "#53: BLOCKED transition failed for %s after livelock escape "
                "(retries=%d, push_error=%s, transition_error=%s). "
                "The todo may remain stuck but the retry loop is broken.",
                tid,
                retry_count,
                last_error,
                transition_exc,
            )

        # Clear the retry counter so a future status change (manual unblock)
        # starts a fresh budget.
        self._push_retry_count.pop(tid, None)

    def _decision_to_status(self, decision: str) -> TodoStatus | None:
        mapping: dict[str, TodoStatus] = {
            "complete": TodoStatus.COMPLETE,
            "needs_more_work": TodoStatus.NEEDS_MORE_WORK,
            "failed": TodoStatus.FAILED,
            "blocked": TodoStatus.BLOCKED,
            "manual_hold": TodoStatus.MANUAL_HOLD,
        }
        return mapping.get(decision)

    async def _try_commit_completed_work(self, todo: Any) -> None:
        """H6: commit/branch/push completed work via git automation.

        #31 (multi-agent safety): this is the git-DELIVERY path — the ONLY point
        where a todo's affected files are actually known. They cannot be reserved
        at dispatch/claim time: the model has not run, no worktree changes exist,
        so claiming an empty set then would be a no-op. Here the worktree has been
        written, so ``repo.changed_files()`` yields the real affected paths. We
        therefore claim those files in the shared FileClaimRegistry BEFORE the
        commit, refuse to proceed if another live worker already holds an
        overlapping file (deferring this commit to a later tick rather than
        clobbering), and release the claim once delivery finishes (success OR
        failure). A None registry leaves behaviour unchanged.
        """
        branch_name = getattr(todo, "branch_name", None) or f"gludd-{todo.todo_id.lower()}"
        worktree = getattr(todo, "worktree", None)
        if worktree:
            from general_ludd.git_automation.repo import GitAutomation

            repo = GitAutomation(worktree)
            registry = self._file_claims
            worker_id = _safe_str(todo, "todo_id", "") or str(id(todo))
            claimed = False
            try:
                # D10 (#31 / #53): discover affected files and atomically
                # claim-or-conflict them in one lock acquisition.  The old
                # claim()+overlaps()+release() pattern was a three-step
                # livelock: two workers could simultaneously claim overlapping
                # sets, each see the other in overlaps(), both release, both
                # retry in lockstep.  claim_or_conflict sorts files for
                # total-order determinism and checks conflicts BEFORE
                # recording the claim, so the first worker to acquire the
                # lock wins and every other worker gets a clean conflict
                # signal without ever holding the lock.
                if registry is not None:
                    affected = await self._bounded_to_thread(repo.changed_files)
                    if affected:
                        acquired = registry.claim_or_conflict(worker_id, affected)
                        if not acquired:
                            logger.info(
                                "#31: deferring commit for todo %s — files %s "
                                "contested by another live worker; will retry next tick",
                                worker_id,
                                sorted(affected),
                            )
                            raise _FileClaimConflict(
                                f"file-claim conflict for {worker_id}: {sorted(affected)} contested"
                            )
                        claimed = True
                # M (LIVE stall fix): commit/push shell out to blocking git.
                # Even with a per-subprocess timeout, a 60s blocking call inside
                # the async tick would freeze every other coroutine. Offload to a
                # worker thread (mirrors the playbook dispatch's asyncio.to_thread)
                # so blocking git can never stall the event loop.
                await self._bounded_to_thread(repo.commit, f"[{todo.todo_id}] {todo.title}")
                # Record the per-commit LOC delta into the accounting ledger
                # (counted via git show --numstat). Best-effort: a counting
                # failure must never abort the commit/push flow that follows.
                if self._loc_ledger is not None:
                    try:
                        delta = await self._bounded_to_thread(repo.lines_changed_in_commit)
                        pid = getattr(todo, "project_id", None) or self._tick_project_id or ""
                        self._loc_ledger.record_loc_changed(pid, delta)
                    except Exception as loc_exc:
                        logger.debug(
                            "loc_changed recording failed for %s: %s",
                            todo.todo_id,
                            loc_exc,
                        )
                await self._bounded_to_thread(repo.push, branch=branch_name)
                logger.info("H6: committed + pushed %s to %s", todo.todo_id, branch_name)
                await self._maybe_open_pr(todo, worktree, branch_name)
            except Exception as exc:
                # F3: surface (don't swallow) so the caller can detect the
                # COMPLETE-but-unpushed split-brain and retry. The caller decides
                # whether to mark the work pushed — a raised failure leaves it
                # unmarked so the push is retried, never silently lost. A
                # _FileClaimConflict is the same shape of "retry next tick" signal.
                if not isinstance(exc, _FileClaimConflict):
                    logger.warning("H6: git automation failed for %s: %s", todo.todo_id, exc)
                raise
            finally:
                # #31: release the claim once delivery settles (committed+pushed
                # OR failed) so the next tick's worker can take overlapping files.
                # release() is a no-op when we never claimed (registry None /
                # no affected files / conflict already released above).
                if registry is not None and claimed:
                    registry.release(worker_id)

    async def _maybe_open_pr(self, todo: Any, worktree: str, branch_name: str) -> None:
        """F1: open a PR for completed work when git_automation.open_pr is set.

        Disabled by default — only fires when config opts in, so the daemon
        never opens PRs unexpectedly. Uses PRDelivery (push branch + gh pr
        create).

        H2 fix: push_and_create_pr shells out to ``gh pr create`` / ``git push``
        which can block for 5-30 s.  Run it in a thread so the event loop is
        never stalled during PR delivery.
        """
        ga_cfg = self.config.get("git_automation", {}) or {}
        if not ga_cfg.get("open_pr", False):
            return
        from general_ludd.git_automation.pr_delivery import PRDelivery

        delivery = PRDelivery(
            base_branch=str(ga_cfg.get("base_branch", "main")),
            draft=bool(ga_cfg.get("pr_draft", False)),
            labels=list(ga_cfg.get("pr_labels", [])),
        )
        result = await self._bounded_to_thread(
            delivery.push_and_create_pr,
            repo_path=worktree,
            branch_name=branch_name,
            todo_id=todo.todo_id,
            title=getattr(todo, "title", todo.todo_id),
        )
        if result.get("error"):
            logger.warning("F1: PR delivery for %s failed: %s", todo.todo_id, result["error"])
        else:
            logger.info("F1: opened PR for %s: %s", todo.todo_id, result.get("pr_url"))

    async def _phase_check_compute_utilization(self) -> None:
        interval = self.config.get("compute_idle_check_interval_ticks", 60)
        if self._total_ticks % interval != 0:
            return

        # Floor auto-tuning: dynamically adjust concurrency floor based on
        # system health metrics (CPU, memory, dispatch success rate, queue depth).
        if self._floor_controller is not None:
            try:
                import psutil

                cpu_pct = psutil.cpu_percent(interval=0)
                mem = psutil.virtual_memory()
                memory_pct = mem.percent

                claimed = self._tick_state.get("claimed_todos", [])
                dispatched = self._tick_metrics.get("todos_dispatched", 0)
                dispatch_success_rate = (dispatched / len(claimed) * 100) if claimed else 100.0

                queue_depth = 0
                if self._todo_repo is not None:
                    try:
                        summary = await self._todo_repo.status_summary()
                        by_status = summary.get("by_status", {})
                        queue_depth = int(by_status.get("queued", 0))
                    except Exception:
                        pass

                new_floor = self._floor_controller.auto_tune(
                    cpu_pct=cpu_pct,
                    memory_pct=memory_pct,
                    dispatch_success_rate=dispatch_success_rate,
                    queue_depth=queue_depth,
                )
                entry = self._floor_controller.floor_history[-1]
                if entry["reason"] != "no_change":
                    logger.info(
                        "Floor auto-tuned: %d -> %d (reason=%s, success_rate=%.1f%%, "
                        "queue_depth=%d, cpu=%.1f%%, mem=%.1f%%)",
                        entry["previous_floor"],
                        new_floor,
                        entry["reason"],
                        dispatch_success_rate,
                        queue_depth,
                        cpu_pct,
                        memory_pct,
                    )
                if self._daemon_state is not None:
                    self._daemon_state["floor_auto_tune"] = {
                        "floor": new_floor,
                        "history_size": len(self._floor_controller.floor_history),
                    }
            except Exception as exc:
                logger.warning("Floor auto-tune failed: %s", exc)

        if self._utilization_tracker is None:
            # GPU metric collection runs even without a utilization tracker
            # so that /admin/compute/gpu-metrics always has fresh data.
            import time as _time

            from general_ludd.infra.gpu_metrics import GPUMetricsCollector

            metrics = GPUMetricsCollector.collect_all_gpu_metrics()
            if self._daemon_state is not None:
                self._daemon_state["_last_gpu_metrics"] = metrics
                self._daemon_state["_last_gpu_metrics_at"] = _time.time()
            return
        threshold = self.config.get("compute_idle_gpu_sm_pct", 5.0)
        teardown_ticks = self.config.get("compute_idle_teardown_threshold_ticks", 3)
        notice_ticks = self.config.get("compute_idle_preemption_notice_ticks", 1)
        idle_endpoints = self._utilization_tracker.find_idle_gpus(
            threshold=float(threshold),
            window=900,
        )
        daemon_state = self._daemon_state if self._daemon_state is not None else {}
        idle_tracking: dict[str, Any] = daemon_state.setdefault("idle_endpoints", {})
        torn_down: list[str] = daemon_state.setdefault("torn_down_endpoints", [])
        current_idle_ids: set[str] = set()
        # bill-6: collect GPU metrics after compute utilization check.
        import time as _time

        from general_ludd.infra.gpu_metrics import GPUMetricsCollector

        metrics = GPUMetricsCollector.collect_all_gpu_metrics()
        daemon_state["_last_gpu_metrics"] = metrics
        daemon_state["_last_gpu_metrics_at"] = _time.time()
        for ep in idle_endpoints:
            current_idle_ids.add(ep.endpoint_id)
            if ep.endpoint_id not in idle_tracking:
                idle_tracking[ep.endpoint_id] = {
                    "endpoint_id": ep.endpoint_id,
                    "url": ep.url,
                    "model": ep.model,
                    "gpu_type": ep.gpu_type,
                    "last_used": ep.last_used,
                    "idle_ticks": 0,
                }
            idle_tracking[ep.endpoint_id]["idle_ticks"] += 1
            count = idle_tracking[ep.endpoint_id]["idle_ticks"]
            logger.warning(
                "Idle GPU endpoint %s (%s, %s): %d consecutive idle ticks",
                ep.endpoint_id,
                ep.model,
                ep.gpu_type,
                count,
            )
            # bill-7: preemption notice — warn N ticks before teardown.
            if notice_ticks > 0 and count == teardown_ticks - notice_ticks:
                logger.warning(
                    "[COMPUTE] endpoint %s will be torn down in %d ticks if idle persists",
                    ep.endpoint_id,
                    notice_ticks,
                )
            if count >= teardown_ticks:
                logger.warning(
                    "Tearing down idle GPU endpoint %s after %d idle ticks",
                    ep.endpoint_id,
                    count,
                )
                # bill-7: record infra cost on teardown.
                if self._infra_tracker is not None:
                    try:
                        gpu_seconds = _time.time() - ep.last_used if ep.last_used > 0 else 0.0
                        self._infra_tracker.record_gpu_seconds(
                            provider=getattr(ep, "provider", "runpod") or "runpod",
                            gpu_type=ep.gpu_type,
                            seconds=max(gpu_seconds, 0.0),
                            spot=True,
                            project_id=getattr(ep, "project_id", None),
                        )
                    except Exception as _exc:
                        logger.warning(
                            "Failed to record infra cost for teardown of %s: %s",
                            ep.endpoint_id,
                            _exc,
                        )
                else:
                    logger.warning(
                        "_infra_tracker not initialized; cannot record GPU cost for teardown of %s",
                        ep.endpoint_id,
                    )
                if self._deployment_manager is not None:
                    try:
                        await self._deployment_manager.destroy(ep.endpoint_id)
                    except Exception as exc:
                        logger.error(
                            "Failed to tear down idle endpoint %s: %s",
                            ep.endpoint_id,
                            exc,
                        )
                        continue
                else:
                    logger.warning(
                        "_deployment_manager not initialized; cannot destroy idle endpoint %s",
                        ep.endpoint_id,
                    )
                self._utilization_tracker.unregister_endpoint(ep.endpoint_id)
                idle_tracking.pop(ep.endpoint_id, None)
                torn_down.append(ep.endpoint_id)
        no_longer_idle = [eid for eid in idle_tracking if eid not in current_idle_ids]
        for eid in no_longer_idle:
            idle_tracking.pop(eid, None)

    async def _phase_check_service_credits(self) -> None:
        """Refresh prepaid service credit balances every N ticks.

        Queries the wired :class:`~general_ludd.budget.credit_tracker.CreditTracker`
        (if any) and stashes the latest per-service balances on
        ``self._daemon_state["credits"]`` so the ``GET /api/credits`` endpoint
        can serve them without issuing a fresh HTTP call per request. The
        tracker handles transport errors / parse failures / missing keys
        per-provider, so a single bad key never poisons the rest.

        Interval: ``credit_check_interval_ticks`` (default 600 = ~10 minutes at
        1s/tick). Set to 0 to disable.
        """
        if self._credit_tracker is None:
            return
        interval = int(self.config.get("credit_check_interval_ticks", 600))
        if interval <= 0:
            return
        if self._total_ticks % interval != 0:
            return
        try:
            results = await self._bounded_to_thread(self._credit_tracker.check_all_balances)
        except Exception as exc:
            logger.warning("Service-credit balance check failed: %s", exc)
            return
        if self._daemon_state is not None:
            self._daemon_state["credits"] = results
        # Surface low-balance providers in tick metrics for the dashboard.
        low = [
            svc
            for svc, r in results.items()
            if r.get("balance_usd") is not None and self._credit_tracker.should_refill(svc)
        ]
        if low:
            self._tick_metrics["low_credit_services"] = low
            logger.warning("Low service credit balance: %s", ", ".join(low))

    async def _phase_flush_spend_ledger(self) -> None:
        """SPD-1: periodically persist in-memory spend records to the DB.

        Gated by ``spend_persist_interval_ticks`` (default 60; <=0 disables).
        Opens a DEDICATED session from the session factory so failures here
        never affect the shared tick session held across post-dispatch phases.
        Records their seq watermark, writes through ``SpendRepository.add()``,
        then calls ``SpendLimiter.mark_flushed()`` to advance the watermark so
        the next flush is incremental.

        Failure semantics: a persist failure (DB error) is logged at WARNING
        and never blocks dispatch — the in-memory limiter stays authoritative
        for the live cap; only the next tick's flush retries.
        """
        if self._spend_limiter is None or self._session_factory is None:
            return
        interval = int(self.config.get("spend_persist_interval_ticks", 60))
        if interval <= 0:
            return
        if self._total_ticks % interval != 0:
            return
        unflushed = self._spend_limiter.unflushed_records()
        if not unflushed:
            return
        try:
            from general_ludd.db.repository import SpendRepository

            max_seq = unflushed[0][0]
            async with self._session_factory() as session:
                repo = SpendRepository(session)
                for seq, ts, cost_usd, project_id in unflushed:
                    await repo.add(
                        ts=float(ts),
                        cost_usd=float(cost_usd),
                        kind="token",
                        project_id=project_id,
                    )
                    if seq > max_seq:
                        max_seq = seq
                await session.commit()
            self._spend_limiter.mark_flushed(max_seq)
            logger.info(
                "SpendLimiter: flushed %d record(s) to spend_records (upto_seq=%d)",
                len(unflushed),
                max_seq,
            )
        except Exception as exc:
            logger.warning(
                "SpendLimiter flush failed (will retry next tick): %s",
                exc,
            )

    async def _phase_remediate_blocked_tasks(self) -> None:
        """Auto-remediation tick phase (#52): scan for blocked todos and act.

        Runs the read-only :class:`BlockerDetector` scan against the tick's
        live session/todo_repo, then hands at most
        ``remediation_max_actions_per_tick`` findings to
        :class:`RemediationDispatcher` per tick — a large blocked backlog is
        drained gradually rather than flooding the todo/human-todo tables in
        one pass. Idempotent: a finding already acted on within the
        configured ``retry_delay_hours`` cooldown is skipped via
        ``RemediationActionRepository.exists_recent`` so a still-blocked task
        does not get a fresh dispatch/retry/human-todo filed every tick.

        Interval: ``remediation_check_interval_ticks`` (default 30). Set to 0
        to disable (kill switch) — mirrors
        ``_phase_refresh_model_performance``'s interval idiom. Reads the
        SAME ``RemediationConfig`` the ``/admin/remediation/*`` HTTP
        endpoints read (``self._daemon_state["remediation_config"]``, wired
        by daemon.py from ``UserConfig.remediation``) so the tick path and
        the HTTP path never disagree on thresholds. Actions are conservative
        (todo/retry/human-todo) and never touch code — every action is
        audited via ``RemediationActionRepository``.
        """
        interval = int(self.config.get("remediation_check_interval_ticks", 30))
        if interval <= 0 or self._total_ticks % interval != 0:
            return
        if self._todo_repo is None or self._active_session is None:
            return
        try:
            from general_ludd.db.repository import (
                HumanTodoRepository,
                RemediationActionRepository,
            )
            from general_ludd.remediation.blocker_detector import (
                BlockerDetector,
                RemediationConfig,
            )
            from general_ludd.remediation.dispatcher import RemediationDispatcher

            cfg = self._daemon_state.get("remediation_config") if self._daemon_state is not None else None
            if not isinstance(cfg, RemediationConfig):
                cfg = RemediationConfig()

            # NEEDS_MORE_WORK → QUEUED requeue sweep: cycle eligible work
            # back into the pipeline before filing human-todos.
            nmw_requeued = await self._todo_repo.requeue_needs_more_work(
                cooldown_hours=cfg.needs_more_work_cooldown_hours,
                max_run_count=cfg.max_requeues_before_chronic,
            )
            self._tick_metrics["remediation_nmw_requeued"] = nmw_requeued

            human_todo_repo = HumanTodoRepository(self._active_session)
            detector = BlockerDetector(
                todo_repo=self._todo_repo,
                human_todo_repo=human_todo_repo,
                config=cfg,
                session=self._active_session,
            )
            findings = await detector.scan(project_id=self._tick_project_id)
            self._tick_metrics["remediation_scanned"] = len(findings)
            if not findings:
                return
            remediation_repo = RemediationActionRepository(self._active_session)
            max_actions = int(self.config.get("remediation_max_actions_per_tick", 5))
            since = datetime.now(UTC) - timedelta(hours=cfg.retry_delay_hours)
            dispatcher = RemediationDispatcher(
                detector=detector,
                todo_repo=self._todo_repo,
                human_todo_repo=human_todo_repo,
                remediation_repo=remediation_repo,
            )
            acted = 0
            for blocked in findings:
                if acted >= max_actions:
                    break
                if await remediation_repo.exists_recent(blocked.todo_id, since):
                    continue
                await dispatcher.remediate(blocked)
                acted += 1
            self._tick_metrics["remediation_actions"] = acted
        except Exception as exc:
            logger.warning("Auto-remediation tick phase failed: %s", exc, exc_info=True)

    # Matrix of concurrent dispatch ceiling overrides below: maximum entries kept
    # in the per-instance idempotency ledgers.
    # Beyond this cap, the oldest entry is evicted (FIFO) so the ledgers never
    # grow without bound (P3: unbounded-memory defect, audit finding MED).
    _MAX_PUSH_RETRIES: ClassVar[int] = 5

    _MAX_LEDGER_SIZE: ClassVar[int] = 10_000

    def _ledger_add(self, ledger: OrderedDict[str, None], key: str) -> None:
        """Insert ``key`` into a bounded LRU ledger, evicting the oldest past cap.

        P3 (unbounded ledger growth): ``ledger`` is an insertion-ordered
        ``OrderedDict`` used as an ordered set. A re-inserted key is moved to the
        most-recently-used end (``move_to_end``) so an id that is still being
        touched is never the eviction victim; a genuinely new key is appended and,
        if the ledger now exceeds ``_MAX_LEDGER_SIZE``, the OLDEST key is dropped in
        O(1) via ``popitem(last=False)``.

        This replaces the previous ``set`` + ``list(set)[:surplus]`` prune, which
        (a) materialised the whole ledger into a list at capacity (O(n) spike) and
        (b) evicted an ARBITRARY victim because a ``set`` has no order — which could
        forget a still-relevant recent id and re-open the F1/F2 re-apply / re-push
        window. FIFO-by-insertion keeps the most recent ``_MAX_LEDGER_SIZE`` ids,
        which is exactly the set most likely to still be in flight.
        """
        if key in ledger:
            ledger.move_to_end(key)
            return
        ledger[key] = None
        while len(ledger) > self._MAX_LEDGER_SIZE:
            ledger.popitem(last=False)

    # Maximum in-memory ExecutionTrace entries. Each trace is small (~1 KB)
    # so 1 000 entries is safe even under heavy dispatch rates.
    _MAX_ACTIVE_TRACES: ClassVar[int] = 1_000
    # P3: cap the daemon-state self_update audit list (one entry per successful
    # self_update reload). 500 recent applies is ample for operator inspection
    # while the list stays small and JSON-serialisable on app.state.
    _MAX_SELF_UPDATE_APPLIES: ClassVar[int] = 500

    _PRIORITY_MAP: ClassVar[dict[str, int]] = {
        "low": 0,
        "medium": 5,
        "high": 10,
        "critical": 20,
    }

    async def _persist_self_improve_todos(self, todos: list[dict[str, Any]], project_id: str | None = None) -> int:
        if self._todo_repo is None or self._active_session is None:
            return 0
        from general_ludd.self_improve.gate import SelfImproveGate

        si_cfg = self.config.get("self_improve", {}) if isinstance(self.config, dict) else {}
        if not isinstance(si_cfg, dict):
            si_cfg = {}
        gate = SelfImproveGate(
            max_open=si_cfg.get("max_open", 10),
        )
        terminal = {
            TodoStatus.COMPLETE.value,
            TodoStatus.FAILED.value,
            TodoStatus.CANCELLED.value,
        }
        existing = await self._todo_repo.list_by_work_type("self_improve", project_id=project_id)
        open_count = sum(1 for t in existing if _safe_str(t, "status") not in terminal)
        persisted = 0
        for todo in todos:
            decision = gate.evaluate(todo, open_count=open_count)
            if not decision.admitted:
                continue
            priority_raw = todo.get("priority", "high")
            if isinstance(priority_raw, int):
                priority = priority_raw
            else:
                priority = self._PRIORITY_MAP.get(str(priority_raw).lower(), 10)
            payload: dict[str, Any] = {
                "title": str(todo.get("title", "Self-improvement task"))[:512],
                "description": str(todo.get("description", "")),
                "status": decision.initial_status,
                "work_type": "self_improve",
                "priority": priority,
                "created_by": "self_improve_harness",
                "project_id": project_id,
            }
            try:
                await self._todo_repo.create(payload)
                persisted += 1
                open_count += 1
            except Exception as exc:
                logger.warning("Failed to persist self-improve todo: %s", exc)
        if persisted:
            try:
                await self._active_session.flush()
            except Exception:
                logger.warning(
                    "Flush of %d self-improve todo(s) failed",
                    persisted,
                    exc_info=True,
                )
        return persisted

    async def dispatch_return_review(self, task_return: TaskReturn) -> dict[str, Any]:
        if task_return.status != TaskReturnStatus.CREATED:
            return {"status": "skipped", "reason": "not_created"}
        job = JobSpec(
            job_id=f"REVIEW-{task_return.return_id}",
            return_id=task_return.return_id,
            todo_id=task_return.todo_id,
            playbook="return_review.yml",
            queue="model",
            work_type="review",
            resource_profile="ai_heavy",
        )
        logger.info("Dispatching return review for %s", task_return.return_id)
        return {"status": "dispatched", "job_id": job.job_id}

    async def claim_runnable_todos(self, todos: list[Todo]) -> list[Todo]:
        runnable = [t for t in todos if t.status == TodoStatus.QUEUED]
        return runnable

    async def reconcile_decision(self, decision: TaskDecision, todo: Todo) -> Todo:
        if decision.decision == "complete":
            todo.transition_to(TodoStatus.COMPLETE)
        elif decision.decision == "needs_more_work":
            todo.transition_to(TodoStatus.NEEDS_MORE_WORK)
        elif decision.decision == "failed":
            todo.transition_to(TodoStatus.FAILED)
        elif decision.decision == "blocked":
            todo.transition_to(TodoStatus.BLOCKED)
        elif decision.decision == "manual_hold":
            todo.transition_to(TodoStatus.MANUAL_HOLD)
        return todo
