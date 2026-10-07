"""Hermetic cross-provider E2E proof for project-private self-improvement."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base, ProjectModel
from general_ludd.db.repository import (
    ProjectRepository,
    TaskReturnRepository,
    TodoRepository,
)
from general_ludd.event_loop.loop import EventLoop
from general_ludd.local_model import get_model
from general_ludd.projects.manager import persist_project
from general_ludd.schemas.task_decision import TaskDecision
from general_ludd.schemas.todo import TodoStatus
from general_ludd.self_improve.approval import SelfImproveApprovalManager
from general_ludd.self_improve.codex_comparison import (
    CandidateEvidence,
    CodexReference,
    ComparisonResult,
    ProposalManifest,
)
from general_ludd.self_improve.harness import SelfImprovementHarness
from general_ludd.self_improve.managed_runner import (
    ApprovedSelfImprovePlan,
    AttemptResult,
    ManagedSelfImproveRunner,
    PlanBoundProposal,
    PromptPlan,
    PromptShard,
    SelfImprovePolicyViolation,
    TaskSpec,
    apply_proposal,
)
from general_ludd.self_improve.model_candidate_planner import PlannedModelCandidate
from general_ludd.self_improve.private_policy import load_self_improve_policy
from general_ludd.self_improve.promotion import ManagedPromotionReceipt
from general_ludd.self_improve.staging import ManagedSelfImprovePlanRequest

pytestmark = pytest.mark.e2e

PROVIDER_MODES = ("fake-local", "fake-azure")
PUBLIC_PATH = "src/public/rules.py"
PUBLIC_TEST_PATH = "tests/public/test_rules.py"
PRIVATE_PATH = "src/business/pricing.py"
PRIVATE_TEST_PATH = "tests/private/test_pricing.py"
SHARED_PATH = "src/shared/rules.py"
PRIVATE_CANARY = "PRIVATE_PRICE_FORMULA_CANARY_7b21"
PRIVATE_OLD = f"{PRIVATE_CANARY}_OLD\n"
PUBLIC_OLD = "def rule() -> int:\n    return 0\n"
PUBLIC_NEW = "def rule() -> int:\n    return 1\n"
PROVIDER_CREDENTIALS = {
    "fake-local": "LOCAL_PROVIDER_SECRET_CANARY_41e8",
    "fake-azure": "AZURE_PROVIDER_SECRET_CANARY_c902",
}


@dataclass
class _Trace:
    provider_calls: list[str] = field(default_factory=list)
    evaluator_calls: list[str] = field(default_factory=list)
    acquisitions: list[str] = field(default_factory=list)
    outcomes: list[dict[str, object]] = field(default_factory=list)
    events: list[str] = field(default_factory=list)


class _ComputeLifecycle:
    """Hermetic execution-environment boundary with an auditable call ledger."""

    def __init__(self, chain: list[str] | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self._chain = chain

    def reconcile_execution_environment(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(dict(kwargs))
        if self._chain is not None:
            self._chain.append(f"compute_{kwargs['state']}")
        return {"status": "successful", "rc": 0, "events": []}


class _Reservation:
    def mark_eligible(self, _identity: object) -> None:
        return None

    def mark_failed(self, _identity: object) -> None:
        return None


class _FakeModelManager:
    def __init__(self, root: Path, mode: str, trace: _Trace) -> None:
        self.cache_root = root / ".model-cache"
        self._root = root
        self._mode = mode
        self._trace = trace

    def resolve_revision(self, _repo_id: str) -> str:
        return "c" * 40

    def owned_identities_for_model_ids(
        self, _model_ids: tuple[str, ...]
    ) -> tuple[object, ...]:
        return ()

    @contextmanager
    def reserve_plan(self, *_args: object, **_kwargs: object) -> Iterator[_Reservation]:
        yield _Reservation()

    @contextmanager
    def acquire(self, *_args: object, **_kwargs: object) -> Iterator[Any]:
        self._trace.acquisitions.append(self._mode)
        yield SimpleNamespace(
            path=self._root / ".fake-providers" / f"{self._mode}.bin",
            model_id="hermetic-code-model",
            source=self._mode,
            resolved_revision="c" * 40,
            artifact_sha256="d" * 64,
        )


class _Outcomes:
    planner_store = object()

    def __init__(self, trace: _Trace) -> None:
        self._trace = trace

    def load_failed_model_ids(self, **_kwargs: object) -> tuple[str, ...]:
        return ()

    def record_outcome(self, **kwargs: object) -> int:
        self._trace.outcomes.append(kwargs)
        return len(self._trace.outcomes)


class _FakeProvider:
    def __init__(
        self,
        mode: str,
        trace: _Trace,
        proposal: ProposalManifest,
        *,
        before_return: Callable[[], None] | None = None,
    ) -> None:
        self.mode = mode
        self.credential = PROVIDER_CREDENTIALS[mode]
        self._trace = trace
        self._proposal = proposal
        self._before_return = before_return

    def __call__(
        self,
        _model_path: Path,
        prompt: PromptPlan | str,
        _task: TaskSpec,
        _reference: CodexReference,
    ) -> ProposalManifest:
        assert PRIVATE_CANARY not in repr(prompt)
        assert self.credential not in repr(prompt)
        self._trace.provider_calls.append(self.mode)
        if self._before_return is not None:
            self._before_return()
        return self._proposal


def _write_policy(
    root: Path,
    *,
    private_paths: tuple[str, ...] = ("src/business/**", "tests/private/**"),
    public_paths: tuple[str, ...] = (),
    default_access: str = "public",
) -> Path:
    destination = root / ".gludd" / "self-improve-policy.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "default_access": default_access,
                "private_paths": list(private_paths),
                "public_paths": list(public_paths),
            }
        ),
        encoding="utf-8",
    )
    return destination


def _task(task_id: str = "S83.145") -> TaskSpec:
    return TaskSpec(
        task_id=task_id,
        objective="Improve one policy-approved repository file.",
        canonical_make_commands=(
            "make test-specific TESTFILE=tests/public/test_rules.py",
        ),
    )


def _reference(
    path: str = PUBLIC_PATH,
    test_path: str = PUBLIC_TEST_PATH,
) -> CodexReference:
    return CodexReference(
        baseline_sha="a" * 40,
        reference_sha="b" * 40,
        changed_files=frozenset({path}),
        test_files=frozenset({test_path}),
        changed_lines=1,
        elapsed_seconds=0.1,
    )


def _prompt(path: str = PUBLIC_PATH, source: str | None = PUBLIC_OLD) -> PromptPlan:
    rendered = "<new file>" if source is None else source
    return PromptPlan(
        shards=(PromptShard((path,), f"L1|{rendered}", ()),),
        source_bytes=0 if source is None else len(source.encode()),
        baseline_files=((path, source),),
        proposal_protocol="self-improve-compact-proposal-v4",
    )


def _proposal(
    path: str,
    operation: str,
    *,
    tests: tuple[str, ...] = (PUBLIC_TEST_PATH,),
    task_id: str = "S83.145",
) -> ProposalManifest:
    old_text, new_text = {
        "create": ("", PUBLIC_NEW),
        "replace": (PUBLIC_OLD, PUBLIC_NEW),
        "delete": (PUBLIC_OLD, ""),
    }[operation]
    if path == PRIVATE_PATH:
        old_text, new_text = {
            "create": ("", f"{PRIVATE_CANARY}\n"),
            "replace": (PRIVATE_OLD, f"{PRIVATE_CANARY}_NEW\n"),
            "delete": (PRIVATE_OLD, ""),
        }[operation]
    return ProposalManifest.from_json(
        json.dumps(
            {
                "schema_version": 1,
                "baseline_sha": "a" * 40,
                "task_id": task_id,
                "edits": [
                    {
                        "operation": operation,
                        "path": path,
                        "old_text": old_text,
                        "new_text": new_text,
                    }
                ],
                "tests": list(tests),
                "make_commands": [
                    "make test-specific TESTFILE=tests/public/test_rules.py"
                ],
                "commit_message": "feat: improve approved public rule",
            }
        )
    )


def _approve(
    root: Path,
    *,
    todo_id: str = "todo-private-policy-e2e",
    project_id: str | None = None,
    task: TaskSpec | None = None,
    path: str = PUBLIC_PATH,
    test_path: str = PUBLIC_TEST_PATH,
    source: str | None = PUBLIC_OLD,
    mechanical_proposal: ProposalManifest | None = None,
) -> ApprovedSelfImprovePlan:
    return ApprovedSelfImprovePlan.approve(
        approval_id=todo_id,
        todo_id=todo_id,
        project_id=project_id or f"project-{root.name}",
        repo_root=root,
        task=task or _task(),
        reference=_reference(path, test_path),
        prompt=_prompt(path, source),
        required_output_tokens=256,
        max_attempts=2,
        mechanical_proposal=mechanical_proposal,
    )


def _candidate() -> PlannedModelCandidate:
    model = get_model("qwen2.5-coder-0.5b")
    assert model is not None
    return PlannedModelCandidate(model, "c" * 40, 0.5, 0)


def _attempt_result(
    root: Path,
    trace: _Trace,
    mode: str,
    bound: PlanBoundProposal,
) -> AttemptResult:
    trace.evaluator_calls.append(mode)
    changed_lines = apply_proposal(root, bound.proposal)
    return AttemptResult(
        comparison=ComparisonResult(
            score=1.0,
            accepted=True,
            blockers=(),
            changed_file_precision=1.0,
            changed_file_recall=1.0,
        ),
        evidence=CandidateEvidence(
            changed_files=frozenset(edit.path for edit in bound.proposal.edits),
            tests_passed=True,
            warnings=0,
            coverage_aggregate=90.0,
            coverage_min_file=80.0,
            ruff_passed=True,
            mypy_passed=True,
            docstrings_passed=True,
            markdown_passed=True,
            cleanup_passed=True,
            commit_count=1,
            worktree_clean=True,
            elapsed_seconds=0.1,
            changed_lines=changed_lines,
        ),
        patch_equivalence="equivalent",
        proposal=bound.proposal,
        diagnostics="",
        attempt_identity_digest=bound.attempt_identity_digest,
    )


def _runner(
    root: Path,
    mode: str,
    trace: _Trace,
    proposal: ProposalManifest,
    *,
    before_provider_return: Callable[[], None] | None = None,
) -> ManagedSelfImproveRunner:
    provider = _FakeProvider(
        mode,
        trace,
        proposal,
        before_return=before_provider_return,
    )
    manager = _FakeModelManager(root, mode, trace)
    outcomes = _Outcomes(trace)

    def evaluate(
        _task_spec: TaskSpec,
        _reference_spec: CodexReference,
        bound: PlanBoundProposal,
        _attempt: int,
        *,
        expected_attempt_identity_digest: str,
        merge: bool,
    ) -> AttemptResult:
        assert bound.attempt_identity_digest == expected_attempt_identity_digest
        assert merge is False
        return _attempt_result(root, trace, mode, bound)

    return ManagedSelfImproveRunner(
        proposal_generator=cast(Any, provider),
        attempt_evaluator=cast(Any, evaluate),
        model_manager_factory=cast(Any, lambda **_kwargs: manager),
        outcome_adapter_factory=lambda _cache: outcomes,
        candidate_planner=cast(Any, lambda *_args, **_kwargs: (_candidate(),)),
        hardware_probe=cast(Any, lambda: object()),
        progress_sink=trace.events.append,
    )


def _prepare_path(root: Path, path: str, operation: str) -> Path:
    destination = root / path
    destination.parent.mkdir(parents=True, exist_ok=True)
    if operation in {"replace", "delete"}:
        content = PRIVATE_OLD if path == PRIVATE_PATH else PUBLIC_OLD
        destination.write_text(content, encoding="utf-8")
    return destination


def _observable(trace: _Trace, error: BaseException | None = None) -> str:
    return json.dumps(
        {
            "error": "" if error is None else str(error),
            "events": trace.events,
            "provider_calls": trace.provider_calls,
            "evaluator_calls": trace.evaluator_calls,
        },
        sort_keys=True,
    )


def _assert_no_private_data_leaked(trace: _Trace, error: BaseException | None) -> None:
    observable = _observable(trace, error)
    for forbidden in (
        PRIVATE_CANARY,
        PRIVATE_PATH,
        PRIVATE_TEST_PATH,
        *PROVIDER_CREDENTIALS.values(),
    ):
        assert forbidden not in observable


@pytest.mark.parametrize("mode", PROVIDER_MODES)
@pytest.mark.asyncio
async def test_self_improvement_uses_common_todo_ranking_compute_and_real_edit(
    tmp_path: Path,
    mode: str,
) -> None:
    """Prove managed improvement is ordinary ranked work with bounded controls."""
    _write_policy(tmp_path)
    destination = _prepare_path(tmp_path, PUBLIC_PATH, "replace")
    plan = _approve(tmp_path)
    plan_artifact = plan.to_json()
    normal_todo_id = f"todo-normal-{mode}"
    trace = _Trace()
    lifecycle = _ComputeLifecycle()
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as session:
            await persist_project(
                ProjectRepository(session),
                project_id=plan.project_id,
                name="Private policy E2E",
                weight=100.0,
                dispatch_mode="active",
            )
            repository = TodoRepository(session)
            await repository.create(
                {
                    "todo_id": normal_todo_id,
                    "title": "Ordinary application work",
                    "description": "Competes in the same durable scheduler.",
                    "status": TodoStatus.QUEUED.value,
                    "priority": 10,
                    "queue": "core",
                    "work_type": "docs",
                    "project_id": plan.project_id,
                }
            )
            await repository.create(
                {
                    "todo_id": plan.todo_id,
                    "title": "Managed self-improvement",
                    "description": "Apply one approved public repository edit.",
                    "status": TodoStatus.QUEUED.value,
                    "priority": 100,
                    "queue": "core",
                    "work_type": "self_improve",
                    "resource_profile": "hybrid",
                    "project_id": plan.project_id,
                    "plan_artifact": plan_artifact,
                    "approved_artifact_digest": hashlib.sha256(
                        plan_artifact.encode("utf-8")
                    ).hexdigest(),
                    "approval_policy": "managed_self_improve_plan",
                }
            )
            await session.commit()
            assert await session.get(ProjectModel, plan.project_id) is not None

        project_manager = SimpleNamespace(
            select_project=lambda: SimpleNamespace(project_id=plan.project_id)
        )
        loop = EventLoop(
            session=factory,
            runner=lifecycle,
            config={
                "repo_root": str(tmp_path),
                "execution_environment": {
                    "machine_cpus": 2,
                    "machine_memory_mb": 4096,
                    "machine_disk_gb": 16,
                },
            },
            project_manager=project_manager,
            project_workspace={
                plan.project_id: SimpleNamespace(repo_dir=tmp_path),
            },
            floor_controller=cast(
                Any,
                SimpleNamespace(get_max_active=lambda: 1),
            ),
            self_improve_runner_factory=lambda root: _runner(
                root,
                mode,
                trace,
                _proposal(PUBLIC_PATH, "replace"),
            ),
        )

        metrics = await loop.tick()

        assert [call["state"] for call in lifecycle.calls] == ["present"]
        assert lifecycle.calls[0]["project_root"] == tmp_path.resolve()
        assert [todo.todo_id for todo in loop._tick_state["claimed_todos"]] == [
            plan.todo_id
        ]
        assert metrics["todos_dispatched"] == 1
        assert destination.read_text(encoding="utf-8") == PUBLIC_NEW
        assert trace.provider_calls == [mode]
        assert trace.evaluator_calls == [mode]
        assert trace.acquisitions == [mode]
        assert len(trace.outcomes) == 1

        async with factory() as session:
            repository = TodoRepository(session)
            improved = await repository.get_by_id(
                plan.todo_id,
                project_id=plan.project_id,
            )
            ordinary = await repository.get_by_id(
                normal_todo_id,
                project_id=plan.project_id,
            )
            assert improved is not None
            assert ordinary is not None
            assert improved.status == TodoStatus.AWAITING_RESULT.value
            assert ordinary.status == TodoStatus.QUEUED.value
            await repository.transition(
                improved.todo_id,
                TodoStatus.CANCELLED,
                improved.version,
                project_id=plan.project_id,
            )
            await repository.transition(
                ordinary.todo_id,
                TodoStatus.CANCELLED,
                ordinary.version,
                project_id=plan.project_id,
            )
            await session.commit()
            loop._active_session = session
            loop._todo_repo = repository
            loop._tick_project_id = plan.project_id
            await loop._phase_release_compute_demand()

        assert [call["state"] for call in lifecycle.calls] == ["present", "absent"]
        assert loop._tick_state["compute_demand"]["runnable_todos"] == 0
        _assert_no_private_data_leaked(trace, None)
    finally:
        await engine.dispose()


@pytest.mark.parametrize("mode", PROVIDER_MODES)
@pytest.mark.asyncio
async def test_discovered_approved_work_reaches_verified_terminal_release_without_manual_todo_mutation(
    tmp_path: Path,
    mode: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prove the durable producer-to-release chain without injected todo rows."""
    _write_policy(tmp_path)
    destination = _prepare_path(tmp_path, PUBLIC_PATH, "replace")
    project_id = "project-durable-chain"
    trace = _Trace()
    chain: list[str] = []
    lifecycle = _ComputeLifecycle(chain)
    discovery_runs = 0
    approved_task_id = ""

    def discover(
        _harness: SelfImprovementHarness,
        _recurring: list[object],
    ) -> list[object]:
        nonlocal discovery_runs
        discovery_runs += 1
        if discovery_runs > 1:
            return []
        chain.append("discovered")
        return [object()]

    def produce(
        _harness: SelfImprovementHarness,
        findings: list[object],
    ) -> list[dict[str, object]]:
        assert len(findings) == 1
        return [
            {
                "title": "Repair one policy-approved public rule",
                "description": "Improve one policy-approved repository file.",
                "source": "durable_chain_acceptance",
                "gap_type": "accepted_rule_gap",
                "source_file": PUBLIC_PATH,
                "work_type": "code",
                "task_type": "feature",
                "blocker_kind": "",
                "incident_count": 1,
                "recent_todo_ids": [],
                "test_commands": [
                    "make test-specific TESTFILE=tests/public/test_rules.py"
                ],
                "priority": "critical",
            }
        ]

    class _Reviewer:
        def review_return(
            self,
            task_return: Any,
            *,
            candidate_todos: list[object],
            artifacts: list[object],
        ) -> TaskDecision:
            assert candidate_todos == []
            assert artifacts == []
            chain.append("review_verified")
            return TaskDecision(
                return_id=task_return.return_id,
                matched_todo_id=task_return.todo_id,
                decision="complete",
                confidence=1.0,
                evidence_refs=[f"artifact:{PUBLIC_PATH}"],
            )

    class _PromotionCoordinator:
        async def promote(self, **kwargs: object) -> ManagedPromotionReceipt:
            todo_id = kwargs.get("todo_id")
            return_id = kwargs.get("return_id")
            receipt_project_id = kwargs.get("project_id")
            repo_root = kwargs.get("repo_root")
            assert isinstance(todo_id, str)
            assert isinstance(return_id, str)
            assert receipt_project_id == project_id
            assert repo_root == tmp_path
            artifact_digest = "e" * 64
            chain.append("promotion_verified")
            return ManagedPromotionReceipt(
                artifact_digest=artifact_digest,
                plan_identity_digest="a" * 64,
                attempt_identity_digest="b" * 64,
                todo_id=todo_id,
                project_id=project_id,
                repo_root=tmp_path,
                return_id=return_id,
                development_commit="d" * 40,
                marker=f"Gludd-Self-Improve-Artifact={artifact_digest}",
                fencing_token=1,
                marker_verified=True,
            )

    monkeypatch.setattr(SelfImprovementHarness, "run_gap_analysis", discover)
    monkeypatch.setattr(SelfImprovementHarness, "generate_fix_todos", produce)

    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as session:
            await persist_project(
                ProjectRepository(session),
                project_id=project_id,
                name="Durable chain acceptance",
                weight=100.0,
                dispatch_mode="active",
            )
            await session.commit()

        project_manager = SimpleNamespace(
            select_project=lambda: SimpleNamespace(project_id=project_id)
        )
        promotion = _PromotionCoordinator()
        loop = EventLoop(
            session=factory,
            runner=lifecycle,
            config={
                "repo_root": str(tmp_path),
                "self_improve": {
                    "execution_mode": "local",
                    "ingest_recurring_failures": False,
                },
                "execution_environment": {
                    "machine_cpus": 2,
                    "machine_memory_mb": 4096,
                    "machine_disk_gb": 16,
                },
            },
            project_manager=project_manager,
            project_workspace={
                project_id: SimpleNamespace(repo_dir=tmp_path),
            },
            floor_controller=cast(Any, SimpleNamespace(get_max_active=lambda: 1)),
            self_improve_interval=1,
            self_improve_runner_factory=lambda root: _runner(
                root,
                mode,
                trace,
                _proposal(PUBLIC_PATH, "replace", task_id=approved_task_id),
            ),
            reviewer=_Reviewer(),
            self_improve_promotion_factory=lambda *_args: promotion,
        )
        monkeypatch.setattr(loop, "_detect_grinding_patterns", lambda: [])

        discovery_metrics = await loop.tick()

        assert discovery_metrics["self_improve_todos_persisted"] == 1
        assert lifecycle.calls == []
        async with factory() as session:
            repository = TodoRepository(session)
            discovered = await repository.list_by_work_type(
                "self_improve",
                project_id=project_id,
            )
            assert len(discovered) == 1
            candidate = discovered[0]
            assert candidate.status == TodoStatus.APPROVAL_REQUIRED.value
            assert candidate.created_by == "self_improve_harness"
            assert candidate.priority == 20
            request = ManagedSelfImprovePlanRequest.from_json(candidate.plan_artifact)
            approved_task_id = request.task.task_id
            plan = _approve(
                tmp_path,
                todo_id=candidate.todo_id,
                project_id=project_id,
                task=request.task,
            )
            prepared = await repository.update(
                candidate.todo_id,
                {"plan_artifact": plan.to_json()},
                expected_version=candidate.version,
                project_id=project_id,
            )

            def resolve_repo(requested_project_id: str) -> Path:
                assert requested_project_id == project_id
                return tmp_path

            approved = await SelfImproveApprovalManager(
                managed_repo_resolver=resolve_repo
            ).approve_by_id(
                repository,
                prepared.todo_id,
                project_id=project_id,
            )
            await session.commit()
            assert approved.status == TodoStatus.QUEUED.value
            chain.append("approved")

        execution_metrics = await loop.tick()

        assert execution_metrics["todos_dispatched"] == 1
        assert [todo.todo_id for todo in loop._tick_state["claimed_todos"]] == [
            approved.todo_id
        ]
        assert [call["state"] for call in lifecycle.calls] == ["present"]
        assert destination.read_text(encoding="utf-8") == PUBLIC_NEW
        assert trace.provider_calls == [mode]
        assert trace.evaluator_calls == [mode]
        assert trace.acquisitions == [mode]
        assert len(trace.outcomes) == 1
        async with factory() as session:
            executed = await TodoRepository(session).get_by_id(
                approved.todo_id,
                project_id=project_id,
            )
            assert executed is not None
            assert executed.status == TodoStatus.AWAITING_RESULT.value
            persisted_return = await TaskReturnRepository(session).get_by_id(
                f"RET-EXEC-{approved.todo_id}"
            )
            assert persisted_return is not None
            assert persisted_return.work_type == "self_improve"
            assert persisted_return.resource_profile == executed.resource_profile

        review_metrics = await loop.tick()

        assert review_metrics["returns_reviewed"] == 1
        async with factory() as session:
            terminal = await TodoRepository(session).get_by_id(
                approved.todo_id,
                project_id=project_id,
            )
            assert terminal is not None
            assert terminal.status == TodoStatus.COMPLETE.value
            assert terminal.status != TodoStatus.CANCELLED.value
        assert [call["state"] for call in lifecycle.calls] == ["present", "absent"]
        assert loop._tick_state["compute_demand"] == {
            "state": "idle",
            "runnable_todos": 0,
            "execution_environment": "absent",
        }
        assert chain == [
            "discovered",
            "approved",
            "compute_present",
            "review_verified",
            "promotion_verified",
            "compute_absent",
        ]
        _assert_no_private_data_leaked(trace, None)
    finally:
        await engine.dispose()


