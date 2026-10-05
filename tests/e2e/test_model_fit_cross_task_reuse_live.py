"""Live E2E: cross-task weight-DB reuse — recorded performance drives model
selection for a never-seen task type.

Offline-safe (real SQLite model-performance DB + real router; no model
download or server). Proves the loop the user asked for: the weight DB is
updated by task performance, and that knowledge is used for OTHER tasks —
not only the task that produced it.

Runtime bounded to < 2 minutes by the pytest-timeout marker.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base
from general_ludd.db.repository import ModelPerformanceRepository
from general_ludd.models.performance_router import ModelPerformanceRouter

_GOOD_MODEL_PROFILE = "local/qwen2.5-0.5b"
_BAD_MODEL_PROFILE = "local/qwen2.5-0.5b-bad"


@pytest_asyncio.fixture
async def repo_session() -> AsyncSession:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def _record(
    repo: ModelPerformanceRepository,
    model_profile_id: str,
    *,
    task_type: str,
    success: bool,
    cost_usd: float,
) -> None:
    service, _, model_name = model_profile_id.partition("/")
    await repo.record_call(
        service=service,
        model_name=model_name,
        model_profile_id=model_profile_id,
        task_type=task_type,
        success=success,
        duration_ms=100.0,
        cost_usd=cost_usd,
    )


@pytest.mark.timeout(120)
async def test_cross_task_weight_db_reuse(repo_session: AsyncSession) -> None:
    """Performance recorded for one task type must be reused to pick the
    model for a DIFFERENT, never-seen task type (no hardcoded fallback)."""
    repo = ModelPerformanceRepository(session=repo_session)
    router = ModelPerformanceRouter(perf_repo=repo, config={"min_calls": 1})

    await _record(repo, _GOOD_MODEL_PROFILE, task_type="local_factoid", success=True, cost_usd=0.05)
    await _record(repo, _BAD_MODEL_PROFILE, task_type="local_factoid", success=False, cost_usd=0.10)

    picked = await router.select_model("brand_new_task_type")
    assert picked["fallback"] is False, f"cross-task reuse must not fall back, got {picked}"
    assert picked["reason"] == "cross_task_reuse", f"unexpected selection reason: {picked}"
    assert picked["model_name"] == "qwen2.5-0.5b", (
        f"the weight DB must pick the model that performed better on other tasks, got {picked}"
    )

    global_ranking = await router.get_global_rankings(strategy="quality")
    assert global_ranking, "global rankings must be non-empty"
    assert global_ranking[0]["model_name"] == "qwen2.5-0.5b"
    assert global_ranking[0]["sample_count"] == 1


@pytest.mark.timeout(120)
async def test_capability_evaluation_feeds_weight_db(repo_session: AsyncSession) -> None:
    """POST /admin/models/local/evaluate records capability outcomes into the
    weight DB — capability probes are task performance evidence."""
    from fastapi.testclient import TestClient

    from general_ludd.daemon import create_daemon_app

    repo = ModelPerformanceRepository(session=repo_session)
    app = create_daemon_app(tick_interval=0.0)
    app.state.model_perf_repo = repo
    client = TestClient(app, raise_server_exceptions=False)

    ok = client.post(
        "/admin/models/local/evaluate",
        json={
            "model_id": "qwen2.5-0.5b",
            "task_kind": "coding",
            "total_cases": 10,
            "passed_cases": 10,
        },
    )
    assert ok.status_code == 200, ok.text
    bad = client.post(
        "/admin/models/local/evaluate",
        json={
            "model_id": "qwen2.5-0.5b-bad",
            "task_kind": "coding",
            "total_cases": 10,
            "passed_cases": 0,
        },
    )
    assert bad.status_code == 200, bad.text

    ranking = await repo.get_ranking("coding")
    assert len(ranking) == 2, f"weight DB must hold both capability outcomes, got {ranking}"

    router = ModelPerformanceRouter(perf_repo=repo, config={"min_calls": 1})
    picked = await router.select_model("coding")
    assert picked["model_name"] == "qwen2.5-0.5b", (
        f"capability-evidence-driven selection must prefer the passing model, got {picked}"
    )


@pytest.mark.timeout(60)
async def test_capability_matrix_drives_policy_authorization() -> None:
    """Capability outcomes become CapabilityEvidence, and authorize() must
    approve a local dispatch for the small model when the core checks pass —
    or escalate when the evidence says the model failed. Offline-safe: the
    evidence is built from a canned capability matrix, exercising the exact
    loop the daemon's evaluate endpoint performs before a small-model
    dispatch is allowed."""
    from general_ludd.routing_roles.small_model_policy import (
        CapabilityEvidence,
        ModelIdentity,
        SmallModelTaskPolicy,
        SmallModelTaskSpec,
        TaskImpact,
    )
    from general_ludd.schemas.benchmark import TaskRole

    model_id = "local/qwen2.5-0.5b"

    spec = SmallModelTaskSpec(
        task_id="fpx.1.capability.authorized",
        task_kind="coding",
        role=TaskRole.CODER,
        collection="general_ludd.agent",
        input_digest="f" * 64,
        impacts=frozenset({TaskImpact.READ_SOURCE, TaskImpact.WRITE_ARTIFACT}),
        acceptance_checks=("syntax_valid", "import_ok", "run_without_crash"),
    )
    spec_contract_digest = spec.acceptance_contract_digest

    identity = ModelIdentity(
        model_profile_id=model_id,
        model_artifact_digest="e" * 64,
        runtime_config_digest="c" * 64,
        prompt_contract_digest="d" * 64,
    )
    policy = SmallModelTaskPolicy()

    def _evidence(task_kind: str, passed: bool) -> CapabilityEvidence:
        return CapabilityEvidence(
            model_profile_id=model_id,
            task_kind=task_kind,
            collection="general_ludd.agent",
            role=TaskRole.CODER,
            suite_id=f"suite-{task_kind}",
            suite_revision="v1",
            total_cases=25,
            passed_cases=25 if passed else 0,
            collection_ok=True,
            local_only=True,
            model_identity_digest=identity.fingerprint,
            acceptance_contract_digest=spec_contract_digest,
            evidence_digest=("1" if passed else "0") + "a" * 31 + "e" * 32,
        )

    passing = policy.authorize(spec, identity, [_evidence("coding", True)])
    assert passing.action.value == "local", f"capability-proven dispatch must be LOCAL, got {passing.action}"

    failing = policy.authorize(spec, identity, [_evidence("coding", False)])
    assert failing.action.value != "local", "failed capability evidence must escalate, not approve"


@pytest.mark.timeout(120)
async def test_cross_task_reuse_ranks_good_model_above_bad(repo_session: AsyncSession) -> None:
    """The global ranking must order models by their cross-task record."""
    repo = ModelPerformanceRepository(session=repo_session)
    router = ModelPerformanceRouter(perf_repo=repo, config={"min_calls": 1})

    await _record(repo, _GOOD_MODEL_PROFILE, task_type="game_gen", success=True, cost_usd=0.05)
    await _record(repo, _GOOD_MODEL_PROFILE, task_type="summary", success=True, cost_usd=0.05)
    await _record(repo, _BAD_MODEL_PROFILE, task_type="game_gen", success=False, cost_usd=0.10)

    ranking = await router.get_global_rankings(strategy="quality")
    assert len(ranking) == 2, f"expected 2 models in the global ranking, got {ranking}"
    assert ranking[0]["model_name"] == "qwen2.5-0.5b"
    assert ranking[0]["sample_count"] == 2
    assert ranking[1]["model_name"] == "qwen2.5-0.5b-bad"
    assert ranking[1]["success_rate"] == 0.0
    assert ranking[0]["score"] > ranking[1]["score"]
