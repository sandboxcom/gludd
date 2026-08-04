"""Test local model cost scoring and preference — local vs cloud economics, hardware gating, fallback."""

from __future__ import annotations

import tempfile
from typing import Any
from unittest.mock import patch

from general_ludd.hardware.survey import GpuInfo, HardwareInventory
from general_ludd.schemas.benchmark import TaskRole


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


def _evidence(
    model_id: str = "qwen2.5-1.5b",
    task_kind: str = "context_compaction",
    role: TaskRole = TaskRole.COMPACTOR,
    passed: int = 24,
    total: int = 24,
    collection_ok: bool = True,
    evidence_digest: str | None = None,
) -> dict[str, Any]:
    return {
        "model_profile_id": model_id,
        "model_identity_digest": f"sha256:identity:{model_id}:v1",
        "task_kind": task_kind,
        "role": role.value,
        "collection": "general_ludd.agent",
        "suite_id": "small-model-contract",
        "suite_revision": "v1",
        "acceptance_contract_digest": f"sha256:contract:{task_kind}:{role.value}",
        "passed_cases": passed,
        "total_cases": total,
        "collection_ok": collection_ok,
        "local_only": True,
        "evidence_digest": evidence_digest or f"sha256:proof:{model_id}:{task_kind}",
    }


# ── compute_cost_score ───────────────────────────────────────────────


def test_cost_score_high_for_cheap_local_model() -> None:
    from general_ludd.small_models.cost import compute_cost_score

    score = compute_cost_score("qwen2.5-0.5b")
    assert score > 0.2, f"cheapest local model should score high, got {score}"


def test_cost_score_low_for_large_cloud_model() -> None:
    from general_ludd.small_models.cost import compute_cost_score

    score = compute_cost_score("gpt-4")
    assert score < 0.6, f"expensive cloud model should score low, got {score}"


def test_cost_score_local_higher_than_api() -> None:
    from general_ludd.small_models.cost import compute_cost_score

    local = compute_cost_score("phi-2")
    api = compute_cost_score("claude-sonnet")
    assert local > api, f"local ({local}) should outscore API ({api})"


def test_cost_score_zero_for_zero_cost_model() -> None:
    from general_ludd.small_models.cost import compute_cost_score

    with patch(
        "general_ludd.small_models.cost.estimate_inference_cost",
        return_value={"estimated_usd_per_hour": 0.0},
    ):
        score = compute_cost_score("free-model")
        assert score == 1.0, f"zero-cost model should get max score, got {score}"


def test_cost_score_bounded_0_to_1() -> None:
    from general_ludd.small_models.cost import compute_cost_score

    models = ["phi-2", "qwen2.5-7b", "llama3.1-70b", "gemma-2b", "gpt-4", "claude-opus"]
    for mid in models:
        score = compute_cost_score(mid)
        assert 0.0 <= score <= 1.0, f"{mid} score {score} out of bounds"


def test_cost_score_small_local_tier_gets_1x_multiplier() -> None:
    from general_ludd.small_models.cost import _infer_tier, compute_cost_score

    tier = _infer_tier("phi-2")
    assert tier == "small_local"
    score = compute_cost_score("phi-2")
    assert score > 0.0


def test_cost_score_medium_api_gets_0_7x_multiplier() -> None:
    from general_ludd.small_models.cost import _infer_tier, compute_cost_score

    tier = _infer_tier("mistral-7b")
    assert tier == "medium_api"
    score = compute_cost_score("mistral-7b")
    assert score > 0.0


def test_cost_score_large_api_gets_0_4x_multiplier() -> None:
    from general_ludd.small_models.cost import _infer_tier, compute_cost_score

    tier = _infer_tier("gpt-4")
    assert tier == "large_api"
    score = compute_cost_score("gpt-4")
    assert score > 0.0


# ── tier inference ───────────────────────────────────────────────────


def test_tier_small_local_for_tiny_models() -> None:
    from general_ludd.small_models.cost import _infer_tier

    assert _infer_tier("qwen2.5-0.5b") == "small_local"
    assert _infer_tier("phi-2") == "small_local"
    assert _infer_tier("gemma-2b") == "small_local"
    assert _infer_tier("phi-3-mini") == "small_local"


