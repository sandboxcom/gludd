"""Behavioral tests for the textile and flexible-material advisor."""

from __future__ import annotations

import pytest

from general_ludd.materials.textiles import (
    KNIT_TYPES,
    TEXTILE_FIBERS,
    WEAVE_TYPES,
    TextileAdvisor,
)


@pytest.fixture
def advisor() -> TextileAdvisor:
    return TextileAdvisor()


def test_catalogs_expose_supported_fibers_and_architectures() -> None:
    assert {"cotton", "aramid", "carbon"} <= set(TEXTILE_FIBERS)
    assert WEAVE_TYPES == ("plain", "twill", "satin")
    assert KNIT_TYPES == ("weft_knit", "warp_knit")


@pytest.mark.parametrize("fiber", TEXTILE_FIBERS)
def test_fiber_properties_are_traceable_positive_estimates(
    advisor: TextileAdvisor,
    fiber: str,
) -> None:
    result = advisor.fiber_properties(fiber.upper())

    assert result["fiber"] == fiber
    assert result["tenacity_cntex"] > 0
    assert result["density_g_cm3"] > 0
    assert result["moisture_regain_pct"] >= 0
    assert "verify" in result["basis"]


def test_unknown_fiber_fails_with_insufficient_data(advisor: TextileAdvisor) -> None:
    result = advisor.fiber_properties("unobtainium")

    assert result == {
        "fiber": "unobtainium",
        "state": "insufficient_data",
        "reason": "unknown fiber: unobtainium",
    }


def test_yarn_load_applies_declared_efficiency(advisor: TextileAdvisor) -> None:
    result = advisor.yarn_linear_density("aramid", 100.0)

    assert result["theoretical_breaking_load_N"] == pytest.approx(200.0)
    assert result["estimated_breaking_load_N"] == pytest.approx(150.0)
    assert result["yarn_efficiency"] == 0.75
    assert "handbook" in result["basis"]


def test_yarn_load_propagates_unknown_fiber(advisor: TextileAdvisor) -> None:
    assert advisor.yarn_linear_density("unknown", 10.0)["state"] == "insufficient_data"


@pytest.mark.parametrize(
    ("weave", "ratio", "drape", "crimp"),
    [
        ("plain", 1.0, 0.4, 10.0),
        ("twill", 1.1, 0.7, 5.0),
        ("satin", 1.25, 0.9, 3.0),
    ],
)
def test_weave_properties_preserve_directional_tradeoffs(
    advisor: TextileAdvisor,
    weave: str,
    ratio: float,
    drape: float,
    crimp: float,
) -> None:
    result = advisor.weave_properties(weave)

    assert result["directional_strength_ratio_warp_to_weft"] == ratio
    assert result["drape_score"] == drape
    assert result["crimp_pct"] == crimp
    assert result["basis"]


def test_unknown_weave_fails_closed(advisor: TextileAdvisor) -> None:
    result = advisor.weave_properties("basket")

    assert result["state"] == "insufficient_data"
    assert "recognized" in result["reason"]


@pytest.mark.parametrize(
    ("knit", "elongation", "recovery", "run_resistant"),
    [
        ("weft_knit", 100.0, 90.0, True),
        ("warp_knit", 50.0, 95.0, False),
    ],
)
def test_knit_classification_returns_architecture_properties(
    advisor: TextileAdvisor,
    knit: str,
    elongation: float,
    recovery: float,
    run_resistant: bool,
) -> None:
    result = advisor.classify_knit(knit.upper())

    assert result["knit"] == knit
    assert result["elongation_pct"] == elongation
    assert result["recovery_pct"] == recovery
    assert result["run_resistant"] is run_resistant


def test_unknown_knit_fails_closed(advisor: TextileAdvisor) -> None:
    assert advisor.classify_knit("crochet")["state"] == "insufficient_data"


@pytest.mark.parametrize(
    ("seam", "efficiency", "interpretation"),
    [
        ("overlock", 0.60, "seam fails before fabric"),
        ("felled", 0.85, "seam near fabric strength"),
    ],
)
def test_seam_efficiency_explains_failure_order(
    advisor: TextileAdvisor,
    seam: str,
    efficiency: float,
    interpretation: str,
) -> None:
    result = advisor.seam_efficiency(seam)

    assert result["seam_efficiency"] == efficiency
    assert result["interpretation"] == interpretation
    assert result["basis"]


def test_unknown_seam_fails_closed(advisor: TextileAdvisor) -> None:
    assert advisor.seam_efficiency("glued")["state"] == "insufficient_data"


@pytest.mark.parametrize(
    ("weave", "rating"),
    [("plain", "moderate"), ("twill", "good"), ("satin", "excellent")],
)
def test_drape_rating_tracks_weave_score(
    advisor: TextileAdvisor,
    weave: str,
    rating: str,
) -> None:
    result = advisor.assess_drape(weave)

    assert result["drape_rating"] == rating
    assert weave in result["reason"]


def test_drape_propagates_unknown_weave(advisor: TextileAdvisor) -> None:
    assert advisor.assess_drape("unknown")["state"] == "insufficient_data"


def test_directional_strength_marks_balanced_and_biased_weaves(
    advisor: TextileAdvisor,
) -> None:
    plain = advisor.directional_strength_ratio("plain")
    satin = advisor.directional_strength_ratio("satin")

    assert plain["balanced"] is True
    assert plain["warp_to_weft_ratio"] == 1.0
    assert satin["balanced"] is False
    assert satin["warp_to_weft_ratio"] == 1.25
    assert "warp" in satin["reason"]


def test_directional_strength_propagates_unknown_weave(advisor: TextileAdvisor) -> None:
    assert advisor.directional_strength_ratio("unknown")["state"] == "insufficient_data"
