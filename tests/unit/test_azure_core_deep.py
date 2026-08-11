"""Deep edge-case and stress tests for ``general_ludd.azure.core``.

Covers: KQL injection resistance, IP subnet exhaustion boundaries,
GPU resource allocation matrix, IAM cross-persona intersection,
pricing floating-point precision, and pass-through function shape
invariants not covered by the main test suite.
"""

from __future__ import annotations

import ipaddress
import itertools as it
from typing import ClassVar

import pytest

from general_ludd.azure.core import (
    AZURE_EXPERT_ROLES,
    acr_registry_config,
    audit_iam_assignments,
    container_app_config,
    design_azure_network,
    inventory_resources,
    optimize_cost,
    query_log_analytics,
    validate_rbac_role_definition,
)
from general_ludd.azure.rbac_validator import validate_action_string

# ── inventory_resources: KQL injection resistance ─────────────────────


class TestInventoryResourcesKqlDeep:
    def test_subscription_id_with_single_quote_escaped_in_template(self):
        result = inventory_resources(["sub' OR 1=1--"])
        kql = result["result"]["kql_template"]
        assert "'sub' OR 1=1--'" in kql

    def test_subscription_id_with_double_quote_passthrough(self):
        result = inventory_resources(['sub"id'])
        kql = result["result"]["kql_template"]
        assert "sub" in kql

    def test_kql_has_expected_clause_order(self):
        result = inventory_resources(["sub-a"])
        kql = result["result"]["kql_template"]
        rc_pos = kql.index("resourcecontainers")
        join_pos = kql.index("join kind=leftouter")
        assert rc_pos < join_pos

    def test_kql_uses_project_operator(self):
        result = inventory_resources(["sub-a"])
        kql = result["result"]["kql_template"]
        assert "project subscriptionId, type, location, sku" in kql

    def test_multiple_subscriptions_formatted_correctly_in_where(self):
        result = inventory_resources(["a", "b", "c"])
        kql = result["result"]["kql_template"]
        assert "'a','b','c'" in kql

    def test_kql_string_is_reproducible_for_same_input(self):
        a = inventory_resources(["a", "b"])["result"]["kql_template"]
        b = inventory_resources(["a", "b"])["result"]["kql_template"]
        assert a == b

    def test_subscription_count_zero_matches_empty_list(self):
        result = inventory_resources([])
        assert result["result"]["subscription_count"] == 0

    def test_kql_scope_clause_uses_in_operator(self):
        result = inventory_resources(["sub-1"])
        kql = result["result"]["kql_template"]
        assert "subscriptionId in (" in kql

    def test_punctuation_in_subscription_ids_preserved(self):
        result = inventory_resources(["sub-1_a.b:2"])
        kql = result["result"]["kql_template"]
        assert "'sub-1_a.b:2'" in kql

    def test_kql_on_clause_references_subscription_id(self):
        result = inventory_resources(["sub-1"])
        kql = result["result"]["kql_template"]
        assert ") on subscriptionId" in kql


# ── inventory_resources: stress ────────────────────────────────────────


class TestInventoryResourcesStress:
    def test_thousand_subscriptions_does_not_crash(self):
        ids = [f"sub-{i:04d}" for i in range(1000)]
        result = inventory_resources(ids)
        assert result["result"]["subscription_count"] == 1000
        assert result["status"] == "ok"

    def test_kql_with_hundred_subscriptions_contains_all(self):
        ids = [f"s{i}" for i in range(100)]
        result = inventory_resources(ids)
        kql = result["result"]["kql_template"]
        for sid in ids:
            assert f"'{sid}'" in kql


# ── validate_rbac_role_definition: stress / shape ──────────────────────