@pytest.mark.parametrize("mode", PROVIDER_MODES)
@pytest.mark.parametrize("operation", ("create", "replace", "delete"))
def test_generated_private_operations_stop_before_evaluation_learning_and_retry(
    tmp_path: Path,
    mode: str,
    operation: str,
) -> None:
    _write_policy(tmp_path)
    destination = _prepare_path(tmp_path, PRIVATE_PATH, operation)
    plan = _approve(tmp_path)
    trace = _Trace()
    service = _runner(tmp_path, mode, trace, _proposal(PRIVATE_PATH, operation))

    with pytest.raises(SelfImprovePolicyViolation) as captured:
        service.run(plan)

    assert trace.provider_calls == [mode]
    assert trace.acquisitions == [mode]
    assert trace.evaluator_calls == []
    assert trace.outcomes == []
    assert sum("SELF_IMPROVE_ATTEMPT_START" in event for event in trace.events) == 1
    assert any("SELF_IMPROVE_POLICY_BLOCKED" in event for event in trace.events)
    assert hashlib.sha256(PRIVATE_PATH.encode()).hexdigest() in _observable(trace)
    if operation == "create":
        assert not destination.exists()
    else:
        assert destination.read_text(encoding="utf-8") == PRIVATE_OLD
    _assert_no_private_data_leaked(trace, captured.value)


