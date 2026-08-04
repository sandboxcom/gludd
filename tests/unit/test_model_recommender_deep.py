"""Deep tests for recommender — task-model matching, cost ranking, hardware filtering, evidence quality scoring."""

from __future__ import annotations

import hashlib
import tempfile
from typing import Any
from unittest.mock import patch

import pytest

from general_ludd.hardware.survey import GpuInfo, HardwareInventory
from general_ludd.schemas.benchmark import TaskRole


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _make_record(
    *,
    model_profile_id: str = "local-qwen-2.5",
    task_kind: str = "context_compaction",
    role: TaskRole = TaskRole.COMPACTOR,
    passed_cases: int = 24,
    total_cases: int = 24,
    collection_ok: bool = True,
    local_only: bool = True,
    evidence_digest: str | None = None,
) -> dict[str, Any]:
    return {
        "model_profile_id": model_profile_id,
        "model_identity_digest": _digest(f"identity:{model_profile_id}:v1"),
        "task_kind": task_kind,
        "role": role.value,
        "collection": "general_ludd.agent",
        "suite_id": "small-model-contract",
        "suite_revision": "v1",
        "acceptance_contract_digest": _digest(f"contract:{task_kind}:{role.value}"),
        "passed_cases": passed_cases,
        "total_cases": total_cases,
        "collection_ok": collection_ok,
        "local_only": local_only,
        "evidence_digest": evidence_digest or _digest(f"proof:{model_profile_id}:{task_kind}"),
    }


def _gpu(name: str = "Apple M2", vram: float = 10.0, backend: str = "metal") -> GpuInfo:
    return GpuInfo(name=name, vram_gb=vram, backend=backend)


def _hw(
    gpus: list[GpuInfo] | None = None,
    ram: float = 16.0,
    disk: float = 100.0,
    cores: int = 8,
) -> HardwareInventory:
    return HardwareInventory(
        gpus=gpus or [],
        total_ram_gb=ram,
        disk_free_gb=disk,
        cpu_cores=cores,
    )


# ─────────────────────────────────────────────────────────────────────
# Task-model matching
# ─────────────────────────────────────────────────────────────────────


def test_task_keyword_map_all_patterns_hit() -> None:
    from general_ludd.small_models.recommender import _map_task_to_capabilities

    matches = _map_task_to_capabilities("compact summarize condense and classify failure errors")
    kinds = {m[0] for m in matches}
    assert "context_compaction" in kinds
    assert "failure_classification" in kinds


def test_task_keyword_map_partial_word_no_match() -> None:
    from general_ludd.small_models.recommender import _map_task_to_capabilities

    matches = _map_task_to_capabilities("doc class enum")
    assert matches == []


def test_task_keyword_map_empty_description() -> None:
    from general_ludd.small_models.recommender import _map_task_to_capabilities

    assert _map_task_to_capabilities("") == []


def test_task_keyword_map_case_insensitive() -> None:
    from general_ludd.small_models.recommender import _map_task_to_capabilities

    lower = _map_task_to_capabilities("enumerate all items")
    upper = _map_task_to_capabilities("ENUMERATE ALL ITEMS")
    assert lower == upper
    assert len(lower) >= 1


def test_recommend_model_multiple_roles_for_same_task() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore
    from general_ludd.small_models.recommender import recommend_model

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = CapabilityEvidenceStore(path)
        store.register_evidence(
            _make_record(model_profile_id="editor-a", task_kind="documentation_draft", role=TaskRole.EDITOR)
        )
        store.register_evidence(
            _make_record(model_profile_id="editor-b", task_kind="format_normalization", role=TaskRole.EDITOR)
        )
        hw = _hw(gpus=[_gpu(vram=16.0)])
        with patch("general_ludd.small_models.recommender.is_off_peak", return_value=False):
            results = recommend_model("document and normalize format", hw, store)
        task_kinds = {r.task_kind for r in results}
        assert "documentation_draft" in task_kinds
        assert "format_normalization" in task_kinds
    finally:
        import os

        os.unlink(path)


def test_recommend_model_single_model_matches_multiple_task_kinds() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore
    from general_ludd.small_models.recommender import recommend_model

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = CapabilityEvidenceStore(path)
        store.register_evidence(_make_record(model_profile_id="versatile-model", task_kind="context_compaction"))
        store.register_evidence(_make_record(model_profile_id="versatile-model", task_kind="bounded_enumeration"))
        hw = _hw(gpus=[_gpu(vram=16.0)])
        with patch("general_ludd.small_models.recommender.is_off_peak", return_value=False):
            results = recommend_model("compact and enumerate items", hw, store)
        model_ids = {r.model_profile_id for r in results}
        assert "versatile-model" in model_ids
    finally:
        import os

        os.unlink(path)


