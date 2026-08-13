"""Azure expert data contracts using dataclasses.

All contracts carry type-annotated fields with sensible defaults so they
serialise cleanly to JSON and are safe to construct with partial kwargs.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AzureRbacRole:
    name: str = ""
    description: str = ""
    actions: list[str] = field(default_factory=list)
    not_actions: list[str] = field(default_factory=list)
    data_actions: list[str] = field(default_factory=list)
    assignable_scopes: list[str] = field(default_factory=list)


@dataclass
class IamAssignment:
    persona: str = ""
    role_name: str = ""
    scope: str = ""
    is_builtin: bool = True


@dataclass
class NetworkDesign:
    vnet_name: str = ""
    address_space: str = ""
    subnets: list[dict[str, str]] = field(default_factory=list)
    nsg_rules: list[dict[str, str]] = field(default_factory=list)


@dataclass
class AcrConfig:
    name: str = ""
    sku: str = ""
    admin_enabled: bool = False
    region: str = ""


@dataclass
class ContainerAppDeployConfig:
    name: str = ""
    image: str = ""
    cpu: str = ""
    memory: str = ""
    gpu_type: str = ""
    min_replicas: int = 0


@dataclass
class LogAnalyticsQuery:
    workspace_id: str = ""
    query: str = ""
    timespan: str = "P1D"


@dataclass
class PricingResult:
    service_type: str = ""
    region: str = ""
    hourly_rate: float = 0.0
    monthly_estimate: float = 0.0


__all__ = [
    "AcrConfig",
    "AzureRbacRole",
    "ContainerAppDeployConfig",
    "IamAssignment",
    "LogAnalyticsQuery",
    "NetworkDesign",
    "PricingResult",
]
