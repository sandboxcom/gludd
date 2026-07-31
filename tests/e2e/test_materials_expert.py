"""E2E tests for the general_ludd.materials expert collection.

Verifies the public API surface end-to-end: package imports, DesignRequirements
construction, material selection screening/ranking, and the EngineeringVerdict
contract shape (margins, uncertainty, sources).
"""

from __future__ import annotations

import pytest


class TestMaterialsPackageImports:
    """Verify the materials package imports cleanly and exposes its API."""

    def test_package_imports_cleanly(self) -> None:
        import general_ludd.materials as materials

        assert materials is not None

    def test_package_exports_design_requirements(self) -> None:
        from general_ludd.materials import DesignRequirements

        assert DesignRequirements is not None

    def test_package_exports_selection_functions(self) -> None:
        from general_ludd.materials import rank_candidates, screen_candidates, select_materials

        assert callable(screen_candidates)
        assert callable(rank_candidates)
        assert callable(select_materials)


class TestDesignRequirementsConstruction:
    """Verify DesignRequirements builds and enforces its invariants."""

    def test_minimal_requirements_construct(self) -> None:
        from general_ludd.materials import DesignRequirements

        req = DesignRequirements(failure_consequence="noncritical")
        assert req.failure_consequence == "noncritical"
        assert req.requires_human_review is False

    def test_safety_critical_auto_sets_human_review(self) -> None:
        from general_ludd.materials import DesignRequirements

        req = DesignRequirements(failure_consequence="safety_critical")
        assert req.requires_human_review is True

    def test_invalid_failure_consequence_rejected(self) -> None:
        from pydantic import ValidationError

        from general_ludd.materials import DesignRequirements

        with pytest.raises(ValidationError):
            DesignRequirements(failure_consequence="not_a_real_consequence")

    def test_schema_version_present(self) -> None:
        from general_ludd.materials import DesignRequirements

        req = DesignRequirements(failure_consequence="unknown")
        assert req.schema_version
        assert isinstance(req.schema_version, str)


class TestMaterialSelectionPipeline:
    """Verify the screening and ranking pipeline returns structured output."""

    def test_select_materials_returns_dict(self) -> None:
        from general_ludd.materials import select_materials

        reqs = {
            "failure_consequence": "noncritical",
            "load_cases": [
                {"id": "lc1", "type": "tensile", "magnitude": 200.0, "unit": "MPa"},
            ],
        }
        result = select_materials(reqs)
        assert isinstance(result, dict)
        assert "verdict" in result

    def test_screen_candidates_returns_dict(self) -> None:
        from general_ludd.materials import screen_candidates

        reqs = {
            "failure_consequence": "noncritical",
            "load_cases": [
                {"id": "lc1", "type": "tensile", "magnitude": 200.0, "unit": "MPa"},
            ],
        }
        result = screen_candidates(reqs)
        assert isinstance(result, dict)

    def test_rank_candidates_returns_traces(self) -> None:
        from general_ludd.materials import rank_candidates

        reqs = {
            "failure_consequence": "noncritical",
            "load_cases": [
                {"id": "lc1", "type": "tensile", "magnitude": 200.0, "unit": "MPa"},
            ],
        }
        ranked = rank_candidates(reqs)
        assert isinstance(ranked, dict)
        assert "nominal" in ranked
        assert "conservative" in ranked
        assert "sensitivity" in ranked


class TestMaterialCandidateContract:
    """Verify MaterialCandidate carries margins, uncertainty, and sources."""

    def test_material_candidate_has_required_fields(self) -> None:
        from general_ludd.materials import MaterialCandidate
        from general_ludd.materials.contracts import (
            MaterialCondition,
            MaterialProperty,
            MaterialSource,
        )

        candidate = MaterialCandidate(
            material_id="AISI-1020",
            condition=MaterialCondition(),
            properties=[
                MaterialProperty(
                    name="yield_strength",
                    value_or_range=295.0,
                    unit="MPa",
                    basis="handbook",
                    method="ASTM-E8",
                    uncertainty=15.0,
                ),
            ],
            source=MaterialSource(publisher="ASM Handbook"),
            confidence=60,
        )
        assert candidate.material_id == "AISI-1020"
        assert candidate.properties[0].uncertainty == 15.0
        assert candidate.properties[0].unit == "MPa"
        assert candidate.source.publisher == "ASM Handbook"
        assert candidate.requirement_margins == []

    def test_material_property_rejects_negative_uncertainty(self) -> None:
        from pydantic import ValidationError

        from general_ludd.materials.contracts import MaterialProperty

        with pytest.raises(ValidationError):
            MaterialProperty(
                name="yield_strength",
                value_or_range=295.0,
                unit="MPa",
                basis="handbook",
                method="ASTM-E8",
                uncertainty=-5.0,
            )