@pytest.mark.parametrize("mode", PROVIDER_MODES)
@pytest.mark.parametrize("operation", ("create", "replace", "delete"))
def test_public_operations_cross_provider_and_evaluator_boundaries(
    tmp_path: Path,
    mode: str,
    operation: str,
) -> None:
    _write_policy(tmp_path)
    destination = _prepare_path(tmp_path, PUBLIC_PATH, operation)
    source = None if operation == "create" else PUBLIC_OLD
    plan = _approve(tmp_path, source=source)
    trace = _Trace()

    result = _runner(tmp_path, mode, trace, _proposal(PUBLIC_PATH, operation)).run(plan)

    assert result.accepted is True
    assert trace.provider_calls == [mode]
    assert trace.acquisitions == [mode]
    assert trace.evaluator_calls == [mode]
    assert len(trace.outcomes) == 1
    assert any("SELF_IMPROVE_POLICY_LOADED" in event for event in trace.events)
    assert any("SELF_IMPROVE_MODEL_OUTCOME" in event for event in trace.events)
    if operation == "delete":
        assert not destination.exists()
    else:
        assert destination.read_text(encoding="utf-8") == PUBLIC_NEW
    _assert_no_private_data_leaked(trace, None)


@pytest.mark.parametrize("mode", PROVIDER_MODES)
@pytest.mark.parametrize("scope", ("prompt", "reference", "proposal-test"))
def test_private_approved_input_scope_never_reaches_a_provider(
    tmp_path: Path,
    mode: str,
    scope: str,
) -> None:
    _write_policy(tmp_path)
    trace = _Trace()

    with pytest.raises(SelfImprovePolicyViolation) as captured:
        if scope == "prompt":
            _approve(tmp_path, path=PRIVATE_PATH, source=f"{PRIVATE_CANARY}\n")
        elif scope == "reference":
            _approve(tmp_path, test_path=PRIVATE_TEST_PATH)
        else:
            _approve(
                tmp_path,
                mechanical_proposal=_proposal(
                    PUBLIC_PATH,
                    "replace",
                    tests=(PRIVATE_TEST_PATH,),
                ),
            )

    assert mode in PROVIDER_MODES
    assert trace.provider_calls == []
    assert trace.acquisitions == []
    assert trace.evaluator_calls == []
    assert trace.outcomes == []
    _assert_no_private_data_leaked(trace, captured.value)


