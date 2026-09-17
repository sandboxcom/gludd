"""Branch-focused behavioral coverage for small-model recommendations."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

import general_ludd.small_models.cost as cost
import general_ludd.small_models.recommender as recommender
from general_ludd.hardware.survey import GpuInfo, HardwareInventory
from general_ludd.schemas.benchmark import TaskRole


def _hardware(vram_gb: float | None = 8.0) -> HardwareInventory:
    gpus = [] if vram_gb is None else [GpuInfo(name="test-gpu", vram_gb=vram_gb, backend="cuda")]
    return HardwareInventory(gpus=gpus, total_ram_gb=16.0, disk_free_gb=100.0, cpu_cores=8)


def _record(
    model_id: str = "model-fast",
    task_kind: str = "coding",
    *,
    collection_ok: bool = True,
) -> dict[str, Any]:
    return {
        "model_profile_id": model_id,
        "task_kind": task_kind,
        "suite_id": "branch-suite",
        "suite_revision": "v1",
        "passed_cases": 3,
        "total_cases": 4,
        "collection_ok": collection_ok,
        "local_only": True,
    }


def _recommendation_kwargs(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "model_profile_id": "model-fast",
        "task_kind": "coding",
        "role": TaskRole.CODER,
        "score": 0.8,
        "cost_score": 0.7,
        "estimated_cost_usd_per_hour": 0.01,
        "evidence_count": 1,
        "hardware_fit": "fits",
        "evidence_details": [],
        "can_run": True,
        "peak_status": "off_peak",
        "prefer_off_peak": False,
    }
    values.update(overrides)
    return values


class _Store:
    def __init__(
        self,
        *,
        by_task: dict[str, list[dict[str, Any]]] | None = None,
        by_model: dict[str, list[dict[str, Any]]] | None = None,
    ) -> None:
        self.by_task = by_task or {}
        self.by_model = by_model or {}

    def query_by_task_kind(self, task_kind: str) -> list[dict[str, Any]]:
        return self.by_task.get(task_kind, [])

    def query_by_model(self, model_id: str) -> list[dict[str, Any]]:
        return self.by_model.get(model_id, [])


class _Radar:
    def __init__(self, scores: dict[str, float]) -> None:
        self.scores = scores

    def normalized(self) -> dict[str, float]:
        return self.scores


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("score", "invalid"),
        ("score", -0.1),
        ("cost_score", "invalid"),
        ("cost_score", 1.1),
        ("evidence_count", True),
        ("evidence_count", -1),
        ("hardware_fit", "unknown"),
        ("peak_status", "later"),
    ],
)
def test_recommendation_rejects_invalid_metadata(field: str, value: object) -> None:
    values = _recommendation_kwargs()
    values[field] = value

    with pytest.raises(ValueError):
        recommender.ModelRecommendation(**values)


def test_recommendation_accepts_valid_boundary_values() -> None:
    recommendation = recommender.ModelRecommendation(
        **_recommendation_kwargs(score=0.0, cost_score=1.0, evidence_count=0)
    )

    assert recommendation.score == 0.0
    assert recommendation.cost_score == 1.0


def test_task_mapper_covers_keywords_alias_and_invalid_input() -> None:
    description = "Compact and document, enumerate, classify, format, extract schema, then code."

    matches = recommender.map_task_to_capabilities(description)

    assert {task_kind for task_kind, _role in matches} == {
        "context_compaction",
        "documentation_draft",
        "bounded_enumeration",
        "failure_classification",
        "format_normalization",
        "schema_extraction",
        "coding",
    }
    assert recommender._map_task_to_capabilities("unrelated prose") == []
    with pytest.raises(ValueError, match="description must be a string"):
        recommender.map_task_to_capabilities(cast(Any, None))


@pytest.mark.parametrize(
    ("vram_gb", "expected"),
    [(None, "insufficient"), (4.0, "fits"), (1.0, "marginal"), (0.5, "insufficient")],
)
def test_hardware_fit_covers_each_capacity_class(vram_gb: float | None, expected: str) -> None:
    assert recommender._assess_hardware_fit(_hardware(vram_gb)) == expected


@pytest.mark.parametrize(("raw_cost", "expected"), [(0.125, 0.125), ("unknown", 0.0)])
def test_cost_factors_normalize_numeric_and_unknown_estimates(
    monkeypatch: pytest.MonkeyPatch,
    raw_cost: object,
    expected: float,
) -> None:
    monkeypatch.setattr(cost, "compute_cost_score", lambda _model_id: 0.75)
    monkeypatch.setattr(
        cost,
        "estimate_inference_cost",
        lambda _model_id: {"estimated_usd_per_hour": raw_cost},
    )

    assert recommender._compute_cost_factors("model-fast") == (0.75, 0.75, expected)


def test_score_covers_empty_peak_urgent_and_radar_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    hardware = _hardware()
    assert recommender._compute_score([], hardware, "model-fast") == (0.0, 1.0, 0.0)

    records = [
        {"passed_cases": 1, "total_cases": 0, "collection_ok": True},
        {"passed_cases": 0, "total_cases": 2, "collection_ok": False},
        {"passed_cases": 2, "total_cases": 2, "collection_ok": True},
        {"passed_cases": 1, "total_cases": 1, "collection_ok": True},
    ]
    monkeypatch.setattr(recommender, "_compute_cost_factors", lambda _model_id: (0.4, 0.4, 0.2))
    monkeypatch.setattr(recommender, "is_off_peak", lambda: False)

    peak = recommender._compute_score(
        records,
        hardware,
        "model-fast",
        radar_profile=cast(Any, _Radar({"coding": 0.8, "formatting": 0.0})),
    )
    urgent = recommender._compute_score(records, hardware, "model-fast", urgent=True)
    monkeypatch.setattr(recommender, "is_off_peak", lambda: True)
    off_peak = recommender._compute_score(records, hardware, "model-fast")

    assert peak[1:] == (0.4, 0.2)
    assert urgent[1:] == (0.4, 0.2)
    assert off_peak == urgent
    assert peak != urgent


def test_evidence_details_preserve_values_and_supply_defaults() -> None:
    assert recommender._build_evidence_details([_record(), {}]) == [
        {
            "suite_id": "branch-suite",
            "suite_revision": "v1",
            "passed_cases": 3,
            "total_cases": 4,
            "collection_ok": True,
            "local_only": True,
        },
        {
            "suite_id": "",
            "suite_revision": "",
            "passed_cases": 0,
            "total_cases": 0,
            "collection_ok": False,
            "local_only": False,
        },
    ]


def test_recommendation_filters_merges_profiles_and_sorts(monkeypatch: pytest.MonkeyPatch) -> None:
    fast_doc = _record("model-fast", "documentation_draft")
    fast_code = _record("model-fast", "coding")
    store = _Store(
        by_task={
            "context_compaction": [_record(collection_ok=False)],
            "documentation_draft": [
                _record("", "documentation_draft"),
                fast_doc,
                _record("model-blocked", "documentation_draft"),
                _record("model-unknown", "documentation_draft"),
            ],
            "coding": [fast_code],
        },
        by_model={"model-fast": [fast_doc, fast_code]},
    )

    def fit_result(_hardware: HardwareInventory, model_id: str) -> SimpleNamespace:
        if model_id == "model-blocked":
            return SimpleNamespace(can_run=False, reason="requires more memory")
        if model_id == "model-unknown":
            return SimpleNamespace(can_run=False, reason="unknown model profile")
        return SimpleNamespace(can_run=True, reason="fits")

    def score_result(
        _records: list[dict[str, Any]],
        _hardware: HardwareInventory,
        model_id: str,
        _radar_profile: object | None = None,
        *,
        radar_profile: object | None = None,
        urgent: bool = False,
    ) -> tuple[float, float, float]:
        del _radar_profile, radar_profile, urgent
        return ((0.9 if model_id == "model-fast" else 0.3), 0.8, 0.01)

    built_profiles: list[str] = []

    def build_profile(model_id: str, _records: list[dict[str, Any]]) -> _Radar:
        built_profiles.append(model_id)
        return _Radar({"coding": 1.0})

    monkeypatch.setattr(recommender, "can_run_model", fit_result)
    monkeypatch.setattr(recommender, "build_profile", build_profile)
    monkeypatch.setattr(recommender, "_compute_score", score_result)
    monkeypatch.setattr(recommender, "is_off_peak", lambda: True)

    recommendations = recommender.recommend_model(
        "compact a document and code the change",
        _hardware(),
        cast(Any, store),
    )

    assert [item.model_profile_id for item in recommendations] == ["model-fast", "model-unknown"]
    assert recommendations[0].evidence_count == 2
    assert recommendations[0].peak_status == "off_peak"
    assert recommendations[0].prefer_off_peak is False
    assert recommendations[1].can_run is False
    assert built_profiles == ["model-fast"]


@pytest.mark.parametrize(("urgent", "prefer_off_peak"), [(False, True), (True, False)])
def test_peak_recommendation_reflects_urgency(
    monkeypatch: pytest.MonkeyPatch,
    urgent: bool,
    prefer_off_peak: bool,
) -> None:
    record = _record("model-peak")
    store = _Store(by_task={"coding": [record]})
    monkeypatch.setattr(
        recommender,
        "can_run_model",
        lambda _hardware, _model_id: SimpleNamespace(can_run=True, reason="fits"),
    )
    monkeypatch.setattr(recommender, "is_off_peak", lambda: False)
    monkeypatch.setattr(recommender, "_compute_score", lambda *_args, **_kwargs: (0.5, 0.5, 0.0))

    recommendations = recommender.recommend_model(
        "code",
        _hardware(),
        cast(Any, store),
        urgent=urgent,
    )

    assert recommendations[0].peak_status == "peak"
    assert recommendations[0].prefer_off_peak is prefer_off_peak


def test_empty_mapping_and_task_listing_paths() -> None:
    store = _Store(
        by_model={
            "model-fast": [
                {"task_kind": "coding"},
                {"task_kind": "format_normalization"},
                {"task_kind": "coding"},
                {"task_kind": ""},
                {},
            ]
        }
    )

    assert recommender.recommend_model("unrelated prose", _hardware(), cast(Any, store)) == []
    assert recommender.list_tasks_for_model("missing", cast(Any, store)) == []
    assert recommender.list_tasks_for_model("model-fast", cast(Any, store)) == [
        "coding",
        "format_normalization",
    ]
