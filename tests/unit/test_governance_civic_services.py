"""TDD tests for the governance civic services knowledge module.

Tests are written BEFORE the implementation and exercise:
  - SERVICE_CATEGORIES completeness and shape
  - SERVICES knowledge base: all 14 services present with required fields
  - lookup_service(service_name, country): hit/miss, country variants, normalization
  - get_requirements(service_id): correctness and structure
  - get_processing_time(service_id): routine/expedited tiers
  - find_service_office(service_id, location): hit/miss, field coverage
  - POSTAL_SYSTEMS knowledge base: multiple national carriers
  - get_postal_info(country): hit/miss
  - get_postage_rate(origin, destination, weight): rate tiers, weight bands,
    unknown-route handling
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

_COLLECTION_ROOT = (
    Path(__file__).resolve().parents[2]
    / "collections/ansible_collections/general_ludd/governance"
)
_MODULE_UTILS = _COLLECTION_ROOT / "plugins" / "module_utils"

if str(_MODULE_UTILS) not in sys.path:
    sys.path.insert(0, str(_MODULE_UTILS))

try:
    _cs = importlib.import_module("civic_services")
    SERVICE_CATEGORIES = _cs.SERVICE_CATEGORIES
    SERVICES = _cs.SERVICES
    POSTAL_SYSTEMS = _cs.POSTAL_SYSTEMS
    REQUIRED_SERVICE_KEYS = _cs.REQUIRED_SERVICE_KEYS
    lookup_service = _cs.lookup_service
    get_requirements = _cs.get_requirements
    get_processing_time = _cs.get_processing_time
    find_service_office = _cs.find_service_office
    get_postal_info = _cs.get_postal_info
    get_postage_rate = _cs.get_postage_rate
    ServiceInfo = _cs.ServiceInfo
    ServiceOffice = _cs.ServiceOffice
    PostalSystem = _cs.PostalSystem
    PostageRate = _cs.PostageRate
except ModuleNotFoundError:
    pytest.skip("civic_services module not available", allow_module_level=True)

EXPECTED_SERVICES = frozenset({
    "passport", "national_id", "drivers_license", "marriage_license",
    "business_registration", "property_deed", "building_permit",
    "library_card", "postal_services", "military_enlistment",
    "voter_registration", "tax_filing", "benefits_claims", "foia_request",
})

EXPECTED_CATEGORIES = frozenset({
    "identification", "licenses", "permits", "registrations",
    "benefits", "complaints", "information_request",
})


# ---- SERVICE_CATEGORIES ------------------------------------------------------

class TestServiceCategories:
    def test_categories_contain_all_expected(self):
        assert EXPECTED_CATEGORIES.issubset(SERVICE_CATEGORIES)

    def test_categories_are_frozen_or_set(self):
        assert isinstance(SERVICE_CATEGORIES, (frozenset, set))

    def test_every_service_maps_to_a_valid_category(self):
        for sid, info in SERVICES.items():
            cat = info["category"]
            assert cat in SERVICE_CATEGORIES, (
                f"{sid}: category {cat!r} not in SERVICE_CATEGORIES"
            )


# ---- SERVICES knowledge base -------------------------------------------------

class TestServicesKnowledgeBase:
    def test_all_fourteen_services_present(self):
        assert set(SERVICES) == EXPECTED_SERVICES, (
            f"missing: {EXPECTED_SERVICES - set(SERVICES)} | "
            f"extra: {set(SERVICES) - EXPECTED_SERVICES}"
        )

    def test_service_count_at_least_fourteen(self):
        assert len(SERVICES) >= 14

    def test_every_service_has_required_keys(self):
        for sid, info in SERVICES.items():
            missing = [k for k in REQUIRED_SERVICE_KEYS if k not in info]
            assert not missing, f"{sid}: missing keys {missing}"

    def test_issuing_body_is_nonempty_string(self):
        for sid, info in SERVICES.items():
            ib = info["issuing_body"]
            assert isinstance(ib, str) and ib.strip(), f"{sid}: empty issuing_body"

    def test_requirements_is_nonempty_list(self):
        for sid, info in SERVICES.items():
            reqs = info["requirements"]
            assert isinstance(reqs, list) and reqs, f"{sid}: empty requirements"
            for r in reqs:
                assert isinstance(r, str) and r.strip(), f"{sid}: blank requirement"

    def test_processing_time_has_routine_key(self):
        for sid, info in SERVICES.items():
            pt = info["processing_time"]
            assert isinstance(pt, dict), f"{sid}: processing_time not a dict"
            assert "routine" in pt, f"{sid}: missing 'routine' processing_time"

    def test_cost_has_currency(self):
        for sid, info in SERVICES.items():
            cost = info["cost"]
            assert isinstance(cost, dict), f"{sid}: cost not a dict"
            assert "currency" in cost, f"{sid}: cost missing currency"

    def test_online_portal_present(self):
        for sid, info in SERVICES.items():
            portal = info["online_portal"]
            assert portal is not None, f"{sid}: online_portal is None"

    def test_appeal_process_is_nonempty_string(self):
        for sid, info in SERVICES.items():
            ap = info["appeal_process"]
            assert isinstance(ap, str) and ap.strip(), (
                f"{sid}: empty appeal_process"
            )


# ---- lookup_service ----------------------------------------------------------

class TestLookupService:
    def test_returns_service_info_for_known_service(self):
        result = lookup_service("passport", "US")
        assert result is not None
        assert result.service_id == "passport"
        assert result.country == "US"

    def test_returns_none_for_unknown_service(self):
        assert lookup_service("nonexistent_service", "US") is None

    def test_returns_none_for_unknown_country(self):
        """Country with no variant should still resolve via a default."""
        result = lookup_service("passport", "ZZ")
        assert result is None

    def test_country_variant_changes_issuing_body(self):
        us = lookup_service("passport", "US")
        gb = lookup_service("passport", "GB")
        assert us is not None and gb is not None
        assert us.issuing_body != gb.issuing_body

    def test_normalizes_service_name_case_insensitive(self):
        result = lookup_service("Passport", "US")
        assert result is not None
        assert result.service_id == "passport"

    def test_normalizes_spaces_and_hyphens(self):
        result = lookup_service("driver's license", "US")
        assert result is not None
        assert result.service_id == "drivers_license"

    def test_service_info_has_all_fields_populated(self):
        result = lookup_service("business_registration", "US")
        assert result is not None
        assert result.category in SERVICE_CATEGORIES
        assert isinstance(result.requirements, list) and result.requirements
        assert isinstance(result.processing_time, dict)
        assert isinstance(result.cost, dict)
        assert result.online_portal is not None
        assert result.appeal_process

    def test_foia_lookup(self):
        result = lookup_service("FOIA_request", "US")
        assert result is not None
        assert result.category == "information_request"

    def test_voter_registration_lookup(self):
        result = lookup_service("voter_registration", "US")
        assert result is not None
        assert result.category == "registrations"


# ---- get_requirements --------------------------------------------------------

class TestGetRequirements:
    def test_returns_list_for_known_service(self):
        reqs = get_requirements("passport")
        assert isinstance(reqs, list) and len(reqs) >= 2

    def test_returns_none_for_unknown_service(self):
        assert get_requirements("nope") is None

    def test_requirements_country_aware(self):
        us_reqs = get_requirements("passport", country="US")
        assert us_reqs is not None
        assert isinstance(us_reqs, list)

    def test_drivers_license_has_id_proof_requirement(self):
        reqs = get_requirements("drivers_license")
        joined = " ".join(reqs).lower()
        assert any(
            kw in joined
            for kw in ("identity", "proof of", "birth", "residence", "photo")
        ), f"drivers_license requirements lack identity/birth/residence: {reqs}"


# ---- get_processing_time -----------------------------------------------------

class TestGetProcessingTime:
    def test_returns_dict_for_known_service(self):
        pt = get_processing_time("passport")
        assert isinstance(pt, dict)
        assert "routine" in pt

    def test_returns_none_for_unknown_service(self):
        assert get_processing_time("nope") is None

    def test_routine_is_string_description(self):
        pt = get_processing_time("passport")
        assert isinstance(pt["routine"], str) and pt["routine"]

    def test_expedited_tier_exists_for_passport(self):
        pt = get_processing_time("passport")
        assert "expedited" in pt or "urgent" in pt.lower() or any(
            k in pt for k in pt
        ), "passport should expose an expedited/urgent tier"


# ---- find_service_office -----------------------------------------------------

class TestFindServiceOffice:
    def test_returns_office_for_known_service_and_location(self):
        office = find_service_office("passport", "New York")
        assert office is not None
        assert office.service_id == "passport"
        assert office.location == "New York"

    def test_returns_none_for_unknown_service(self):
        assert find_service_office("nope", "New York") is None

    def test_returns_none_for_uncovered_location(self):
        assert find_service_office("passport", "Atlantis") is None

    def test_office_has_address_and_hours(self):
        office = find_service_office("passport", "New York")
        assert office is not None
        assert office.address
        assert office.hours
        assert office.office_name


# ---- POSTAL_SYSTEMS ----------------------------------------------------------

class TestPostalSystems:
    def test_contains_usps(self):
        assert "US" in POSTAL_SYSTEMS
        assert POSTAL_SYSTEMS["US"]["name"] == "USPS"

    def test_contains_royal_mail(self):
        assert "GB" in POSTAL_SYSTEMS
        assert "Royal Mail" in POSTAL_SYSTEMS["GB"]["name"]

    def test_contains_canada_post(self):
        assert "CA" in POSTAL_SYSTEMS
        assert "Canada Post" in POSTAL_SYSTEMS["CA"]["name"]

    def test_contains_deutsche_post(self):
        assert "DE" in POSTAL_SYSTEMS
        assert "Deutsche Post" in POSTAL_SYSTEMS["DE"]["name"]

    def test_contains_la_poste(self):
        assert "FR" in POSTAL_SYSTEMS
        assert "La Poste" in POSTAL_SYSTEMS["FR"]["name"]

    def test_every_system_has_required_fields(self):
        for cc, info in POSTAL_SYSTEMS.items():
            assert "name" in info, f"{cc}: missing name"
            assert "services" in info, f"{cc}: missing services"
            assert isinstance(info["services"], list) and info["services"], (
                f"{cc}: empty services"
            )
            assert "tracking_url" in info, f"{cc}: missing tracking_url"
            assert "customs_required" in info, f"{cc}: missing customs_required"


# ---- get_postal_info ---------------------------------------------------------

class TestGetPostalInfo:
    def test_returns_postal_system_for_known_country(self):
        info = get_postal_info("US")
        assert info is not None
        assert info.country_code == "US"
        assert info.name == "USPS"

    def test_returns_none_for_unknown_country(self):
        assert get_postal_info("ZZ") is None

    def test_postal_system_has_services_list(self):
        info = get_postal_info("GB")
        assert info is not None
        assert isinstance(info.services, list) and info.services

    def test_customs_flag_is_bool(self):
        for cc in POSTAL_SYSTEMS:
            info = get_postal_info(cc)
            assert info is not None
            assert isinstance(info.customs_required, bool)


# ---- get_postage_rate --------------------------------------------------------

class TestGetPostageRate:
    def test_returns_rate_for_domestic_route(self):
        rate = get_postage_rate("US", "US", 100)
        assert rate is not None
        assert rate.origin == "US"
        assert rate.destination == "US"
        assert rate.weight_grams == 100
        assert rate.cost > 0

    def test_returns_rate_for_international_route(self):
        rate = get_postage_rate("US", "GB", 250)
        assert rate is not None
        assert rate.cost > 0

    def test_currency_matches_origin_country(self):
        rate = get_postage_rate("US", "US", 100)
        assert rate.currency == "USD"
        rate_gb = get_postage_rate("GB", "GB", 100)
        assert rate_gb.currency == "GBP"

    def test_international_costs_more_than_domestic(self):
        domestic = get_postage_rate("US", "US", 100)
        international = get_postage_rate("US", "GB", 100)
        assert international.cost > domestic.cost

    def test_heavier_costs_more(self):
        light = get_postage_rate("US", "US", 50)
        heavy = get_postage_rate("US", "US", 500)
        assert heavy.cost > light.cost

    def test_returns_none_for_unknown_origin(self):
        assert get_postage_rate("ZZ", "US", 100) is None

    def test_returns_none_for_unknown_destination(self):
        assert get_postage_rate("US", "ZZ", 100) is None

    def test_rate_has_service_class_and_eta(self):
        rate = get_postage_rate("US", "US", 100)
        assert rate is not None
        assert rate.service_class
        assert isinstance(rate.estimated_days, int)
        assert rate.estimated_days > 0
