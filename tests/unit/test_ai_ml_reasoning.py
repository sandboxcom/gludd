"""Unit tests for AIML Phase B: reasoning (AIML-007) and retrieval (AIML-006).

Covers docs/specs/FEATURE_AI_ML_EXPERT.md §6.2 (Retrieval) and §6.3
(Reasoning and verification):

  - ReasoningEngine plan/act/observe/verify state machine with typed, bounded
    steps.
  - At least one independent check for high-impact numerical answers; a failed
    check never yields ``succeeded`` (AIML-AT-007).
  - Numerical answers preserve units, significant figures, and uncertainty.
  - Query rewrite and retrieved source IDs are recorded.
  - RetrievalService hybrid search (lexical + dense stub), reranking, result
    recording, and evaluation metrics (recall@k, MRR, nDCG).
"""

from __future__ import annotations

import pytest

from general_ludd.ai_ml.reasoning import (
    IndependentCheck,
    IndependentCheckKind,
    NumericalAnswer,
    ReasoningEngine,
    ReasoningPhase,
    ReasoningResult,
)
from general_ludd.ai_ml.retrieval import (
    RetrievalMetrics,
    RetrievalService,
)
from general_ludd.ai_ml.schemas import ResultStatus, VerificationStatus

# ---------------------------------------------------------------------------
# AIML-007 — Reasoning state machine transitions
# ---------------------------------------------------------------------------


class TestReasoningStateMachine:
    def test_engine_starts_in_plan_phase(self) -> None:
        engine = ReasoningEngine()
        assert engine.phase is ReasoningPhase.PLAN
        assert engine.steps == ()

    def test_plan_transitions_to_act(self) -> None:
        engine = ReasoningEngine()
        step = engine.plan("decompose the query into retrieval + calculation")
        assert step.phase is ReasoningPhase.PLAN
        assert engine.phase is ReasoningPhase.ACT
        assert len(engine.steps) == 1

    def test_act_transitions_to_observe(self) -> None:
        engine = ReasoningEngine()
        engine.plan("plan")
        step = engine.act(tool="calculator", rationale="compute 2+2")
        assert step.phase is ReasoningPhase.ACT
        assert engine.phase is ReasoningPhase.OBSERVE

    def test_observe_stays_in_observe_awaiting_replan_or_verify(self) -> None:
        engine = ReasoningEngine()
        engine.plan("plan")
        engine.act(tool="calculator", rationale="compute")
        step = engine.observe("result is 4")
        assert step.phase is ReasoningPhase.OBSERVE
        # After observe, the engine stays in OBSERVE so the caller can
        # choose to replan (iterate) or verify (finalize).
        assert engine.phase is ReasoningPhase.OBSERVE

    def test_verify_transitions_to_terminal(self) -> None:
        engine = ReasoningEngine()
        engine.plan("plan")
        engine.act(tool="calculator", rationale="compute")
        engine.observe("result")
        result = engine.verify(
            rationale="answer grounded in calculation",
            query_rewrite="rewritten query",
            retrieved_source_ids=("evd-1",),
        )
        assert engine.phase is ReasoningPhase.TERMINAL
        assert isinstance(result, ReasoningResult)

    def test_invalid_transition_act_before_plan_raises(self) -> None:
        engine = ReasoningEngine()
        with pytest.raises(ValueError, match="cannot act"):
            engine.act(tool="calculator", rationale="skip planning")

    def test_invalid_transition_observe_before_act_raises(self) -> None:
        engine = ReasoningEngine()
        engine.plan("plan")
        with pytest.raises(ValueError, match="cannot observe"):
            engine.observe("skip acting")

    def test_invalid_transition_verify_from_plan_raises(self) -> None:
        engine = ReasoningEngine()
        engine.plan("plan")
        # Cannot verify directly from ACT — must act and observe first.
        with pytest.raises(ValueError, match="cannot verify"):
            engine.verify(rationale="r", query_rewrite="q")
        assert engine.phase is ReasoningPhase.ACT

    def test_replan_from_observe_allows_iteration(self) -> None:
        engine = ReasoningEngine()
        engine.plan("initial plan")
        engine.act(tool="search", rationale="search for evidence")
        engine.observe("found partial evidence")
        # iterate: go back to planning for a refinement
        step = engine.replan("need to search a different index")
        assert step.phase is ReasoningPhase.PLAN
        assert engine.phase is ReasoningPhase.ACT
        assert len(engine.steps) == 4

    def test_step_count_is_bounded(self) -> None:
        engine = ReasoningEngine(max_steps=4)
        engine.plan("p1")
        engine.act(tool="t", rationale="a1")
        engine.observe("o1")
        engine.replan("p2")
        # max_steps=4, already 4 steps recorded -> next should raise
        with pytest.raises(ValueError, match="max_steps"):
            engine.act(tool="t", rationale="a2")


