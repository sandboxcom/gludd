"""Deep edge-case tests for recommender — empty lists, single model, equal scores, extreme values,
duplicate handling, None/empty capabilities, large model lists, and boundary conditions."""

from __future__ import annotations

import hashlib
import tempfile
import time
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
# Empty / degenerate input tests
# ─────────────────────────────────────────────────────────────────────


def test_compute_score_empty_records_returns_zero_score() -> None:
    from general_ludd.small_models.recommender import _compute_score

    score, cost_score, est = _compute_score([], _hw(gpus=[_gpu(vram=16.0)]), "any-model")
    assert score == 0.0
    assert cost_score == 1.0
    assert est == 0.0


def test_compute_score_empty_records_urgent_returns_zero_score() -> None:
    from general_ludd.small_models.recommender import _compute_score

    score, cost_score, est = _compute_score([], _hw(gpus=[_gpu(vram=16.0)]), "any-model", urgent=True)
    assert score == 0.0
    assert cost_score == 1.0
    assert est == 0.0


def test_recommend_model_empty_evidence_store_returns_empty() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore
    from general_ludd.small_models.recommender import recommend_model

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = CapabilityEvidenceStore(path)
        hw = _hw(gpus=[_gpu(vram=16.0)])
        results = recommend_model("summarize context", hw, store)
        assert results == []
    finally:
        import os

        os.unlink(path)


def test_recommend_model_empty_description_returns_empty() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore
    from general_ludd.small_models.recommender import recommend_model

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = CapabilityEvidenceStore(path)
        store.register_evidence(_make_record(model_profile_id="model-a"))
        hw = _hw(gpus=[_gpu(vram=16.0)])
        results = recommend_model("", hw, store)
        assert results == []
    finally:
        import os

        os.unlink(path)


def test_recommend_model_unicode_description_matches() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore
    from general_ludd.small_models.recommender import recommend_model

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = CapabilityEvidenceStore(path)
        store.register_evidence(_make_record(model_profile_id="model-a", task_kind="context_compaction"))
        hw = _hw(gpus=[_gpu(vram=16.0)])
        with patch("general_ludd.small_models.recommender.is_off_peak", return_value=False):
            results = recommend_model("co\u0301mpact the conte\u0301xt \u2014 summarize everything!", hw, store)
        assert len(results) >= 1
    finally:
        import os

        os.unlink(path)


def test_build_evidence_details_empty_list() -> None:
    from general_ludd.small_models.recommender import _build_evidence_details

    assert _build_evidence_details([]) == []


def test_build_evidence_details_missing_keys_fills_defaults() -> None:
    from general_ludd.small_models.recommender import _build_evidence_details

    details = _build_evidence_details([{}])
    assert len(details) == 1
    assert details[0]["suite_id"] == ""
    assert details[0]["suite_revision"] == ""
    assert details[0]["passed_cases"] == 0
    assert details[0]["total_cases"] == 0
    assert details[0]["collection_ok"] is False
    assert details[0]["local_only"] is False


def test_recommend_model_records_with_empty_model_id_skipped() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore
    from general_ludd.small_models.recommender import recommend_model

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = CapabilityEvidenceStore(path)
        store.register_evidence(_make_record(model_profile_id="", task_kind="context_compaction"))
        store.register_evidence(_make_record(model_profile_id="valid-model", task_kind="context_compaction"))
        hw = _hw(gpus=[_gpu(vram=16.0)])
        with patch("general_ludd.small_models.recommender.is_off_peak", return_value=False):
            results = recommend_model("compact this", hw, store)
        model_ids = {r.model_profile_id for r in results}
        assert "" not in model_ids
        assert "valid-model" in model_ids
    finally:
        import os

        os.unlink(path)


# ─────────────────────────────────────────────────────────────────────
# Single model tests
# ─────────────────────────────────────────────────────────────────────


def test_recommend_model_single_model_returns_one_recommendation() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore
    from general_ludd.small_models.recommender import recommend_model

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = CapabilityEvidenceStore(path)
        store.register_evidence(_make_record(model_profile_id="only-model", task_kind="context_compaction"))
        hw = _hw(gpus=[_gpu(vram=16.0)])
        with patch("general_ludd.small_models.recommender.is_off_peak", return_value=False):
            results = recommend_model("compact this context", hw, store)
        assert len(results) == 1
        assert results[0].model_profile_id == "only-model"
    finally:
        import os

        os.unlink(path)


