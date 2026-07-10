"""Regression: AdaptiveRouter's avg_cost is a REAL computed value, and it
actually drives cheaper-equivalent routing decisions.

Residual D-#59/#69 (TASKS.md Phase D): "avg_cost real-value routing:
AdaptiveRouter daemon-wired; avg_cost regression pending."

Wiring under test:

  * ``daemon.py`` (``_lifespan``/route-context construction, around line 2241)
    wires ``AdaptiveRouter(benchmark_repo=BenchmarkRepository(session_factory=
    session_factory), ...)`` -- a REAL, DB-backed ``BenchmarkRepository``, not
    a stub -- into the app-level adaptive router.
  * ``BenchmarkRepository.get_aggregate_scores`` (db/repository.py:943)
    computes ``avg_cost`` via a real SQL ``func.avg(BenchmarkResultModel.
    cost_usd)`` aggregate, grouped by (prompt, model, task_type, project,
    role) over rows where ``success IS TRUE``.
  * ``AdaptiveRouter._candidate_from_agg`` / ``_get_cheapest_for_task`` /
    ``get_leaderboard`` (scoring/router.py) read that ``avg_cost`` straight
    into ``RoutingCandidate.avg_cost_usd`` with a ``0.0`` fallback ONLY when
    the aggregate is literally missing the key -- so a silent regression
    where ``avg_cost`` is dropped/miscomputed would degrade every
    cost-aware routing decision (the cap check in ``route()``, the
    cost-adjusted rank, and the "cheapest quality-equivalent" tie-break in
    ``_select_cheapest_equivalent``) back to constant-0.0, effectively
    disabling cost-aware routing without any visible error.

Existing coverage (``tests/unit/test_router_cheaper_equivalent.py``) already
exercises the tie-break logic thoroughly, but ONLY against a hand-fed fake
dict repo -- it assumes ``avg_cost`` is already a correct, real number. The
gap this file closes is the layer *underneath* that: proving ``avg_cost`` is
actually computed from real persisted ``BenchmarkResultModel`` rows (not a
constant/hardcoded ``0.0``), through the REAL ``BenchmarkRepository`` against
a real (in-memory sqlite) database, and that this real value is what the
router uses to prefer a cheaper model when quality is tied.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base, BenchmarkResultModel
from general_ludd.db.repository import BenchmarkRepository
from general_ludd.schemas.benchmark import TaskType
from general_ludd.scoring.router import AdaptiveRouter


@pytest_asyncio.fixture
async def db_factory():
    """Real (in-memory) sqlite DB with the full schema."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        yield factory
    finally:
        await engine.dispose()


async def _seed_benchmark_rows(
    factory,
    *,
    model_profile_id: str,
    prompt_profile_id: str,
    task_type: str,
    cost_values: list[float],
    completion: float = 0.8,
    code_quality: float = 0.8,
    instruction: float = 0.8,
    efficiency: float = 0.8,
) -> None:
    """Insert one BenchmarkResultModel row per cost value, all successful."""
    async with factory() as session:
        for cost in cost_values:
            session.add(
                BenchmarkResultModel(
                    prompt_profile_id=prompt_profile_id,
                    model_profile_id=model_profile_id,
                    task_type=task_type,
                    task_description="regression seed",
                    completion_score=completion,
                    code_quality_score=code_quality,
                    instruction_adherence_score=instruction,
                    token_efficiency_score=efficiency,
                    time_seconds=1.0,
                    input_tokens=100,
                    output_tokens=100,
                    cost_usd=cost,
                    success=True,
                )
            )
        await session.commit()


