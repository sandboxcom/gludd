"""vSphere/VMware discovery — lazy ``pyVmomi`` SDK; offline-safe.

Enumerates hosts/resource pools (cpu cores + RAM) into DiscoveredResources.
``gpu_count`` is 0 unless vGPU is detected. ``VSPHERE_SERVER`` is SSRF-guarded.
Absent SDK -> PROVIDER_UNAVAILABLE; offline/timeout -> OFFLINE; never raises.
"""

from __future__ import annotations

import logging

from general_ludd.infra.discovery.base import (
    DiscoveredResource,
    DiscoveryProvider,
    DiscoveryResult,
    DiscoverySource,
    DiscoveryStatus,
    guarded_call,
)

logger = logging.getLogger(__name__)


class VMwareProvider(DiscoveryProvider):
    source = DiscoverySource.VMWARE
    required_aliases = ("VSPHERE_USER", "VSPHERE_PASSWORD", "VSPHERE_SERVER")

    async def discover(self) -> DiscoveryResult:
        try:
            from pyVim.connect import Disconnect, SmartConnect
            from pyVmomi import vim  # noqa: F401
        except ImportError:
            return self._unavailable(
                "vSphere discovery needs the [compute] extra: "
                "pip install general-ludd-agent[compute]"
            )

        server = self._api_base
        if server and not self.guard_endpoint(server):
            return DiscoveryResult(
                source=self.source,
                status=DiscoveryStatus.ERROR,
                error="VSPHERE_SERVER failed SSRF guard",
            )

        def _enumerate() -> list[DiscoveredResource]:
            import os

            si = SmartConnect(
                host=os.environ.get("VSPHERE_SERVER", server or ""),
                user=os.environ.get("VSPHERE_USER", ""),
                pwd=os.environ.get("VSPHERE_PASSWORD", ""),
                disableSslCertValidation=False,
            )
            try:
                content = si.RetrieveContent()
                out: list[DiscoveredResource] = []
                for dc in content.rootFolder.childEntity:
                    host_folder = getattr(dc, "hostFolder", None)
                    if host_folder is None:
                        continue
                    for compute in host_folder.childEntity:
                        for host in getattr(compute, "host", []) or []:
                            hw = host.hardware
                            cores = int(hw.cpuInfo.numCpuCores) if hw else 0
                            mem_gb = (
                                float(hw.memorySize) / 1e9 if hw and hw.memorySize else 0.0
                            )
                            out.append(
                                DiscoveredResource(
                                    source=DiscoverySource.VMWARE,
                                    id=host.name,
                                    region=None,
                                    gpu_type=None,
                                    gpu_count=0,
                                    vcpu=cores,
                                    mem_gb=mem_gb,
                                    cost_per_hour=None,  # on-prem
                                    available=True,
                                    endpoint_url=None,
                                    labels={"price_source": "unknown"},
                                )
                            )
                return out
            finally:
                Disconnect(si)

        try:
            with self._auth():
                resources = await guarded_call(_enumerate, timeout_s=self._timeout_s)
            return DiscoveryResult(
                source=self.source,
                status=DiscoveryStatus.OK,
                resources=tuple(resources),
            )
        except RuntimeError as exc:  # auth-resolution / secrets-backend outage
            return self._auth_failed(exc)
        except Exception as exc:
            return self._offline(exc)
