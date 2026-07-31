"""Unit tests for ``general_ludd.azure.network_designer``."""

from __future__ import annotations

from general_ludd.azure.network_designer import (
    DEFAULT_CIDR,
    design_vnet,
    generate_nsg_rules,
)


class TestDesignVnet:
    def test_returns_network_design_with_correct_fields(self):
        design = design_vnet("my-vnet", "10.0.0.0/16")
        assert design.vnet_name == "my-vnet"
        assert design.address_space == "10.0.0.0/16"
        assert len(design.subnets) >= 1
        assert len(design.nsg_rules) >= 1

    def test_default_cidr_used_when_none_provided(self):
        design = design_vnet("default-vnet")
        assert design.address_space == DEFAULT_CIDR

    def test_at_least_four_subnets_when_requested(self):
        design = design_vnet("big-vnet", "10.0.0.0/16", num_subnets=4)
        assert len(design.subnets) == 4

    def test_six_subnets_possible(self):
        design = design_vnet("big-vnet", "10.0.0.0/16", num_subnets=6)
        assert len(design.subnets) == 6

    def test_subnet_cidrs_dont_overlap(self):
        design = design_vnet("overlap-test", "10.0.0.0/16", num_subnets=4)
        cidrs = [s["cidr"] for s in design.subnets]
        from ipaddress import IPv4Network

        nets = [IPv4Network(c) for c in cidrs]
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


class TestGenerateNsgRules:
    def test_returns_list_of_rules(self):
        rules = generate_nsg_rules()
        assert isinstance(rules, list)
        assert len(rules) >= 1

    def test_each_rule_has_required_keys(self):
        required = {"name", "priority", "direction", "protocol", "access"}
        for rule in generate_nsg_rules():
            assert required.issubset(rule.keys()), f"missing keys in {rule['name']}"

    def test_rules_are_dicts(self):
        for rule in generate_nsg_rules():
            assert isinstance(rule, dict)
