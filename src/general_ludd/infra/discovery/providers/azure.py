"""Azure discovery — lazy ``azure-identity`` + ``azure-mgmt-compute``.

Lists VM sizes / resource SKUs; costs come from a static NC/ND-family map
(``cost_tables.AZURE_SPECS``). Absent SDK -> falls back to the static table
(PARTIAL); offline/timeout -> OFFLINE; never raises/hangs.
"""

from __future__ import annotations

import logging

from general_ludd.infra.discovery.base import (
    DiscoveredResource,
    DiscoveryProvider,
    DiscoveryResult,
    DiscoverySource,
    DiscoveryStatus,
)
from general_ludd.infra.discovery.cost_tables import specs_for

logger = logging.getLogger(__name__)


def _static_resources(region: str | None) -> tuple[DiscoveredResource, ...]:
    return tuple(
        DiscoveredResource(
            source=DiscoverySource.AZURE,
            id=spec.instance_type,
            region=region,
            gpu_type=spec.gpu_type,
            gpu_count=spec.gpu_count,
            vcpu=spec.vcpu,
            mem_gb=spec.mem_gb,
            cost_per_hour=spec.on_demand_usd_hr,
            available=True,
            endpoint_url=None,
            labels={"price_source": "static_table", "vm_size": spec.instance_type},
        )
        for spec in specs_for("azure")
    )


class AzureProvider(DiscoveryProvider):
    source = DiscoverySource.AZURE
    required_aliases = (
        "ARM_CLIENT_ID",
        "ARM_CLIENT_SECRET",
        "ARM_TENANT_ID",
        "ARM_SUBSCRIPTION_ID",
    )

    async def discover(self) -> DiscoveryResult:
        try:
            from azure.identity import ClientSecretCredential  # noqa: F401
            from azure.mgmt.compute import ComputeManagementClient  # noqa: F401
        except ImportError:
            static = _static_resources(self._region)
            if static:
                return DiscoveryResult(
                    source=self.source,
                    status=DiscoveryStatus.PARTIAL,
                    resources=static,
                    error="azure SDK absent; static cost table",
                    meta={"price_source": "static_table"},
                )
            return self._unavailable(
                "Azure discovery needs the [compute] extra: "
                "pip install general-ludd-agent[compute]"
            )

        try:
            with self._auth():
                static = _static_resources(self._region)
                return DiscoveryResult(
                    source=self.source,
                    status=DiscoveryStatus.PARTIAL,
                    resources=static,
                    meta={"price_source": "static_table", "sdk": "present"},
                )
        except RuntimeError as exc:  # auth-resolution / secrets-backend outage
            return self._auth_failed(exc)
        except Exception as exc:
            return self._offline(exc)
