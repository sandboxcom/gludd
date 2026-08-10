"""Deep edge-case tests for benchmark_report —
empty results, malformed evidence, zero scores, dict store, large sets."""

from __future__ import annotations

import hashlib
import tempfile
from typing import Any

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
    model_profile_id: str = "test-model",
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


def _setup_store(path: str):
    from general_ludd.small_models.evidence_store import CapabilityEvidenceStore

    return CapabilityEvidenceStore(path)


# ── Empty / all-zero results ────────────────────────────────────────


def test_generate_report_all_zero_scores_yields_no_winner() -> None:
    """When all models have 0 passed_cases across all tasks, overall_winner is None."""
    from general_ludd.small_models.benchmark_report import generate_report

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = _setup_store(path)
        for mid in ["model-x", "model-y"]:
            for task in ["coding", "math"]:
                store.register_evidence(
                    _make_evidence_record(
                        model_profile_id=mid,
                        task_kind=task,
                        passed_cases=0,
                        total_cases=25,
                    )
                )

        report = generate_report(["model-x", "model-y"], store)

        assert report.overall_winner is None
        # per_model_scores should be empty dicts (no positive scores)
        for mid in ["model-x", "model-y"]:
            assert report.per_model_scores[mid] == {}
        # best_per_axis should have all None values
        for v in report.best_per_axis.values():
            assert v is None
    finally:
        import os

        os.unlink(path)


def test_generate_report_no_evidence_all_models() -> None:
    """Models without any evidence get empty scores and no winner."""
    from general_ludd.small_models.benchmark_report import generate_report

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = _setup_store(path)
        report = generate_report(["ghost-a", "ghost-b", "ghost-c"], store)

        assert len(report.models) == 3
        assert report.overall_winner is None
        for mid in report.models:
            assert report.per_model_scores[mid] == {}
        for v in report.best_per_axis.values():
            assert v is None
    finally:
        import os

        os.unlink(path)


def test_generate_report_zero_then_some_evidence() -> None:
    """One model with zero scores, another with real scores — winner is the real one."""
    from general_ludd.small_models.benchmark_report import generate_report

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = _setup_store(path)
        store.register_evidence(
            _make_evidence_record(model_profile_id="empty-model", task_kind="coding", passed_cases=0, total_cases=25)
        )
        store.register_evidence(
            _make_evidence_record(model_profile_id="real-model", task_kind="coding", passed_cases=20, total_cases=25)
        )
        store.register_evidence(
            _make_evidence_record(model_profile_id="real-model", task_kind="math", passed_cases=22, total_cases=25)
        )

        report = generate_report(["empty-model", "real-model"], store)

        assert report.overall_winner == "real-model"
        assert report.per_model_scores["empty-model"] == {}
        assert report.per_model_scores["real-model"] != {}
    finally:
        import os

        os.unlink(path)


# ── Malformed / corrupt evidence ────────────────────────────────────


def test_generate_report_malformed_evidence_missing_fields() -> None:
    """Evidence dicts missing required fields are skipped gracefully."""
    from general_ludd.small_models.benchmark_report import generate_report

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = _setup_store(path)
        store.register_evidence(
            _make_evidence_record(model_profile_id="valid-model", task_kind="coding", passed_cases=20, total_cases=25)
        )
        store.register_evidence({"model_profile_id": "broken-model", "task_kind": "coding"})
        store.register_evidence({"garbage": True})

        report = generate_report(["valid-model", "broken-model"], store)

        assert report.models == ["valid-model", "broken-model"]
        assert report.per_model_scores["valid-model"] != {}
        assert report.per_model_scores["broken-model"] == {}
    finally:
        import os

        os.unlink(path)


def test_generate_report_evidence_wrong_types() -> None:
    """Evidence with wrong field types (string where int expected) is skipped."""
    from general_ludd.small_models.benchmark_report import generate_report

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = _setup_store(path)
        store.register_evidence(
            _make_evidence_record(model_profile_id="good-model", task_kind="coding", passed_cases=18, total_cases=25)
        )
        store.register_evidence(
            {
                "model_profile_id": "bad-types",
                "model_identity_digest": _digest("identity:bad-types:v1"),
                "task_kind": "math",
                "role": "editor",
                "collection": "general_ludd.agent",
                "suite_id": "small-model-contract",
                "suite_revision": "v1",
                "acceptance_contract_digest": _digest("contract:math:editor"),
                "passed_cases": "not_an_int",
                "total_cases": "also_not_an_int",
                "collection_ok": "wrong_type",
                "local_only": "wrong_type",
                "evidence_digest": _digest("proof:bad-types:math"),
            }
        )

        report = generate_report(["good-model", "bad-types"], store)

        assert report.per_model_scores["good-model"] != {}
        assert report.per_model_scores["bad-types"] == {}
    finally:
        import os

        os.unlink(path)