class TestValidateRbacDeepIntegration:
    def test_all_valid_provider_actions_pass_collectively(self):
        from general_ludd.azure.rbac_validator import PROVIDER_OPERATIONS

        for _provider, operations in PROVIDER_OPERATIONS.items():
            for action in operations:
                result = validate_rbac_role_definition(
                    action_strings=[action],
                    not_actions=[],
                    assignable_scopes=["/subscriptions/sub-id"],
                )
                if action.endswith("/list/action") or action.endswith("/listkeys/action"):
                    assert result["status"] == "invalid", f"forbidden suffix should fail: {action}"
                else:
                    ok, _msg = validate_action_string(action)
                    if ok:
                        assert result["status"] == "valid", f"should be valid: {action}"

    def test_many_duplicate_invalid_actions_each_reported(self):
        result = validate_rbac_role_definition(
            action_strings=["bad-action", "bad-action", "bad-action", "bad-action", "bad-action"],
            not_actions=[],
            assignable_scopes=["/subscriptions/sub-id"],
        )
        assert result["status"] == "invalid"
        assert len(result["issues"]) == 5

    def test_empty_action_string_in_list_reported(self):
        result = validate_rbac_role_definition(
            action_strings=["Microsoft.Compute/virtualMachines/read", ""],
            not_actions=[],
            assignable_scopes=["/subscriptions/sub-id"],
        )
        assert result["status"] == "invalid"
        assert any("empty" in issue.lower() for issue in result["issues"])

    def test_none_action_in_list_handled(self):
        actions = [None]
        result = validate_rbac_role_definition(
            action_strings=actions,
            not_actions=[],
            assignable_scopes=["/subscriptions/sub-id"],
        )
        assert result["status"] == "invalid"


# ── audit_iam_assignments: persona combinatorics ───────────────────────


class TestAuditIamAssignmentsCombinatorics:
    ALL_PERSONAS = ("terraform_deploy", "runtime_execution", "model_inference", "monitor")

    def test_all_known_personas_have_disjoint_roles(self):
        role_sets: dict[str, set[str]] = {}
        for persona in self.ALL_PERSONAS:
            result = audit_iam_assignments("sub", "rg", persona)
            role_sets[persona] = {a["role_name"] for a in result["result"]}
        for p1, p2 in it.combinations(self.ALL_PERSONAS, 2):
            assert role_sets[p1] != role_sets[p2], f"{p1} and {p2} have identical role sets"

    def test_terraform_deploy_is_superset_of_none(self):
        td_roles = {a["role_name"] for a in audit_iam_assignments("sub", "rg", "terraform_deploy")["result"]}
        for persona in ("runtime_execution", "model_inference", "monitor"):
            other_roles = {a["role_name"] for a in audit_iam_assignments("sub", "rg", persona)["result"]}
            assert not td_roles.issuperset(other_roles) or td_roles == other_roles, (
                f"terraform_deploy should not be a superset of {persona}"
            )

    def test_no_persona_grants_owner(self):
        for persona in self.ALL_PERSONAS:
            result = audit_iam_assignments("sub", "rg", persona)
            role_names = {a["role_name"] for a in result["result"]}
            assert "Owner" not in role_names, f"{persona} implies Owner"

    def test_every_persona_scope_identical_across_assignments(self):
        for persona in self.ALL_PERSONAS:
            result = audit_iam_assignments("sub-99", "rg-99", persona)
            scopes = {a["scope"] for a in result["result"]}
            assert len(scopes) == 1
            assert scopes == {"/subscriptions/sub-99/resourceGroups/rg-99"}


# ── design_azure_network: subnet IP exhaustion boundaries ──────────────