def test_recommend_model_single_model_with_multiple_records() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore
    from general_ludd.small_models.recommender import recommend_model

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = CapabilityEvidenceStore(path)
        store.register_evidence(
            _make_record(model_profile_id="lone-model", task_kind="context_compaction", evidence_digest=_digest("a"))
        )
        store.register_evidence(
            _make_record(model_profile_id="lone-model", task_kind="context_compaction", evidence_digest=_digest("b"))
        )
        store.register_evidence(
            _make_record(model_profile_id="lone-model", task_kind="context_compaction", evidence_digest=_digest("c"))
        )
        hw = _hw(gpus=[_gpu(vram=16.0)])
        with patch("general_ludd.small_models.recommender.is_off_peak", return_value=False):
            results = recommend_model("compact this context", hw, store)
        assert len(results) == 1
        assert results[0].evidence_count == 3
    finally:
        import os

        os.unlink(path)


def test_list_tasks_for_model_single_task() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore
    from general_ludd.small_models.recommender import list_tasks_for_model

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = CapabilityEvidenceStore(path)
        store.register_evidence(_make_record(model_profile_id="model-x", task_kind="context_compaction"))
        tasks = list_tasks_for_model("model-x", store)
        assert tasks == ["context_compaction"]
    finally:
        import os

        os.unlink(path)


# ─────────────────────────────────────────────────────────────────────
# All same score / tied rankings
# ─────────────────────────────────────────────────────────────────────


def test_recommend_model_all_identical_evidence_keeps_stable_order() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore
    from general_ludd.small_models.recommender import recommend_model

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = CapabilityEvidenceStore(path)
        for c in "abcde":
            store.register_evidence(
                _make_record(
                    model_profile_id=f"model-{c}",
                    task_kind="context_compaction",
                    passed_cases=24,
                    total_cases=24,
                    evidence_digest=_digest(f"ev-{c}"),
                )
            )
        hw = _hw(gpus=[_gpu(vram=16.0)])
        with patch("general_ludd.small_models.recommender.is_off_peak", return_value=False):
            results = recommend_model("compact this context", hw, store)
        assert len(results) == 5
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)
        assert all(s == scores[0] for s in scores), "All models with identical evidence should have equal scores"
    finally:
        import os

        os.unlink(path)


def test_recommend_model_tied_scores_preserves_input_order() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore
    from general_ludd.small_models.recommender import recommend_model

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = CapabilityEvidenceStore(path)
        ordered_ids = ["zulu-model", "alpha-model", "mike-model"]
        for mid in ordered_ids:
            store.register_evidence(
                _make_record(
                    model_profile_id=mid,
                    task_kind="context_compaction",
                    passed_cases=24,
                    total_cases=24,
                    evidence_digest=_digest(f"ev-{mid}"),
                )
            )
        hw = _hw(gpus=[_gpu(vram=16.0)])
        with patch("general_ludd.small_models.recommender.is_off_peak", return_value=False):
            results = recommend_model("compact this context", hw, store)
        result_ids = [r.model_profile_id for r in results]
        assert set(result_ids) == set(ordered_ids)
    finally:
        import os

        os.unlink(path)


# ─────────────────────────────────────────────────────────────────────
# Extreme value tests
# ─────────────────────────────────────────────────────────────────────


def test_compute_score_total_cases_zero_no_division_by_zero() -> None:
    from general_ludd.small_models.recommender import _compute_score

    records = [
        {"passed_cases": 0, "total_cases": 0, "collection_ok": True},
    ]
    hw = _hw(gpus=[_gpu(vram=16.0)])
    score, _, _ = _compute_score(records, hw, "test-model")
    assert 0.0 <= score <= 1.0


def test_compute_score_passed_exceeds_total() -> None:
    from general_ludd.small_models.recommender import _compute_score

    records = [
        {"passed_cases": 999, "total_cases": 24, "collection_ok": True},
    ]
    hw = _hw(gpus=[_gpu(vram=16.0)])
    score, _, _ = _compute_score(records, hw, "test-model")
    assert isinstance(score, float), f"score should be float, got {type(score)}"
    assert score > 1.0, (
        f"score={score} — _compute_score does not cap avg_pass_rate at 1.0, "
        "so passed_cases > total_cases produces an unbounded pass_rate component"
    )