def test_generate_report_only_malformed_evidence() -> None:
    """When ALL evidence is corrupt, report is empty but not broken."""
    from general_ludd.small_models.benchmark_report import generate_report

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = _setup_store(path)
        store.register_evidence({"bad": "record"})

        report = generate_report(["corrupt-model"], store)

        assert report.models == ["corrupt-model"]
        assert report.overall_winner is None
        assert report.per_model_scores["corrupt-model"] == {}
        assert len(report.cost_analysis) == 1
    finally:
        import os

        os.unlink(path)


# ── Dict store (non-CapabilityEvidenceStore path) ───────────────────


def test_generate_report_dict_store() -> None:
    """generate_report works with a plain dict store keyed by cap:<model_id>."""
    from general_ludd.small_models.benchmark_report import generate_report

    store: dict[str, list[dict[str, Any]]] = {
        "cap:dict-model": [
            _make_evidence_record(model_profile_id="dict-model", task_kind="coding", passed_cases=20, total_cases=25),
            _make_evidence_record(model_profile_id="dict-model", task_kind="math", passed_cases=18, total_cases=25),
        ],
    }

    report = generate_report(["dict-model"], store)

    assert report.models == ["dict-model"]
    assert report.overall_winner == "dict-model"
    assert "dict-model" in report.per_model_scores
    assert len(report.cost_analysis) == 1


def test_generate_report_dict_store_missing_model() -> None:
    """Dict store with no matching cap:<model_id> key yields empty report."""
    from general_ludd.small_models.benchmark_report import generate_report

    store: dict[str, list[dict[str, Any]]] = {
        "cap:other-model": [
            _make_evidence_record(model_profile_id="other-model", task_kind="coding", passed_cases=15, total_cases=25),
        ],
    }

    report = generate_report(["not-in-store"], store)

    assert report.models == ["not-in-store"]
    assert report.overall_winner is None
    assert report.per_model_scores["not-in-store"] == {}


def test_generate_report_dict_store_partial() -> None:
    """Dict store with some models present, some absent."""
    from general_ludd.small_models.benchmark_report import generate_report

    store: dict[str, list[dict[str, Any]]] = {
        "cap:model-a": [
            _make_evidence_record(model_profile_id="model-a", task_kind="coding", passed_cases=20, total_cases=25),
        ],
    }

    report = generate_report(["model-a", "model-b"], store)

    assert len(report.models) == 2
    assert report.per_model_scores["model-a"] != {}
    assert report.per_model_scores["model-b"] == {}


# ── Non-dict, non-store object ──────────────────────────────────────


def test_generate_report_unknown_store_type() -> None:
    """A store object without query_by_model or dict interface yields empty evidence."""
    from general_ludd.small_models.benchmark_report import generate_report

    class UnknownStore:
        pass

    report = generate_report(["any-model"], UnknownStore())

    assert report.models == ["any-model"]
    assert report.overall_winner is None
    assert report.per_model_scores["any-model"] == {}


# ── _query_evidence direct ──────────────────────────────────────────


def test_query_evidence_dict_store() -> None:
    """_query_evidence returns records from a plain dict."""
    from general_ludd.small_models.benchmark_report import _query_evidence

    rec = _make_evidence_record(model_profile_id="qm", task_kind="coding")
    store = {"cap:qm": [rec]}
    result = _query_evidence(store, "qm")
    assert len(result) == 1
    assert result[0]["model_profile_id"] == "qm"


def test_query_evidence_dict_store_missing_key() -> None:
    """_query_evidence on dict with no matching key returns empty list."""
    from general_ludd.small_models.benchmark_report import _query_evidence

    result = _query_evidence({"cap:x": []}, "y")
    assert result == []