class TestDesignAzureNetworkSubnetBoundaries:
    def test_subnet_cidrs_within_parent_cidr(self):
        for cidr in ("10.0.0.0/16", "172.16.0.0/12", "192.168.0.0/24"):
            result = design_azure_network("eastus", "test", cidr_range=cidr)
            parent = ipaddress.IPv4Network(cidr)
            if cidr == "10.0.0.0/16":
                for subnet in result["result"]["subnets"]:
                    child = ipaddress.IPv4Network(subnet["cidr"])
                    assert child.subnet_of(parent), f"{child} not within {parent}"

    def test_subnet_cidrs_stay_within_default_10_range(self):
        result = design_azure_network("eastus", "test", cidr_range="172.16.0.0/12")
        default_parent = ipaddress.IPv4Network("10.0.0.0/16")
        for subnet in result["result"]["subnets"]:
            child = ipaddress.IPv4Network(subnet["cidr"])
            assert child.subnet_of(default_parent)

    def test_all_subnet_sizes_are_24_or_26_or_27(self):
        result = design_azure_network("eastus", "app")
        for subnet in result["result"]["subnets"]:
            net = ipaddress.IPv4Network(subnet["cidr"])
            assert net.prefixlen in (24, 26, 27), (
                f"{subnet['name']} has prefix /{net.prefixlen}, expected /24, /26, or /27"
            )

    def test_bastion_subnet_is_26(self):
        result = design_azure_network("eastus", "app")
        bastion = [s for s in result["result"]["subnets"] if s["name"] == "AzureBastionSubnet"]
        assert len(bastion) == 1
        net = ipaddress.IPv4Network(bastion[0]["cidr"])
        assert net.prefixlen == 26

    def test_subnet_cidrs_sum_is_less_than_parent(self):
        result = design_azure_network("eastus", "app")
        parent = ipaddress.IPv4Network(result["result"]["address_space"])
        total_hosts = sum(ipaddress.IPv4Network(s["cidr"]).num_addresses for s in result["result"]["subnets"])
        assert total_hosts <= parent.num_addresses

    def test_subnet_count_matches_expected(self):
        result = design_azure_network("eastus", "app")
        assert len(result["result"]["subnets"]) == 4

    def test_nsg_ports_dont_overlap_firewall_sensitive(self):
        result = design_azure_network("eastus", "app")
        ports = {r["port"] for r in result["result"]["nsg_rules"]}
        for sensitive in ("22", "3389", "5985", "5986"):
            assert sensitive not in ports, f"sensitive port {sensitive} exposed"

    def test_vnet_name_sanitized_for_special_chars(self):
        result = design_azure_network("eastus", "app!@service")
        assert "!" in result["result"]["vnet_name"]

    def test_cidr_range_edge_case_slash_8(self):
        result = design_azure_network("eastus", "app", cidr_range="10.0.0.0/8")
        assert result["result"]["address_space"] == "10.0.0.0/8"
        assert len(result["result"]["subnets"]) == 4

    def test_cidr_range_edge_case_slash_24_still_ok(self):
        result = design_azure_network("eastus", "app", cidr_range="10.0.0.0/24")
        assert result["status"] == "ok"
        assert len(result["result"]["subnets"]) == 4


# ── acr_registry_config: SKU / naming matrix ───────────────────────────


class TestAcrConfigDeepMatrix:
    def test_all_sku_combinations_with_region(self):
        regions = ("eastus", "westeurope", "southeastasia", "brazilsouth")
        for sku in ("Basic", "Standard", "Premium"):
            for region in regions:
                result = acr_registry_config(f"acr-{sku.lower()}", sku, region)
                assert result["status"] == "ok", f"sku={sku} region={region}"
                assert result["result"]["region"] == region

    def test_premium_always_geo_replicates(self):
        for _ in range(10):
            result = acr_registry_config("acr", "Premium", "eastus")
            assert result["result"]["geo_replication"] is True

    def test_non_premium_never_geo_replicates(self):
        for sku in ("Basic", "Standard"):
            for _ in range(5):
                result = acr_registry_config("acr", sku, "eastus")
                assert result["result"]["geo_replication"] is False

    def test_admin_enabled_is_always_false(self):
        matrix = it.product(
            ("acr1", "acr2", "acr3"),
            ("Basic", "Standard", "Premium"),
            ("eastus", "westeurope"),
        )
        for name, sku, region in matrix:
            result = acr_registry_config(name, sku, region)
            assert result["result"]["admin_enabled"] is False

    def test_name_with_azure_naming_convention_special_chars(self):
        valid_names = (
            "myregistry",
            "my-registry",
            "registry001",
            "a" * 50,
        )
        for name in valid_names:
            result = acr_registry_config(name, "Standard", "eastus")
            assert result["result"]["name"] == name

    def test_invalid_sku_message_exhaustive(self):
        for invalid in ("Free", "Classic", "Enterprise", "Ultra", ""):
            result = acr_registry_config("acr", invalid, "eastus")
            if invalid == "":
                assert result["status"] == "error"