# ---------------------------------------------------------------------------
# AIML-007 — Typed step artifacts
# ---------------------------------------------------------------------------


class TestStepArtifacts:
    def test_steps_recorded_in_order_with_indices(self) -> None:
        engine = ReasoningEngine()
        engine.plan("first plan")
        engine.act(tool="search", rationale="search evidence")
        engine.observe("found 3 sources")
        indices = [s.step_index for s in engine.steps]
        assert indices == [0, 1, 2]
        phases = [s.phase for s in engine.steps]
        assert phases == [
            ReasoningPhase.PLAN,
            ReasoningPhase.ACT,
            ReasoningPhase.OBSERVE,
        ]

    def test_step_artifact_has_concise_rationale_and_verifiable_uri(self) -> None:
        """Spec §6.3: externally visible reasoning is a concise rationale plus
        verifiable artifacts, not private token-level chain-of-thought."""
        engine = ReasoningEngine()
        step = engine.plan(
            "decompose into sub-problems",
            artifact_uri="artifact://plan-001",
        )
        assert step.rationale == "decompose into sub-problems"
        assert step.artifact_uri == "artifact://plan-001"
        assert step.tool == "planner"
        assert step.timestamp > 0

    def test_step_rejects_empty_rationale(self) -> None:
        engine = ReasoningEngine()
        with pytest.raises(ValueError, match="rationale"):
            engine.plan("")


# ---------------------------------------------------------------------------
# AIML-007 — Numerical answer preservation
# ---------------------------------------------------------------------------


class TestNumericalAnswer:
    def test_numerical_answer_preserves_units(self) -> None:
        ans = NumericalAnswer(
            value=0.76,
            unit="recall@10",
            significant_figures=2,
            uncertainty=0.03,
        )
        assert ans.unit == "recall@10"

    def test_numerical_answer_preserves_significant_figures(self) -> None:
        ans = NumericalAnswer(
            value=3.14159,
            unit="m/s",
            significant_figures=5,
            uncertainty=0.00001,
        )
        assert ans.significant_figures == 5

    def test_numerical_answer_preserves_uncertainty(self) -> None:
        ans = NumericalAnswer(
            value=42.0,
            unit="J",
            significant_figures=3,
            uncertainty=0.5,
            assumptions=("closed system",),
            boundary_conditions=("STP",),
        )
        assert ans.uncertainty == 0.5
        assert ans.assumptions == ("closed system",)
        assert ans.boundary_conditions == ("STP",)

    def test_numerical_answer_rejects_negative_uncertainty(self) -> None:
        with pytest.raises(ValueError, match="uncertainty"):
            NumericalAnswer(value=1.0, unit="m", significant_figures=1, uncertainty=-0.1)

    def test_numerical_answer_rejects_zero_sig_figs(self) -> None:
        with pytest.raises(ValueError, match="significant_figures"):
            NumericalAnswer(value=1.0, unit="m", significant_figures=0, uncertainty=0.1)


# ---------------------------------------------------------------------------
# AIML-007 — Independent checks
# ---------------------------------------------------------------------------


