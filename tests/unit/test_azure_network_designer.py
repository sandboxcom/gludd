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


# ---------------------------------------------------------------------------
# Deep tests: design_vnet edge cases not covered above
# ---------------------------------------------------------------------------


class TestDesignVnetDeep:
    def test_invalid_cidr_raises(self):
        with pytest.raises((ipaddress.AddressValueError, ValueError)):
            design_vnet("bad", "not-a-valid-cidr")

    def test_negative_num_subnets_returns_empty(self):
        design = design_vnet("neg", num_subnets=-5)
        assert design.subnets == []

    def test_boundary_exactly_available_subnets(self):
        """A /20 yields exactly 16 /24 subnets."""
        design = design_vnet("bound", "10.0.0.0/20", num_subnets=16)
        assert len(design.subnets) == 16

    def test_subnet_cidrs_are_strings_not_ipaddress_objects(self):
        design = design_vnet("types", num_subnets=2)
        for s in design.subnets:
            assert isinstance(s["cidr"], str)

    def test_very_long_vnet_name_preserved(self):
        name = "a" * 255
        design = design_vnet(name)
        assert design.vnet_name == name
        assert len(design.vnet_name) == 255

    def test_unicode_vnet_name(self):
        design = design_vnet("vnet-\u00e9\u00e0")
        assert design.vnet_name == "vnet-\u00e9\u00e0"

    def test_returns_new_network_design_each_call(self):
        a = design_vnet("a")
        b = design_vnet("b")
        assert a is not b
        assert a.vnet_name != b.vnet_name

    def test_subnet_list_is_independent_copy(self):
        design = design_vnet("copy-test", num_subnets=2)
        design.subnets.append({"name": "extra", "purpose": "test", "cidr": "10.0.99.0/24"})
        fresh = design_vnet("copy-test", num_subnets=2)
        assert len(fresh.subnets) == 2

    def test_slash_8_yields_65536_possible_subnets(self):
        """A /8 subnetted to /24 yields 2^16 = 65536 subnets — verify bound."""
        design = design_vnet("huge", "10.0.0.0/8", num_subnets=256)
        assert len(design.subnets) == 256

    def test_single_host_cidr_yields_one_subnet_with_host_prefix(self):
        """A /32 yields itself as a single subnet (no further subdivision)."""
        design = design_vnet("host", "10.0.0.1/32", num_subnets=10)
        assert len(design.subnets) == 1
        assert design.subnets[0]["cidr"] == "10.0.0.1/32"

    def test_slash_25_raises_on_subnet_request(self):
        """A /25 cannot be split into /24; ipaddress raises ValueError."""
        with pytest.raises(ValueError, match="new prefix must be longer"):
            design_vnet("tight25", "10.0.0.0/25", num_subnets=10)


# ---------------------------------------------------------------------------
# Deep tests: generate_nsg_rules detailed structure
# ---------------------------------------------------------------------------


class TestGenerateNsgRulesDeep:
    def test_rules_are_ordered_by_priority_ascending(self):
        rules = generate_nsg_rules()
        priorities = [int(r["priority"]) for r in rules]
        assert priorities == sorted(priorities)

    def test_no_empty_or_whitespace_only_rule_names(self):
        for rule in generate_nsg_rules():
            assert rule["name"].strip(), f"empty name in rule {rule}"

    def test_all_protocols_are_valid(self):
        valid = {"Tcp", "Udp", "*", "Icmp", "Ah", "Esp"}
        for rule in generate_nsg_rules():
            assert rule["protocol"] in valid, f"invalid protocol in {rule['name']}"

    def test_all_priorities_are_numeric_strings(self):
        for rule in generate_nsg_rules():
            int(rule["priority"])

    def test_all_source_ports_are_wildcard_or_valid(self):
        for rule in generate_nsg_rules():
            assert rule["source_port"] == "*" or rule["source_port"].isdigit()

    def test_all_destination_ports_are_wildcard_or_valid(self):
        for rule in generate_nsg_rules():
            assert rule["destination_port"] == "*" or rule["destination_port"].isdigit()

    def test_deny_rule_priority_is_highest(self):
        rules = generate_nsg_rules()
        deny = next(r for r in rules if r["name"] == "DenyAllInbound")
        max_prio = max(int(r["priority"]) for r in rules)
        assert int(deny["priority"]) == max_prio

    def test_allow_rules_have_internet_source(self):
        for rule in generate_nsg_rules():
            if rule["access"] == "Allow":
                assert rule["source"] == "Internet" or rule["source"] == "*", (
                    f"Allow rule '{rule['name']}' has unexpected source: {rule['source']}"
                )

    def test_no_rule_has_both_allow_and_deny(self):
        for rule in generate_nsg_rules():
            assert rule["access"] in ("Allow", "Deny")

    def test_rule_directions_are_valid(self):
        for rule in generate_nsg_rules():
            assert rule["direction"] in ("Inbound", "Outbound")

    def test_generated_rules_are_consistent_across_calls(self):
        first = generate_nsg_rules()
        second = generate_nsg_rules()
        assert first == second

    def test_each_rule_is_serializable_dict(self):
        import json

        json.dumps(generate_nsg_rules())