# ─────────────────────────────────────────────────────────────────────
# Hardware filtering
# ─────────────────────────────────────────────────────────────────────


def test_assess_hardware_fit_fits() -> None:
    from general_ludd.small_models.recommender import _assess_hardware_fit

    hw = _hw(gpus=[_gpu(vram=8.0)])
    assert _assess_hardware_fit(hw) == "fits"


def test_assess_hardware_fit_marginal() -> None:
    from general_ludd.small_models.recommender import _assess_hardware_fit

    hw = _hw(gpus=[_gpu(vram=2.0)])
    assert _assess_hardware_fit(hw) == "marginal"


def test_assess_hardware_fit_insufficient_no_gpus() -> None:
    from general_ludd.small_models.recommender import _assess_hardware_fit

    assert _assess_hardware_fit(_hw(gpus=[])) == "insufficient"


def test_assess_hardware_fit_insufficient_tiny_vram() -> None:
    from general_ludd.small_models.recommender import _assess_hardware_fit

    hw = _hw(gpus=[_gpu(vram=0.25)])
    assert _assess_hardware_fit(hw) == "insufficient"


def test_assess_hardware_fit_boundary_min_vram() -> None:
    from general_ludd.small_models.recommender import _assess_hardware_fit

    assert _assess_hardware_fit(_hw(gpus=[_gpu(vram=1.0)])) == "marginal"


def test_assess_hardware_fit_boundary_recommended_vram() -> None:
    from general_ludd.small_models.recommender import _assess_hardware_fit

    assert _assess_hardware_fit(_hw(gpus=[_gpu(vram=4.0)])) == "fits"


def test_assess_hardware_fit_uses_min_vram_across_gpus() -> None:
    from general_ludd.small_models.recommender import _assess_hardware_fit

    hw = _hw(gpus=[_gpu(vram=8.0), _gpu(vram=0.5)])
    assert _assess_hardware_fit(hw) == "insufficient"


def test_recommend_model_filters_by_can_run_unknown_model_passes() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore
    from general_ludd.small_models.recommender import recommend_model

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = CapabilityEvidenceStore(path)
        store.register_evidence(_make_record(model_profile_id="my-custom-model-v2", task_kind="context_compaction"))
        hw = _hw(gpus=[])
        with patch("general_ludd.small_models.recommender.is_off_peak", return_value=False):
            results = recommend_model("compact this", hw, store)
        assert len(results) == 1
        assert results[0].can_run is False
        assert results[0].hardware_fit == "insufficient"
    finally:
        import os

        os.unlink(path)


# ─────────────────────────────────────────────────────────────────────
# Cost ranking
# ─────────────────────────────────────────────────────────────────────


def test_compute_cost_factors_returns_tuple_shape() -> None:
    from general_ludd.small_models.recommender import _compute_cost_factors

    score, cost_score, estimated_cost = _compute_cost_factors("phi-2")
    assert isinstance(score, float)
    assert isinstance(cost_score, float)
    assert isinstance(estimated_cost, float)
    assert 0.0 <= score <= 1.0
    assert 0.0 <= cost_score <= 1.0
    assert estimated_cost >= 0.0


def test_compute_score_no_records_returns_default() -> None:
    from general_ludd.small_models.recommender import _compute_score

    score, cost_score, est = _compute_score([], _hw(), "phi-2")
    assert score == 0.0
    assert cost_score == 1.0
    assert est == 0.0


def test_compute_score_with_records_and_hardware() -> None:
    from general_ludd.small_models.recommender import _compute_score

    records = [
        {"passed_cases": 20, "total_cases": 20, "collection_ok": True},
        {"passed_cases": 10, "total_cases": 20, "collection_ok": True},
    ]
    hw = _hw(gpus=[_gpu(vram=16.0)])
    score, cost_score, est = _compute_score(records, hw, "phi-2")
    assert 0.0 < score <= 1.0
    assert 0.0 <= cost_score <= 1.0


def test_compute_score_collection_not_ok_counts_zero() -> None:
    from general_ludd.small_models.recommender import _compute_score

    records = [
        {"passed_cases": 20, "total_cases": 20, "collection_ok": False},
        {"passed_cases": 10, "total_cases": 20, "collection_ok": False},
    ]
    hw = _hw(gpus=[_gpu(vram=16.0)])
    score, _, _ = _compute_score(records, hw, "phi-2")
    records_ok = [
        {"passed_cases": 20, "total_cases": 20, "collection_ok": True},
        {"passed_cases": 10, "total_cases": 20, "collection_ok": True},
    ]
    score_ok, _, _ = _compute_score(records_ok, hw, "phi-2")
    assert score < score_ok