def test_query_evidence_unknown_object() -> None:
    """_query_evidence on non-store, non-dict object returns empty list."""
    from general_ludd.small_models.benchmark_report import _query_evidence

    result = _query_evidence(object(), "any")
    assert result == []


# ── _empty_comparison shape ─────────────────────────────────────────


def test_empty_comparison_structure() -> None:
    """_empty_comparison returns the expected dict shape."""
    from general_ludd.small_models.benchmark_report import _empty_comparison

    ec = _empty_comparison()
    assert ec == {"profiles": {}, "mean": {}, "ranking": [], "winner": None}


# ── _compute_best_per_axis edge cases ───────────────────────────────


def test_compute_best_per_axis_no_profiles() -> None:
    """No profiles → all axes return None."""
    from general_ludd.small_models.benchmark_report import _compute_best_per_axis

    result = _compute_best_per_axis([], [])
    assert len(result) == len(_MT_BENCH_AXES)
    for v in result.values():
        assert v is None


def test_compute_best_per_axis_single_profile() -> None:
    """Single profile — becomes best on all non-zero axes."""
    from general_ludd.small_models.benchmark_report import _compute_best_per_axis
    from general_ludd.small_models.radar_profile import ModelRadarProfile

    profile = ModelRadarProfile(model_profile_id="solo")
    result = _compute_best_per_axis([profile], ["solo"])
    # no scores set → all None
    for v in result.values():
        assert v is None


def test_compute_best_per_axis_tie_same_scores() -> None:
    """When two models have identical normalized scores on an axis, the first wins."""

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        from general_ludd.small_models.benchmark_report import generate_report

        store = _setup_store(path)
        for mid in ["tie-a", "tie-b"]:
            store.register_evidence(
                _make_evidence_record(model_profile_id=mid, task_kind="coding", passed_cases=20, total_cases=25)
            )

        report = generate_report(["tie-a", "tie-b"], store)

        assert report.best_per_axis["coding"] is not None
    finally:
        import os

        os.unlink(path)


# ── _build_cost_analysis edge cases ─────────────────────────────────


def test_build_cost_analysis_shape() -> None:
    """_build_cost_analysis returns well-formed dict with all keys."""
    from general_ludd.small_models.benchmark_report import _build_cost_analysis

    ca = _build_cost_analysis("test-model")
    assert ca["model_id"] == "test-model"
    assert "tier" in ca
    assert "inference" in ca
    assert "download" in ca
    assert "estimated_usd_per_hour" in ca
    inf = ca["inference"]
    dld = ca["download"]
    assert isinstance(inf, dict) and isinstance(dld, dict)
    assert "input_usd_per_1m_tokens" in inf
    assert "output_usd_per_1m_tokens" in inf
    assert "estimated_usd_per_hour" in inf
    assert "estimated_tokens_per_hour" in inf
    assert "size_gb" in dld
    assert "data_transfer_usd" in dld
    assert "estimated_storage_usd_per_month" in dld


# ── Extreme scores ──────────────────────────────────────────────────


def test_generate_report_perfect_score() -> None:
    """Model with 100% pass on all tasks — should be winner."""
    from general_ludd.small_models.benchmark_report import generate_report

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = _setup_store(path)
        for task in ("coding", "math", "reasoning", "writing"):
            store.register_evidence(
                _make_evidence_record(model_profile_id="perfect", task_kind=task, passed_cases=25, total_cases=25)
            )

        report = generate_report(["perfect"], store)

        assert report.overall_winner == "perfect"
        scores = report.per_model_scores["perfect"]
        assert all(isinstance(v, float) for v in scores.values())
        assert all(0.0 <= v <= 1.0 for v in scores.values())
    finally:
        import os

        os.unlink(path)


def test_generate_report_worst_score() -> None:
    """Model with 0% pass — overall_winner is None."""
    from general_ludd.small_models.benchmark_report import generate_report

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = _setup_store(path)
        store.register_evidence(
            _make_evidence_record(model_profile_id="worst", task_kind="coding", passed_cases=0, total_cases=25)
        )

        report = generate_report(["worst"], store)

        assert report.overall_winner is None
        assert report.per_model_scores["worst"] == {}
    finally:
        import os

        os.unlink(path)


