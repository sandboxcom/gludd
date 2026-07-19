"""Security architecture models per platform.

Covers SIP, SELinux, AppArmor, Defender, Gatekeeper, TrustZone,
and their audit/configuration interfaces.
"""

from __future__ import annotations

from enum import Enum
from typing import TypedDict


class SecurityLayer(Enum):
    KERNEL = "kernel"
    MANDATORY_ACCESS = "mandatory_access"
    CODE_SIGNING = "code_signing"
    ANTI_MALWARE = "anti_malware"
    FIREWALL = "firewall"
    TRUSTED_EXECUTION = "trusted_execution"


class SecurityArchitecture(TypedDict):
    platform: str
    layer: str
    name: str
    config_path: str
    audit_command: str


SECURITY_ARCHITECTURES: list[SecurityArchitecture] = []