@pytest.mark.parametrize("mode", PROVIDER_MODES)
@pytest.mark.parametrize("failure", ("malformed", "symlink", "drift"))
def test_policy_invalidity_and_toctou_fail_closed_at_effect_boundaries(
    tmp_path: Path,
    mode: str,
    failure: str,
) -> None:
    policy_path = _write_policy(tmp_path, private_paths=("unrelated/**",))
    _prepare_path(tmp_path, PUBLIC_PATH, "replace")
    plan = _approve(tmp_path)
    trace = _Trace()
    before_return: Callable[[], None] | None = None

    if failure == "malformed":
        policy_path.write_text("{" + PRIVATE_CANARY, encoding="utf-8")
    elif failure == "symlink":
        replacement = tmp_path / "replacement-policy.json"
        replacement.write_text(policy_path.read_text(encoding="utf-8"), encoding="utf-8")
        policy_path.unlink()
        policy_path.symlink_to(replacement)
    else:
        def replace_policy_during_provider_call() -> None:
            _write_policy(tmp_path, private_paths=(PUBLIC_PATH,))

        before_return = replace_policy_during_provider_call

    service = _runner(
        tmp_path,
        mode,
        trace,
        _proposal(PUBLIC_PATH, "replace"),
        before_provider_return=before_return,
    )
    with pytest.raises(SelfImprovePolicyViolation) as captured:
        service.run(plan)

    expected_calls = [mode] if failure == "drift" else []
    assert trace.provider_calls == expected_calls
    assert trace.evaluator_calls == []
    assert trace.outcomes == []
    assert any("SELF_IMPROVE_POLICY_BLOCKED" in event for event in trace.events)
    assert captured.value.__cause__ is None
    _assert_no_private_data_leaked(trace, captured.value)