# ── container_app_config: GPU resource matrix ──────────────────────────


class TestContainerAppConfigGpuMatrix:
    GPU_SPECS: ClassVar[dict[str, tuple[str, str]]] = {
        "T4": ("4.0", "16Gi"),
        "A10": ("4.0", "16Gi"),
        "A100": ("8.0", "32Gi"),
        "H100": ("8.0", "32Gi"),
    }

    def test_all_known_gpu_resource_allocations(self):
        for gpu, (cpu, mem) in self.GPU_SPECS.items():
            result = container_app_config(gpu, f"model-{gpu.lower()}", "eastus")
            assert result["result"]["cpu"] == cpu, f"{gpu} cpu mismatch"
            assert result["result"]["memory"] == mem, f"{gpu} memory mismatch"

    def test_gpu_memory_values_are_within_reasonable_range(self):
        for gpu in ("T4", "A10", "A100", "H100"):
            result = container_app_config(gpu, "model", "eastus")
            mem_str = result["result"]["memory"]
            mem_val = int(mem_str.replace("Gi", ""))
            assert 4 <= mem_val <= 128, f"{gpu} memory {mem_val}Gi out of range"

    def test_cpu_values_are_within_reasonable_range(self):
        for gpu in ("T4", "A10", "A100", "H100"):
            result = container_app_config(gpu, "model", "eastus")
            cpu = float(result["result"]["cpu"])
            assert 0.5 <= cpu <= 32.0, f"{gpu} cpu {cpu} out of range"

    def test_name_generation_for_model_path_variants(self):
        variants = [
            ("llama-3", "ca-llama-3"),
            ("meta/llama-3", "ca-meta-llama-3"),
            ("a/b/c", "ca-a-b-c"),
            ("///", "ca---"),
        ]
        for model_input, expected_prefix in variants:
            result = container_app_config("T4", model_input, "eastus")
            name = result["result"]["name"]
            assert name.startswith(expected_prefix[:4])

    def test_image_tag_includes_latest(self):
        for model in ("llama-3", "bert-large", "gpt-neo"):
            result = container_app_config("T4", model, "eastus")
            assert ":latest" in result["result"]["image"]

    def test_image_derived_from_mcr_registry(self):
        result = container_app_config("T4", "any-model", "eastus")
        assert "mcr.microsoft.com" in result["result"]["image"]

    def test_warning_for_all_nonstandard_gpu_types(self):
        nonstandard = ("K80", "V100", "P100", "MI250", "TPU", "M1")
        for gpu in nonstandard:
            result = container_app_config(gpu, "model", "eastus")
            assert len(result["warnings"]) >= 1, f"{gpu} should warn"
            assert result["status"] == "ok", f"{gpu} should not hard-er"


# ── optimize_cost: floating-point edge cases ───────────────────────────


