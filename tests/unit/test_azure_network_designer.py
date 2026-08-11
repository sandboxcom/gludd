"""Unit tests for ``general_ludd.azure.network_designer``."""

from __future__ import annotations

import ipaddress

import pytest

from general_ludd.azure import network_designer as nd
from general_ludd.azure.contracts import NetworkDesign
from general_ludd.azure.network_designer import (
    DEFAULT_CIDR,
    design_vnet,
    generate_nsg_rules,
)

# ---------------------------------------------------------------------------
# design_vnet
# ---------------------------------------------------------------------------


class TestDesignVnet:
    # -- happy path ---------------------------------------------------------

    def test_returns_network_design_with_correct_fields(self):
        design = design_vnet("my-vnet", "10.0.0.0/16")
        assert isinstance(design, NetworkDesign)
        assert design.vnet_name == "my-vnet"
        assert design.address_space == "10.0.0.0/16"
        assert len(design.subnets) >= 1
        assert len(design.nsg_rules) >= 1

    def test_default_cidr_used_when_argument_omitted(self):
        design = design_vnet("default-vnet")
        assert design.address_space == DEFAULT_CIDR

    def test_exactly_four_subnets_when_requested(self):
        design = design_vnet("big-vnet", "10.0.0.0/16", num_subnets=4)
        assert len(design.subnets) == 4

    def test_six_subnets_possible(self):
        design = design_vnet("big-vnet", "10.0.0.0/16", num_subnets=6)
        assert len(design.subnets) == 6

    def test_subnet_cidrs_non_overlapping(self):
        design = design_vnet("overlap-test", "10.0.0.0/16", num_subnets=4)
        cidrs = [s["cidr"] for s in design.subnets]
        nets = [ipaddress.IPv4Network(c) for c in cidrs]
        for i, a in enumerate(nets):
            for j, b in enumerate(nets):
                if i != j:
                    assert not a.overlaps(b), f"overlap: {a} vs {b}"

    def test_each_subnet_has_required_keys(self):
        design = design_vnet("keys-test", num_subnets=2)
        for s in design.subnets:
            assert "name" in s
            assert "purpose" in s
            assert "cidr" in s

    def test_vnet_name_preserved(self):
        design = design_vnet("unusual-vnet-name")
        assert design.vnet_name == "unusual-vnet-name"

    # -- edge: empty / falsy address_space ----------------------------------

    @pytest.mark.parametrize("empty_value", ["", None])
    def test_empty_or_none_address_space_falls_back_to_default(self, empty_value):
        design = design_vnet("vnet", empty_value)
        assert design.address_space == DEFAULT_CIDR

    # -- edge: num_subnets --------------------------------------------------

    def test_zero_subnets_returns_empty_list(self):
        design = design_vnet("zero", num_subnets=0)
        assert design.subnets == []

    def test_one_subnet_returns_single_entry(self):
        design = design_vnet("one", num_subnets=1)
        assert len(design.subnets) == 1
        s = design.subnets[0]
        assert s["name"] == nd._SUBNET_DEFAULTS[0][0]
        assert s["purpose"] == nd._SUBNET_DEFAULTS[0][1]

    def test_max_subnets_from_slash16_is_256(self):
        """A /16 subnetted into /24 yields at most 256 subnets."""
        design = design_vnet("max", "10.0.0.0/16", num_subnets=256)
        assert len(design.subnets) == 256

    def test_requesting_more_than_256_clips_to_available(self):
        """When num_subnets exceeds what the CIDR can yield, clip gracefully."""
        design = design_vnet("overflow", "10.0.0.0/16", num_subnets=500)
        assert len(design.subnets) == 256

    # -- edge: subnet naming cycles through _SUBNET_DEFAULTS ----------------

    def test_subnet_names_and_purposes_cycle_through_defaults(self):
        defaults = nd._SUBNET_DEFAULTS
        design = design_vnet("cycle", num_subnets=len(defaults) * 2)
        for idx, s in enumerate(design.subnets):
            expected = defaults[idx % len(defaults)]
            assert s["name"] == expected[0]
            assert s["purpose"] == expected[1]

    # -- edge: different address spaces -------------------------------------

    def test_tighter_cidr_yields_fewer_subnets(self):
        """A /20 can only yield 2^(24-20)=16 /24 subnets."""
        design = design_vnet("tight", "10.0.0.0/20", num_subnets=100)
        assert len(design.subnets) == 16

    def test_different_address_range(self):
        design = design_vnet("range", "172.16.0.0/16", num_subnets=4)
        first_cidr = design.subnets[0]["cidr"]
        assert first_cidr.startswith("172.16.")

    # -- edge: all subnets are /24 ------------------------------------------

    def test_every_subnet_is_slash_24(self):
        design = design_vnet("prefix", num_subnets=8)
        for s in design.subnets:
            net = ipaddress.IPv4Network(s["cidr"])
            assert net.prefixlen == nd._SUBNET_PREFIX, s["cidr"]

    # -- edge: nsg_rules populated ------------------------------------------

    def test_nsg_rules_included_in_design(self):
        design = design_vnet("nsg-test")
        assert design.nsg_rules == nd._NSG_WEB_RULES


