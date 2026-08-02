"""E2E tests for the general_ludd.governance Ansible collection.

Verifies that the full collection is structurally complete: galaxy.yml valid,
all roles have tasks/main.yml, all module_utils are loadable, the Python loader
works, and the CLI subparser registers cleanly.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from typing import ClassVar
from unittest.mock import MagicMock

import pytest
import yaml

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_COLLECTION_DIR = os.path.join(
    _PROJECT_ROOT,
    "collections",
    "ansible_collections",
    "general_ludd",
    "governance",
)
_MODULE_UTILS_DIR = os.path.join(_COLLECTION_DIR, "plugins", "module_utils")
_ROLES_DIR = os.path.join(_COLLECTION_DIR, "roles")


def _load_module_util(name: str):
    path = os.path.join(_MODULE_UTILS_DIR, f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"e2e_gov_{name}", path)
    assert spec is not None and spec.loader is not None, f"{name}.py spec failed"
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class TestGalaxyYAML:
    """Verify galaxy.yml is valid and complete."""

    def test_galaxy_yml_exists_and_parses(self) -> None:
        path = os.path.join(_COLLECTION_DIR, "galaxy.yml")
        assert os.path.isfile(path), "galaxy.yml missing"
        with open(path) as fh:
            data = yaml.safe_load(fh)
        assert isinstance(data, dict)
        assert data.get("namespace") == "general_ludd"
        assert data.get("name") == "governance"
        assert "version" in data

    def test_galaxy_has_required_fields(self) -> None:
        path = os.path.join(_COLLECTION_DIR, "galaxy.yml")
        with open(path) as fh:
            data = yaml.safe_load(fh)
        required = {"namespace", "name", "version", "description"}
        missing = required - set(data)
        assert not missing, f"galaxy.yml missing fields: {missing}"


class TestAllModuleUtilsLoadable:
    """Verify every module_util Python file imports cleanly."""

    EXPECTED_MODULES: ClassVar[frozenset[str]] = frozenset(
        {
            "borders",
            "governing_bodies",
            "civic_services",
            "conflicts_treaties",
            "tax_currency",
            "info_classification",
            "decision_makers",
            "international_relations",
            "legal_systems",
            "public_finance",
            "elections_voting",
            "postal_delivery",
            "military_service",
            "licenses_permits",
        }
    )

    def test_all_expected_module_utils_exist(self) -> None:
        for name in sorted(self.EXPECTED_MODULES):
            path = os.path.join(_MODULE_UTILS_DIR, f"{name}.py")
            assert os.path.isfile(path), f"module_util {name}.py missing"

    def test_each_module_loads_cleanly(self) -> None:
        for name in sorted(self.EXPECTED_MODULES):
            mod = _load_module_util(name)
            assert mod is not None, f"{name}.py failed to load"

    def test_borders_has_expected_exports(self) -> None:
        mod = _load_module_util("borders")
        expected = {"BORDER_TYPES", "BORDER_DATA", "lookup_border", "get_crossing_requirements"}
        missing = expected - set(dir(mod))
        assert not missing, f"borders.py missing exports: {missing}"

    def test_governing_bodies_has_expected_exports(self) -> None:
        mod = _load_module_util("governing_bodies")
        expected = {"INTERNATIONAL_BODIES", "lookup_body", "get_children", "get_descendants"}
        missing = expected - set(dir(mod))
        assert not missing, f"governing_bodies.py missing exports: {missing}"

    def test_civic_services_has_expected_exports(self) -> None:
        mod = _load_module_util("civic_services")
        expected = {"SERVICES", "lookup_service", "get_postal_info", "get_postage_rate"}
        missing = expected - set(dir(mod))
        assert not missing, f"civic_services.py missing exports: {missing}"

    def test_conflicts_treaties_has_expected_exports(self) -> None:
        mod = _load_module_util("conflicts_treaties")
        assert hasattr(mod, "ACTIVE_CONFLICTS")
        assert hasattr(mod, "TREATY_DATABASE")

    def test_tax_currency_has_expected_exports(self) -> None:
        mod = _load_module_util("tax_currency")
        assert hasattr(mod, "TAX_SYSTEMS")
        assert hasattr(mod, "CURRENCIES")

    def test_info_classification_has_expected_exports(self) -> None:
        mod = _load_module_util("info_classification")
        assert hasattr(mod, "CLASSIFICATION_LEVELS")
        assert hasattr(mod, "CLASSIFICATION_BY_COUNTRY")

    def test_decision_makers_has_expected_exports(self) -> None:
        mod = _load_module_util("decision_makers")
        assert hasattr(mod, "DECISION_MAKER_PROFILES")
        assert hasattr(mod, "ROLE_TYPES")

    def test_international_relations_has_expected_exports(self) -> None:
        mod = _load_module_util("international_relations")
        assert hasattr(mod, "ALLIANCES")

    def test_legal_systems_has_expected_exports(self) -> None:
        mod = _load_module_util("legal_systems")
        assert hasattr(mod, "COUNTRY_LEGAL_SYSTEMS")

    def test_public_finance_has_expected_exports(self) -> None:
        mod = _load_module_util("public_finance")
        assert hasattr(mod, "COUNTRY_BUDGETS")

    def test_elections_voting_has_expected_exports(self) -> None:
        mod = _load_module_util("elections_voting")
        assert hasattr(mod, "COUNTRY_ELECTIONS")

    def test_postal_delivery_has_expected_exports(self) -> None:
        mod = _load_module_util("postal_delivery")
        assert hasattr(mod, "POSTAL_CODE_PATTERNS")
        assert hasattr(mod, "COURIER_TRACKING")

    def test_military_service_has_expected_exports(self) -> None:
        mod = _load_module_util("military_service")
        assert hasattr(mod, "CONSCRIPTION_DATA")
        assert hasattr(mod, "MILITARY_BRANCHES")

    def test_licenses_permits_has_expected_exports(self) -> None:
        mod = _load_module_util("licenses_permits")
        assert hasattr(mod, "LICENSE_TYPES")
        assert hasattr(mod, "LICENSE_REGISTRIES")


EXPECTED_ROLES = {
    "borders",
    "governing_bodies",
    "tax_systems",
    "currencies",
    "conflicts",
    "treaties",
    "civic_services",
    "decision_makers",
    "info_classification",
    "postal_delivery",
    "military_service",
    "licenses_permits",
    "navigate_borders",
    "lookup_governing_body",
    "tax_currency_info",
    "civic_service_finder",
    "decision_maker_lookup",
    "info_classification_check",
    "conflicts_treaties_lookup",
    "governance_navigator",
}

ROLE_FILES = ("tasks/main.yml", "defaults/main.yml", "meta/main.yml", "vars/main.yml")


class TestAllRolesComplete:
    """Verify every role has the required files."""

    @pytest.mark.parametrize("role_name", sorted(EXPECTED_ROLES))
    def test_role_has_all_required_files(self, role_name: str) -> None:
        role_dir = os.path.join(_ROLES_DIR, role_name)
        assert os.path.isdir(role_dir), f"Role dir missing: {role_name}"
        for rel_path in ROLE_FILES:
            full_path = os.path.join(role_dir, rel_path)
            assert os.path.isfile(full_path), f"{role_name}/{rel_path} missing"

    @pytest.mark.parametrize("role_name", sorted(EXPECTED_ROLES))
    def test_role_tasks_is_nonempty_yaml(self, role_name: str) -> None:
        path = os.path.join(_ROLES_DIR, role_name, "tasks/main.yml")
        with open(path) as fh:
            data = yaml.safe_load(fh)
        assert isinstance(data, list)
        assert len(data) > 0, f"{role_name} tasks/main.yml is empty"


class TestLoaderFunctions:
    """Verify the Python loader can load every module."""

    def test_loader_imports_cleanly(self) -> None:
        import general_ludd.governance.loader as loader

        assert hasattr(loader, "get_borders")
        assert hasattr(loader, "get_postal_delivery")
        assert hasattr(loader, "get_military_service")
        assert hasattr(loader, "get_licenses_permits")

    def test_each_loader_getter_returns_module(self) -> None:
        import general_ludd.governance.loader as loader

        getters = [
            "get_borders",
            "get_governing_bodies",
            "get_civic_services",
            "get_conflicts_treaties",
            "get_tax_currency",
            "get_info_classification",
            "get_decision_makers",
            "get_international_relations",
            "get_legal_systems",
            "get_public_finance",
            "get_elections_voting",
            "get_postal_delivery",
            "get_military_service",
            "get_licenses_permits",
        ]
        for name in getters:
            func = getattr(loader, name)
            mod = func()
            assert mod is not None, f"loader.{name}() returned None"

    def test_loader_cache_returns_same_object(self) -> None:
        import general_ludd.governance.loader as loader

        loader.clear_cache()
        mod1 = loader.get_borders()
        mod2 = loader.get_borders()
        assert mod1 is mod2, "Loader cache not working"


class TestCLISubparserRegistration:
    """Verify the governance CLI subparser registers cleanly."""

    def test_subparser_added_without_error(self) -> None:
        sys.path.insert(0, os.path.join(_PROJECT_ROOT, "src"))
        try:
            from general_ludd.governance.cli_governance import add_governance_subparser

            mock_subparsers = MagicMock()
            mock_parser = MagicMock()
            mock_parser.add_subparsers.return_value = mock_subparsers
            mock_sub = MagicMock()
            mock_subparsers.add_parser.return_value = mock_sub

            add_governance_subparser(mock_subparsers)
            mock_subparsers.add_parser.assert_called_once()
        finally:
            sys.path.remove(os.path.join(_PROJECT_ROOT, "src"))

    def test_governance_help_string(self) -> None:
        sys.path.insert(0, os.path.join(_PROJECT_ROOT, "src"))
        try:
            from general_ludd.governance.cli_governance import add_governance_subparser

            mock_subparsers = MagicMock()
            mock_parser = MagicMock()
            mock_parser.add_subparsers.return_value = MagicMock()

            add_governance_subparser(mock_subparsers)
            call_args = mock_subparsers.add_parser.call_args
            assert call_args[0][0] == "governance"
        finally:
            sys.path.remove(os.path.join(_PROJECT_ROOT, "src"))


class TestInitExports:
    """Verify __init__.py re-exports all expected symbols."""

    def test_init_exports_all_expected(self) -> None:
        sys.path.insert(0, os.path.join(_PROJECT_ROOT, "src"))
        try:
            from general_ludd.governance import __all__ as govexports

            expected = {
                "get_authority_registry",
                "get_borders",
                "get_civic_services",
                "get_classification_markings",
                "get_conflicts_treaties",
                "get_decision_makers",
                "get_elections_voting",
                "get_governing_bodies",
                "get_info_classification",
                "get_international_relations",
                "get_jurisdictions",
                "get_legal_systems",
                "get_licenses_permits",
                "get_military_service",
                "get_postal_delivery",
                "get_public_finance",
                "get_tax_currency",
            }
            assert set(govexports) == expected, (
                f"__all__ mismatch: got {set(govexports) - expected} extra, missing {expected - set(govexports)}"
            )
        finally:
            sys.path.remove(os.path.join(_PROJECT_ROOT, "src"))
