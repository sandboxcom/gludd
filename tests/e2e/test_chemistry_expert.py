"""E2E tests for the general_ludd.chemistry expert collection.

Verifies the public API surface end-to-end: package imports, ChemistryRequest
construction, routing through route_chemistry_task / ChemistryExpertAPI, and
the ChemistryResult contract shape.
"""

from __future__ import annotations

import pytest


class TestChemistryPackageImports:
    """Verify the chemistry package imports cleanly and exposes its API."""

    def test_package_imports_cleanly(self) -> None:
        import general_ludd.chemistry as chemistry

        assert chemistry is not None

    def test_package_exports_request_and_result(self) -> None:
        from general_ludd.chemistry import ChemistryRequest, ChemistryResult

        assert ChemistryRequest is not None
        assert ChemistryResult is not None

    def test_package_exports_router(self) -> None:
        from general_ludd.chemistry import route_chemistry_task

        assert callable(route_chemistry_task)


class TestChemistryRequestConstruction:
    """Verify ChemistryRequest builds and enforces its invariants."""

    def test_minimal_request_constructs(self) -> None:
        from general_ludd.chemistry import ChemistryRequest, TaskKind

        req = ChemistryRequest(
            request_id="req-001",
            tenant_id="tenant-a",
            task=TaskKind.identity,
            entities=["water"],
        )
        assert req.request_id == "req-001"
        assert req.task == TaskKind.identity
        assert req.entities == ["water"]

    def test_request_rejects_empty_entities(self) -> None:
        from general_ludd.chemistry import ChemistryRequest, TaskKind
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ChemistryRequest(
                request_id="req-002",
                tenant_id="tenant-a",
                task=TaskKind.identity,
                entities=[],
            )

    def test_request_rejects_unknown_task(self) -> None:
        from general_ludd.chemistry import ChemistryRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ChemistryRequest(
                request_id="req-003",
                tenant_id="tenant-a",
                task="not_a_real_task",
                entities=["water"],
            )

    def test_constraints_default_to_internal(self) -> None:
        from general_ludd.chemistry import ChemistryRequest, DataClassification, TaskKind

        req = ChemistryRequest(
            request_id="req-004",
            tenant_id="tenant-a",
            task=TaskKind.identity,
            entities=["ethanol"],
        )
        assert req.constraints.data_classification == DataClassification.internal


class TestChemistryRouting:
    """Verify route_chemistry_task returns structured routing decisions."""

    def test_route_identity_task_succeeds(self) -> None:
        from general_ludd.chemistry import route_chemistry_task

        result = route_chemistry_task({"task": "identity", "entities": ["water"]})
        assert result["status"] == "succeeded"
        assert result["capability"] is not None
        assert "risk_tier" in result
        assert "requires_hazard_review" in result
        assert isinstance(result["errors"], list)

    def test_route_unknown_task_refused(self) -> None:
        from general_ludd.chemistry import route_chemistry_task

        result = route_chemistry_task({"task": "unknown_task", "entities": []})
        assert result["status"] == "refused"
        assert result["capability"] is None
        assert len(result["errors"]) > 0

    def test_route_missing_task_refused(self) -> None:
        from general_ludd.chemistry import route_chemistry_task

        result = route_chemistry_task({})
        assert result["status"] == "refused"

    def test_route_hazardous_entity_triggers_review(self) -> None:
        from general_ludd.chemistry import route_chemistry_task

        result = route_chemistry_task({"task": "protocol", "entities": ["hydrofluoric acid"]})
        assert result["requires_hazard_review"] is True


class TestChemistryResultContract:
    """Verify ChemistryResult carries values, citations, and safety records."""

    def test_result_has_required_structure(self) -> None:
        from general_ludd.chemistry import ChemistryResult, ResultStatus

        result = ChemistryResult(
            request_id="req-001",
            run_id="run-001",
            status=ResultStatus.succeeded,
        )
        assert result.request_id == "req-001"
        assert result.run_id == "run-001"
        assert result.status == ResultStatus.succeeded
        assert isinstance(result.values, list)
        assert isinstance(result.citations, list)
        assert isinstance(result.errors, list)
        assert isinstance(result.limitations, list)
        assert result.safety is not None