def test_compute_score_pass_rate_affects_ranking() -> None:
    from general_ludd.small_models.recommender import _compute_score

    hw = _hw(gpus=[_gpu(vram=16.0)])
    perfect = [{"passed_cases": 20, "total_cases": 20, "collection_ok": True}]
    poor = [{"passed_cases": 5, "total_cases": 20, "collection_ok": True}]
    score_perfect, _, _ = _compute_score(perfect, hw, "phi-2")
    score_poor, _, _ = _compute_score(poor, hw, "phi-2")
    assert score_perfect > score_poor


def test_compute_score_evidence_count_boosts_score() -> None:
    from general_ludd.small_models.recommender import _compute_score

    hw = _hw(gpus=[_gpu(vram=16.0)])
    one = [{"passed_cases": 20, "total_cases": 20, "collection_ok": True}]
    many = [
        {"passed_cases": 20, "total_cases": 20, "collection_ok": True},
        {"passed_cases": 20, "total_cases": 20, "collection_ok": True},
        {"passed_cases": 20, "total_cases": 20, "collection_ok": True},
    ]
    score_one, _, _ = _compute_score(one, hw, "phi-2")
    score_many, _, _ = _compute_score(many, hw, "phi-2")
    assert score_many > score_one


def test_compute_score_urgent_uses_different_weights() -> None:
    from general_ludd.small_models.recommender import _compute_score

    records = [{"passed_cases": 24, "total_cases": 24, "collection_ok": True}]
    hw = _hw(gpus=[_gpu(vram=16.0)])
    with patch("general_ludd.small_models.recommender.is_off_peak", return_value=False):
        urgent_score, _, _ = _compute_score(records, hw, "phi-2", urgent=True)
        non_urgent_score, _, _ = _compute_score(records, hw, "phi-2", urgent=False)
    assert urgent_score != non_urgent_score


def test_recommend_model_ranks_by_score_descending() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore
    from general_ludd.small_models.recommender import recommend_model

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = CapabilityEvidenceStore(path)
        store.register_evidence(_make_record(model_profile_id="mid-model", passed_cases=15, total_cases=24))
        store.register_evidence(
            _make_record(model_profile_id="top-model", passed_cases=24, total_cases=24, evidence_digest=_digest("e1"))
        )
        store.register_evidence(
            _make_record(model_profile_id="low-model", passed_cases=5, total_cases=24, evidence_digest=_digest("e2"))
        )
        hw = _hw(gpus=[_gpu(vram=16.0)])
        with patch("general_ludd.small_models.recommender.is_off_peak", return_value=False):
            results = recommend_model("compact this context", hw, store)
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)
    finally:
        import os

        os.unlink(path)


def test_recommend_model_cost_score_present() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore
    from general_ludd.small_models.recommender import recommend_model

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = CapabilityEvidenceStore(path)
        store.register_evidence(_make_record())
        hw = _hw(gpus=[_gpu(vram=16.0)])
        with patch("general_ludd.small_models.recommender.is_off_peak", return_value=False):
            results = recommend_model("compact this", hw, store)
        assert len(results) >= 1
        for rec in results:
            assert 0.0 <= rec.cost_score <= 1.0
            assert rec.estimated_cost_usd_per_hour >= 0.0
    finally:
        import os

        os.unlink(path)


# ─────────────────────────────────────────────────────────────────────
# Evidence quality scoring
# ─────────────────────────────────────────────────────────────────────


def test_compute_score_with_radar_profile() -> None:
    from general_ludd.small_models.radar_profile import ModelRadarProfile
    from general_ludd.small_models.recommender import _compute_score

    records = [{"passed_cases": 24, "total_cases": 24, "collection_ok": True}]
    hw = _hw(gpus=[_gpu(vram=16.0)])
    rp = ModelRadarProfile(model_profile_id="test-model")
    rp.scores["writing"] = 0.9
    rp.scores["extraction"] = 0.8

    score_no_radar, _, _ = _compute_score(records, hw, "test-model", radar_profile=None)
    score_with_radar, _, _ = _compute_score(records, hw, "test-model", radar_profile=rp)
    assert score_with_radar > score_no_radar


def test_recommend_model_evidence_details_present() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore
    from general_ludd.small_models.recommender import recommend_model

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = CapabilityEvidenceStore(path)
        store.register_evidence(_make_record(passed_cases=20, total_cases=24))
        hw = _hw(gpus=[_gpu(vram=16.0)])
        with patch("general_ludd.small_models.recommender.is_off_peak", return_value=False):
            results = recommend_model("compact this", hw, store)
        assert len(results) >= 1
        details = results[0].evidence_details
        assert len(details) >= 1
        assert "passed_cases" in details[0]
        assert "total_cases" in details[0]
        assert "collection_ok" in details[0]
        assert "suite_id" in details[0]
    finally:
        import os

        os.unlink(path)


