"""Deep tests for radar_profile — edge cases, build_profile, cost delegation, SVG structure."""

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
    _AXIS_LABELS,
    _MT_BENCH_AXES,
    _TASK_TO_AXIS,
    ModelRadarProfile,
    RadarProfile,
    _map_task_kind_to_axis,
    _ScoresDict,
    best_for_task,
    build_profile,
    compare_models,
    generate_radar,
    render_radar_svg,
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _identity(model_profile_id: str = "deep-model") -> ModelIdentity:
    return ModelIdentity(
        model_profile_id=model_profile_id,
        model_artifact_digest=_digest("weights-v1"),
        runtime_config_digest=_digest("runtime:v1"),
        prompt_contract_digest=_digest("prompt:v1"),
    )


def _evidence(
    model_profile_id: str = "deep-model",
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


class TestMapTaskKindToAxis:
    def test_known_task_kind_direct_map(self) -> None:
        assert _map_task_kind_to_axis("context_compaction") == "extraction"
        assert _map_task_kind_to_axis("format_normalization") == "extraction"
        assert _map_task_kind_to_axis("schema_extraction") == "extraction"
        assert _map_task_kind_to_axis("documentation_draft") == "writing"
        assert _map_task_kind_to_axis("bounded_enumeration") == "reasoning"
        assert _map_task_kind_to_axis("failure_classification") == "reasoning"
        assert _map_task_kind_to_axis("_cost_awareness") == "cost"

    def test_known_axis_name_returns_self(self) -> None:
        for axis in ("coding", "math", "reasoning", "stem", "humanities", "writing", "roleplay"):
            assert _map_task_kind_to_axis(axis) == axis

    def test_unknown_task_returns_none(self) -> None:
        assert _map_task_kind_to_axis("completely_unknown") is None
        assert _map_task_kind_to_axis("zzz_nonexistent_zzz") is None

    def test_substring_match_finds_axis(self) -> None:
        assert _map_task_kind_to_axis("advanced_math_problem") == "math"
        assert _map_task_kind_to_axis("creative_writing_task") == "writing"
        assert _map_task_kind_to_axis("coding_interview_prompt") == "coding"

    def test_substring_match_first_match_wins(self) -> None:
        result = _map_task_kind_to_axis("stem_math_hybrid")
        assert result is not None
        assert result in _MT_BENCH_AXES

    def test_empty_string_returns_none(self) -> None:
        assert _map_task_kind_to_axis("") is None

    def test_none_input_returns_none(self) -> None:
        assert _map_task_kind_to_axis(None) is None

    def test_non_string_input_returns_none(self) -> None:
        assert _map_task_kind_to_axis(123) is None
        assert _map_task_kind_to_axis([]) is None

    def test_all_entries_in_task_to_axis_map_to_valid_axes(self) -> None:
        for task_kind, axis in _TASK_TO_AXIS.items():
            assert axis in _MT_BENCH_AXES, f"{task_kind} -> {axis} not in _MT_BENCH_AXES"


class TestScoresDict:
    def test_clamps_values_to_unit_interval(self) -> None:
        d = _ScoresDict({axis: 0.0 for axis in _MT_BENCH_AXES})
        d["writing"] = 1.5
        assert d["writing"] == 1.0
        d["math"] = -3.0
        assert d["math"] == 0.0

    def test_rejects_non_axis_keys(self) -> None:
        d = _ScoresDict({axis: 0.0 for axis in _MT_BENCH_AXES})
        with pytest.raises(KeyError):
            d["nonexistent"] = 0.5

    def test_cost_delegates_to_parent_cost_score(self) -> None:
        profile = ModelRadarProfile(model_profile_id="cost-delegation", cost_score=0.42)
        assert profile.scores["cost"] == 0.42
        profile.scores["cost"] = 0.88
        assert profile.cost_score == 0.88
        assert profile.scores["cost"] == 0.88

    def test_cost_clamped_on_parent_side(self) -> None:
        profile = ModelRadarProfile(model_profile_id="cost-clamp", cost_score=2.0)
        assert profile.cost_score == 1.0
        assert profile.scores["cost"] == 1.0
        profile.scores["cost"] = 1.8
        assert profile.cost_score == 1.0
        assert profile.scores["cost"] == 1.0

    def test_get_method_delegates_cost(self) -> None:
        profile = ModelRadarProfile(model_profile_id="cost-get", cost_score=0.33)
        assert profile.scores.get("cost") == 0.33
        assert profile.scores.get("cost", 0.0) == 0.33
        assert profile.scores.get("writing", -1.0) == 0.0

    def test_no_parent_stores_cost_in_dict(self) -> None:
        d = _ScoresDict({axis: 0.0 for axis in _MT_BENCH_AXES})
        d["cost"] = 0.5
        assert d._parent is None
        assert d["cost"] == 0.5


class TestModelRadarProfileDeep:
    def test_vector_returns_list_in_axis_order(self) -> None:
        profile = ModelRadarProfile(model_profile_id="vec-test")
        profile.scores["writing"] = 0.1
        profile.scores["roleplay"] = 0.2
        profile.scores["extraction"] = 0.3
        profile.scores["reasoning"] = 0.4
        profile.scores["math"] = 0.5
        profile.scores["coding"] = 0.6
        profile.scores["stem"] = 0.7
        profile.scores["humanities"] = 0.8
        profile.cost_score = 0.9
        v = profile.vector()
        assert v == [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]

    def test_vector_all_zeros(self) -> None:
        profile = ModelRadarProfile(model_profile_id="zero-vec")
        v = profile.vector()
        assert len(v) == 9
        assert all(x == 0.0 for x in v)

    def test_scores_setter_full_dict(self) -> None:
        profile = ModelRadarProfile(model_profile_id="setter-test")
        profile.scores = {
            "writing": 0.5,
            "roleplay": 0.6,
            "extraction": 0.7,
            "reasoning": 0.8,
            "math": 0.9,
            "coding": 1.0,
            "stem": 0.0,
            "humanities": 0.0,
            "cost": 0.75,
        }
        assert profile.scores["writing"] == 0.5
        assert profile.scores["coding"] == 1.0
        assert profile.cost_score == 0.75
        assert profile.scores["cost"] == 0.75

    def test_scores_setter_clamps_values(self) -> None:
        profile = ModelRadarProfile(model_profile_id="setter-clamp")
        profile.scores = {
            "writing": 2.0,
            "roleplay": -1.0,
            "extraction": 1.5,
            "reasoning": 0.5,
            "math": 0.3,
            "coding": 0.0,
            "stem": 0.0,
            "humanities": 0.0,
            "cost": 3.0,
        }
        assert profile.scores["writing"] == 1.0
        assert profile.scores["roleplay"] == 0.0
        assert profile.scores["extraction"] == 1.0
        assert profile.cost_score == 1.0

    def test_scores_setter_missing_keys_default_to_zero(self) -> None:
        profile = ModelRadarProfile(model_profile_id="partial")
        profile.scores = {"writing": 0.9, "coding": 0.8}
        assert profile.scores["writing"] == 0.9
        assert profile.scores["coding"] == 0.8
        assert profile.scores["roleplay"] == 0.0
        assert profile.scores["math"] == 0.0

    def test_normalized_includes_cost(self) -> None:
        profile = ModelRadarProfile(model_profile_id="norm-cost", cost_score=0.42)
        norm = profile.normalized()
        assert norm["cost"] == 0.42
        assert len(norm) == 9

    def test_score_cost_delegation_getitem(self) -> None:
        profile = ModelRadarProfile(model_profile_id="delegate", cost_score=0.77)
        assert profile.scores["cost"] == 0.77
        profile.cost_score = 0.33
        assert profile.scores["cost"] == 0.33

    def test_whitespace_only_id_rejected(self) -> None:
        with pytest.raises(ValueError):
            ModelRadarProfile(model_profile_id="   ")

    def test_cost_score_field_unchanged_when_cost_not_in_setter(self) -> None:
        profile = ModelRadarProfile(model_profile_id="keep-cost", cost_score=0.42)
        profile.scores = {"writing": 0.5, "coding": 0.7}
        assert profile.cost_score == 0.42

    def test_radar_profile_alias_is_same_class(self) -> None:
        assert RadarProfile is ModelRadarProfile
        p = RadarProfile(model_profile_id="alias-test")
        assert isinstance(p, ModelRadarProfile)
        assert p.model_profile_id == "alias-test"


class TestGenerateRadarDeep:
    def test_cost_axis_included_in_result(self) -> None:
        evs = [_evidence(task_kind="coding", passed_cases=100, total_cases=100)]
        profile = generate_radar(evs)
        assert "cost" in profile.scores
        assert 0.0 <= profile.scores["cost"] <= 1.0

    def test_generate_radar_clamps_result_within_0_1(self) -> None:
        evs = [_evidence(task_kind="coding", passed_cases=100, total_cases=100)]
        profile = generate_radar(evs)
        assert profile.scores["coding"] == 1.0
        assert profile.scores["math"] == 0.0

    def test_zero_passed_all_okay_ratio_is_zero(self) -> None:
        evs = [_evidence(task_kind="coding", passed_cases=0, total_cases=100)]
        profile = generate_radar(evs)
        assert profile.scores["coding"] == 0.0

    def test_substring_matched_task_kind(self) -> None:
        evs = [_evidence(task_kind="solve_math_equations", passed_cases=80, total_cases=100)]
        profile = generate_radar(evs)
        assert profile.scores["math"] == 0.8

    def test_multiple_evidence_same_axis_mean(self) -> None:
        evs = [
            _evidence(task_kind="coding", passed_cases=90, total_cases=100),
            _evidence(task_kind="coding", passed_cases=70, total_cases=100),
        ]
        profile = generate_radar(evs)
        assert profile.scores["coding"] == 0.8

    def test_mixed_known_and_substring_evidence(self) -> None:
        evs = [
            _evidence(task_kind="coding", passed_cases=100, total_cases=100),
            _evidence(task_kind="fix_bug_coding", passed_cases=50, total_cases=100),
        ]
        profile = generate_radar(evs)
        assert profile.scores["coding"] == 0.75

    def test_all_axes_zero_when_all_unknown(self) -> None:
        evs = [
            _evidence(task_kind="unknown_a", passed_cases=80, total_cases=100),
            _evidence(task_kind="unknown_b", passed_cases=90, total_cases=100),
        ]
        profile = generate_radar(evs)
        assert all(v == 0.0 for v in profile.scores.values())

    def test_model_id_matches_first_evidence(self) -> None:
        evs = [
            _evidence(model_profile_id="model-x", task_kind="coding", passed_cases=80, total_cases=100),
            _evidence(model_profile_id="model-y", task_kind="math", passed_cases=90, total_cases=100),
        ]
        profile = generate_radar(evs)
        assert profile.model_profile_id == "model-x"


class TestBuildProfile:
    def test_empty_evidence_list_returns_zero_profile(self) -> None:
        profile = build_profile("empty-model", [])
        assert profile.model_profile_id == "empty-model"
        assert all(v == 0.0 for v in profile.scores.values())

    def test_single_record_maps_to_axis(self) -> None:
        profile = build_profile(
            "single",
            [
                {"task_kind": "coding", "passed_cases": 80, "total_cases": 100},
            ],
        )
        assert profile.scores["coding"] == pytest.approx(0.8)

    def test_best_pass_rate_selected_per_axis(self) -> None:
        profile = build_profile(
            "best-rate",
            [
                {"task_kind": "coding", "passed_cases": 60, "total_cases": 100},
                {"task_kind": "coding", "passed_cases": 95, "total_cases": 100},
                {"task_kind": "coding", "passed_cases": 40, "total_cases": 100},
            ],
        )
        assert profile.scores["coding"] == 0.95

    def test_mixed_task_kinds_aggregate_across_axes(self) -> None:
        profile = build_profile(
            "mixed",
            [
                {"task_kind": "coding", "passed_cases": 90, "total_cases": 100},
                {"task_kind": "math", "passed_cases": 70, "total_cases": 100},
                {"task_kind": "writing", "passed_cases": 85, "total_cases": 100},
            ],
        )
        assert profile.scores["coding"] == 0.9
        assert profile.scores["math"] == 0.7
        assert profile.scores["writing"] == 0.85

    def test_unknown_task_kind_skipped(self) -> None:
        profile = build_profile(
            "skip-unknown",
            [
                {"task_kind": "nonexistent_task", "passed_cases": 80, "total_cases": 100},
            ],
        )
        assert all(v == 0.0 for v in profile.scores.values())

    def test_record_with_zero_total_defaults_to_zero(self) -> None:
        profile = build_profile(
            "zero-total",
            [
                {"task_kind": "coding", "passed_cases": 50, "total_cases": 0},
            ],
        )
        assert profile.scores["coding"] == 0.0

    def test_record_with_missing_total_defaults_to_zero(self) -> None:
        profile = build_profile(
            "missing-total",
            [
                {"task_kind": "coding", "passed_cases": 50},
            ],
        )
        assert profile.scores["coding"] == 0.0

    def test_record_with_missing_passed_defaults_to_zero(self) -> None:
        profile = build_profile(
            "missing-passed",
            [
                {"task_kind": "coding", "total_cases": 100},
            ],
        )
        assert profile.scores["coding"] == 0.0

    def test_record_with_missing_task_kind_skipped(self) -> None:
        profile = build_profile(
            "missing-kind",
            [
                {"passed_cases": 80, "total_cases": 100},
            ],
        )
        assert all(v == 0.0 for v in profile.scores.values())

    def test_string_numeric_values_parsed(self) -> None:
        profile = build_profile(
            "string-vals",
            [
                {"task_kind": "coding", "passed_cases": "80", "total_cases": "100"},
            ],
        )
        assert profile.scores["coding"] == pytest.approx(0.8)

    def test_best_takes_over_negative_initial(self) -> None:
        profile = build_profile(
            "negative-init",
            [
                {"task_kind": "coding", "passed_cases": 10, "total_cases": 100},
            ],
        )
        assert profile.scores["coding"] == 0.1

    def test_cost_axis_included(self) -> None:
        profile = build_profile(
            "cost-included",
            [
                {"task_kind": "coding", "passed_cases": 100, "total_cases": 100},
            ],
        )
        assert "cost" in profile.scores
        assert 0.0 <= profile.scores["cost"] <= 1.0

    def test_non_numeric_passed_cases_handled(self) -> None:
        profile = build_profile(
            "bad-passed",
            [
                {"task_kind": "coding", "passed_cases": object(), "total_cases": 100},
            ],
        )
        assert profile.scores["coding"] == 0.0

    def test_non_numeric_total_cases_handled(self) -> None:
        profile = build_profile(
            "bad-total",
            [
                {"task_kind": "coding", "passed_cases": 80, "total_cases": object()},
            ],
        )
        assert profile.scores["coding"] == 0.0

    def test_substring_matched_task_kind(self) -> None:
        profile = build_profile(
            "substr",
            [
                {"task_kind": "advanced_coding_challenge", "passed_cases": 70, "total_cases": 100},
            ],
        )
        assert profile.scores["coding"] == 0.7

    def test_multiple_axes_with_overlapping_task_kinds(self) -> None:
        profile = build_profile(
            "overlap",
            [
                {"task_kind": "coding", "passed_cases": 80, "total_cases": 100},
                {"task_kind": "coding", "passed_cases": 95, "total_cases": 100},
                {"task_kind": "math", "passed_cases": 60, "total_cases": 100},
                {"task_kind": "math", "passed_cases": 75, "total_cases": 100},
            ],
        )
        assert profile.scores["coding"] == 0.95
        assert profile.scores["math"] == 0.75


class TestCompareModelsDeep:
    def test_multi_model_ranking_correct(self) -> None:
        a = ModelRadarProfile(model_profile_id="low")
        b = ModelRadarProfile(model_profile_id="mid")
        c = ModelRadarProfile(model_profile_id="high")
        for ax in _MT_BENCH_AXES:
            if ax == "cost":
                a.cost_score = 0.1
                b.cost_score = 0.5
                c.cost_score = 0.9
            else:
                a.scores[ax] = 0.1
                b.scores[ax] = 0.5
                c.scores[ax] = 0.9
        result = compare_models([a, b, c])
        assert result["ranking"][0] == "high"
        assert result["ranking"][1] == "mid"
        assert result["ranking"][2] == "low"
        assert result["winner"] == "high"

    def test_mean_across_all_axes(self) -> None:
        a = ModelRadarProfile(model_profile_id="a")
        for ax in _MT_BENCH_AXES:
            if ax == "cost":
                a.cost_score = 0.5
            else:
                a.scores[ax] = 0.5
        result = compare_models([a])
        for ax in _MT_BENCH_AXES:
            assert result["mean"][ax] == pytest.approx(0.5)

    def test_profiles_key_contains_all_models(self) -> None:
        a = ModelRadarProfile(model_profile_id="one")
        b = ModelRadarProfile(model_profile_id="two")
        c = ModelRadarProfile(model_profile_id="three")
        result = compare_models([a, b, c])
        assert set(result["profiles"].keys()) == {"one", "two", "three"}

    def test_normalized_scores_used_in_comparison(self) -> None:
        a = ModelRadarProfile(model_profile_id="norm-compare", cost_score=0.42)
        result = compare_models([a])
        assert result["profiles"]["norm-compare"]["cost"] == 0.42

    def test_cost_included_in_mean(self) -> None:
        a = ModelRadarProfile(model_profile_id="cost-mean-a", cost_score=0.2)
        b = ModelRadarProfile(model_profile_id="cost-mean-b", cost_score=0.8)
        result = compare_models([a, b])
        assert result["mean"]["cost"] == pytest.approx(0.5)


class TestBestForTaskDeep:
    def test_cost_axis_works(self) -> None:
        a = ModelRadarProfile(model_profile_id="cheap", cost_score=0.9)
        b = ModelRadarProfile(model_profile_id="expensive", cost_score=0.1)
        best = best_for_task([a, b], "cost")
        assert best is not None
        assert best.model_profile_id == "cheap"

    def test_whitespace_in_category_trimmed(self) -> None:
        a = ModelRadarProfile(model_profile_id="trim-test")
        a.scores["coding"] = 0.99
        best = best_for_task([a], "  coding  ")
        assert best is not None
        assert best.model_profile_id == "trim-test"

    def test_non_lowercase_category_works(self) -> None:
        a = ModelRadarProfile(model_profile_id="mixed-case")
        a.scores["humanities"] = 0.77
        best = best_for_task([a], "Humanities")
        assert best is not None
        assert best.model_profile_id == "mixed-case"

    def test_returns_first_on_tie(self) -> None:
        a = ModelRadarProfile(model_profile_id="first")
        a.scores["coding"] = 0.5
        b = ModelRadarProfile(model_profile_id="second")
        b.scores["coding"] = 0.5
        best = best_for_task([a, b], "coding")
        assert best is not None
        assert best.model_profile_id == "first"

    def test_all_zero_scores_returns_first_profile(self) -> None:
        a = ModelRadarProfile(model_profile_id="a-zero")
        b = ModelRadarProfile(model_profile_id="b-zero")
        best = best_for_task([a, b], "writing")
        assert best is not None
        assert best.model_profile_id == "a-zero"

    def test_invalid_axis_raises_helpful_message(self) -> None:
        p = ModelRadarProfile(model_profile_id="err")
        with pytest.raises(ValueError) as ctx:
            best_for_task([p], "cooking")
        assert "cooking" in str(ctx.value)
        assert "Must be one of" in str(ctx.value)


class TestRenderRadarSvgDeep:
    def test_svg_contains_nine_axis_label_texts(self) -> None:
        profile = ModelRadarProfile(model_profile_id="nine-axes")
        for axis in _MT_BENCH_AXES:
            if axis == "cost":
                profile.cost_score = 0.5
            else:
                profile.scores[axis] = 0.5
        svg = render_radar_svg(profile)
        root = ET.fromstring(svg)
        ns = {"svg": "http://www.w3.org/2000/svg"}
        texts = root.findall(".//svg:text", ns)
        labels = {t.text.split(" (")[0] if t.text else "" for t in texts if t.text}
        for label_name in _AXIS_LABELS.values():
            assert label_name in labels, f"Missing label: {label_name}"

    def test_svg_contains_grid_polygons(self) -> None:
        profile = ModelRadarProfile(model_profile_id="grid")
        profile.scores["writing"] = 0.5
        svg = render_radar_svg(profile)
        root = ET.fromstring(svg)
        ns = {"svg": "http://www.w3.org/2000/svg"}
        polygons = root.findall(".//svg:polygon", ns)
        assert len(polygons) >= 3

    def test_svg_contains_data_dots(self) -> None:
        profile = ModelRadarProfile(model_profile_id="dots")
        for axis in _MT_BENCH_AXES:
            if axis == "cost":
                profile.cost_score = 0.5
            else:
                profile.scores[axis] = 0.5
        svg = render_radar_svg(profile)
        root = ET.fromstring(svg)
        ns = {"svg": "http://www.w3.org/2000/svg"}
        circles = root.findall(".//svg:circle", ns)
        assert len(circles) == 9

    def test_svg_contains_radial_axis_lines(self) -> None:
        profile = ModelRadarProfile(model_profile_id="lines")
        profile.scores["writing"] = 0.5
        svg = render_radar_svg(profile)
        root = ET.fromstring(svg)
        ns = {"svg": "http://www.w3.org/2000/svg"}
        lines = root.findall(".//svg:line", ns)
        assert len(lines) >= 8

    def test_svg_contains_background_rect(self) -> None:
        profile = ModelRadarProfile(model_profile_id="bg")
        profile.scores["writing"] = 0.5
        svg = render_radar_svg(profile)
        root = ET.fromstring(svg)
        ns = {"svg": "http://www.w3.org/2000/svg"}
        rects = root.findall(".//svg:rect", ns)
        assert len(rects) >= 1

    def test_svg_contains_data_polygon(self) -> None:
        profile = ModelRadarProfile(model_profile_id="datapoly")
        for axis in _MT_BENCH_AXES:
            if axis == "cost":
                profile.cost_score = 0.6
            else:
                profile.scores[axis] = 0.6
        svg = render_radar_svg(profile)
        root = ET.fromstring(svg)
        ns = {"svg": "http://www.w3.org/2000/svg"}
        polygons = root.findall(".//svg:polygon", ns)
        colored = [p for p in polygons if p.attrib.get("fill", "") == "#e94560"]
        assert len(colored) == 1

    def test_svg_with_zero_scores_still_valid(self) -> None:
        profile = ModelRadarProfile(model_profile_id="nil")
        svg = render_radar_svg(profile)
        root = ET.fromstring(svg)
        assert root is not None
        assert "</svg>" in svg

    def test_all_score_labels_contain_numbers(self) -> None:
        profile = ModelRadarProfile(model_profile_id="scored")
        for axis in _MT_BENCH_AXES:
            if axis == "cost":
                profile.cost_score = 0.42
            else:
                profile.scores[axis] = 0.42
        svg = render_radar_svg(profile)
        assert "0.42" in svg
