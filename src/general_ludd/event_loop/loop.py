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
from types import FunctionType
from typing import TYPE_CHECKING, Any, ClassVar, cast
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from general_ludd.agents.hibernation import (
    AgentEnvironmentSnapshot,
    DispatchState,
)
from general_ludd.compaction.aggressive import level_at as _level_at
from general_ludd.controllers.compaction_aggressiveness import (
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
from general_ludd.event_loop import runtime_helpers as _runtime_helpers
from general_ludd.event_loop import task_routing as _task_routing
from general_ludd.event_loop.compute_lifecycle import ComputeLifecycleMixin as _ComputeLifecycleMixin
from general_ludd.event_loop.decision_completion import DecisionCompletionMixin as _DecisionCompletionMixin
from general_ludd.event_loop.decision_reconciliation import (
    reconcile_completed_decisions,
)
from general_ludd.event_loop.execution_dispatch import ExecutionDispatchMixin as _ExecutionDispatchMixin
from general_ludd.event_loop.execution_supervision import (
    ExecutionLeaseIdentity,
    ExecutionLeaseSupervisor,
    OwnedExecutionCancelled,
)
from general_ludd.event_loop.lease import (
    LeaseRenewalStatus,
    reclaim_expired_leases,
    release_lease,
)
from general_ludd.event_loop.loop_handlers import EventLoopHandlers
from general_ludd.event_loop.managed_self_improve_dispatch import (
    bind_local_plan as _bind_approved_local_plan,
)
from general_ludd.event_loop.managed_self_improve_dispatch import (
    configured_execution_mode as _configured_self_improve_execution_mode,
)
from general_ludd.event_loop.managed_self_improve_dispatch import (
    decode_worker_response as _decode_managed_worker_response,
)
from general_ludd.event_loop.managed_self_improve_dispatch import (
    resolve_repository_binding as _resolve_approved_repository_binding,
)
from general_ludd.event_loop.managed_self_improve_dispatch import (
    serialize_run_result as _serialize_managed_run_result,
)
from general_ludd.event_loop.managed_self_improve_dispatch import (
    validate_approved_plan as _validate_managed_plan,
)
from general_ludd.event_loop.managed_self_improve_dispatch import (
    validate_worker_result as _validate_managed_worker_result,
)
from general_ludd.event_loop.managed_self_improve_dispatch import (
    worker_rejection_reason as _managed_worker_rejection_reason,
)
from general_ludd.event_loop.review_dispatch import ReviewDispatchMixin as _ReviewDispatchMixin
from general_ludd.event_loop.review_orchestration import EventLoopReviewMixin
from general_ludd.event_loop.review_orchestration import (
    is_managed_self_improve_todo as _is_managed_self_improve_todo,
)
from general_ludd.event_loop.review_orchestration import (
    safe_string_attribute as _safe_str,
)
from general_ludd.event_loop.runtime_helpers import (
    runtime_lease_bucket_keys as _runtime_lease_bucket_keys,
)
from general_ludd.event_loop.runtime_helpers import (
    runtime_work_identity as _runtime_work_identity,
)
from general_ludd.event_loop.runtime_helpers import (
    todo_dependency_ids as _todo_dependency_ids,
)
from general_ludd.event_loop.self_improve_lifecycle import SelfImproveLifecycleMixin as _SelfImproveLifecycleMixin
from general_ludd.event_loop.tick_lifecycle import TickLifecycleMixin as _TickLifecycleMixin
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
from general_ludd.projects.repository_binding import ProjectRepositoryBinding
from general_ludd.reload.self_improve import SelfImprovementWorkflow
from general_ludd.rules.engine import Rule, apply_rule_actions, evaluate_rules
from general_ludd.schemas.benchmark import TaskType
from general_ludd.schemas.job import JobSpec
from general_ludd.schemas.project_identity import ProjectWorkIdentity
from general_ludd.schemas.queue import Queue
from general_ludd.schemas.task_decision import TaskDecision
from general_ludd.schemas.task_return import TaskReturn, TaskReturnStatus
from general_ludd.schemas.todo import Todo, TodoStatus
from general_ludd.self_improve.managed_runner import ApprovedSelfImprovePlan
from general_ludd.self_improve.promotion import (
    ManagedPromotionReceipt,
    build_managed_self_improve_promotion_coordinator,
)
from general_ludd.self_improve.runtime import build_managed_self_improve_runner

# Moved method bodies are rebound to this module's globals so every historical
# ``general_ludd.event_loop.loop.<dependency>`` monkeypatch target remains live.
# Keep the complete dependency-port set explicit for static analysis as well as
# for maintainers reviewing that compatibility boundary.
_MIXIN_GLOBAL_PORTS = (
    asyncio,
    contextlib,
    json,
    logging,
    _stdqueue,
    random,
    time,
    OrderedDict,
    Callable,
    Mapping,
    UTC,
    datetime,
    timedelta,
    Path,
    FunctionType,
    ClassVar,
    cast,
    uuid4,
    select,
    AsyncSession,
    async_sessionmaker,
    AgentEnvironmentSnapshot,
    DispatchState,
    _level_at,
    CompactionAggressivenessController,
    FloorController,
    LoadSnapshot,
    LoadController,
    TaskDecisionModel,
    AuditEventRepository,
    ConcurrencyError,
    TaskReturnRepository,
    TodoRepository,
    VariableNamespaceRepository,
    _reset_tenant,
    _set_tenant,
    _runtime_helpers,
    _task_routing,
    reconcile_completed_decisions,
    ExecutionLeaseIdentity,
    ExecutionLeaseSupervisor,
    OwnedExecutionCancelled,
    LeaseRenewalStatus,
    reclaim_expired_leases,
    release_lease,
    EventLoopHandlers,
    _TickLifecycleMixin,
    _ReviewDispatchMixin,
    _ComputeLifecycleMixin,
    _ExecutionDispatchMixin,
    _SelfImproveLifecycleMixin,
    _DecisionCompletionMixin,
    _bind_approved_local_plan,
    _configured_self_improve_execution_mode,
    _decode_managed_worker_response,
    _resolve_approved_repository_binding,
    _serialize_managed_run_result,
    _validate_managed_plan,
    _validate_managed_worker_result,
    _managed_worker_rejection_reason,
    EventLoopReviewMixin,
    _is_managed_self_improve_todo,
    _safe_str,
    _runtime_lease_bucket_keys,
    _runtime_work_identity,
    _todo_dependency_ids,
    TickCheckpointer,
    HumanGate,
    BadCallSituationStore,
    ToolCallAuditor,
    MCPClient,
    MCPToolRegistry,
    invoke_model_for_generation,
    is_generation_work_type,
    default_tracker,
    DebtEvaluator,
    make_debt_evaluate_fn,
    ProjectRepositoryBinding,
    SelfImprovementWorkflow,
    Rule,
    apply_rule_actions,
    evaluate_rules,
    TaskType,
    JobSpec,
    ProjectWorkIdentity,
    Queue,
    TaskDecision,
    TaskReturn,
    TaskReturnStatus,
    Todo,
    TodoStatus,
    ApprovedSelfImprovePlan,
    ManagedPromotionReceipt,
    build_managed_self_improve_promotion_coordinator,
    build_managed_self_improve_runner,
)

_runtime_lease_bucket_key = _runtime_helpers.runtime_lease_bucket_key

if TYPE_CHECKING:
    # TYPE_CHECKING-only: keep injected boundaries decoupled at import time.
    from general_ludd.decision_codification.service import DecisionCodificationAdapter
    from general_ludd.ipc.queue import WriteQueue

logger = logging.getLogger(__name__)

# Compatibility surface: callers historically imported these private helpers
# from ``event_loop.loop``. Keep those objects available while the cohesive
# implementation lives in the smaller routing module.
_CODE_WORK_TYPES = _task_routing.CODE_WORK_TYPES
_TOOL_USE_WORK_TYPES = _task_routing.TOOL_USE_WORK_TYPES
_WORK_TYPE_TASK_TYPE_MAP = _task_routing.WORK_TYPE_TASK_TYPE_MAP
_WORK_TYPE_PLAYBOOK_MAP = _task_routing.WORK_TYPE_PLAYBOOK_MAP
_ROUTING_TASK_TYPE = TaskType
_compute_todo_estimate = _task_routing.compute_todo_estimate
_format_acceptance_criteria = _task_routing.format_acceptance_criteria
_playbook_for_work_type = _task_routing.playbook_for_work_type
_resolve_prompt_text_static = _task_routing.resolve_prompt_text_static
_self_update_work_item_from_todo = _task_routing.self_update_work_item_from_todo


def _work_type_to_task_type(work_type: str) -> TaskType:
    """Map through compatibility globals retained for existing patch points."""
    if (
        _WORK_TYPE_TASK_TYPE_MAP is _task_routing.WORK_TYPE_TASK_TYPE_MAP
        and TaskType is _ROUTING_TASK_TYPE
    ):
        return _task_routing.work_type_to_task_type(work_type)
    mapped = _WORK_TYPE_TASK_TYPE_MAP.get(work_type, "feature")
    try:
        return TaskType(mapped)
    except ValueError:
        return TaskType.FEATURE


class _FileClaimConflict(Exception):
    """Signal that a todo's delivery would clobber another worker's file.

    Treated by the completed-work push path exactly like any other delivery
    failure: the work id is left OUT of the pushed ledger so the commit is
    retried on a later tick (after the conflicting worker releases its claim),
    rather than two todos committing the same file simultaneously.
    """


PHASE_ORDER = [
    "load_config_snapshot",
    "evaluate_pid_controllers",
    "refill_task_buckets",
    "run_scheduler",
    "self_improve",
    "poll_issue_sources",
    "sdlc_gate",
    "claim_unreviewed_task_returns",
    "dispatch_return_review_jobs",
    "claim_runnable_todos",
    "evaluate_rules",
    "reconcile_compute_demand",
    "dispatch_execute_jobs",
    "reconcile_completed_decisions",
    "release_compute_demand",
    "refresh_model_performance",
    "check_compute_utilization",
    "check_service_credits",
    "flush_spend_ledger",
    "remediate_blocked_tasks",
    "consolidate_memory",
    "service_discovery",
    "reap_expired_sts_tokens",
    "purge_old_task_decisions",
    "emit_tick_metrics",
]

# S83.158: durable claim/lease state is committed and its session is closed
# before any compute lifecycle call.  The provision phase therefore has its own
# boundary before the already-sessionless dispatch phase.
PROVISION_PHASE_INDEX = PHASE_ORDER.index("reconcile_compute_demand")

# E10 (PERF-1): index of the dispatch phase in PHASE_ORDER.  The tick session
# is committed + closed BEFORE this phase so the dispatch gather (up to ~30 min)
# does not hold the DB writer lock.  A fresh session is opened for the
# post-dispatch phases.
DISPATCH_PHASE_INDEX = PHASE_ORDER.index("dispatch_execute_jobs")
RELEASE_PHASE_INDEX = PHASE_ORDER.index("release_compute_demand")



def _rebind_function_globals(function: FunctionType) -> FunctionType:
    """Clone one mixin function against the compatibility facade globals."""
    rebound = FunctionType(
        function.__code__,
        globals(),
        function.__name__,
        function.__defaults__,
        function.__closure__,
    )
    rebound.__kwdefaults__ = function.__kwdefaults__
    rebound.__annotations__ = dict(function.__annotations__)
    rebound.__dict__.update(function.__dict__)
    rebound.__doc__ = function.__doc__
    rebound.__module__ = __name__
    rebound.__qualname__ = function.__qualname__
    return rebound


def _bind_mixin_to_facade(mixin: type[Any]) -> None:
    """Keep every historical ``event_loop.loop`` monkeypatch seam live."""
    for name, descriptor in vars(mixin).items():
        rebound: object | None = None
        if isinstance(descriptor, FunctionType):
            rebound = _rebind_function_globals(descriptor)
        elif isinstance(descriptor, staticmethod):
            rebound = staticmethod(
                _rebind_function_globals(cast(FunctionType, descriptor.__func__))
            )
        elif isinstance(descriptor, classmethod):
            rebound = classmethod(
                _rebind_function_globals(cast(FunctionType, descriptor.__func__))
            )
        elif isinstance(descriptor, property):
            rebound = property(
                _rebind_function_globals(cast(FunctionType, descriptor.fget))
                if descriptor.fget is not None
                else None,
                _rebind_function_globals(cast(FunctionType, descriptor.fset))
                if descriptor.fset is not None
                else None,
                _rebind_function_globals(cast(FunctionType, descriptor.fdel))
                if descriptor.fdel is not None
                else None,
                descriptor.__doc__,
            )
        if rebound is not None:
            setattr(mixin, name, rebound)


for _event_loop_mixin in (
    _TickLifecycleMixin,
    _ReviewDispatchMixin,
    _ComputeLifecycleMixin,
    _ExecutionDispatchMixin,
    _SelfImproveLifecycleMixin,
    _DecisionCompletionMixin,
):
    _bind_mixin_to_facade(_event_loop_mixin)


class EventLoop(
    _TickLifecycleMixin,
    _ReviewDispatchMixin,
    _ComputeLifecycleMixin,
    _ExecutionDispatchMixin,
    _SelfImproveLifecycleMixin,
    _DecisionCompletionMixin,
    EventLoopReviewMixin,
    EventLoopHandlers,
):
    """Coordinate one durable scheduling, dispatch, and reconciliation loop."""

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
        self_improve_runner_factory: Callable[[Path], Any] | None = None,
        self_improve_executor: Any | None = None,
        self_improve_promotion_factory: Callable[
            [AsyncSession, Path, str], Any
        ]
        | None = None,
        decision_codification: DecisionCodificationAdapter | None = None,
    ) -> None:
        """Initialize the loop and its injected service boundaries."""
        self.worker_base_url = worker_base_url
        self.config = config or {}
        self._daemon_state = daemon_state
        self._run_recorder = run_recorder
        self._decision_codification = decision_codification
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
        self._self_improve_runner_factory = (
            self_improve_runner_factory or build_managed_self_improve_runner
        )
        self._self_improve_executor = self_improve_executor
        self._self_improve_run_lock = asyncio.Lock()
        self._self_improve_promotion_factory = (
            self_improve_promotion_factory
            or build_managed_self_improve_promotion_coordinator
        )
        self._self_improve_promotion_instance = uuid4().hex[:12]
        # One process-stable identity owns every lease acquired by this loop.
        # The todo version completes the execution-attempt fence, so a later
        # attempt in the same process cannot accidentally renew an older one.
        self._lease_owner_id = f"event-loop-{uuid4().hex}"
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
        # Last successful desired lifecycle state per exact repository root.
        # This is deliberately process-local: after a daemon restart the first
        # tick reconciles actual state again instead of trusting stale memory.
        self._execution_environment_states: dict[str, tuple[str, str]] = {}
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