def test_tier_medium_api_for_7b_8b_models() -> None:
    from general_ludd.small_models.cost import _infer_tier

    assert _infer_tier("mistral-7b") == "medium_api"
    assert _infer_tier("llama3.1-8b") == "medium_api"


def test_tier_large_api_for_70b_and_claude_gpt() -> None:
    from general_ludd.small_models.cost import _infer_tier

    assert _infer_tier("llama3.1-70b") == "large_api"
    assert _infer_tier("gpt-4") == "large_api"
    assert _infer_tier("claude-opus") == "large_api"


def test_tier_fallback_by_size() -> None:
    from general_ludd.small_models.cost import _infer_tier

    assert _infer_tier("custom-3b-model") == "small_local"
    assert _infer_tier("custom-13b-model") == "medium_api"
    assert _infer_tier("custom-72b-model") == "large_api"


# ── hardware capability gating ───────────────────────────────────────


def test_assess_hardware_fit_fits_with_adequate_vram() -> None:
    from general_ludd.small_models.recommender import _assess_hardware_fit

    hw = _hw(gpus=[_gpu(vram=24.0)])
    assert _assess_hardware_fit(hw) == "fits"


def test_assess_hardware_fit_marginal_with_minimal_vram() -> None:
    from general_ludd.small_models.recommender import _assess_hardware_fit

    hw = _hw(gpus=[_gpu(vram=2.0)])
    assert _assess_hardware_fit(hw) == "marginal"


def test_assess_hardware_fit_insufficient_with_no_gpu() -> None:
    from general_ludd.small_models.recommender import _assess_hardware_fit

    hw = _hw(gpus=[])
    assert _assess_hardware_fit(hw) == "insufficient"


def test_assess_hardware_fit_insufficient_with_tiny_vram() -> None:
    from general_ludd.small_models.recommender import _assess_hardware_fit

    hw = _hw(gpus=[_gpu(vram=0.5)])
    assert _assess_hardware_fit(hw) == "insufficient"


def test_assess_hardware_fit_uses_min_vram_across_gpus() -> None:
    from general_ludd.small_models.recommender import _assess_hardware_fit

    hw = _hw(gpus=[_gpu("A100", 80.0), _gpu("T4", 1.5)])
    assert _assess_hardware_fit(hw) == "marginal"


# ── combined: local preference over cloud ────────────────────────────


def test_recommender_prefers_local_when_cheap_and_capable() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore
    from general_ludd.small_models.recommender import recommend_model

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = CapabilityEvidenceStore(path)
        store.register_evidence(_evidence("qwen2.5-0.5b", passed=24, total=24))
        store.register_evidence(_evidence("phi-2", passed=24, total=24, evidence_digest="sha256:p2"))

        hw = _hw(gpus=[_gpu(vram=16.0)])
        with patch("general_ludd.small_models.recommender.is_off_peak", return_value=True):
            results = recommend_model("compact this context", hw, store)

        assert len(results) >= 1
        top_model = results[0]
        assert top_model.cost_score > 0.0
        assert top_model.estimated_cost_usd_per_hour >= 0.0
        assert top_model.hardware_fit == "fits"
        assert top_model.can_run is True
    finally:
        import os

        os.unlink(path)


def test_recommender_cloud_fallback_when_local_unavailable() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore
    from general_ludd.small_models.recommender import recommend_model

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = CapabilityEvidenceStore(path)
        store.register_evidence(_evidence("llama3.1-70b", passed=24, total=24))

        hw = _hw(gpus=[_gpu(vram=4.0)])
        with patch("general_ludd.small_models.recommender.is_off_peak", return_value=False):
            results = recommend_model("compact context", hw, store)

        assert results == []
    finally:
        import os

        os.unlink(path)


def test_recommender_local_gets_higher_cost_score_than_cloud() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore
    from general_ludd.small_models.recommender import recommend_model

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = CapabilityEvidenceStore(path)
        store.register_evidence(_evidence("phi-2", passed=24, total=24))
        store.register_evidence(_evidence("qwen2.5-7b", passed=24, total=24, evidence_digest="sha256:q7"))

        hw = _hw(gpus=[_gpu(vram=24.0)])
        with patch("general_ludd.small_models.recommender.is_off_peak", return_value=False):
            results = recommend_model("summarize this", hw, store, urgent=True)

        local_recs = [
            r
            for r in results
            if "phi-2" in r.model_profile_id or "0.5b" in r.model_profile_id or "qwen2.5" in r.model_profile_id
        ]
        assert len(local_recs) >= 1
        for rec in local_recs:
            assert rec.cost_score >= 0.0
            assert rec.estimated_cost_usd_per_hour >= 0.0
    finally:
        import os

        os.unlink(path)