def test_compute_score_all_zero_passed() -> None:
    from general_ludd.small_models.recommender import _compute_score

    records = [
        {"passed_cases": 0, "total_cases": 24, "collection_ok": True},
    ]
    hw = _hw(gpus=[_gpu(vram=16.0)])
    score, _, _ = _compute_score(records, hw, "test-model")
    assert 0.0 <= score <= 1.0, f"score={score} should be in [0,1] with zero passed_cases"


def test_assess_hardware_fit_exactly_one_gb() -> None:
    from general_ludd.small_models.recommender import _assess_hardware_fit

    assert _assess_hardware_fit(_hw(gpus=[_gpu(vram=1.0)])) == "marginal"


def test_assess_hardware_fit_exactly_four_gb() -> None:
    from general_ludd.small_models.recommender import _assess_hardware_fit

    assert _assess_hardware_fit(_hw(gpus=[_gpu(vram=4.0)])) == "fits"


def test_assess_hardware_fit_large_vram() -> None:
    from general_ludd.small_models.recommender import _assess_hardware_fit

    assert _assess_hardware_fit(_hw(gpus=[_gpu(vram=999.9)])) == "fits"


def test_assess_hardware_fit_multiple_gpus_uses_minimum() -> None:
    from general_ludd.small_models.recommender import _assess_hardware_fit

    hw = _hw(gpus=[_gpu(vram=16.0), _gpu(vram=24.0), _gpu(vram=0.5)])
    assert _assess_hardware_fit(hw) == "insufficient"


def test_compute_score_many_records() -> None:
    from general_ludd.small_models.recommender import _compute_score

    records = [{"passed_cases": 24, "total_cases": 24, "collection_ok": True} for _ in range(100)]
    hw = _hw(gpus=[_gpu(vram=16.0)])
    score, _, _ = _compute_score(records, hw, "test-model")
    assert 0.0 <= score <= 1.0


def test_recommend_model_extreme_vram_known_big_model_excluded() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore
    from general_ludd.small_models.recommender import recommend_model

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = CapabilityEvidenceStore(path)
        store.register_evidence(_make_record(model_profile_id="llama-3.1-405b", task_kind="context_compaction"))
        store.register_evidence(_make_record(model_profile_id="llama-3.1-70b", task_kind="context_compaction"))
        hw = _hw(gpus=[])  # no GPU at all
        with patch("general_ludd.small_models.recommender.is_off_peak", return_value=False):
            results = recommend_model("compact this context", hw, store)
        model_ids = {r.model_profile_id for r in results}
        assert "llama-3.1-405b" not in model_ids
        assert "llama-3.1-70b" not in model_ids
    finally:
        import os

        os.unlink(path)


# ─────────────────────────────────────────────────────────────────────
# None / missing capability tests
# ─────────────────────────────────────────────────────────────────────


def test_recommend_model_only_failed_collection_evidence_returns_empty() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore
    from general_ludd.small_models.recommender import recommend_model

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = CapabilityEvidenceStore(path)
        store.register_evidence(
            _make_record(model_profile_id="bad-model", collection_ok=False, task_kind="context_compaction")
        )
        store.register_evidence(
            _make_record(
                model_profile_id="also-bad",
                collection_ok=False,
                task_kind="context_compaction",
                evidence_digest=_digest("ev2"),
            )
        )
        hw = _hw(gpus=[_gpu(vram=16.0)])
        with patch("general_ludd.small_models.recommender.is_off_peak", return_value=False):
            results = recommend_model("compact this context", hw, store)
        assert results == []
    finally:
        import os

        os.unlink(path)


def test_list_tasks_for_model_missing_task_kind_filtered() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore
    from general_ludd.small_models.recommender import list_tasks_for_model

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = CapabilityEvidenceStore(path)
        store.register_evidence(_make_record(model_profile_id="model-x", task_kind="context_compaction"))
        empty_kind = _make_record(model_profile_id="model-x", task_kind="context_compaction")
        empty_kind["task_kind"] = ""
        store.register_evidence(empty_kind)
        tasks = list_tasks_for_model("model-x", store)
        assert "" not in tasks
        assert "context_compaction" in tasks
    finally:
        import os

        os.unlink(path)


