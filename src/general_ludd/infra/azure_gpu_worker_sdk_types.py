"""Public value and error types for Azure GPU worker SDK readback."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass


class AzureGpuWorkerSdkError(RuntimeError):
    """Censored official-SDK readback failure."""

    def __init__(self, phase: str) -> None:
        """Retain one stable failure phase without provider response content."""
        self.phase = phase
        super().__init__(f"Azure GPU worker SDK readback failed: {phase}")


@dataclass(frozen=True, slots=True)
class AzureGpuWorkerInstance:
    """One exact VMSS instance and its private controller-routed address."""

    host_id: str
    address: str

    def __post_init__(self) -> None:
        """Require a bounded ARM identifier and one private IPv4 address."""
        if (
            not isinstance(self.host_id, str)
            or not self.host_id.startswith("/subscriptions/")
            or len(self.host_id) > 2_048
            or any(character in self.host_id for character in "\x00\r\n")
        ):
            raise ValueError("host_id must be one bounded ARM identifier")
        try:
            parsed = ipaddress.ip_address(self.address)
        except ValueError:
            raise ValueError("address must be one private IPv4 address") from None
        if parsed.version != 4 or not parsed.is_private:
            raise ValueError("address must be one private IPv4 address")


__all__ = ("AzureGpuWorkerInstance", "AzureGpuWorkerSdkError")
