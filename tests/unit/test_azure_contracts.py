"""Unit tests for ``general_ludd.azure.contracts`` — behaviour, edges, and
serialisation roundtrips.

Covers: instantiation, equality, default-factory isolation, JSON roundtrip,
immutability, edge values (empty/zero/negative/long), ``__all__`` completeness,
and ``asdict`` fidelity.
"""

from __future__ import annotations

import json
from dataclasses import asdict, fields
from typing import get_type_hints

import pytest

from general_ludd.azure.contracts import (
    AcrConfig,
    AzureRbacRole,
    ContainerAppDeployConfig,
    IamAssignment,
    LogAnalyticsQuery,
    NetworkDesign,
    PricingResult,
    __all__,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ALL_CLASSES = [
    AzureRbacRole,
    IamAssignment,
    NetworkDesign,
    AcrConfig,
    ContainerAppDeployConfig,
    LogAnalyticsQuery,
    PricingResult,
]


def _roundtrip(obj: object) -> dict:
    """json.dumps / loads via ``asdict``."""
    return json.loads(json.dumps(asdict(obj)))


# ---------------------------------------------------------------------------
# AzureRbacRole
# ---------------------------------------------------------------------------


class TestAzureRbacRole:
    def test_instantiates_minimal(self):
        role = AzureRbacRole(name="Reader", description="Read-only")
        assert role.name == "Reader"
        assert role.description == "Read-only"
        assert role.actions == []
        assert role.not_actions == []
        assert role.data_actions == []
        assert role.assignable_scopes == []

    def test_instantiates_full(self):
        role = AzureRbacRole(
            name="Contributor",
            description="Full access",
            actions=["*"],
            not_actions=["Microsoft.Authorization/*/Write"],
            data_actions=["Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read"],
            assignable_scopes=["/subscriptions/sub-id"],
        )
        assert len(role.actions) == 1
        assert len(role.not_actions) == 1
        assert len(role.data_actions) == 1
        assert len(role.assignable_scopes) == 1

    def test_default_lists_are_isolated_per_instance(self):
        a = AzureRbacRole(name="A", description="", actions=["read"])
        b = AzureRbacRole(name="B", description="")
        assert b.actions == []
        assert id(a.actions) != id(b.actions)
        assert id(a.not_actions) != id(b.not_actions)
        assert id(a.data_actions) != id(b.data_actions)
        assert id(a.assignable_scopes) != id(b.assignable_scopes)

    def test_equality_value_based(self):
        a = AzureRbacRole(name="X", description="Y", actions=["a"])
        b = AzureRbacRole(name="X", description="Y", actions=["a"])
        c = AzureRbacRole(name="X", description="Y", actions=["b"])
        assert a == b
        assert a != c
        assert a != (1, 2)  # cross-type

    def test_is_not_frozen(self):
        role = AzureRbacRole(name="X", description="Y")
        role.actions.append("read")
        assert role.actions == ["read"]

    def test_json_roundtrip(self):
        role = AzureRbacRole(
            name="Reader",
            description="Read-only",
            actions=["*/read"],
        )
        d = _roundtrip(role)
        assert d["name"] == "Reader"
        assert d["actions"] == ["*/read"]
        assert d["not_actions"] == []

    def test_empty_strings_ok(self):
        role = AzureRbacRole(name="", description="")
        assert role.name == ""
        assert role.description == ""

    def test_type_hints_present(self):
        hints = get_type_hints(AzureRbacRole)
        for field_name in ("name", "description", "actions", "not_actions", "data_actions", "assignable_scopes"):
            assert field_name in hints, f"missing hint for {field_name}"


# ---------------------------------------------------------------------------
# IamAssignment
# ---------------------------------------------------------------------------


class TestIamAssignment:
    def test_instantiates_minimal(self):
        a = IamAssignment(persona="developer", role_name="Contributor", scope="/subscriptions/x")
        assert a.persona == "developer"
        assert a.role_name == "Contributor"
        assert a.scope == "/subscriptions/x"
        assert a.is_builtin is True

    def test_is_builtin_default(self):
        a = IamAssignment(persona="admin", role_name="Owner", scope="/")
        assert a.is_builtin is True

    def test_custom_role(self):
        a = IamAssignment(
            persona="bot",
            role_name="Custom-Auditor",
            scope="/",
            is_builtin=False,
        )
        assert a.is_builtin is False

    def test_equality(self):
        a = IamAssignment("x", "y", "/z")
        b = IamAssignment("x", "y", "/z")
        c = IamAssignment("x", "y", "/z", is_builtin=False)
        assert a == b
        assert a != c
        assert a is not None

    def test_json_roundtrip(self):
        a = IamAssignment("dev", "Reader", "/sub/x", is_builtin=True)
        d = _roundtrip(a)
        assert d["persona"] == "dev"
        assert d["is_builtin"] is True

    def test_edge_characters_in_scope(self):
        special = "/subscriptions/my-id/resourceGroups/my-rg/providers/Microsoft.Compute/virtualMachines/my-vm"
        a = IamAssignment("u", "r", special)
        assert a.scope == special

    def test_empty_string_persona(self):
        a = IamAssignment("", "Reader", "/")
        assert a.persona == ""


# ---------------------------------------------------------------------------
# NetworkDesign
# ---------------------------------------------------------------------------


class TestNetworkDesign:
    def test_instantiates_minimal(self):
        d = NetworkDesign(vnet_name="vnet1", address_space="10.0.0.0/16")
        assert d.vnet_name == "vnet1"
        assert d.address_space == "10.0.0.0/16"
        assert d.subnets == []
        assert d.nsg_rules == []

    def test_subnet_cidrs_are_valid(self):
        from ipaddress import IPv4Network

        d = NetworkDesign(
            vnet_name="vnet1",
            address_space="10.0.0.0/16",
            subnets=[
                {"name": "web", "cidr": "10.0.1.0/24"},
                {"name": "app", "cidr": "10.0.2.0/24"},
            ],
        )
        for s in d.subnets:
            net = IPv4Network(s["cidr"])
            assert net.prefixlen <= 32

    def test_default_lists_isolated(self):
        NetworkDesign("a", "10.0.0.0/8", subnets=[{"name": "s"}])
        b = NetworkDesign("b", "10.1.0.0/8")
        assert b.subnets == []
        assert b.nsg_rules == []

    def test_equality(self):
        a = NetworkDesign("v", "10.0.0.0/8", subnets=[{"n": "s"}])
        b = NetworkDesign("v", "10.0.0.0/8", subnets=[{"n": "s"}])
        c = NetworkDesign("v", "10.0.0.0/8")
        assert a == b
        assert a != c

    def test_json_roundtrip(self):
        d = NetworkDesign(
            vnet_name="hub",
            address_space="172.16.0.0/12",
            subnets=[{"name": "dmz", "cidr": "172.16.0.0/24"}],
            nsg_rules=[{"name": "AllowSSH", "priority": "100"}],
        )
        j = _roundtrip(d)
        assert j["vnet_name"] == "hub"
        assert len(j["subnets"]) == 1
        assert len(j["nsg_rules"]) == 1

    def test_address_space_ipv6(self):
        d = NetworkDesign(vnet_name="v6", address_space="2001:db8::/32")
        assert d.address_space == "2001:db8::/32"

    def test_multiple_subnets_preserved(self):
        subnets = [{"name": f"s{i}", "cidr": f"10.0.{i}.0/24"} for i in range(10)]
        d = NetworkDesign("big", "10.0.0.0/8", subnets=subnets)
        assert len(d.subnets) == 10

    def test_type_hints_present(self):
        hints = get_type_hints(NetworkDesign)
        for field_name in ("vnet_name", "address_space", "subnets", "nsg_rules"):
            assert field_name in hints


# ---------------------------------------------------------------------------
# AcrConfig
# ---------------------------------------------------------------------------


class TestAcrConfig:
    def test_instantiates_minimal(self):
        acr = AcrConfig(name="myRegistry", sku="Standard")
        assert acr.name == "myRegistry"
        assert acr.sku == "Standard"
        assert acr.admin_enabled is False
        assert acr.region == ""

    def test_sku_values(self):
        for sku in ("Basic", "Standard", "Premium"):
            acr = AcrConfig(name="reg", sku=sku)
            assert acr.sku in {"Basic", "Standard", "Premium"}

    def test_equality(self):
        a = AcrConfig("r", "Standard", admin_enabled=True, region="eastus")
        b = AcrConfig("r", "Standard", admin_enabled=True, region="eastus")
        c = AcrConfig("r", "Standard")
        assert a == b
        assert a != c
        assert a != "string"

    def test_json_roundtrip(self):
        acr = AcrConfig(name="prod", sku="Premium", admin_enabled=False, region="westeurope")
        d = _roundtrip(acr)
        assert d["name"] == "prod"
        assert d["sku"] == "Premium"
        assert d["admin_enabled"] is False
        assert d["region"] == "westeurope"

    def test_empty_name_ok(self):
        acr = AcrConfig(name="", sku="Basic")
        assert acr.name == ""

    def test_admin_enabled_true(self):
        acr = AcrConfig(name="r", sku="Basic", admin_enabled=True)
        assert acr.admin_enabled is True

    def test_type_hints_present(self):
        hints = get_type_hints(AcrConfig)
        for field_name in ("name", "sku", "admin_enabled", "region"):
            assert field_name in hints


# ---------------------------------------------------------------------------
# ContainerAppDeployConfig
# ---------------------------------------------------------------------------


class TestContainerAppDeployConfig:
    def test_instantiates_minimal(self):
        cfg = ContainerAppDeployConfig(
            name="myapp",
            image="nginx:latest",
            cpu="0.5",
            memory="1Gi",
        )
        assert cfg.name == "myapp"
        assert cfg.gpu_type == ""
        assert cfg.min_replicas == 0

    def test_gpu_configured(self):
        cfg = ContainerAppDeployConfig(
            name="gpu-app",
            image="ml:v2",
            cpu="4",
            memory="16Gi",
            gpu_type="nvidia-a100",
            min_replicas=1,
        )
        assert cfg.gpu_type == "nvidia-a100"
        assert cfg.min_replicas == 1

    def test_equality(self):
        a = ContainerAppDeployConfig("x", "i", "1", "2G", min_replicas=3)
        b = ContainerAppDeployConfig("x", "i", "1", "2G", min_replicas=3)
        c = ContainerAppDeployConfig("x", "i", "1", "2G")
        assert a == b
        assert a != c

    def test_json_roundtrip(self):
        cfg = ContainerAppDeployConfig(
            name="frontend",
            image="ghcr.io/x/app:1.2.3",
            cpu="2",
            memory="4Gi",
            min_replicas=3,
        )
        d = _roundtrip(cfg)
        assert d["image"] == "ghcr.io/x/app:1.2.3"
        assert d["min_replicas"] == 3
        assert d["gpu_type"] == ""

    def test_negative_min_replicas_not_rejected_by_dataclass(self):
        cfg = ContainerAppDeployConfig(
            name="bad",
            image="i",
            cpu="1",
            memory="1G",
            min_replicas=-1,
        )
        assert cfg.min_replicas == -1

    def test_empty_gpu(self):
        cfg = ContainerAppDeployConfig(
            name="n",
            image="i",
            cpu="1",
            memory="2G",
            gpu_type="",
        )
        assert cfg.gpu_type == ""


# ---------------------------------------------------------------------------
# LogAnalyticsQuery
# ---------------------------------------------------------------------------


class TestLogAnalyticsQuery:
    def test_instantiates_minimal(self):
        q = LogAnalyticsQuery(workspace_id="ws-1", query="Heartbeat")
        assert q.workspace_id == "ws-1"
        assert q.query == "Heartbeat"
        assert q.timespan == "P1D"

    def test_custom_timespan(self):
        q = LogAnalyticsQuery(
            workspace_id="ws-2",
            query="AppRequests",
            timespan="PT12H",
        )
        assert q.timespan == "PT12H"

    def test_equality(self):
        a = LogAnalyticsQuery("w", "q", "P1D")
        b = LogAnalyticsQuery("w", "q", "P1D")
        c = LogAnalyticsQuery("w", "q", "P7D")
        assert a == b
        assert a != c

    def test_json_roundtrip(self):
        q = LogAnalyticsQuery(
            workspace_id="089bd233",
            query="AzureActivity | take 10",
            timespan="P7D",
        )
        d = _roundtrip(q)
        assert d["workspace_id"] == "089bd233"
        assert d["timespan"] == "P7D"

    def test_long_kql_query(self):
        long_q = " | join ".join(["AzureActivity" for _ in range(100)])
        q = LogAnalyticsQuery(workspace_id="w", query=long_q)
        assert q.query == long_q

    def test_empty_workspace_id(self):
        q = LogAnalyticsQuery(workspace_id="", query="Heartbeat")
        assert q.workspace_id == ""

    def test_type_hints_present(self):
        hints = get_type_hints(LogAnalyticsQuery)
        for field_name in ("workspace_id", "query", "timespan"):
            assert field_name in hints


# ---------------------------------------------------------------------------
# PricingResult
# ---------------------------------------------------------------------------


class TestPricingResult:
    def test_instantiates_full(self):
        pr = PricingResult(
            service_type="Virtual Machines",
            region="eastus",
            hourly_rate=0.10,
            monthly_estimate=72.00,
        )
        assert pr.service_type == "Virtual Machines"
        assert pr.region == "eastus"
        assert pr.hourly_rate == 0.10
        assert pr.monthly_estimate == 72.00

    def test_equality(self):
        a = PricingResult("VM", "eastus", 0.1, 72.0)
        b = PricingResult("VM", "eastus", 0.1, 72.0)
        c = PricingResult("VM", "eastus", 0.2, 72.0)
        assert a == b
        assert a != c

    def test_json_roundtrip(self):
        pr = PricingResult("SQL DB", "westeurope", 0.5, 365.0)
        d = _roundtrip(pr)
        assert d["service_type"] == "SQL DB"
        assert pytest.approx(d["hourly_rate"]) == 0.5
        assert pytest.approx(d["monthly_estimate"]) == 365.0

    def test_zero_hourly_rate(self):
        pr = PricingResult("Free Tier", "eastus", 0.0, 0.0)
        assert pr.hourly_rate == 0.0
        assert pr.monthly_estimate == 0.0

    def test_float_precision_roundtrip(self):
        pr = PricingResult("VM", "eastus", 0.000123456, 0.089123456)
        d = _roundtrip(pr)
        assert pytest.approx(d["hourly_rate"], rel=1e-6) == 0.000123456
        assert pytest.approx(d["monthly_estimate"], rel=1e-6) == 0.089123456

    def test_negative_monthly_not_rejected(self):
        pr = PricingResult("Credit", "eastus", -0.10, -72.0)
        assert pr.hourly_rate < 0
        assert pr.monthly_estimate < 0

    def test_empty_service_type(self):
        pr = PricingResult("", "eastus", 0.0, 0.0)
        assert pr.service_type == ""

    def test_type_hints_present(self):
        hints = get_type_hints(PricingResult)
        for field_name in ("service_type", "region", "hourly_rate", "monthly_estimate"):
            assert field_name in hints


# ---------------------------------------------------------------------------
# Cross-cutting — ``__all__``
# ---------------------------------------------------------------------------


class TestModuleExports:
    def test_all_is_a_list_of_strings(self):
        assert isinstance(__all__, list)
        assert all(isinstance(name, str) for name in __all__)

    def test_all_covers_every_dataclass(self):
        exported = set(__all__)
        for cls in ALL_CLASSES:
            assert cls.__name__ in exported, f"{cls.__name__} not in __all__"

    def test_all_has_no_extra_names(self):
        exported = set(__all__)
        declared = {cls.__name__ for cls in ALL_CLASSES}
        extra = exported - declared
        assert not extra, f"stale __all__ entries: {extra}"


# ---------------------------------------------------------------------------
# Cross-cutting — ``asdict`` fidelity
# ---------------------------------------------------------------------------


class TestAsdictFidelity:
    def test_asdict_returns_all_fields(self):
        cfg = ContainerAppDeployConfig(
            name="x",
            image="y",
            cpu="1",
            memory="2G",
            gpu_type="nvidia",
            min_replicas=2,
        )
        d = asdict(cfg)
        assert set(d.keys()) == {
            "name",
            "image",
            "cpu",
            "memory",
            "gpu_type",
            "min_replicas",
        }

    @pytest.mark.parametrize("cls", ALL_CLASSES)
    def test_asdict_then_reconstruct(self, cls):
        hints = get_type_hints(cls)
        cls_field_names = {f.name for f in fields(cls) if f.default is not ...}
        required_fields = {f for f in hints if f not in cls_field_names}
        kwargs: dict = {}
        for f, t in hints.items():
            origin = getattr(t, "__origin__", None)
            if origin is list:
                kwargs[f] = []
            elif f in required_fields and origin is None and t is str:
                kwargs[f] = "x"
            elif f in required_fields and origin is None and t is float:
                kwargs[f] = 0.0
            elif f in required_fields and origin is None and t is int:
                kwargs[f] = 0
        instance = cls(**kwargs)
        d = asdict(instance)
        reconstructed = cls(**d)
        assert instance == reconstructed, f"{cls.__name__} roundtrip broken"