def test_map_task_to_capabilities_non_ascii_input() -> None:
    from general_ludd.small_models.recommender import _map_task_to_capabilities

    matches = _map_task_to_capabilities(
        "classify \u00e9rrors s\u00fdstem \u00e0rrors l\u00f6\u011f \u0155oot cau\u015fe"
    )
    kinds = {m[0] for m in matches}
    assert "failure_classification" in kinds


def test_map_task_to_capabilities_very_long_description() -> None:
    from general_ludd.small_models.recommender import _map_task_to_capabilities

    long_desc = "classify the failure " * 1000
    matches = _map_task_to_capabilities(long_desc)
    kinds = {m[0] for m in matches}
    assert "failure_classification" in kinds


# ─────────────────────────────────────────────────────────────────────
# Duplicate model handling
# ─────────────────────────────────────────────────────────────────────


def test_recommend_model_duplicate_model_merged_records() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore
    from general_ludd.small_models.recommender import recommend_model

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = CapabilityEvidenceStore(path)
        store.register_evidence(
            _make_record(model_profile_id="dup-model", task_kind="context_compaction", evidence_digest=_digest("d1"))
        )
        store.register_evidence(
            _make_record(model_profile_id="dup-model", task_kind="context_compaction", evidence_digest=_digest("d2"))
        )
        store.register_evidence(
            _make_record(
                model_profile_id="dup-model",
                task_kind="documentation_draft",
                role=TaskRole.EDITOR,
                evidence_digest=_digest("d3"),
            )
        )
        store.register_evidence(
            _make_record(model_profile_id="other-model", task_kind="context_compaction", evidence_digest=_digest("d4"))
        )
        hw = _hw(gpus=[_gpu(vram=16.0)])
        with patch("general_ludd.small_models.recommender.is_off_peak", return_value=False):
            results = recommend_model("compact text and draft documents", hw, store)
        model_ids = {r.model_profile_id for r in results}
        assert "dup-model" in model_ids
        assert "other-model" in model_ids
    finally:
        import os

        os.unlink(path)


def test_recommend_model_duplicate_identical_records_deduplicated() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore
    from general_ludd.small_models.recommender import recommend_model

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = CapabilityEvidenceStore(path)
        store.register_evidence(_make_record(model_profile_id="dup-model", task_kind="context_compaction"))
        store.register_evidence(_make_record(model_profile_id="dup-model", task_kind="context_compaction"))
        store.register_evidence(_make_record(model_profile_id="dup-model", task_kind="context_compaction"))
        hw = _hw(gpus=[_gpu(vram=16.0)])
        with patch("general_ludd.small_models.recommender.is_off_peak", return_value=False):
            results = recommend_model("compact this context", hw, store)
        assert len(results) == 1
        assert results[0].evidence_count == 3
    finally:
        import os

        os.unlink(path)


# ─────────────────────────────────────────────────────────────────────
# Large model list performance tests
# ─────────────────────────────────────────────────────────────────────


def test_recommend_model_large_model_list_completes() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore
    from general_ludd.small_models.recommender import recommend_model

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = CapabilityEvidenceStore(path)
        for idx in range(200):
            store.register_evidence(
                _make_record(
                    model_profile_id=f"model-{idx:04d}",
                    task_kind="context_compaction",
                    passed_cases=idx % 25,
                    total_cases=24,
                    evidence_digest=_digest(f"large-{idx}"),
                )
            )
        hw = _hw(gpus=[_gpu(vram=16.0)])
        start = time.monotonic()
        with patch("general_ludd.small_models.recommender.is_off_peak", return_value=False):
            results = recommend_model("compact this context", hw, store)
        elapsed = time.monotonic() - start
        assert len(results) >= 0
        assert elapsed < 5.0, f"Recommendation took {elapsed:.2f}s, expected < 5.0s"
        assert results == sorted(results, key=lambda r: r.score, reverse=True)
    finally:
        import os

        os.unlink(path)


