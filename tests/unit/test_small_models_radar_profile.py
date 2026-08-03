"""Tests for radar_profile — MT-Bench 8-axis capability profiling for small models."""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET

import pytest

from general_ludd.routing_roles.small_model_policy import (
    CapabilityEvidence,
    ModelIdentity,
)
from general_ludd.schemas.benchmark import TaskRole
from general_ludd.small_models.radar_profile import (
    _MT_BENCH_AXES,
    ModelRadarProfile,
    best_for_task,
    compare_models,
    generate_radar,
    render_radar_svg,
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _identity(model_profile_id: str = "test-model") -> ModelIdentity:
    return ModelIdentity(
        model_profile_id=model_profile_id,
        model_artifact_digest=_digest("weights-v1"),
        runtime_config_digest=_digest("runtime:v1"),
        prompt_contract_digest=_digest("prompt:v1"),
    )


def _evidence(
    model_profile_id: str = "test-model",
    task_kind: str = "context_compaction",
    role: TaskRole = TaskRole.COMPACTOR,
    passed_cases: int = 90,
    total_cases: int = 100,
) -> CapabilityEvidence:
    identity = _identity(model_profile_id)
    return CapabilityEvidence(
        model_profile_id=identity.model_profile_id,
        model_identity_digest=identity.fingerprint,
        task_kind=task_kind,
        role=role,
        collection="general_ludd.agent",
        suite_id="small-model-contract",
        suite_revision="v1",
        acceptance_contract_digest=_digest(f"contract:{task_kind}:{role.value}"),
        passed_cases=passed_cases,
        total_cases=total_cases,
        collection_ok=True,
        local_only=True,
        evidence_digest=_digest(f"proof:{model_profile_id}:{task_kind}"),
    )


# -- ModelRadarProfile --------------------------------------------------


class TestModelRadarProfile:
    def test_creates_with_default_scores(self) -> None:
        profile = ModelRadarProfile(model_profile_id="model-a")
        assert profile.model_profile_id == "model-a"
        assert len(profile.scores) == 9
        assert all(s == 0.0 for s in profile.scores.values())

    def test_has_correct_axis_count(self) -> None:
        assert len(_MT_BENCH_AXES) == 9
        assert "writing" in _MT_BENCH_AXES
        assert "roleplay" in _MT_BENCH_AXES
        assert "extraction" in _MT_BENCH_AXES
        assert "reasoning" in _MT_BENCH_AXES
        assert "math" in _MT_BENCH_AXES
        assert "coding" in _MT_BENCH_AXES
        assert "stem" in _MT_BENCH_AXES
        assert "humanities" in _MT_BENCH_AXES
        assert "cost" in _MT_BENCH_AXES

    def test_scores_clamped_to_0_1(self) -> None:
        profile = ModelRadarProfile(model_profile_id="clamped")
        profile.scores["writing"] = 1.5
        profile.scores["math"] = -2.0
        assert profile.scores["writing"] == 1.0
        assert profile.scores["math"] == 0.0

    def test_rejects_invalid_model_id(self) -> None:
        with pytest.raises(ValueError):
            ModelRadarProfile(model_profile_id="")

    def test_rejects_invalid_axis_name(self) -> None:
        profile = ModelRadarProfile(model_profile_id="test")
        with pytest.raises(KeyError):
            profile.scores["nonexistent"]

    def test_normalized_returns_0_1_copy(self) -> None:
        profile = ModelRadarProfile(model_profile_id="test")
        profile.scores["writing"] = 0.9
        profile.scores["math"] = 0.3
        norm = profile.normalized()
        assert norm is not profile
        assert all(0.0 <= v <= 1.0 for v in norm.values())

    def test_active_axes_filters_nonzero(self) -> None:
        profile = ModelRadarProfile(model_profile_id="test")
        profile.scores["writing"] = 0.8
        profile.scores["reasoning"] = 0.6
        active = profile.active_axes()
        assert len(active) == 2
        assert "writing" in active
        assert "reasoning" in active
        assert "math" not in active


# -- generate_radar -----------------------------------------------------


class TestGenerateRadar:
    def test_empty_evidence_returns_zero_profile(self) -> None:
        profile = generate_radar([])
        assert profile.model_profile_id == "unknown"
        assert all(v == 0.0 for v in profile.scores.values())

    def test_single_task_kind_maps_to_correct_axis(self) -> None:
        evs = [
            _evidence(task_kind="documentation_draft", passed_cases=80, total_cases=100),
        ]
        profile = generate_radar(evs)
        assert profile.scores["writing"] == 0.8
        assert profile.scores["reasoning"] == 0.0

    def test_multiple_evidence_same_axis_averaged(self) -> None:
        evs = [
            _evidence(task_kind="documentation_draft", passed_cases=80, total_cases=100),
            _evidence(task_kind="schema_extraction", passed_cases=60, total_cases=100),
        ]
        profile = generate_radar(evs)
        assert profile.scores["writing"] == 0.8
        assert profile.scores["extraction"] == 0.6

    def test_multiple_evidence_axes_aggregated(self) -> None:
        evs = [
            _evidence(task_kind="context_compaction", passed_cases=90, total_cases=100),
            _evidence(task_kind="failure_classification", passed_cases=70, total_cases=100),
            _evidence(task_kind="documentation_draft", passed_cases=85, total_cases=100),
            _evidence(task_kind="schema_extraction", passed_cases=60, total_cases=100),
        ]
        profile = generate_radar(evs)
        assert profile.scores["extraction"] == pytest.approx(0.75)
        assert profile.scores["reasoning"] == 0.7
        assert profile.scores["writing"] > 0.0

    def test_unknown_task_kind_skipped(self) -> None:
        evs = [
            _evidence(task_kind="unknown_kind_xyz", passed_cases=50, total_cases=100),
        ]
        profile = generate_radar(evs)
        assert all(v == 0.0 for v in profile.scores.values())

    def test_direct_axis_name_mapped(self) -> None:
        evs = [
            _evidence(task_kind="coding", passed_cases=95, total_cases=100),
        ]
        profile = generate_radar(evs)
        assert profile.scores["coding"] == 0.95

    def test_uses_model_profile_id_from_evidence(self) -> None:
        evs = [
            _evidence(model_profile_id="specific-model", task_kind="coding", passed_cases=100, total_cases=100),
        ]
        profile = generate_radar(evs)
        assert profile.model_profile_id == "specific-model"

    def test_zero_total_cases_skipped(self) -> None:
        evs = [
            _evidence(task_kind="coding", passed_cases=0, total_cases=1),
        ]
        profile = generate_radar(evs)
        assert profile.scores["coding"] == 0.0


# -- render_radar_svg ---------------------------------------------------


class TestRenderRadarSvg:
    def test_renders_valid_svg(self) -> None:
        profile = ModelRadarProfile(model_profile_id="svg-test")
        profile.scores["writing"] = 0.9
        profile.scores["reasoning"] = 0.6
        profile.scores["coding"] = 0.8
        svg = render_radar_svg(profile)
        assert svg.startswith("<?xml") or svg.startswith("<svg")
        root = ET.fromstring(svg)
        assert root.tag == "{http://www.w3.org/2000/svg}svg"

    def test_svg_contains_axis_labels(self) -> None:
        profile = ModelRadarProfile(model_profile_id="label-test")
        profile.scores["writing"] = 0.5
        profile.scores["coding"] = 0.7
        svg = render_radar_svg(profile)
        assert "Writing" in svg
        assert "Coding" in svg

    def test_svg_contains_model_id_title(self) -> None:
        profile = ModelRadarProfile(model_profile_id="unique-model")
        profile.scores["writing"] = 0.4
        svg = render_radar_svg(profile)
        assert "unique-model" in svg

    def test_svg_all_zero_profile_still_renders(self) -> None:
        profile = ModelRadarProfile(model_profile_id="empty")
        svg = render_radar_svg(profile)
        root = ET.fromstring(svg)
        assert root is not None

    def test_svg_full_profile_renders_all_9_axes(self) -> None:
        profile = ModelRadarProfile(model_profile_id="full")
        for axis in _MT_BENCH_AXES:
            if axis == "cost":
                profile.cost_score = 0.75
            else:
                profile.scores[axis] = 0.75
        svg = render_radar_svg(profile)
        for axis in _MT_BENCH_AXES:
            label = axis.upper() if axis == "stem" else axis.capitalize()
            assert label in svg

    def test_rejects_invalid_axis_keyerror_on_write(self) -> None:
        profile = ModelRadarProfile(model_profile_id="bad")
        with pytest.raises(KeyError):
            profile.scores["not_an_axis"] = 0.5

    def test_profile_created_with_all_axes_present(self) -> None:
        p = ModelRadarProfile(model_profile_id="created")
        for axis in _MT_BENCH_AXES:
            assert axis in p.scores

    def test_cost_score_included_in_scores(self) -> None:
        p = ModelRadarProfile(model_profile_id="cost-test", cost_score=0.85)
        assert p.scores["cost"] == 0.85
        assert "cost" in p.scores

    def test_cost_score_clamped(self) -> None:
        p = ModelRadarProfile(model_profile_id="clamp-cost", cost_score=1.5)
        assert p.cost_score == 1.0
        p2 = ModelRadarProfile(model_profile_id="clamp-cost-neg", cost_score=-0.5)
        assert p2.cost_score == 0.0


# -- compare_models -----------------------------------------------------


class TestCompareModels:
    def test_single_model_returns_self_comparison(self) -> None:
        p = ModelRadarProfile(model_profile_id="only")
        p.scores["writing"] = 0.8
        result = compare_models([p])
        assert result["profiles"]["only"] == p.scores
        assert result["mean"]["writing"] == 0.8

    def test_two_models_compute_mean_and_std(self) -> None:
        a = ModelRadarProfile(model_profile_id="a")
        a.scores["coding"] = 0.9
        a.scores["writing"] = 0.4
        b = ModelRadarProfile(model_profile_id="b")
        b.scores["coding"] = 0.5
        b.scores["writing"] = 0.8
        result = compare_models([a, b])
        assert abs(result["mean"]["coding"] - 0.7) < 0.01
        assert abs(result["mean"]["writing"] - 0.6) < 0.01

    def test_ranking_sorts_by_mean_score(self) -> None:
        a = ModelRadarProfile(model_profile_id="low")
        a.scores["writing"] = 0.2
        a.scores["coding"] = 0.2
        b = ModelRadarProfile(model_profile_id="high")
        b.scores["writing"] = 0.9
        b.scores["coding"] = 0.9
        result = compare_models([a, b])
        assert result["ranking"][0] == "high"
        assert result["ranking"][1] == "low"
        assert result["winner"] == "high"

    def test_empty_profiles_returns_clean_result(self) -> None:
        result = compare_models([])
        assert result["profiles"] == {}
        assert result["ranking"] == []
        assert result["winner"] is None
        assert result["mean"] == {}

    def test_all_zero_profiles(self) -> None:
        a = ModelRadarProfile(model_profile_id="zero-a")
        b = ModelRadarProfile(model_profile_id="zero-b")
        result = compare_models([a, b])
        assert result["mean"]["writing"] == 0.0
        assert len(result["ranking"]) == 2


# -- best_for_task ------------------------------------------------------


class TestBestForTask:
    def test_selects_highest_scoring_model_for_axis(self) -> None:
        a = ModelRadarProfile(model_profile_id="coder")
        a.scores["coding"] = 0.95
        b = ModelRadarProfile(model_profile_id="writer")
        b.scores["coding"] = 0.2
        best = best_for_task([a, b], "coding")
        assert best is not None
        assert best.model_profile_id == "coder"

    def test_returns_none_for_empty_profiles(self) -> None:
        assert best_for_task([], "writing") is None

    def test_handles_tie_by_returning_first(self) -> None:
        a = ModelRadarProfile(model_profile_id="first")
        a.scores["math"] = 0.5
        b = ModelRadarProfile(model_profile_id="second")
        b.scores["math"] = 0.5
        best = best_for_task([a, b], "math")
        assert best is not None
        assert best.model_profile_id in ("first", "second")

    def test_invalid_category_raises(self) -> None:
        p = ModelRadarProfile(model_profile_id="test")
        with pytest.raises(ValueError, match="Unknown task category"):
            best_for_task([p], "baking")

    def test_category_case_insensitive(self) -> None:
        a = ModelRadarProfile(model_profile_id="stem-model")
        a.scores["stem"] = 0.88
        best = best_for_task([a], "STEM")
        assert best is not None
        assert best.model_profile_id == "stem-model"

    def test_all_zero_on_axis_still_returns_a_model(self) -> None:
        a = ModelRadarProfile(model_profile_id="a")
        b = ModelRadarProfile(model_profile_id="b")
        best = best_for_task([a, b], "roleplay")
        assert best is not None


# -- SVG dimension bounds -----------------------------------------------


class TestSvgDimensions:
    def test_svg_width_and_height_defaults(self) -> None:
        profile = ModelRadarProfile(model_profile_id="size-test")
        profile.scores["writing"] = 0.5
        svg = render_radar_svg(profile)
        root = ET.fromstring(svg)
        assert root.attrib["width"] == "400"
        assert root.attrib["height"] == "400"

    def test_svg_custom_dimensions(self) -> None:
        profile = ModelRadarProfile(model_profile_id="custom-size")
        profile.scores["writing"] = 0.5
        svg = render_radar_svg(profile, width=600, height=500)
        root = ET.fromstring(svg)
        assert root.attrib["width"] == "600"
        assert root.attrib["height"] == "500"
