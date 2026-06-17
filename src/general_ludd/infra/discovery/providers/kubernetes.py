"""Kubernetes node discovery — lazy ``kubernetes`` SDK; offline-safe.

Lists nodes and reads ``status.allocatable`` (cpu, memory, nvidia.com/gpu) into
one :class:`DiscoveredResource` per node. ``cost_per_hour`` is None (on-prem)
unless a cost label is present. The API-server URL is SSRF-guarded. Absent SDK
-> PROVIDER_UNAVAILABLE; offline/timeout -> OFFLINE; never raises/hangs.
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


def _parse_cpu(raw: str | None) -> int:
    if not raw:
        return 0
    raw = raw.strip()
    try:
        if raw.endswith("m"):
            return max(0, int(float(raw[:-1]) / 1000.0))
        return int(float(raw))
    except (ValueError, TypeError):
        return 0


def _parse_mem_gb(raw: str | None) -> float:
    if not raw:
        return 0.0
    raw = raw.strip()
    units = {"Ki": 1 / 1e6, "Mi": 1 / 1e3, "Gi": 1.0, "Ti": 1e3}
    for suffix, factor in units.items():
        if raw.endswith(suffix):
            try:
                return float(raw[: -len(suffix)]) * factor
            except (ValueError, TypeError):
                return 0.0
    try:
        return float(raw) / 1e9  # bytes -> GB
    except (ValueError, TypeError):
        return 0.0


class KubernetesProvider(DiscoveryProvider):
    source = DiscoverySource.KUBERNETES
    required_aliases = ("KUBECONFIG",)

    async def discover(self) -> DiscoveryResult:
        try:
            from kubernetes import client, config
        except ImportError:
            return self._unavailable(
                "Kubernetes discovery needs the [compute] extra: "
                "pip install general-ludd-agent[compute]"
            )

        if self._api_base and not self.guard_endpoint(self._api_base):
            return DiscoveryResult(
                source=self.source,
                status=DiscoveryStatus.ERROR,
                error="api_base failed SSRF guard",
            )

        def _list_nodes() -> list[DiscoveredResource]:
            try:
                config.load_kube_config()
            except Exception:
                config.load_incluster_config()
            v1 = client.CoreV1Api()
            nodes = v1.list_node()
            out: list[DiscoveredResource] = []
            for node in nodes.items:
                alloc = (node.status.allocatable or {}) if node.status else {}
                labels = (node.metadata.labels or {}) if node.metadata else {}
                gpu_count = int(alloc.get("nvidia.com/gpu", 0) or 0)
                cost_label = labels.get("gludd.io/cost-per-hour")
                cost = None
                if cost_label:
                    try:
                        cost = float(cost_label)
                    except (ValueError, TypeError):
                        cost = None
                out.append(
                    DiscoveredResource(
                        source=self.source,
                        id=node.metadata.name if node.metadata else "node",
                        region=labels.get("topology.kubernetes.io/region"),
                        gpu_type=None,
                        gpu_count=gpu_count,
                        vcpu=_parse_cpu(alloc.get("cpu")),
                        mem_gb=_parse_mem_gb(alloc.get("memory")),
                        cost_per_hour=cost,
                        available=True,
                        endpoint_url=None,
                        labels={"price_source": "label" if cost else "unknown"},
                    )
                )
            return out

        try:
            with self._auth():
                resources = await guarded_call(_list_nodes, timeout_s=self._timeout_s)
            return DiscoveryResult(
                source=self.source,
                status=DiscoveryStatus.OK,
                resources=tuple(resources),
            )
        except RuntimeError as exc:  # auth-resolution / secrets-backend outage
            return self._auth_failed(exc)
        except Exception as exc:
            return self._offline(exc)