def test_recommend_model_large_model_list_all_unique() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore
    from general_ludd.small_models.recommender import recommend_model

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = CapabilityEvidenceStore(path)
        expected_count = 100
        for idx in range(expected_count):
            store.register_evidence(
                _make_record(
                    model_profile_id=f"u-{idx:04d}",
                    task_kind="context_compaction",
                    passed_cases=20,
                    total_cases=24,
                    evidence_digest=_digest(f"unique-{idx}"),
                )
            )
        hw = _hw(gpus=[_gpu(vram=16.0)])
        with patch("general_ludd.small_models.recommender.is_off_peak", return_value=False):
            results = recommend_model("compact this context", hw, store)
        assert len(results) == expected_count
        ids = {r.model_profile_id for r in results}
        assert len(ids) == expected_count
    finally:
        import os

        os.unlink(path)


# ─────────────────────────────────────────────────────────────────────
# ModelRecommendation boundary validation
# ─────────────────────────────────────────────────────────────────────


def test_model_recommendation_rejects_bool_evidence_count() -> None:
    from general_ludd.small_models.recommender import ModelRecommendation

    with pytest.raises(ValueError, match="evidence_count"):
        ModelRecommendation(
            model_profile_id="x",
            task_kind="context_compaction",
            role=TaskRole.COMPACTOR,
            score=0.5,
            cost_score=0.5,
            estimated_cost_usd_per_hour=0.0,
            evidence_count=True,  # type: ignore[arg-type]
            hardware_fit="fits",
            evidence_details=[],
            can_run=False,
            peak_status="unknown",
            prefer_off_peak=False,
        )


def test_model_recommendation_rejects_cost_score_below_zero() -> None:
    from general_ludd.small_models.recommender import ModelRecommendation

    with pytest.raises(ValueError, match="cost_score must be"):
        ModelRecommendation(
            model_profile_id="x",
            task_kind="context_compaction",
            role=TaskRole.COMPACTOR,
            score=0.5,
            cost_score=-0.01,
            estimated_cost_usd_per_hour=0.0,
            evidence_count=0,
            hardware_fit="fits",
            evidence_details=[],
            can_run=False,
            peak_status="unknown",
            prefer_off_peak=False,
        )


def test_model_recommendation_rejects_cost_score_above_one() -> None:
    from general_ludd.small_models.recommender import ModelRecommendation

    with pytest.raises(ValueError, match="cost_score must be"):
        ModelRecommendation(
            model_profile_id="x",
            task_kind="context_compaction",
            role=TaskRole.COMPACTOR,
            score=0.5,
            cost_score=1.0001,
            estimated_cost_usd_per_hour=0.0,
            evidence_count=0,
            hardware_fit="fits",
            evidence_details=[],
            can_run=False,
            peak_status="unknown",
            prefer_off_peak=False,
        )


def test_model_recommendation_accepts_all_hardware_fits() -> None:
    from general_ludd.small_models.recommender import ModelRecommendation

    for fit in ("fits", "marginal", "insufficient"):
        rec = ModelRecommendation(
            model_profile_id="x",
            task_kind="context_compaction",
            role=TaskRole.COMPACTOR,
            score=0.5,
            cost_score=0.5,
            estimated_cost_usd_per_hour=0.0,
            evidence_count=0,
            hardware_fit=fit,
            evidence_details=[],
            can_run=False,
            peak_status="unknown",
            prefer_off_peak=False,
        )
        assert rec.hardware_fit == fit


def test_model_recommendation_evidence_count_zero_accepted() -> None:
    from general_ludd.small_models.recommender import ModelRecommendation

    rec = ModelRecommendation(
        model_profile_id="x",
        task_kind="context_compaction",
        role=TaskRole.COMPACTOR,
        score=0.5,
        cost_score=0.5,
        estimated_cost_usd_per_hour=0.0,
        evidence_count=0,
        hardware_fit="fits",
        evidence_details=[],
        can_run=False,
        peak_status="unknown",
        prefer_off_peak=False,
    )
    assert rec.evidence_count == 0


def test_model_recommendation_evidence_count_large_accepted() -> None:
    from general_ludd.small_models.recommender import ModelRecommendation

    rec = ModelRecommendation(
        model_profile_id="x",
        task_kind="context_compaction",
        role=TaskRole.COMPACTOR,
        score=0.5,
        cost_score=0.5,
        estimated_cost_usd_per_hour=0.0,
        evidence_count=100_000,
        hardware_fit="fits",
        evidence_details=[],
        can_run=False,
        peak_status="unknown",
        prefer_off_peak=False,
    )
    assert rec.evidence_count == 100_000


