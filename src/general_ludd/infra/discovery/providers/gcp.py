"""GCP discovery — lazy ``google-cloud-compute`` SDK, static GPU price map.

Lists accelerator-attached machine types; costs come from a small static a2/g2
price map (``cost_tables.GCP_SPECS``) since the Billing Catalog API is heavy.
Absent SDK -> falls back to the static table (PARTIAL); never raises/hangs.
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
            source=DiscoverySource.GCP,
            id=spec.instance_type,
            region=region,
            gpu_type=spec.gpu_type,
            gpu_count=spec.gpu_count,
            vcpu=spec.vcpu,
            mem_gb=spec.mem_gb,
            cost_per_hour=spec.on_demand_usd_hr,
            available=True,
            endpoint_url=None,
            labels={"price_source": "static_table", "machine_family": spec.instance_type},
        )
        for spec in specs_for("gcp")
    )


class GCPProvider(DiscoveryProvider):
    source = DiscoverySource.GCP
    required_aliases = ("GOOGLE_CREDENTIALS",)

    async def discover(self) -> DiscoveryResult:
        try:
            from google.cloud import compute_v1  # noqa: F401 - lazy
        except ImportError:
            static = _static_resources(self._region)
            if static:
                return DiscoveryResult(
                    source=self.source,
                    status=DiscoveryStatus.PARTIAL,
                    resources=static,
                    error="google-cloud-compute absent; static cost table",
                    meta={"price_source": "static_table"},
                )
            return self._unavailable(
                "GCP discovery needs the [compute] extra: "
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