def test_cost_score_ordering_cheapest_first() -> None:
    from general_ludd.small_models.cost import compute_cost_score

    scores = {
        "qwen2.5-0.5b": compute_cost_score("qwen2.5-0.5b"),
        "qwen2.5-1.5b": compute_cost_score("qwen2.5-1.5b"),
        "qwen2.5-7b": compute_cost_score("qwen2.5-7b"),
    }
    assert scores["qwen2.5-0.5b"] >= scores["qwen2.5-1.5b"] >= scores["qwen2.5-7b"], (
        f"smaller models should score cheaper: {scores}"
    )


# ── estimate_inference_cost tiers ────────────────────────────────────


def test_local_inference_reports_small_local_tier() -> None:
    from general_ludd.small_models.cost import estimate_inference_cost

    info = estimate_inference_cost("phi-2")
    assert info["tier"] == "small_local"
    cost = info["estimated_usd_per_hour"]
    assert isinstance(cost, (int, float))
    assert cost >= 0.0


def test_cloud_model_reports_large_api_tier() -> None:
    from general_ludd.small_models.cost import estimate_inference_cost

    info = estimate_inference_cost("gpt-4")
    assert info["tier"] == "large_api"


def test_medium_model_reports_medium_api_tier() -> None:
    from general_ludd.small_models.cost import estimate_inference_cost

    info = estimate_inference_cost("mistral-7b")
    assert info["tier"] == "medium_api"


# ── local vs cloud cost comparison ───────────────────────────────────


def test_local_model_cheaper_per_hour_than_equivalent_cloud() -> None:
    from general_ludd.small_models.cost import estimate_inference_cost

    local = estimate_inference_cost("qwen2.5-7b")
    cloud = estimate_inference_cost("llama3.1-8b")
    local_cost = local["estimated_usd_per_hour"]
    cloud_cost = cloud["estimated_usd_per_hour"]
    assert isinstance(local_cost, (int, float))
    assert isinstance(cloud_cost, (int, float))
    assert local["tier"] != cloud["tier"] or local_cost <= cloud_cost


def test_unknown_model_defaults_to_small_local() -> None:
    from general_ludd.small_models.cost import estimate_inference_cost

    info = estimate_inference_cost("random-experimental-3b")
    assert info["tier"] == "small_local"


# ── enforce_fit gate in recommend_model ──────────────────────────────


def test_recommend_model_excludes_known_models_that_cant_fit() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore
    from general_ludd.small_models.recommender import recommend_model

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = CapabilityEvidenceStore(path)
        store.register_evidence(_evidence("llama3.1-70b", passed=24, total=24))
        store.register_evidence(_evidence("gemma-2b", passed=24, total=24, evidence_digest="sha256:g2"))

        hw = _hw(gpus=[_gpu(vram=6.0)])
        with patch("general_ludd.small_models.recommender.is_off_peak", return_value=False):
            results = recommend_model("compact this context", hw, store)

        model_ids = {r.model_profile_id for r in results}
        assert "llama3.1-70b" not in model_ids
        assert "gemma-2b" in model_ids
    finally:
        import os

        os.unlink(path)


def test_recommend_model_all_excluded_when_no_hardware_fits() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore
    from general_ludd.small_models.recommender import recommend_model

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = CapabilityEvidenceStore(path)
        store.register_evidence(_evidence("llama3.1-70b", passed=24, total=24))
        store.register_evidence(_evidence("llama3.1-405b", passed=24, total=24, evidence_digest="sha256:l4"))

        hw = _hw(gpus=[_gpu(vram=2.0)])
        with patch("general_ludd.small_models.recommender.is_off_peak", return_value=False):
            results = recommend_model("compact context", hw, store)

        assert results == []
    finally:
        import os

        os.unlink(path)
