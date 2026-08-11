"""Deep tests for db/__init__.py — every __all__ export verified importable."""

from __future__ import annotations

import importlib
import inspect

import pytest

import general_ludd.db as db_module
from general_ludd.db import __all__ as db_all


class TestAllExportsComplete:
    """Every name in __all__ must be importable from general_ludd.db."""

    def test_all_not_empty(self) -> None:
        assert len(db_all) > 0

    def test_no_duplicates_in_all(self) -> None:
        seen: set[str] = set()
        dupes: list[str] = []
        for name in db_all:
            if name in seen:
                dupes.append(name)
            seen.add(name)
        assert not dupes, f"Duplicate exports in __all__: {dupes}"

    def test_all_sorted(self) -> None:
        assert db_all == tuple(sorted(db_all)), "__all__ must be alphabetically sorted"

    @pytest.mark.parametrize("name", db_all)
    def test_every_export_importable(self, name: str) -> None:
        obj = getattr(db_module, name, _MISSING := object())
        assert obj is not _MISSING, f"__all__ entry {name!r} not importable from general_ludd.db"


class TestErrorExports:
    """Error classes exported from __init__ must be proper Exception subclasses."""

    EXPECTED_ERRORS: frozenset[str] = frozenset(
        {
            "DeploymentBusyError",
            "ImmutableAzureCostIdentityError",
            "StaleAzureCostLeaseError",
            "NonMonotonicAzureCostStateError",
            "ConcurrencyError",
            "InvalidTransitionError",
        }
    )

    @pytest.mark.parametrize("name", sorted(EXPECTED_ERRORS))
    def test_error_is_exception_subclass(self, name: str) -> None:
        obj = getattr(db_module, name)
        assert issubclass(obj, Exception), f"{name} must subclass Exception"

    def test_invalid_transition_is_concurrency_error(self) -> None:
        assert issubclass(
            db_module.InvalidTransitionError,
            db_module.ConcurrencyError,
        )

    @pytest.mark.parametrize("name", sorted(EXPECTED_ERRORS))
    def test_error_is_in_all(self, name: str) -> None:
        assert name in db_all, f"Error {name} exported from __init__.py but missing from __all__"


class TestRepositoryExports:
    """Repository classes re-exported from __init__."""

    REPOS: frozenset[str] = frozenset(
        {
            "AuditEventRepository",
            "BenchmarkRepository",
            "DeploymentRegistryRepository",
            "AzureCostReconciliationRepository",
            "ModelPerformanceRepository",
            "ProjectRepository",
            "PromptProfileRepository",
            "QueueRepository",
            "TaskReturnRepository",
            "TodoRepository",
            "VariableNamespaceRepository",
        }
    )

    @pytest.mark.parametrize("name", sorted(REPOS))
    def test_repo_class_exists_and_is_in_all(self, name: str) -> None:
        obj = getattr(db_module, name, None)
        assert obj is not None, f"Repository class {name} not importable"
        assert inspect.isclass(obj), f"{name} must be a class"
        assert name in db_all, f"Repository {name} missing from __all__"


class TestModelExports:
    """Model classes re-exported from __init__."""

    MODELS: frozenset[str] = frozenset(
        {
            "AuditEventModel",
            "BenchmarkResultModel",
            "BucketLeaseModel",
            "DeploymentRecordModel",
            "AzureCostObservationModel",
            "AzureCostOutboxEventModel",
            "AzureCostPredictionModel",
            "ModelCallLogModel",
            "ModelPerformanceModel",
            "ProjectModel",
            "PromptProfileModel",
            "QueueModel",
            "TaskDecisionModel",
            "TaskReturnModel",
            "TodoEventModel",
            "TodoModel",
            "VariableNamespaceModel",
            "VariableValueModel",
        }
    )

    @pytest.mark.parametrize("name", sorted(MODELS))
    def test_model_class_exists_and_is_in_all(self, name: str) -> None:
        obj = getattr(db_module, name, None)
        assert obj is not None, f"Model class {name} not importable"
        assert inspect.isclass(obj), f"{name} must be a class"
        assert name in db_all, f"Model {name} missing from __all__"