def test_generate_report_lopsided_comparison() -> None:
    """One model dominates all axes, another has near-zero — winner is clear."""
    from general_ludd.small_models.benchmark_report import generate_report

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = _setup_store(path)
        for task in ("coding", "math", "reasoning", "writing", "stem"):
            store.register_evidence(
                _make_evidence_record(model_profile_id="dominant", task_kind=task, passed_cases=24, total_cases=25)
            )
            store.register_evidence(
                _make_evidence_record(model_profile_id="weak", task_kind=task, passed_cases=1, total_cases=25)
            )

        report = generate_report(["dominant", "weak"], store)

        assert report.overall_winner == "dominant"
        # best_per_axis all point to dominant
        for _axis, best in report.best_per_axis.items():
            if best is not None:
                assert best == "dominant"
    finally:
        import os

        os.unlink(path)


# ── Large result sets ──────────────────────────────────────────────


def test_generate_report_many_models_ranking() -> None:
    """Report with many models produces correct ranking length."""
    from general_ludd.small_models.benchmark_report import generate_report

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = _setup_store(path)
        n_models = 20
        for i in range(n_models):
            mid = f"model-{i:03d}"
            for task in ("coding", "math"):
                store.register_evidence(
                    _make_evidence_record(model_profile_id=mid, task_kind=task, passed_cases=5 + i, total_cases=25)
                )

        model_ids = [f"model-{i:03d}" for i in range(n_models)]
        report = generate_report(model_ids, store)

        assert len(report.models) == n_models
        assert len(report.cost_analysis) == n_models
        assert len(report.per_model_scores) == n_models
        ranking = report.radar_comparison.get("ranking", [])
        assert isinstance(ranking, list)
        assert len(ranking) == n_models
    finally:
        import os

        os.unlink(path)


# ── Model ID edge cases ─────────────────────────────────────────────


def test_generate_report_whitespace_model_id() -> None:
    """Model IDs with surrounding whitespace are stripped."""
    from general_ludd.small_models.benchmark_report import generate_report

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = _setup_store(path)
        store.register_evidence(
            _make_evidence_record(model_profile_id="clean", task_kind="coding", passed_cases=20, total_cases=25)
        )

        report = generate_report(["  clean  "], store)

        assert report.models == ["clean"]
    finally:
        import os

        os.unlink(path)


def test_generate_report_non_string_model_id() -> None:
    """Model IDs that aren't strings are converted via str()."""
    from general_ludd.small_models.benchmark_report import generate_report

    dict_ids: list[object] = [123]  # type: ignore[list-item]
    report = generate_report(dict_ids, {})  # type: ignore[arg-type]

    assert report.models == ["123"]
    assert report.overall_winner is None


# ── render_report edge cases ────────────────────────────────────────


def test_render_report_with_svgs() -> None:
    """render_report includes radar_svgs when present."""
    from general_ludd.small_models.benchmark_report import BenchmarkReport, render_report

    report = BenchmarkReport(
        models=["m"],
        per_model_scores={"m": {"coding": 0.5}},
        radar_comparison={},
        cost_analysis={},
        best_per_axis={},
        overall_winner="m",
        radar_svgs={"m": "<svg>fake</svg>"},
    )
    output = render_report(report)
    assert output["radar_svgs"] == {"m": "<svg>fake</svg>"}


def test_render_report_svgs_none_when_empty() -> None:
    """render_report returns None for radar_svgs when dict is empty."""
    from general_ludd.small_models.benchmark_report import BenchmarkReport, render_report

    report = BenchmarkReport(
        models=["m"],
        per_model_scores={},
        radar_comparison={},
        cost_analysis={},
        best_per_axis={},
        overall_winner=None,
        radar_svgs={},
    )
    output = render_report(report)
    assert output["radar_svgs"] is None


def test_render_report_includes_all_top_level_keys() -> None:
    """render_report output contains all 7 expected keys."""
    from general_ludd.small_models.benchmark_report import BenchmarkReport, render_report

    report = BenchmarkReport(
        models=["x"],
        per_model_scores={},
        radar_comparison={},
        cost_analysis={},
        best_per_axis={},
        overall_winner=None,
    )
    output = render_report(report)
    expected_keys = {
        "models",
        "per_model_scores",
        "radar_comparison",
        "cost_analysis",
        "best_per_axis",
        "overall_winner",
        "radar_svgs",
    }
    assert set(output.keys()) == expected_keys