class TestIndependentChecks:
    def _numerical_engine(self) -> ReasoningEngine:
        engine = ReasoningEngine()
        engine.plan("compute recall")
        engine.act(tool="calculator", rationale="compute recall@10")
        engine.observe("recall@10 = 0.76 +/- 0.03")
        return engine

    def test_high_impact_numerical_without_check_is_degraded(self) -> None:
        """Spec §6.3: at least one independent check required for high-impact
        numerical answers. Missing check -> degraded, never succeeded."""
        engine = self._numerical_engine()
        result = engine.verify(
            rationale="recall computed",
            query_rewrite="rewrite",
            retrieved_source_ids=("evd-1",),
            answer=NumericalAnswer(value=0.76, unit="recall@10", significant_figures=2, uncertainty=0.03),
            independent_checks=(),
        )
        assert result.status is ResultStatus.DEGRADED
        assert "not_run" in result.uncertainty.method or "not" in result.uncertainty.method

    def test_failed_independent_check_produces_failed(self) -> None:
        """Spec §6.3: failed checks produce degraded/failed, never a confident answer."""
        engine = self._numerical_engine()
        failing = IndependentCheck(
            kind=IndependentCheckKind.ALTERNATIVE_SOLVER,
            status=VerificationStatus.FAIL,
            detail="alternative solver disagrees",
        )
        result = engine.verify(
            rationale="recall computed",
            query_rewrite="rewrite",
            retrieved_source_ids=("evd-1",),
            answer=NumericalAnswer(value=0.76, unit="recall@10", significant_figures=2, uncertainty=0.03),
            independent_checks=(failing,),
        )
        assert result.status is ResultStatus.FAILED
        assert result.status is not ResultStatus.SUCCEEDED

    def test_passing_independent_check_can_succeed(self) -> None:
        engine = self._numerical_engine()
        passing = IndependentCheck(
            kind=IndependentCheckKind.DIMENSIONAL_ANALYSIS,
            status=VerificationStatus.PASS,
            detail="units are consistent",
        )
        result = engine.verify(
            rationale="recall computed and verified",
            query_rewrite="rewrite",
            retrieved_source_ids=("evd-1",),
            answer=NumericalAnswer(value=0.76, unit="recall@10", significant_figures=2, uncertainty=0.03),
            independent_checks=(passing,),
        )
        assert result.status is ResultStatus.SUCCEEDED

    def test_non_numerical_answer_without_check_can_succeed(self) -> None:
        """Non-numerical answers do not require an independent numerical check."""
        engine = ReasoningEngine()
        engine.plan("answer a conceptual question")
        engine.act(tool="search", rationale="retrieve docs")
        engine.observe("found relevant docs")
        result = engine.verify(
            rationale="conceptual answer grounded in evidence",
            query_rewrite="rewrite",
            retrieved_source_ids=("evd-1",),
        )
        assert result.status is ResultStatus.SUCCEEDED


# ---------------------------------------------------------------------------
# AIML-007 — Query rewrite + source tracking in reasoning result
# ---------------------------------------------------------------------------


class TestReasoningRecording:
    def test_query_rewrite_recorded_in_result(self) -> None:
        engine = ReasoningEngine()
        engine.plan("p")
        engine.act(tool="t", rationale="r")
        engine.observe("o")
        result = engine.verify(
            rationale="done",
            query_rewrite="BM25 recall MS MARCO -> recall@10 BM25 MSMARCO",
            retrieved_source_ids=("evd-1",),
        )
        assert result.query_rewrite == "BM25 recall MS MARCO -> recall@10 BM25 MSMARCO"

    def test_retrieved_source_ids_tracked_in_result(self) -> None:
        engine = ReasoningEngine()
        engine.plan("p")
        engine.act(tool="retrieval", rationale="search evidence")
        engine.observe("found 3 sources")
        result = engine.verify(
            rationale="grounded in 3 sources",
            query_rewrite="rewrite",
            retrieved_source_ids=("evd-a", "evd-b", "evd-c"),
        )
        assert set(result.retrieved_source_ids) == {"evd-a", "evd-b", "evd-c"}

    def test_result_preserves_all_step_artifacts(self) -> None:
        engine = ReasoningEngine()
        engine.plan("p1", artifact_uri="artifact://plan")
        engine.act(tool="calc", rationale="compute", artifact_uri="artifact://calc")
        engine.observe("result", artifact_uri="artifact://obs")
        result = engine.verify(
            rationale="final",
            query_rewrite="rw",
            retrieved_source_ids=(),
        )
        assert len(result.steps) == 3
        assert result.steps[0].artifact_uri == "artifact://plan"
        assert result.steps[1].tool == "calc"
        assert result.steps[2].artifact_uri == "artifact://obs"


# ---------------------------------------------------------------------------
# AIML-006 — Retrieval service
# ---------------------------------------------------------------------------


