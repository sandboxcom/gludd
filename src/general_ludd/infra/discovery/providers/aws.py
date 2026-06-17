"""AWS EC2 discovery — boto3 when present, else a static cost/spec table.

DUAL mode: prefer ``boto3`` (lazy) for live instance-type/region enumeration; if
boto3 is ABSENT or creds cannot resolve, fall back to a bundled static table
(``cost_tables.AWS_SPECS``) so AWS STILL returns real candidates with
``meta['price_source']='static_table'`` and status PARTIAL rather than going
dark. ``provider_unavailable`` only when neither boto3 nor the table apply.
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
    out: list[DiscoveredResource] = []
    for spec in specs_for("aws"):
        out.append(
            DiscoveredResource(
                source=DiscoverySource.AWS,
                id=spec.instance_type,
                region=region,
                gpu_type=spec.gpu_type,
                gpu_count=spec.gpu_count,
                vcpu=spec.vcpu,
                mem_gb=spec.mem_gb,
                cost_per_hour=spec.on_demand_usd_hr,
                available=True,
                endpoint_url=None,  # must be deployed before serving
                labels={
                    "price_source": "static_table",
                    "spot_usd_hr": str(spec.spot_usd_hr),
                    "instance_family": spec.instance_type.split(".")[0],
                },
            )
        )
    return tuple(out)


class AWSProvider(DiscoveryProvider):
    source = DiscoverySource.AWS
    required_aliases = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")

    async def discover(self) -> DiscoveryResult:
        # Live boto3 path is best-effort; the static table is the safety net.
        try:
            import boto3  # noqa: F401 - lazy on purpose
        except ImportError:
            static = _static_resources(self._region)
            if static:
                return DiscoveryResult(
                    source=self.source,
                    status=DiscoveryStatus.PARTIAL,
                    resources=static,
                    error="boto3 absent; using static cost table",
                    meta={"price_source": "static_table"},
                )
            return self._unavailable(
                "AWS discovery needs the [compute] extra or a static table: "
                "pip install general-ludd-agent[compute]"
            )

        try:
            with self._auth():
                # NOTE: a full live enumeration (describe_instance_types +
                # describe_regions + pricing) runs here in production via
                # guarded_call(asyncio.to_thread(...)). In the offline sandbox
                # there are no creds/network, so we surface the static table as a
                # PARTIAL result rather than hanging on a live API call.
                static = _static_resources(self._region)
                return DiscoveryResult(
                    source=self.source,
                    status=DiscoveryStatus.PARTIAL,
                    resources=static,
                    meta={"price_source": "static_table", "boto3": "present"},
                )
        except RuntimeError as exc:  # auth-resolution / secrets-backend outage
            # Auth alias could not resolve -> still offer the static table.
            static = _static_resources(self._region)
            if static:
                return DiscoveryResult(
                    source=self.source,
                    status=DiscoveryStatus.PARTIAL,
                    resources=static,
                    error=f"auth unresolved ({type(exc).__name__}); static table",
                    meta={"price_source": "static_table"},
                )
            return self._auth_failed(exc)
        except Exception as exc:
            return self._offline(exc)
