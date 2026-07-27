"""Tests for the general_ludd.governance military_service module."""

from __future__ import annotations

import importlib.util
import os

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_MODULE_PATH = os.path.join(
    _PROJECT_ROOT,
    "collections",
    "ansible_collections",
    "general_ludd",
    "governance",
    "plugins",
    "module_utils",
    "military_service.py",
)


def _load_module():
    spec = importlib.util.spec_from_file_location("gov_military_service", _MODULE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ms = _load_module()


class TestConscriptionData:
    def test_data_present(self) -> None:
        assert isinstance(ms.CONSCRIPTION_DATA, dict)
        assert len(ms.CONSCRIPTION_DATA) >= 8

    def test_known_countries(self) -> None:
        for code in ("US", "GB", "DE", "FR", "IL", "KR", "RU", "FI", "CH", "BR", "CA", "AU"):
            assert code in ms.CONSCRIPTION_DATA, f"Missing {code}"

    def test_each_entry_has_required_keys(self) -> None:
        for code, entry in ms.CONSCRIPTION_DATA.items():
            assert "active" in entry, f"{code} missing active"
            assert "type" in entry, f"{code} missing type"


class TestGetConscriptionInfo:
    def test_known_country(self) -> None:
        result = ms.get_conscription_info("US")
        assert result is not None
        assert result["active"] is False

    def test_mandatory_country(self) -> None:
        result = ms.get_conscription_info("KR")
        assert result is not None
        assert result["active"] is True

    def test_unknown_country(self) -> None:
        assert ms.get_conscription_info("XX") is None


class TestListMandatoryServiceCountries:
    def test_returns_list(self) -> None:
        countries = ms.list_mandatory_service_countries()
        assert isinstance(countries, list)
        assert len(countries) >= 5
        assert "IL" in countries
        assert "KR" in countries

    def test_all_entries_active(self) -> None:
        countries = ms.list_mandatory_service_countries()
        for code in countries:
            info = ms.CONSCRIPTION_DATA[code]
            assert info["active"] is True, f"{code} listed but not active"


class TestMilitaryBranches:
    def test_branches_present(self) -> None:
        assert isinstance(ms.MILITARY_BRANCHES, dict)
        assert len(ms.MILITARY_BRANCHES) >= 8

    def test_known_countries(self) -> None:
        for code in ("US", "GB", "CA", "DE", "FR", "AU", "JP", "IL", "KR", "RU"):
            assert code in ms.MILITARY_BRANCHES, f"Missing {code}"

    def test_each_branch_has_required_keys(self) -> None:
        for code, branches in ms.MILITARY_BRANCHES.items():
            assert len(branches) > 0, f"{code} has 0 branches"
            for branch in branches:
                assert "name" in branch
                assert "role" in branch


class TestGetMilitaryBranches:
    def test_known_country(self) -> None:
        branches = ms.get_military_branches("US")
        assert branches is not None
        assert len(branches) >= 5

    def test_unknown_country(self) -> None:
        assert ms.get_military_branches("XX") is None


class TestVeteranBenefits:
    def test_benefits_present(self) -> None:
        assert isinstance(ms.VETERAN_BENEFITS, dict)
        assert len(ms.VETERAN_BENEFITS) >= 6

    def test_known_countries(self) -> None:
        for code in ("US", "GB", "CA", "DE", "FR", "AU", "IL", "KR"):
            assert code in ms.VETERAN_BENEFITS, f"Missing {code}"

    def test_each_entry_has_admin_body(self) -> None:
        for code, entry in ms.VETERAN_BENEFITS.items():
            assert "administering_body" in entry, f"{code} missing administering_body"
            assert "categories" in entry, f"{code} missing categories"


class TestGetVeteranBenefits:
    def test_known_country(self) -> None:
        result = ms.get_veteran_benefits("US")
        assert result is not None
        assert "categories" in result

    def test_filtered_category(self) -> None:
        result = ms.get_veteran_benefits("US", "healthcare")
        assert result is not None
        assert "healthcare" in result["categories"]

    def test_unknown_category(self) -> None:
        assert ms.get_veteran_benefits("US", "free_lunch") is None

    def test_unknown_country(self) -> None:
        assert ms.get_veteran_benefits("XX") is None


class TestGetEnlistmentProcess:
    def test_known_country(self) -> None:
        result = ms.get_enlistment_process("US")
        assert result is not None
        assert "conscription_active" in result

    def test_unknown_country(self) -> None:
        assert ms.get_enlistment_process("XX") is None