# ---------------------------------------------------------------------------
# generate_nsg_rules
# ---------------------------------------------------------------------------


class TestGenerateNsgRules:
    def test_returns_list_of_dicts(self):
        rules = generate_nsg_rules()
        assert isinstance(rules, list)
        assert all(isinstance(r, dict) for r in rules)

    def test_has_at_least_one_rule(self):
        assert len(generate_nsg_rules()) >= 1

    def test_each_rule_has_required_keys(self):
        required = {"name", "priority", "direction", "protocol", "access"}
        for rule in generate_nsg_rules():
            assert required.issubset(rule.keys()), f"missing keys in {rule['name']}"

    def test_rule_count_matches_default(self):
        assert len(generate_nsg_rules()) == len(nd._NSG_WEB_RULES)

    # -- specific rule assertions -------------------------------------------

    def test_allow_http_inbound_rule_exists(self):
        rules = generate_nsg_rules()
        http_rule = next((r for r in rules if r["name"] == "AllowHTTPInbound"), None)
        assert http_rule is not None
        assert http_rule["destination_port"] == "80"
        assert http_rule["direction"] == "Inbound"
        assert http_rule["access"] == "Allow"
        assert http_rule["source"] == "Internet"

    def test_allow_https_inbound_rule_exists(self):
        rules = generate_nsg_rules()
        https_rule = next((r for r in rules if r["name"] == "AllowHTTPSInbound"), None)
        assert https_rule is not None
        assert https_rule["destination_port"] == "443"
        assert https_rule["direction"] == "Inbound"
        assert https_rule["access"] == "Allow"
        assert https_rule["source"] == "Internet"

    def test_deny_all_inbound_rule_is_last_by_priority(self):
        rules = generate_nsg_rules()
        deny_rule = next((r for r in rules if r["name"] == "DenyAllInbound"), None)
        assert deny_rule is not None
        assert deny_rule["priority"] == "4096"
        assert deny_rule["access"] == "Deny"
        priorities = [int(r["priority"]) for r in rules]
        assert int(deny_rule["priority"]) == max(priorities)

    def test_priorities_are_unique(self):
        rules = generate_nsg_rules()
        priorities = [r["priority"] for r in rules]
        assert len(priorities) == len(set(priorities))

    # -- immutability: returned list is independent copy --------------------

    def test_mutating_returned_list_does_not_affect_source(self):
        rules = generate_nsg_rules()
        original = list(rules)
        rules.append({"name": "synthetic"})
        assert len(generate_nsg_rules()) == len(nd._NSG_WEB_RULES)
        assert len(original) == len(nd._NSG_WEB_RULES)