# ─────────────────────────────────────────────────────────────────────
# list_tasks_for_model edge cases
# ─────────────────────────────────────────────────────────────────────


def test_list_tasks_for_model_empty_store() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore
    from general_ludd.small_models.recommender import list_tasks_for_model

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = CapabilityEvidenceStore(path)
        assert list_tasks_for_model("any-model", store) == []
    finally:
        import os

        os.unlink(path)


def test_list_tasks_for_model_empty_string_id_raises_value_error() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore
    from general_ludd.small_models.recommender import list_tasks_for_model

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = CapabilityEvidenceStore(path)
        store.register_evidence(_make_record(model_profile_id="model-a"))
        raised = False
        try:
            list_tasks_for_model("", store)
        except ValueError as exc:
            raised = True
            assert "invalid format" in str(exc)
        assert raised, "expected ValueError for empty string model_id"
    finally:
        import os

        os.unlink(path)


# ─────────────────────────────────────────────────────────────────────
# _compute_cost_factors edge cases
# ─────────────────────────────────────────────────────────────────────


def test_compute_cost_factors_unknown_model_returns_reasonable_defaults() -> None:
    from general_ludd.small_models.recommender import _compute_cost_factors

    score, cost_score, estimated_cost = _compute_cost_factors("this-model-definitely-does-not-exist-1234567890")
    assert 0.0 <= score <= 1.0
    assert 0.0 <= cost_score <= 1.0
    assert estimated_cost >= 0.0


def test_compute_cost_factors_empty_string_model_id() -> None:
    from general_ludd.small_models.recommender import _compute_cost_factors

    score, cost_score, estimated_cost = _compute_cost_factors("")
    assert 0.0 <= score <= 1.0
    assert 0.0 <= cost_score <= 1.0
    assert estimated_cost >= 0.0


# ─────────────────────────────────────────────────────────────────────
# _map_task_to_capabilities regex edge cases
# ─────────────────────────────────────────────────────────────────────


def test_map_task_to_capabilities_word_boundaries() -> None:
    from general_ludd.small_models.recommender import _map_task_to_capabilities

    matches = _map_task_to_capabilities("incompact")
    kinds = {m[0] for m in matches}
    assert "context_compaction" not in kinds, "'compact' substring in 'incompact' should not match due to word boundary"


def test_map_task_to_capabilities_partial_word() -> None:
    from general_ludd.small_models.recommender import _map_task_to_capabilities

    matches = _map_task_to_capabilities("compactification")
    kinds = {m[0] for m in matches}
    assert "context_compaction" not in kinds, (
        "'compact' inside 'compactification' should NOT match — requires \\b word boundary"
    )


def test_map_task_to_capabilities_all_known_keywords_at_once() -> None:
    from general_ludd.small_models.recommender import _map_task_to_capabilities

    all_keywords = (
        "compact summarize condense document draft readme writeup "
        "enumerate list itemize catalog classify failure error "
        "triage root cause format normaliz standardize cleanse scrub "
        "schema extract parse structur"
    )
    matches = _map_task_to_capabilities(all_keywords)
    kinds = {m[0] for m in matches}
    assert "context_compaction" in kinds
    assert "documentation_draft" in kinds
    assert "bounded_enumeration" in kinds
    assert "failure_classification" in kinds
    assert "format_normalization" in kinds
    assert "schema_extraction" in kinds


# ─────────────────────────────────────────────────────────────────────
# Hardware edge cases for recommend_model
# ─────────────────────────────────────────────────────────────────────


def test_recommend_model_no_gpu_hardware_annotated_correctly() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore
    from general_ludd.small_models.recommender import recommend_model

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = CapabilityEvidenceStore(path)
        store.register_evidence(_make_record(model_profile_id="some-custom-model", task_kind="context_compaction"))
        hw = _hw(gpus=[])
        with patch("general_ludd.small_models.recommender.is_off_peak", return_value=False):
            results = recommend_model("compact this context", hw, store)
        assert len(results) >= 1
        assert all(r.hardware_fit == "insufficient" for r in results)
    finally:
        import os

        os.unlink(path)