class TestAvgCostIsRealNotHardcoded:
    @pytest.mark.asyncio
    async def test_avg_cost_computed_from_real_benchmark_rows(self, db_factory) -> None:
        """avg_cost must equal the REAL mean of the seeded cost_usd values,
        not a constant 0.0 (the silent-regression failure mode)."""
        cost_values = [0.01, 0.02, 0.03]
        await _seed_benchmark_rows(
            db_factory,
            model_profile_id="model-a",
            prompt_profile_id="prompt-a",
            task_type=TaskType.BUG_FIX.value,
            cost_values=cost_values,
        )
        repo = BenchmarkRepository(session_factory=db_factory)

        scores = await repo.get_aggregate_scores(task_type=TaskType.BUG_FIX.value)

        assert len(scores) == 1
        row = scores[0]
        assert row["model_profile_id"] == "model-a"
        assert row["sample_count"] == len(cost_values)
        assert row["avg_cost"] == pytest.approx(sum(cost_values) / len(cost_values))
        assert row["avg_cost"] != 0.0, (
            "avg_cost regressed to a hardcoded/constant 0.0 despite real "
            "non-zero cost_usd rows -- cost-aware routing would be silently "
            "disabled."
        )

    @pytest.mark.asyncio
    async def test_avg_cost_differs_across_models_reflecting_real_data(
        self, db_factory
    ) -> None:
        """Two models with genuinely different real cost histories must
        produce genuinely different avg_cost values -- guards against a
        regression that collapses every group to the same (e.g. 0.0) value."""
        await _seed_benchmark_rows(
            db_factory,
            model_profile_id="model-cheap",
            prompt_profile_id="prompt-a",
            task_type=TaskType.BUG_FIX.value,
            cost_values=[0.001, 0.001, 0.001],
        )
        await _seed_benchmark_rows(
            db_factory,
            model_profile_id="model-expensive",
            prompt_profile_id="prompt-a",
            task_type=TaskType.BUG_FIX.value,
            cost_values=[0.10, 0.10, 0.10],
        )
        repo = BenchmarkRepository(session_factory=db_factory)

        scores = await repo.get_aggregate_scores(task_type=TaskType.BUG_FIX.value)
        by_model = {r["model_profile_id"]: r for r in scores}

        assert by_model["model-cheap"]["avg_cost"] == pytest.approx(0.001)
        assert by_model["model-expensive"]["avg_cost"] == pytest.approx(0.10)
        assert by_model["model-cheap"]["avg_cost"] != by_model["model-expensive"]["avg_cost"]


class TestRouterPrefersCheaperOnRealAvgCost:
    @pytest.mark.asyncio
    async def test_router_selects_cheaper_model_when_quality_ties_real_repo(
        self, db_factory
    ) -> None:
        """End-to-end: real DB rows -> real BenchmarkRepository.get_aggregate_scores
        -> real avg_cost -> AdaptiveRouter.route() prefers the cheaper model
        when composite quality is IDENTICAL between candidates.

        Uses the REAL (unpatched) per-task-type cost/quality weights from
        ``routing_roles.weights_for`` -- BUG_FIX carries a non-zero cost
        weight (0.15), so with tied quality the cheaper candidate wins the
        cost-adjusted rank outright (top pick), proving avg_cost is not just
        present but actually load-bearing in the ranking arithmetic.
        """
        # Identical score quadruple for both models -> identical composite
        # score computed by the SAME SQL aggregate -> quality ties exactly.
        await _seed_benchmark_rows(
            db_factory,
            model_profile_id="gludd-cheap",
            prompt_profile_id="prompt-x",
            task_type=TaskType.BUG_FIX.value,
            cost_values=[0.001, 0.001, 0.001],
        )
        await _seed_benchmark_rows(
            db_factory,
            model_profile_id="gludd-expensive",
            prompt_profile_id="prompt-x",
            task_type=TaskType.BUG_FIX.value,
            cost_values=[0.10, 0.10, 0.10],
        )
        repo = BenchmarkRepository(session_factory=db_factory)

        # Sanity: confirm the premise (tied quality, different real avg_cost)
        # directly against the repository before asking the router to choose.
        scores = await repo.get_aggregate_scores(task_type=TaskType.BUG_FIX.value)
        by_model = {r["model_profile_id"]: r for r in scores}
        assert (
            by_model["gludd-cheap"]["composite_score"]
            == pytest.approx(by_model["gludd-expensive"]["composite_score"])
        )
        assert by_model["gludd-cheap"]["avg_cost"] < by_model["gludd-expensive"]["avg_cost"]

        router = AdaptiveRouter(benchmark_repo=repo, min_samples=3)

        decision = await router.route(TaskType.BUG_FIX)

        assert decision.fallback is False
        assert decision.selected_model_profile_id == "gludd-cheap", (
            "With composite quality tied, the router must prefer the model "
            "with the lower REAL avg_cost, not fall back to an arbitrary "
            "(e.g. first-seen) candidate."
        )
        assert decision.estimated_cost_usd == pytest.approx(0.001)
        assert decision.estimated_cost_usd != 0.0