class TestSessionFunctionExports:
    """Session-management callables re-exported from __init__."""

    FUNCTIONS: frozenset[str] = frozenset(
        {
            "create_async_session_factory",
            "ensure_tables",
            "get_async_session",
            "get_default_db_url",
            "init_async_engine",
            "init_engine_from_config",
            "is_sqlite_url",
            "json_dumps",
            "run_wal_pragmas",
            "seed_initial_queues",
        }
    )

    @pytest.mark.parametrize("name", sorted(FUNCTIONS))
    def test_function_is_callable_and_in_all(self, name: str) -> None:
        obj = getattr(db_module, name, None)
        assert obj is not None, f"Session function {name} not importable"
        assert callable(obj), f"{name} must be callable"
        assert name in db_all, f"Session function {name} missing from __all__"


class TestTenantFunctionExports:
    """Tenant-scoping callables re-exported from __init__."""

    FUNCTIONS: frozenset[str] = frozenset({"get_tenant", "reset_tenant", "set_tenant"})

    @pytest.mark.parametrize("name", sorted(FUNCTIONS))
    def test_tenant_function_is_callable_and_in_all(self, name: str) -> None:
        obj = getattr(db_module, name, None)
        assert obj is not None, f"Tenant function {name} not importable"
        assert callable(obj), f"{name} must be callable"
        assert name in db_all, f"Tenant function {name} missing from __all__"


class TestMigrationExports:
    """Alembic helpers re-exported from __init__."""

    def test_stamp_head_callable_and_in_all(self) -> None:
        assert callable(db_module.stamp_head)
        assert "stamp_head" in db_all

    def test_get_alembic_config_callable_and_in_all(self) -> None:
        assert callable(db_module.get_alembic_config)
        assert "get_alembic_config" in db_all


class TestAllCoverageComplete:
    """Every __all__ name must be verified by at least one test class above."""

    KNOWN_FALSE_POSITIVES: frozenset[str] = frozenset()

    def _collect_verified(self) -> set[str]:
        verified: set[str] = set()
        for cls in [
            TestErrorExports,
            TestRepositoryExports,
            TestModelExports,
            TestSessionFunctionExports,
            TestTenantFunctionExports,
        ]:
            for attr_name in dir(cls):
                attr = getattr(cls, attr_name)
                if isinstance(attr, frozenset):
                    verified |= set(attr)
        verified.add("stamp_head")
        verified.add("get_alembic_config")
        verified.add("AuditEventType")
        verified.add("Base")
        verified.add("AzureCostLeaseClaim")
        return verified

    def test_all_entries_checked(self) -> None:
        verified = self._collect_verified()
        all_set = set(db_all)
        unchecked = all_set - verified - self.KNOWN_FALSE_POSITIVES
        assert not unchecked, (
            f"__all__ entries not verified by any test: {sorted(unchecked)}. "
            "Add to a frozenset in the appropriate test class."
        )


class TestSubmoduleImportConsistency:
    """Verify the three main submodule sources have the expected imports."""

    def test_azure_cost_submodule_only_exports_expected(self) -> None:
        import general_ludd.db.azure_cost_repository as acr

        expected = {
            "AzureCostReconciliationRepository",
            "AzureCostLeaseClaim",
            "ImmutableAzureCostIdentityError",
            "NonMonotonicAzureCostStateError",
            "StaleAzureCostLeaseError",
        }
        actual = set(getattr(acr, "__all__", []))
        assert actual == expected, f"azure_cost_repository __all__ mismatch: expected {expected}, got {actual}"

    def test_deployment_submodule_expected_names_importable(self) -> None:
        import general_ludd.db.deployment_repository as dr

        for name in ("DeploymentRegistryRepository", "DeploymentBusyError"):
            assert hasattr(dr, name), f"Expected {name} importable from deployment_repository"

    def test_tenant_submodule_only_exports_expected(self) -> None:
        import general_ludd.db.tenant as t

        expected = {"get_tenant", "reset_tenant", "set_tenant"}
        actual = set(getattr(t, "__all__", set(())))
        if actual:
            assert actual == expected, f"tenant __all__ mismatch: expected {expected}, got {actual}"


class TestImportSideEffectSafety:
    """Verify __init__.py imports don't cause harmful side effects on load."""

    def test_module_imports_repeatably(self) -> None:
        mod1 = importlib.import_module("general_ludd.db")
        mod2 = importlib.import_module("general_ludd.db")
        assert mod1 is mod2

    def test_all_importable_without_circular_errors(self) -> None:
        importlib.reload(db_module)