# ── Duplicate model IDs ─────────────────────────────────────────────


def test_generate_report_duplicate_model_ids() -> None:
    """Identical model IDs yield a profile for each occurrence (not deduplicated)."""
    from general_ludd.small_models.benchmark_report import generate_report

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = _setup_store(path)
        store.register_evidence(
            _make_evidence_record(model_profile_id="dup", task_kind="coding", passed_cases=20, total_cases=25)
        )

        report = generate_report(["dup", "dup", "dup"], store)

        assert len(report.models) == 3
        # last write wins for per_model_scores
        assert "dup" in report.per_model_scores
    finally:
        import os

        os.unlink(path)


# ── _build_profile_from_evidence edge cases ─────────────────────────


def test_build_profile_from_evidence_empty_list() -> None:
    """Empty evidence list returns a profile with model_profile_id set."""
    from general_ludd.small_models.benchmark_report import _build_profile_from_evidence

    profile = _build_profile_from_evidence("test-id", [])
    assert profile.model_profile_id == "test-id"


def test_build_profile_from_evidence_all_invalid() -> None:
    """All evidence dicts fail conversion → profile with just model_profile_id."""
    from general_ludd.small_models.benchmark_report import _build_profile_from_evidence

    profile = _build_profile_from_evidence(
        "fallback-id",
        [
            {"bad": "record"},
            {"also": "invalid"},
        ],
    )
    assert profile.model_profile_id == "fallback-id"


# ── overall_winner edge: ranking present but scores zero ────────────


def test_overall_winner_ranking_present_but_zero_scores() -> None:
    """When ranking[0] exists but model scores are empty → overall_winner is None."""
    from general_ludd.small_models.benchmark_report import generate_report

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = _setup_store(path)
        store.register_evidence(
            _make_evidence_record(model_profile_id="zero-model", task_kind="coding", passed_cases=0, total_cases=25)
        )

        report = generate_report(["zero-model"], store)

        ranking = report.radar_comparison.get("ranking", [])
        assert isinstance(ranking, list)
        assert len(ranking) == 1
        assert report.overall_winner is None
    finally:
        import os

        os.unlink(path)


# ── Score formatting: verified floats ───────────────────────────────


def test_generate_report_scores_are_bounded_floats() -> None:
    """All normalized scores are floats in [0.0, 1.0]."""
    from general_ludd.small_models.benchmark_report import generate_report

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = _setup_store(path)
        for task in ("coding", "math", "reasoning", "writing", "stem", "humanities"):
            store.register_evidence(
                _make_evidence_record(model_profile_id="bounded", task_kind=task, passed_cases=12, total_cases=25)
            )

        report = generate_report(["bounded"], store)

        scores = report.per_model_scores.get("bounded", {})
        for key, val in scores.items():
            assert isinstance(val, float), f"{key} is {type(val)}"
            assert 0.0 <= val <= 1.0, f"{key} = {val}"
    finally:
        import os

        os.unlink(path)


# ── BenchmarkReport frozen + equality ───────────────────────────────


def test_benchmark_report_not_equal_different_models() -> None:
    """Two reports with different model lists are not equal (default dataclass eq)."""
    from general_ludd.small_models.benchmark_report import BenchmarkReport

    a = BenchmarkReport(
        models=["m1"],
        per_model_scores={},
        radar_comparison={},
        cost_analysis={},
        best_per_axis={},
        overall_winner=None,
    )
    b = BenchmarkReport(
        models=["m2"],
        per_model_scores={},
        radar_comparison={},
        cost_analysis={},
        best_per_axis={},
        overall_winner=None,
    )
    assert a != b


def test_benchmark_report_equal_same_fields() -> None:
    """Two reports with identical fields are equal."""
    from general_ludd.small_models.benchmark_report import BenchmarkReport

    a = BenchmarkReport(
        models=["m"],
        per_model_scores={"m": {"c": 0.5}},
        radar_comparison={},
        cost_analysis={},
        best_per_axis={"c": "m"},
        overall_winner="m",
    )
    b = BenchmarkReport(
        models=["m"],
        per_model_scores={"m": {"c": 0.5}},
        radar_comparison={},
        cost_analysis={},
        best_per_axis={"c": "m"},
        overall_winner="m",
    )
    assert a == b