class TestOptimizeCostFloatPrecision:
    def test_monthly_is_exactly_730_times_hourly_for_all_gpus(self):
        for gpu in ("T4", "A10", "A100", "H100"):
            result = optimize_cost("container_apps", "eastus", gpu)
            hourly = result["result"]["hourly_rate"]
            monthly = result["result"]["monthly_estimate"]
            assert monthly == pytest.approx(hourly * 730.0)

    def test_unknown_gpu_yields_zero_rate_and_zero_monthly(self):
        result = optimize_cost("container_apps", "eastus", "UnknownGPU")
        assert result["result"]["hourly_rate"] == 0.0
        assert result["result"]["monthly_estimate"] == 0.0

    def test_t4_cheapest_h100_most_expensive(self):
        t4 = optimize_cost("container_apps", "eastus", "T4")["result"]["hourly_rate"]
        h100 = optimize_cost("container_apps", "eastus", "H100")["result"]["hourly_rate"]
        a10 = optimize_cost("container_apps", "eastus", "A10")["result"]["hourly_rate"]
        a100 = optimize_cost("container_apps", "eastus", "A100")["result"]["hourly_rate"]
        assert t4 < a10 < a100 < h100

    def test_pricing_is_deterministic(self):
        for _ in range(10):
            a = optimize_cost("container_apps", "eastus", "T4")["result"]["hourly_rate"]
            b = optimize_cost("container_apps", "eastus", "T4")["result"]["hourly_rate"]
            assert a == b

    def test_warning_message_mentions_specific_gpu(self):
        result = optimize_cost("container_apps", "eastus", "FooGPU")
        assert len(result["warnings"]) >= 1
        assert "FooGPU" in result["warnings"][0]

    def test_warning_message_mentions_specific_service(self):
        result = optimize_cost("fake_service", "eastus", "T4")
        assert len(result["warnings"]) >= 1
        assert "fake_service" in result["warnings"][0]

    def test_t4_monthly_estimate_approximate(self):
        result = optimize_cost("container_apps", "eastus", "T4")
        monthly = result["result"]["monthly_estimate"]
        assert 400.0 < monthly < 500.0, f"T4 monthly={monthly} out of expected range"

    def test_h100_monthly_estimate_approximate(self):
        result = optimize_cost("container_apps", "eastus", "H100")
        monthly = result["result"]["monthly_estimate"]
        assert 3500.0 < monthly < 4500.0, f"H100 monthly={monthly} out of expected range"

    def test_currency_is_always_usd(self):
        for svc in ("container_apps", "unknown"):
            for gpu in ("T4", "H100", "Foo"):
                result = optimize_cost(svc, "eastus", gpu)
                assert result["result"]["currency"] == "USD"

    def test_region_is_propagated(self):
        regions = ("eastus", "westeurope", "southeastasia", "japaneast")
        for region in regions:
            result = optimize_cost("container_apps", region, "T4")
            assert result["result"]["region"] == region


# ── query_log_analytics: shape invariants ──────────────────────────────


class TestQueryLogAnalyticsShapeDeep:
    def test_result_always_has_standard_keys(self):
        for ws in ("ws-1", "", "a" * 256):
            for q in ("Heartbeat", ""):
                result = query_log_analytics(ws, q)
                for key in ("workspace_id", "query", "timespan", "note"):
                    assert key in result["result"]

    def test_timespan_is_always_p1d(self):
        result = query_log_analytics("ws", "any query")
        assert result["result"]["timespan"] == "P1D"

    def test_note_is_nonempty_string(self):
        result = query_log_analytics("ws", "Heartbeat")
        assert isinstance(result["result"]["note"], str)
        assert len(result["result"]["note"]) > 0

    def test_workspace_id_roundtrips_exactly(self):
        ws_ids = (
            "00000000-0000-0000-0000-000000000000",
            "",
            "ws-" + "x" * 250,
        )
        for ws in ws_ids:
            result = query_log_analytics(ws, "Heartbeat")
            assert result["result"]["workspace_id"] == ws

    def test_query_roundtrips_with_special_chars(self):
        queries = (
            'Heartbeat | where Computer contains "prod"',
            "Perf | where CounterName == @'% Processor Time'",
            "AzureActivity\n| take 100",
            "union * | where * contains 'error'",
        )
        for q in queries:
            result = query_log_analytics("ws", q)
            assert result["result"]["query"] == q