@pytest.mark.parametrize("mode", PROVIDER_MODES)
def test_identical_paths_remain_isolated_by_each_projects_inverse_policy(
    tmp_path: Path,
    mode: str,
) -> None:
    private_root = tmp_path / "private-project"
    public_root = tmp_path / "public-project"
    _write_policy(
        private_root,
        private_paths=(SHARED_PATH,),
        public_paths=(PUBLIC_TEST_PATH,),
        default_access="private",
    )
    _write_policy(
        public_root,
        private_paths=(),
        public_paths=(SHARED_PATH, PUBLIC_TEST_PATH),
        default_access="private",
    )
    private_trace = _Trace()
    public_trace = _Trace()

    with pytest.raises(SelfImprovePolicyViolation):
        _approve(private_root, path=SHARED_PATH, source=None)

    public_plan = _approve(public_root, path=SHARED_PATH, source=None)
    public_result = _runner(
        public_root,
        mode,
        public_trace,
        _proposal(SHARED_PATH, "create"),
    ).run(public_plan)

    assert private_trace.provider_calls == []
    assert public_result.accepted is True
    assert public_trace.provider_calls == [mode]
    assert (public_root / SHARED_PATH).read_text(encoding="utf-8") == PUBLIC_NEW
    assert load_self_improve_policy(private_root).digest != public_plan.policy_digest
    assert SHARED_PATH not in _observable(public_trace)
    _assert_no_private_data_leaked(public_trace, None)


@pytest.mark.parametrize("mode", PROVIDER_MODES)
def test_absent_policy_preserves_public_compatibility_without_credentials(
    tmp_path: Path,
    mode: str,
) -> None:
    _prepare_path(tmp_path, PUBLIC_PATH, "replace")
    plan = _approve(tmp_path)
    trace = _Trace()

    result = _runner(
        tmp_path,
        mode,
        trace,
        _proposal(PUBLIC_PATH, "replace"),
    ).run(plan)

    assert result.accepted is True
    assert trace.provider_calls == [mode]
    assert not (tmp_path / ".gludd" / "self-improve-policy.json").exists()
    assert plan.policy_digest == load_self_improve_policy(tmp_path).digest
    _assert_no_private_data_leaked(trace, None)