class TestRetrievalService:
    def test_hybrid_search_returns_passages_with_lexical_and_dense_scores(self) -> None:
        svc = RetrievalService()
        svc.index("evd-1", "BM25 recall at 10 on MS MARCO dataset")
        svc.index("evd-2", "completely unrelated content about cooking")
        result = svc.search("BM25 recall MS MARCO", k=2)
        assert len(result.passages) == 2
        top = result.passages[0]
        assert top.source_id == "evd-1"
        assert top.lexical_score > 0.0
        assert top.dense_score > 0.0
        assert top.hybrid_score > 0.0
        assert top.rank == 0

    def test_reranking_reorders_by_hybrid_score(self) -> None:
        svc = RetrievalService()
        svc.index("evd-low", "the the the the the recall")
        svc.index("evd-high", "BM25 recall MS MARCO evaluation")
        result = svc.search("BM25 recall MS MARCO", k=2)
        assert result.passages[0].source_id == "evd-high"
        assert result.passages[0].hybrid_score >= result.passages[1].hybrid_score

    def test_result_records_query_rewrite_index_version_and_scores(self) -> None:
        """Spec §6.2: every answer records query rewrite, index version, filter
        policy, retrieved source IDs, scores, reranker version, citation spans."""
        svc = RetrievalService(index_version="idx-v2", reranker_version="rr-v3")
        svc.index("evd-1", "neural retrieval survey")
        result = svc.search(
            "dense retrieval",
            k=1,
            query_rewrite="dense vector retrieval methods",
        )
        assert result.query_rewrite == "dense vector retrieval methods"
        assert result.index_version == "idx-v2"
        assert result.reranker_version == "rr-v3"
        assert result.filter_policy == "default"
        assert result.retrieved_source_ids == ("evd-1",)
        assert result.passages[0].citation_span[1] > 0  # span end > 0
        assert result.latency_ms >= 0.0

    def test_empty_corpus_returns_empty_result(self) -> None:
        svc = RetrievalService()
        result = svc.search("anything", k=5)
        assert result.passages == ()
        assert result.retrieved_source_ids == ()

    def test_search_rejects_empty_query(self) -> None:
        svc = RetrievalService()
        with pytest.raises(ValueError, match="query"):
            svc.search("")


# ---------------------------------------------------------------------------
# AIML-006 — Evaluation metrics
# ---------------------------------------------------------------------------


class TestRetrievalMetrics:
    def test_recall_at_k_computes_correctly(self) -> None:
        svc = RetrievalService()
        svc.index("evd-1", "relevant content about retrieval")
        svc.index("evd-2", "also relevant retrieval content")
        svc.index("evd-3", "irrelevant cooking recipe")
        metrics = svc.evaluate(
            "retrieval content",
            relevant_ids={"evd-1", "evd-2"},
            k=3,
        )
        assert metrics.recall_at_k == 1.0  # both relevant in top-3

    def test_recall_at_k_partial(self) -> None:
        svc = RetrievalService()
        svc.index("evd-1", "relevant retrieval")
        svc.index("evd-2", "irrelevant recipe")
        metrics = svc.evaluate(
            "retrieval",
            relevant_ids={"evd-1", "evd-9"},  # evd-9 not in corpus
            k=2,
        )
        assert 0.0 < metrics.recall_at_k <= 1.0

    def test_mrr_computes_correctly(self) -> None:
        svc = RetrievalService()
        svc.index("evd-irrelevant", "cooking recipe pasta")
        svc.index("evd-relevant", "BM25 retrieval evaluation metrics")
        metrics = svc.evaluate(
            "BM25 retrieval",
            relevant_ids={"evd-relevant"},
            k=2,
        )
        # relevant doc is rank 1 (0-indexed), so MRR = 1/2 = 0.5
        assert 0.0 < metrics.mrr <= 1.0

    def test_ndcg_in_valid_range(self) -> None:
        svc = RetrievalService()
        for i in range(5):
            svc.index(f"evd-{i}", f"document number {i} about retrieval")
        metrics = svc.evaluate(
            "retrieval",
            relevant_ids={"evd-0", "evd-1"},
            k=5,
        )
        assert 0.0 <= metrics.ndcg <= 1.0

    def test_metrics_reject_out_of_range(self) -> None:
        with pytest.raises(ValueError, match="recall_at_k"):
            RetrievalMetrics(recall_at_k=1.5, mrr=0.5, ndcg=0.5)