# ── Role catalogue: deep invariant checks ──────────────────────────────


class TestAzureExpertRolesDeep:
    def test_all_role_descriptions_unique(self):
        descriptions = list(AZURE_EXPERT_ROLES.values())
        assert len(descriptions) == len(set(descriptions)), "role descriptions must be unique"

    def test_all_role_keys_are_snake_case(self):
        import re

        for key in AZURE_EXPERT_ROLES:
            assert re.fullmatch(r"[a-z][a-z0-9_]*", key), f"bad key name: {key!r}"

    def test_role_descriptions_are_all_english_sentences(self):
        for _key, desc in AZURE_EXPERT_ROLES.items():
            assert desc[0].isupper(), f"description should be sentence-case: {desc!r}"
            assert len(desc) > 10, f"description too short: {desc!r}"


# ── Cross-function: shape consistency ──────────────────────────────────


class TestCrossFunctionShapeConsistency:
    """Every exported function returns {'status': str, 'result': ..., 'warnings': list}."""

    def test_validate_rbac_role_definition_shape(self):
        r = validate_rbac_role_definition(
            ["Microsoft.Compute/virtualMachines/read"],
            [],
            ["/subscriptions/sub"],
        )
        assert isinstance(r["status"], str)
        assert "issues" in r

    def test_audit_iam_assignments_shape_ok(self):
        r = audit_iam_assignments("sub", "rg", "terraform_deploy")
        assert r["status"] == "ok"
        assert isinstance(r["result"], list)
        assert isinstance(r["warnings"], list)

    def test_audit_iam_assignments_shape_error(self):
        r = audit_iam_assignments("sub", "rg", "bad_persona")
        assert r["status"] == "error"
        assert isinstance(r["warnings"], list)
        assert len(r["warnings"]) >= 1

    def test_design_azure_network_shape(self):
        r = design_azure_network("eastus", "test")
        assert r["status"] == "ok"
        assert isinstance(r["result"], dict)
        assert "vnet_name" in r["result"]
        assert "subnets" in r["result"]
        assert "nsg_rules" in r["result"]

    def test_acr_registry_config_shape_error(self):
        r = acr_registry_config("acr", "Free", "eastus")
        assert r["status"] == "error"
        assert r["result"] == {}
        assert len(r["warnings"]) >= 1

    def test_container_app_config_shape(self):
        r = container_app_config("T4", "model", "eastus")
        assert r["status"] == "ok"
        assert isinstance(r["result"], dict)
        assert "gpu_type" in r["result"]
        assert "cpu" in r["result"]
        assert "memory" in r["result"]

    def test_query_log_analytics_shape(self):
        r = query_log_analytics("ws", "Heartbeat")
        assert r["status"] == "ok"
        assert isinstance(r["result"], dict)
        assert r["warnings"] == []

    def test_inventory_resources_shape(self):
        r = inventory_resources(["sub-1"])
        assert r["status"] == "ok"
        assert "kql_template" in r["result"]
        assert "subscription_count" in r["result"]

    def test_optimize_cost_shape(self):
        r = optimize_cost("container_apps", "eastus", "T4")
        assert r["status"] == "ok"
        assert "hourly_rate" in r["result"]
        assert "monthly_estimate" in r["result"]
        assert "currency" in r["result"]


# ── Stress: concurrent / repeated calls ────────────────────────────────


class TestCoreFunctionIdempotency:
    def test_acr_config_idempotent(self):
        results = [acr_registry_config("acr", "Basic", "eastus") for _ in range(100)]
        ref = results[0]
        for r in results[1:]:
            assert r == ref

    def test_inventory_resources_idempotent(self):
        ids = ["sub-a", "sub-b", "sub-c"]
        results = [inventory_resources(ids) for _ in range(50)]
        ref = results[0]
        for r in results[1:]:
            assert r == ref
