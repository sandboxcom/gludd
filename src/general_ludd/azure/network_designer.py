"""Azure network designer — VNet/subnet layout and NSG rule generation."""

from __future__ import annotations

import ipaddress

from general_ludd.azure.contracts import NetworkDesign

DEFAULT_CIDR = "10.0.0.0/16"

# Default subnet allocation: split a /16 into /24 subnets.
_SUBNET_PREFIX = 24
_SUBNET_DEFAULTS = [
    ("snet-web", "web-tier"),
    ("snet-app", "app-tier"),
    ("snet-data", "data-tier"),
    ("snet-mgmt", "management"),
    ("AzureBastionSubnet", "bastion"),
    ("GatewaySubnet", "gateway"),
]

_NSG_WEB_RULES: list[dict[str, str]] = [
    {
        "name": "AllowHTTPInbound",
        "priority": "100",
        "direction": "Inbound",
        "protocol": "Tcp",
        "source_port": "*",
        "destination_port": "80",
        "source": "Internet",
        "destination": "*",
        "access": "Allow",
    },
    {
        "name": "AllowHTTPSInbound",
        "priority": "110",
        "direction": "Inbound",
        "protocol": "Tcp",
        "source_port": "*",
        "destination_port": "443",
        "source": "Internet",
        "destination": "*",
        "access": "Allow",
    },
    {
        "name": "DenyAllInbound",
        "priority": "4096",
        "direction": "Inbound",
        "protocol": "*",
        "source_port": "*",
        "destination_port": "*",
        "source": "*",
        "destination": "*",
        "access": "Deny",
    },
]


def design_vnet(
    vnet_name: str,
    address_space: str = DEFAULT_CIDR,
    num_subnets: int = 4,
) -> NetworkDesign:
    """Design a VNet with *num_subnets* non-overlapping /24 subnets."""
    if not address_space:
        address_space = DEFAULT_CIDR

    network = ipaddress.IPv4Network(address_space)
    subnets_iter = network.subnets(new_prefix=_SUBNET_PREFIX)

    subnets: list[dict[str, str]] = []
    for idx in range(num_subnets):
        cidr = next(subnets_iter, None)
        if cidr is None:
            break
        name, purpose = _SUBNET_DEFAULTS[idx % len(_SUBNET_DEFAULTS)]
        subnets.append({"name": name, "purpose": purpose, "cidr": str(cidr)})

    return NetworkDesign(
        vnet_name=vnet_name,
        address_space=address_space,
        subnets=subnets,
        nsg_rules=generate_nsg_rules(),
    )


def generate_nsg_rules() -> list[dict[str, str]]:
    """Generate a set of default NSG rules for a web-application VNet."""
    return list(_NSG_WEB_RULES)
