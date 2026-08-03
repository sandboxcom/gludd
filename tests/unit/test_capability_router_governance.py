"""Verify capability router discovers governance collection from
galaxy.yml declarations — 12 domains + 3 knowledge modules + contracts."""

from __future__ import annotations

import pytest

from general_ludd.dispatch.capabilities import discover_capabilities


class TestGovernanceCapabilities:
    @pytest.fixture
    def registry(self):
        return discover_capabilities()

    # ── Collection discovery ──────────────────────────────────────────────

    def test_governance_collection_discovered(self, registry):
        assert "governance" in registry.collections

    # -- Capability tags (12 domains x 2 = 24 + 3 knowledge + 2 contracts = 29) --

    def test_governance_model_capabilities(self, registry):
        gov = registry.collections["governance"]
        caps = gov.raw_tags
        for cap in (
            "border_lookup",
            "crossing_info",
            "visa_regime",
            "body_lookup",
            "mandate_lookup",
            "tax_lookup",
            "tax_compliance",
            "currency_lookup",
            "fx_regime",
            "conflict_status",
            "sanctions_lookup",
            "treaty_lookup",
            "ratification_status",
            "service_lookup",
            "civil_registry",
            "official_lookup",
            "authority_chain",
            "classification_lookup",
            "clearance_check",
            "postal_lookup",
            "customs_declaration",
            "conscription_lookup",
            "veteran_status",
            "license_lookup",
            "permit_check",
            "jurisdiction_lookup",
            "classification_markings",
            "authority_registry",
            "contract_lookup",
            "contract_search",
        ):
            assert cap in caps, f"governance missing capability tag: {cap}"

    # ── Domain tags ───────────────────────────────────────────────────────

    def test_governance_domain_tags(self, registry):
        gov = registry.collections["governance"]
        for domain in (
            "governance",
            "borders",
            "governing_bodies",
            "tax",
            "currency",
            "conflicts",
            "treaties",
            "civic_services",
            "decision_makers",
            "classification",
            "postal",
            "military",
            "licenses",
            "political",
            "jurisdiction",
        ):
            assert domain in gov.tags, f"governance missing domain tag: {domain}"

    # ── Tag index lookups ─────────────────────────────────────────────────

    def test_governance_tag_index_lookups(self, registry):
        for tag in (
            "border_lookup",
            "body_lookup",
            "tax_lookup",
            "currency_lookup",
            "conflict_status",
            "treaty_lookup",
            "service_lookup",
            "official_lookup",
            "classification_lookup",
            "postal_lookup",
            "conscription_lookup",
            "license_lookup",
            "jurisdiction_lookup",
            "authority_registry",
            "contract_lookup",
            "governance",
            "borders",
            "political",
        ):
            matching = registry.lookup_by_tag(tag)
            assert matching, f"no collections found for governance tag: {tag}"

    # ── Knowledge module capabilities ─────────────────────────────────────

    def test_governance_knowledge_modules(self, registry):
        gov = registry.collections["governance"]
        caps = gov.raw_tags
        for module_tag in (
            "jurisdiction_lookup",
            "classification_markings",
            "authority_registry",
        ):
            assert module_tag in caps, f"governance missing knowledge module tag: {module_tag}"

    # ── Contracts module ──────────────────────────────────────────────────

    def test_governance_contracts_capabilities(self, registry):
        gov = registry.collections["governance"]
        caps = gov.raw_tags
        for contract_tag in ("contract_lookup", "contract_search"):
            assert contract_tag in caps, f"governance missing contract tag: {contract_tag}"

    # ── Cross-collection isolation ────────────────────────────────────────

    def test_governance_tags_dont_leak_to_radio(self, registry):
        radio = registry.collections.get("radio")
        if radio is not None:
            assert "border_lookup" not in radio.tags
            assert "treaty_lookup" not in radio.tags
            assert "tax_lookup" not in radio.tags

    def test_radio_tags_dont_leak_to_governance(self, registry):
        gov = registry.collections["governance"]
        assert "sdr" not in gov.tags
        assert "spectrum_scan" not in gov.tags
        assert "antenna_design" not in gov.tags

    def test_binary_re_tags_dont_leak_to_governance(self, registry):
        gov = registry.collections["governance"]
        assert "pe_analyze" not in gov.tags
        assert "ghidra_analyze" not in gov.tags
        assert "fuzz_target" not in gov.tags

    def test_governance_tags_dont_leak_to_binary_re(self, registry):
        bre = registry.collections.get("binary_re")
        if bre is not None:
            assert "border_lookup" not in bre.tags
            assert "tax_lookup" not in bre.tags
            assert "contract_lookup" not in bre.tags

    # ── Role capability mappings ──────────────────────────────────────────

    def test_governance_roles_discovered(self, registry):
        gov = registry.collections["governance"]
        role_names = {r["name"] for r in gov.roles}
        for role in (
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
        ):
            assert role in role_names, f"governance role not discovered: {role}"
