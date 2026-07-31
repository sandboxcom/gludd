"""Unit tests for ``general_ludd.azure.contracts`` — dataclass instantiation and
value constraints.
"""

from __future__ import annotations

from general_ludd.azure.contracts import (
    AcrConfig,
    AzureRbacRole,
    ContainerAppDeployConfig,
    IamAssignment,
    LogAnalyticsQuery,
    NetworkDesign,
    PricingResult,
)


class TestAzureRbacRole:
    def test_instantiates(self):
        role = AzureRbacRole(name="Reader", description="Read-only")
        assert role.name == "Reader"
        assert role.actions == []
        assert role.not_actions == []

    def test_defaults_are_lists(self):
        role = AzureRbacRole(name="Test", description="")
        assert isinstance(role.actions, list)
        assert isinstance(role.not_actions, list)
        assert isinstance(role.data_actions, list)
        assert isinstance(role.assignable_scopes, list)


class TestIamAssignment:
    def test_instantiates(self):
        a = IamAssignment(persona="developer", role_name="Contributor", scope="/subscriptions/x")
        assert a.persona == "developer"
        assert a.is_builtin is True

    def test_is_builtin_default(self):
        a = IamAssignment(persona="admin", role_name="Owner", scope="/")
        assert a.is_builtin is True


class TestNetworkDesign:
    def test_instantiates(self):
        design = NetworkDesign(vnet_name="vnet1", address_space="10.0.0.0/16")
        assert design.vnet_name == "vnet1"
        assert design.subnets == []
        assert design.nsg_rules == []

    def test_subnet_cidrs_are_valid(self):
        from ipaddress import IPv4Network

        design = NetworkDesign(
            vnet_name="vnet1",
            address_space="10.0.0.0/16",
            subnets=[
                {"name": "web", "cidr": "10.0.1.0/24"},
                {"name": "app", "cidr": "10.0.2.0/24"},
            ],
        )
        for s in design.subnets:
            net = IPv4Network(s["cidr"])
            assert net.prefixlen <= 32


class TestAcrConfig:
    def test_instantiates(self):
        acr = AcrConfig(name="myRegistry", sku="Standard")
        assert acr.name == "myRegistry"
        assert acr.admin_enabled is False

    def test_sku_is_valid(self):
        for sku in ("Basic", "Standard", "Premium"):
            acr = AcrConfig(name="reg", sku=sku)
            assert acr.sku in {"Basic", "Standard", "Premium"}


class TestContainerAppDeployConfig:
    def test_instantiates(self):
        cfg = ContainerAppDeployConfig(name="myapp", image="nginx:latest", cpu="0.5", memory="1Gi")
        assert cfg.name == "myapp"
        assert cfg.image == "nginx:latest"
        assert cfg.gpu_type == ""
        assert cfg.min_replicas == 0


class TestLogAnalyticsQuery:
    def test_instantiates(self):
        q = LogAnalyticsQuery(workspace_id="ws-1", query="Heartbeat")
        assert q.workspace_id == "ws-1"
        assert q.timespan == "P1D"


class TestPricingResult:
    def test_instantiates(self):
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
