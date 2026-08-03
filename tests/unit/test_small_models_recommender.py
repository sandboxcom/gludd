"""Tests for recommender — task→model reverse lookup using capability evidence and hardware fit."""

from __future__ import annotations

import hashlib
import tempfile
from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from general_ludd.hardware.survey import GpuInfo, HardwareInventory
from general_ludd.schemas.benchmark import TaskRole


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _make_evidence_record(
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


def _gpu(name: str = "Apple M2", vram: float = 10.0, backend: str = "metal") -> GpuInfo:
    return GpuInfo(name=name, vram_gb=vram, backend=backend)


# ─────────────────────────────────────────────────────────────────────
# query_by_task_kind on evidence store
# ─────────────────────────────────────────────────────────────────────


def test_evidence_store_query_by_task_kind_returns_matching_records() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = CapabilityEvidenceStore(path)
        store.register_evidence(_make_evidence_record(model_profile_id="model-a", task_kind="context_compaction"))
        store.register_evidence(
            _make_evidence_record(
                model_profile_id="model-a", task_kind="context_compaction", evidence_digest=_digest("ev-2")
            )
        )
        store.register_evidence(_make_evidence_record(model_profile_id="model-b", task_kind="documentation_draft"))
        store.register_evidence(_make_evidence_record(model_profile_id="model-c", task_kind="context_compaction"))

        results = store.query_by_task_kind("context_compaction")
        assert len(results) == 3
        assert all(r["task_kind"] == "context_compaction" for r in results)
    finally:
        import os

        os.unlink(path)


def test_evidence_store_query_by_task_kind_returns_empty_for_no_match() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = CapabilityEvidenceStore(path)
        store.register_evidence(_make_evidence_record(task_kind="context_compaction"))
        assert store.query_by_task_kind("bounded_enumeration") == []
    finally:
        import os

        os.unlink(path)


def test_evidence_store_query_by_task_kind_empty_store() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = CapabilityEvidenceStore(path)
        assert store.query_by_task_kind("context_compaction") == []
    finally:
        import os

        os.unlink(path)


def test_evidence_store_query_by_task_kind_preserves_shallow_copy() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = CapabilityEvidenceStore(path)
        store.register_evidence(_make_evidence_record())
        results = store.query_by_task_kind("context_compaction")
        results.clear()
        assert len(store.query_by_task_kind("context_compaction")) == 1
    finally:
        import os

        os.unlink(path)


# ─────────────────────────────────────────────────────────────────────
# ModelRecommendation dataclass
# ─────────────────────────────────────────────────────────────────────


def test_model_recommendation_dataclass_shape() -> None:
    from general_ludd.small_models.recommender import ModelRecommendation

    rec = ModelRecommendation(
        model_profile_id="local-qwen-2.5",
        task_kind="context_compaction",
        role=TaskRole.COMPACTOR,
        score=0.85,
        cost_score=0.9,
        estimated_cost_usd_per_hour=0.003,
        evidence_count=3,
        hardware_fit="fits",
        evidence_details=[{"passed_cases": 24, "total_cases": 24}],
    )
    assert rec.model_profile_id == "local-qwen-2.5"
    assert rec.task_kind == "context_compaction"
    assert rec.score == 0.85
    assert rec.cost_score == 0.9
    assert rec.estimated_cost_usd_per_hour == 0.003
    assert rec.role == TaskRole.COMPACTOR


def test_model_recommendation_is_frozen() -> None:
    from general_ludd.small_models.recommender import ModelRecommendation

    rec = ModelRecommendation(
        model_profile_id="local-qwen-2.5",
        task_kind="context_compaction",
        role=TaskRole.COMPACTOR,
        score=0.85,
        cost_score=0.9,
        estimated_cost_usd_per_hour=0.003,
        evidence_count=3,
        hardware_fit="fits",
        evidence_details=[],
    )
    with pytest.raises(FrozenInstanceError):
        rec.score = 0.5  # type: ignore[misc]


# ─────────────────────────────────────────────────────────────────────
# recommend_model
# ─────────────────────────────────────────────────────────────────────


def test_recommend_model_returns_ranked_recommendations() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore
    from general_ludd.small_models.recommender import recommend_model

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = CapabilityEvidenceStore(path)
        store.register_evidence(
            _make_evidence_record(
                model_profile_id="model-a", task_kind="context_compaction", passed_cases=24, total_cases=24
            )
        )
        store.register_evidence(
            _make_evidence_record(
                model_profile_id="model-a", task_kind="context_compaction", evidence_digest=_digest("e2")
            )
        )
        store.register_evidence(
            _make_evidence_record(
                model_profile_id="model-b", task_kind="context_compaction", passed_cases=10, total_cases=24
            )
        )

        hw = _hw(gpus=[_gpu(vram=12.0)])
        results = recommend_model("summarize and compact this context", hw, store)

        assert len(results) >= 1
        assert results[0].model_profile_id == "model-a"
        assert results[0].score >= results[-1].score
        assert results[0].task_kind == "context_compaction"
    finally:
        import os

        os.unlink(path)


def test_recommend_model_maps_natural_language_to_capabilities() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore
    from general_ludd.small_models.recommender import recommend_model

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = CapabilityEvidenceStore(path)
        store.register_evidence(_make_evidence_record(task_kind="documentation_draft", role=TaskRole.EDITOR))
        store.register_evidence(_make_evidence_record(task_kind="bounded_enumeration", role=TaskRole.ENUMERATOR))

        hw = _hw(gpus=[_gpu(vram=16.0)])

        doc_results = recommend_model("draft documentation for this module", hw, store)
        assert len(doc_results) >= 1
        assert any(r.task_kind == "documentation_draft" for r in doc_results)

        enum_results = recommend_model("enumerate all possible options", hw, store)
        assert len(enum_results) >= 1
        assert any(r.task_kind == "bounded_enumeration" for r in enum_results)
    finally:
        import os

        os.unlink(path)


def test_recommend_model_no_evidence_returns_empty() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore
    from general_ludd.small_models.recommender import recommend_model

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = CapabilityEvidenceStore(path)
        hw = _hw(gpus=[_gpu(vram=16.0)])
        results = recommend_model("classify this failure", hw, store)
        assert results == []
    finally:
        import os

        os.unlink(path)


def test_recommend_model_ranks_by_evidence_quality() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore
    from general_ludd.small_models.recommender import recommend_model

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = CapabilityEvidenceStore(path)
        store.register_evidence(
            _make_evidence_record(
                model_profile_id="perfect-model",
                task_kind="format_normalization",
                passed_cases=24,
                total_cases=24,
                role=TaskRole.EDITOR,
            )
        )
        store.register_evidence(
            _make_evidence_record(
                model_profile_id="ok-model",
                task_kind="format_normalization",
                passed_cases=12,
                total_cases=24,
                role=TaskRole.EDITOR,
            )
        )

        hw = _hw(gpus=[_gpu(vram=16.0)])
        results = recommend_model("normalize the format of this data", hw, store)
        assert len(results) >= 2
        assert results[0].model_profile_id == "perfect-model"
        assert results[0].score > results[1].score
    finally:
        import os

        os.unlink(path)


def test_recommend_model_annotates_hardware_fit() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore
    from general_ludd.small_models.recommender import recommend_model

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = CapabilityEvidenceStore(path)
        store.register_evidence(_make_evidence_record(model_profile_id="model-x", task_kind="context_compaction"))

        hw_good = _hw(gpus=[_gpu(vram=24.0)])
        results_good = recommend_model("compact this context", hw_good, store)
        assert any(r.hardware_fit == "fits" for r in results_good)

        hw_none = _hw(gpus=[])
        results_none = recommend_model("compact this context", hw_none, store)
        assert any(r.hardware_fit == "insufficient" for r in results_none)

        hw_tiny = _hw(gpus=[_gpu(vram=0.5)])
        results_tiny = recommend_model("compact this context", hw_tiny, store)
        fits_tiny = [r.hardware_fit for r in results_tiny]
        assert all(f in ("fits", "marginal", "insufficient") for f in fits_tiny)
    finally:
        import os

        os.unlink(path)


def test_recommend_model_maps_multiple_task_kinds_from_description() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore
    from general_ludd.small_models.recommender import recommend_model

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = CapabilityEvidenceStore(path)
        store.register_evidence(
            _make_evidence_record(model_profile_id="doc-model", task_kind="documentation_draft", role=TaskRole.EDITOR)
        )
        store.register_evidence(
            _make_evidence_record(model_profile_id="schema-model", task_kind="schema_extraction", role=TaskRole.EDITOR)
        )

        hw = _hw(gpus=[_gpu(vram=16.0)])
        results = recommend_model("document and extract schema", hw, store)
        task_kinds = {r.task_kind for r in results}
        assert "documentation_draft" in task_kinds
        assert "schema_extraction" in task_kinds
    finally:
        import os

        os.unlink(path)


def test_recommend_model_unknown_task_description_returns_empty() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore
    from general_ludd.small_models.recommender import recommend_model

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = CapabilityEvidenceStore(path)
        store.register_evidence(_make_evidence_record())
        hw = _hw()
        results = recommend_model("zzzblargh nonsense noise", hw, store)
        assert results == []
    finally:
        import os

        os.unlink(path)


def test_recommend_model_filters_failed_collection_evidence() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore
    from general_ludd.small_models.recommender import recommend_model

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = CapabilityEvidenceStore(path)
        store.register_evidence(
            _make_evidence_record(model_profile_id="bad-model", collection_ok=False, passed_cases=0, total_cases=24)
        )
        store.register_evidence(
            _make_evidence_record(model_profile_id="good-model", collection_ok=True, passed_cases=24, total_cases=24)
        )

        hw = _hw(gpus=[_gpu(vram=16.0)])
        results = recommend_model("compact this context", hw, store)
        model_ids = {r.model_profile_id for r in results}
        assert "good-model" in model_ids
    finally:
        import os

        os.unlink(path)


def test_recommend_model_no_hardware_constraint_when_evidence_exists() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore
    from general_ludd.small_models.recommender import recommend_model

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = CapabilityEvidenceStore(path)
        store.register_evidence(_make_evidence_record())

        hw = _hw(gpus=[_gpu(vram=32.0)])
        results = recommend_model("compact context", hw, store)
        assert len(results) >= 1
    finally:
        import os

        os.unlink(path)


def test_recommend_model_score_in_range() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore
    from general_ludd.small_models.recommender import recommend_model

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = CapabilityEvidenceStore(path)
        store.register_evidence(_make_evidence_record(passed_cases=24, total_cases=24))
        store.register_evidence(
            _make_evidence_record(model_profile_id="partial-model", passed_cases=12, total_cases=24)
        )

        hw = _hw(gpus=[_gpu(vram=16.0)])
        results = recommend_model("compact context", hw, store)
        for rec in results:
            assert 0.0 <= rec.score <= 1.0
    finally:
        import os

        os.unlink(path)


# ─────────────────────────────────────────────────────────────────────
# list_tasks_for_model
# ─────────────────────────────────────────────────────────────────────


def test_list_tasks_for_model_returns_task_kinds() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore
    from general_ludd.small_models.recommender import list_tasks_for_model

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = CapabilityEvidenceStore(path)
        store.register_evidence(
            _make_evidence_record(model_profile_id="model-x", task_kind="context_compaction", role=TaskRole.COMPACTOR)
        )
        store.register_evidence(
            _make_evidence_record(model_profile_id="model-x", task_kind="documentation_draft", role=TaskRole.EDITOR)
        )
        store.register_evidence(
            _make_evidence_record(model_profile_id="model-y", task_kind="bounded_enumeration", role=TaskRole.ENUMERATOR)
        )

        tasks = list_tasks_for_model("model-x", store)
        assert set(tasks) == {"context_compaction", "documentation_draft"}
    finally:
        import os

        os.unlink(path)


def test_list_tasks_for_model_no_evidence() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore
    from general_ludd.small_models.recommender import list_tasks_for_model

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = CapabilityEvidenceStore(path)
        store.register_evidence(_make_evidence_record(model_profile_id="model-a"))
        tasks = list_tasks_for_model("nonexistent-model", store)
        assert tasks == []
    finally:
        import os

        os.unlink(path)


def test_list_tasks_for_model_deduplicates() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore
    from general_ludd.small_models.recommender import list_tasks_for_model

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = CapabilityEvidenceStore(path)
        store.register_evidence(_make_evidence_record(model_profile_id="model-x", task_kind="context_compaction"))
        store.register_evidence(
            _make_evidence_record(
                model_profile_id="model-x", task_kind="context_compaction", evidence_digest=_digest("e2")
            )
        )
        store.register_evidence(
            _make_evidence_record(
                model_profile_id="model-x", task_kind="context_compaction", evidence_digest=_digest("e3")
            )
        )

        tasks = list_tasks_for_model("model-x", store)
        assert tasks == ["context_compaction"]
    finally:
        import os

        os.unlink(path)


def test_list_tasks_for_model_sorted() -> None:
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore
    from general_ludd.small_models.recommender import list_tasks_for_model

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = CapabilityEvidenceStore(path)
        store.register_evidence(
            _make_evidence_record(model_profile_id="model-x", task_kind="schema_extraction", role=TaskRole.EDITOR)
        )
        store.register_evidence(
            _make_evidence_record(model_profile_id="model-x", task_kind="bounded_enumeration", role=TaskRole.ENUMERATOR)
        )
        store.register_evidence(
            _make_evidence_record(model_profile_id="model-x", task_kind="context_compaction", role=TaskRole.COMPACTOR)
        )

        tasks = list_tasks_for_model("model-x", store)
        assert tasks == sorted(tasks)
    finally:
        import os

        os.unlink(path)