def test_recommend_model_evidence_count_matches_records() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore
    from general_ludd.small_models.recommender import recommend_model

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = CapabilityEvidenceStore(path)
        store.register_evidence(_make_record(evidence_digest=_digest("e1")))
        store.register_evidence(_make_record(evidence_digest=_digest("e2")))
        store.register_evidence(_make_record(evidence_digest=_digest("e3")))
        hw = _hw(gpus=[_gpu(vram=16.0)])
        with patch("general_ludd.small_models.recommender.is_off_peak", return_value=False):
            results = recommend_model("compact this", hw, store)
        assert len(results) >= 1
        assert results[0].evidence_count == 3
    finally:
        import os

        os.unlink(path)


def test_recommend_model_all_six_task_kinds_matchable() -> None:
    from general_ludd.small_models.recommender import _map_task_to_capabilities

    descriptions = {
        "compact this text": "context_compaction",
        "draft a document": "documentation_draft",
        "enumerate the list": "bounded_enumeration",
        "classify this failure": "failure_classification",
        "normalize the format": "format_normalization",
        "extract schema from": "schema_extraction",
    }
    for desc, expected_kind in descriptions.items():
        matches = _map_task_to_capabilities(desc)
        kinds = {m[0] for m in matches}
        assert expected_kind in kinds, f"'{desc}' should match '{expected_kind}'"


# ─────────────────────────────────────────────────────────────────────
# ModelRecommendation validation edge cases
# ─────────────────────────────────────────────────────────────────────


def test_model_recommendation_rejects_score_below_zero() -> None:
    from general_ludd.small_models.recommender import ModelRecommendation

    with pytest.raises(ValueError, match="score must be"):
        ModelRecommendation(
            model_profile_id="x",
            task_kind="context_compaction",
            role=TaskRole.COMPACTOR,
            score=-0.1,
            cost_score=0.5,
            estimated_cost_usd_per_hour=0.0,
            evidence_count=0,
            hardware_fit="fits",
            evidence_details=[],
            can_run=False,
            peak_status="unknown",
            prefer_off_peak=False,
        )


def test_model_recommendation_rejects_score_above_one() -> None:
    from general_ludd.small_models.recommender import ModelRecommendation

    with pytest.raises(ValueError, match="score must be"):
        ModelRecommendation(
            model_profile_id="x",
            task_kind="context_compaction",
            role=TaskRole.COMPACTOR,
            score=1.01,
            cost_score=0.5,
            estimated_cost_usd_per_hour=0.0,
            evidence_count=0,
            hardware_fit="fits",
            evidence_details=[],
            can_run=False,
            peak_status="unknown",
            prefer_off_peak=False,
        )


def test_model_recommendation_rejects_negative_evidence_count() -> None:
    from general_ludd.small_models.recommender import ModelRecommendation

    with pytest.raises(ValueError, match="evidence_count"):
        ModelRecommendation(
            model_profile_id="x",
            task_kind="context_compaction",
            role=TaskRole.COMPACTOR,
            score=0.5,
            cost_score=0.5,
            estimated_cost_usd_per_hour=0.0,
            evidence_count=-1,
            hardware_fit="fits",
            evidence_details=[],
            can_run=False,
            peak_status="unknown",
            prefer_off_peak=False,
        )


def test_model_recommendation_rejects_invalid_hardware_fit() -> None:
    from general_ludd.small_models.recommender import ModelRecommendation

    with pytest.raises(ValueError, match="hardware_fit"):
        ModelRecommendation(
            model_profile_id="x",
            task_kind="context_compaction",
            role=TaskRole.COMPACTOR,
            score=0.5,
            cost_score=0.5,
            estimated_cost_usd_per_hour=0.0,
            evidence_count=0,
            hardware_fit="excellent",
            evidence_details=[],
            can_run=False,
            peak_status="unknown",
            prefer_off_peak=False,
        )


def test_model_recommendation_accepts_boundary_scores() -> None:
    from general_ludd.small_models.recommender import ModelRecommendation

    rec_zero = ModelRecommendation(
        model_profile_id="x",
        task_kind="context_compaction",
        role=TaskRole.COMPACTOR,
        score=0.0,
        cost_score=0.0,
        estimated_cost_usd_per_hour=0.0,
        evidence_count=0,
        hardware_fit="fits",
        evidence_details=[],
        can_run=False,
        peak_status="unknown",
        prefer_off_peak=False,
    )
    assert rec_zero.score == 0.0
    assert rec_zero.cost_score == 0.0

    rec_one = ModelRecommendation(
        model_profile_id="x",
        task_kind="context_compaction",
        role=TaskRole.COMPACTOR,
        score=1.0,
        cost_score=1.0,
        estimated_cost_usd_per_hour=0.0,
        evidence_count=0,
        hardware_fit="fits",
        evidence_details=[],
        can_run=False,
        peak_status="unknown",
        prefer_off_peak=False,
    )
    assert rec_one.score == 1.0
    assert rec_one.cost_score == 1.0
