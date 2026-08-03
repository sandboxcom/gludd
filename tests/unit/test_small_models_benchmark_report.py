"""Tests for benchmark_report — aggregate benchmark reporting with radar comparison and cost analysis."""

from __future__ import annotations

import hashlib
import tempfile
from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from general_ludd.hardware.survey import GpuInfo, HardwareInventory

_MT_BENCH_AXES = (
    "writing",
    "roleplay",
    "extraction",
    "reasoning",
    "math",
    "coding",
    "stem",
    "humanities",
    "cost",
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _make_evidence_record(
    *,
    model_profile_id: str = "smollm2-135m",
    task_kind: str = "coding",
    passed_cases: int = 18,
    total_cases: int = 25,
    collection_ok: bool = True,
    local_only: bool = True,
) -> dict[str, Any]:
    return {
        "model_profile_id": model_profile_id,
        "model_identity_digest": _digest(f"identity:{model_profile_id}:v1"),
        "task_kind": task_kind,
        "role": "editor",
        "collection": "general_ludd.agent",
        "suite_id": "small-model-contract",
        "suite_revision": "v1",
        "acceptance_contract_digest": _digest(f"contract:{task_kind}:editor"),
        "passed_cases": passed_cases,
        "total_cases": total_cases,
        "collection_ok": collection_ok,
        "local_only": local_only,
        "evidence_digest": _digest(f"proof:{model_profile_id}:{task_kind}"),
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


# ── BenchmarkReport dataclass ──────────────────────────────────────


def test_benchmark_report_dataclass_shape() -> None:
    from general_ludd.small_models.benchmark_report import BenchmarkReport

    report = BenchmarkReport(
        models=["smollm2-135m", "qwen2.5-0.5b"],
        per_model_scores={
            "smollm2-135m": {"coding": 0.72, "math": 0.48},
            "qwen2.5-0.5b": {"coding": 0.55, "math": 0.65},
        },
        radar_comparison={"profiles": {}, "mean": {}, "ranking": ["smollm2-135m"], "winner": "smollm2-135m"},
        cost_analysis={
            "smollm2-135m": {"inference_usd_per_hour": 0.0001},
            "qwen2.5-0.5b": {"inference_usd_per_hour": 0.00005},
        },
        best_per_axis={"coding": "smollm2-135m", "math": "qwen2.5-0.5b"},
        overall_winner="smollm2-135m",
    )
    assert report.models == ["smollm2-135m", "qwen2.5-0.5b"]
    assert report.overall_winner == "smollm2-135m"
    assert report.best_per_axis["coding"] == "smollm2-135m"


def test_benchmark_report_defaults() -> None:
    from general_ludd.small_models.benchmark_report import BenchmarkReport

    report = BenchmarkReport(
        models=["a"],
        per_model_scores={},
        radar_comparison={},
        cost_analysis={},
        best_per_axis={},
        overall_winner="a",
    )
    assert report.models == ["a"]
    assert report.overall_winner == "a"


def test_benchmark_report_is_frozen() -> None:
    from general_ludd.small_models.benchmark_report import BenchmarkReport

    report = BenchmarkReport(
        models=["a"],
        per_model_scores={},
        radar_comparison={},
        cost_analysis={},
        best_per_axis={},
        overall_winner="a",
    )
    with pytest.raises(FrozenInstanceError):
        report.models = ["b"]  # type: ignore[misc]


# ── generate_report ────────────────────────────────────────────────


def test_generate_report_single_model() -> None:
    from general_ludd.small_models.benchmark_report import generate_report

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = _setup_store(path)
        store.register_evidence(
            _make_evidence_record(model_profile_id="smollm2-135m", task_kind="coding", passed_cases=20, total_cases=25)
        )
        store.register_evidence(
            _make_evidence_record(model_profile_id="smollm2-135m", task_kind="math", passed_cases=15, total_cases=25)
        )
        store.register_evidence(
            _make_evidence_record(
                model_profile_id="smollm2-135m", task_kind="reasoning", passed_cases=22, total_cases=25
            )
        )

        report = generate_report(["smollm2-135m"], store)

        assert report.models == ["smollm2-135m"]
        assert report.overall_winner == "smollm2-135m"
        assert "smollm2-135m" in report.per_model_scores
        assert "smollm2-135m" in report.cost_analysis
    finally:
        import os

        os.unlink(path)


def _setup_store(path: str):
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore

    return CapabilityEvidenceStore(path)


def test_generate_report_multiple_models() -> None:
    from general_ludd.small_models.benchmark_report import generate_report

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = _setup_store(path)
        store.register_evidence(
            _make_evidence_record(model_profile_id="model-a", task_kind="coding", passed_cases=24, total_cases=25)
        )
        store.register_evidence(
            _make_evidence_record(model_profile_id="model-a", task_kind="math", passed_cases=10, total_cases=25)
        )
        store.register_evidence(
            _make_evidence_record(model_profile_id="model-a", task_kind="reasoning", passed_cases=20, total_cases=25)
        )
        store.register_evidence(
            _make_evidence_record(model_profile_id="model-b", task_kind="coding", passed_cases=10, total_cases=25)
        )
        store.register_evidence(
            _make_evidence_record(model_profile_id="model-b", task_kind="math", passed_cases=24, total_cases=25)
        )
        store.register_evidence(
            _make_evidence_record(model_profile_id="model-b", task_kind="reasoning", passed_cases=18, total_cases=25)
        )

        report = generate_report(["model-a", "model-b"], store)

        assert len(report.models) == 2
        assert report.overall_winner is not None
        assert report.radar_comparison.get("ranking") is not None
        assert len(report.cost_analysis) == 2
        assert len(report.per_model_scores) == 2
    finally:
        import os

        os.unlink(path)


def test_generate_report_empty_models_raises() -> None:
    from general_ludd.small_models.benchmark_report import generate_report

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = _setup_store(path)
        with pytest.raises(ValueError, match="model_ids"):
            generate_report([], store)
    finally:
        import os

        os.unlink(path)


def test_generate_report_no_evidence_returns_empty_report() -> None:
    from general_ludd.small_models.benchmark_report import generate_report

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = _setup_store(path)
        report = generate_report(["unknown-model"], store)

        assert report.models == ["unknown-model"]
        assert report.overall_winner is None
        assert report.per_model_scores["unknown-model"] == {}
    finally:
        import os

        os.unlink(path)


def test_generate_report_cost_analysis_included() -> None:
    from general_ludd.small_models.benchmark_report import generate_report

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = _setup_store(path)
        store.register_evidence(
            _make_evidence_record(model_profile_id="smollm2-135m", task_kind="coding", passed_cases=20, total_cases=25)
        )

        report = generate_report(["smollm2-135m"], store)
        cost = report.cost_analysis["smollm2-135m"]

        assert "inference" in cost
        assert "download" in cost
        assert "tier" in cost
        assert "estimated_usd_per_hour" in cost["inference"] or cost["estimated_usd_per_hour"] is not None
    finally:
        import os

        os.unlink(path)


def test_generate_report_best_per_axis() -> None:
    from general_ludd.small_models.benchmark_report import generate_report

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = _setup_store(path)
        store.register_evidence(
            _make_evidence_record(model_profile_id="model-a", task_kind="coding", passed_cases=24, total_cases=25)
        )
        store.register_evidence(
            _make_evidence_record(model_profile_id="model-a", task_kind="math", passed_cases=5, total_cases=25)
        )
        store.register_evidence(
            _make_evidence_record(model_profile_id="model-b", task_kind="coding", passed_cases=10, total_cases=25)
        )
        store.register_evidence(
            _make_evidence_record(model_profile_id="model-b", task_kind="math", passed_cases=24, total_cases=25)
        )

        report = generate_report(["model-a", "model-b"], store)

        assert report.best_per_axis.get("coding") == "model-a"
        assert report.best_per_axis.get("math") == "model-b"
    finally:
        import os

        os.unlink(path)


def test_generate_report_overall_winner() -> None:
    from general_ludd.small_models.benchmark_report import generate_report

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = _setup_store(path)
        store.register_evidence(
            _make_evidence_record(model_profile_id="strong-model", task_kind="coding", passed_cases=24, total_cases=25)
        )
        store.register_evidence(
            _make_evidence_record(model_profile_id="strong-model", task_kind="math", passed_cases=22, total_cases=25)
        )
        store.register_evidence(
            _make_evidence_record(
                model_profile_id="strong-model", task_kind="reasoning", passed_cases=23, total_cases=25
            )
        )
        store.register_evidence(
            _make_evidence_record(model_profile_id="weak-model", task_kind="coding", passed_cases=5, total_cases=25)
        )
        store.register_evidence(
            _make_evidence_record(model_profile_id="weak-model", task_kind="math", passed_cases=3, total_cases=25)
        )
        store.register_evidence(
            _make_evidence_record(model_profile_id="weak-model", task_kind="reasoning", passed_cases=4, total_cases=25)
        )

        report = generate_report(["strong-model", "weak-model"], store)

        assert report.overall_winner == "strong-model"
    finally:
        import os

        os.unlink(path)


def test_generate_report_with_radar_svg() -> None:
    from general_ludd.small_models.benchmark_report import generate_report

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = _setup_store(path)
        store.register_evidence(
            _make_evidence_record(model_profile_id="smollm2-135m", task_kind="coding", passed_cases=18, total_cases=25)
        )

        report = generate_report(["smollm2-135m"], store, include_svg=True)

        assert "smollm2-135m" in report.radar_svgs
        svg = report.radar_svgs["smollm2-135m"]
        assert svg.startswith("<?xml")
        assert "<svg" in svg
    finally:
        import os

        os.unlink(path)


def test_generate_report_without_svg_has_empty_svgs() -> None:
    from general_ludd.small_models.benchmark_report import generate_report

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = _setup_store(path)
        store.register_evidence(
            _make_evidence_record(model_profile_id="smollm2-135m", task_kind="coding", passed_cases=18, total_cases=25)
        )

        report = generate_report(["smollm2-135m"], store, include_svg=False)

        assert report.radar_svgs == {}
    finally:
        import os

        os.unlink(path)


def test_generate_report_radar_comparison_structure() -> None:
    from general_ludd.small_models.benchmark_report import generate_report

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = _setup_store(path)
        for idx, mid in enumerate(["model-x", "model-y", "model-z"]):
            for task in ["coding", "math", "reasoning"]:
                store.register_evidence(
                    _make_evidence_record(
                        model_profile_id=mid,
                        task_kind=task,
                        passed_cases=5 + idx * 5,
                        total_cases=25,
                    )
                )

        report = generate_report(["model-x", "model-y", "model-z"], store)

        comp = report.radar_comparison
        assert "profiles" in comp
        assert "mean" in comp
        assert "ranking" in comp
        assert "winner" in comp
        assert len(comp["ranking"]) == 3
    finally:
        import os

        os.unlink(path)


# ── render_report utility ──────────────────────────────────────────


def test_render_report_json_serializable() -> None:
    import json

    from general_ludd.small_models.benchmark_report import BenchmarkReport, render_report

    report = BenchmarkReport(
        models=["a"],
        per_model_scores={"a": {"coding": 0.72}},
        radar_comparison={"profiles": {}, "mean": {}, "ranking": ["a"], "winner": "a"},
        cost_analysis={"a": {"inference_usd_per_hour": 0.0001}},
        best_per_axis={"coding": "a"},
        overall_winner="a",
    )

    output = render_report(report)
    serialized = json.dumps(output)
    assert isinstance(json.loads(serialized), dict)
    assert output["models"] == ["a"]
    assert output["overall_winner"] == "a"
