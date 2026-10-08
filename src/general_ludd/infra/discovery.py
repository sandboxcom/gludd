"""vSphere inventory and compute resource discovery — lazy, fail-safe, optional."""

from __future__ import annotations

import contextlib
import logging
import os
import ssl
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Compute resource discovery (D.5 - auto-select provider for GPU deployments)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DiscoveredResource:
    """Normalized capacity returned by a local or injected provider probe.

    The original probe fields remain backward compatible.  The optional
    identity, availability, endpoint, region, and labels fields let the
    budget-aware orchestration layer reason about live resources without
    forcing provider SDK dependencies into this module.
    """

    provider: str
    kind: str
    id: str = ""
    cpu: float = 0.0
    mem_gb: float = 0.0
    gpu: str = ""
    gpu_count: int = 0
    cost_per_hour: float | None = 0.0
    available: bool = True
    endpoint_url: str | None = None
    region: str | None = None
    labels: dict[str, str] = field(default_factory=dict)

    def label(self) -> str:
        """Return a stable human-readable provider/capacity label."""
        return f"{self.provider}:{self.kind}:{self.gpu or 'cpu-only'}"


class ResourceProbe(Protocol):
    """Synchronous provider probe contract used by discovery orchestration."""

    def probe(self) -> list[DiscoveredResource]:
        """Return the currently discoverable resources for one provider."""
        ...


class LocalProbe:
    """Describe the current process host without external dependencies."""

    def probe(self) -> list[DiscoveredResource]:
        """Return one CPU-only resource representing the local host."""
        cpu = float(os.cpu_count() or 1)
        return [
            DiscoveredResource(
                provider="local",
                kind="process",
                cpu=cpu,
                mem_gb=0.0,
                gpu="",
                gpu_count=0,
                cost_per_hour=0.0,
            )
        ]


class KubernetesProbe:
    """Translate an injected Kubernetes node transport into candidates."""

    def __init__(self, transport: Callable[..., object] | None = None) -> None:
        """Store an optional transport without importing a Kubernetes SDK."""
        self._transport = transport

    def probe(self) -> list[DiscoveredResource]:
        """Return node capacity, degrading to an empty list on transport error."""
        transport = self._transport
        if transport is None:
            return []
        try:
            result = transport("GET", "/api/v1/nodes")
        except Exception:
            logger.warning("Kubernetes node discovery failed", exc_info=True)
            return []

        items: list[object] = []
        if isinstance(result, dict):
            items = list(result.get("items") or [])
        resources: list[DiscoveredResource] = []
        for node in items:
            node_dict: dict[str, object] = {}
            if isinstance(node, dict):
                node_dict = node
            else:
                continue
            status: dict[str, object] = {}
            sn = node_dict.get("status")
            if isinstance(sn, dict):
                status = sn
            caps: dict[str, object] = {}
            cn = status.get("capacity")
            if isinstance(cn, dict):
                caps = cn
            cpu_str = str(caps.get("cpu", "0"))
            mem_str = str(caps.get("memory", "0Ki"))
            gpu_str = str(caps.get("nvidia.com/gpu", "0"))
            cpu_val = _parse_k8s_cpu(cpu_str)
            mem_gb = _parse_k8s_memory_gb(mem_str)
            gpu_count = int(float(gpu_str))
            resources.append(
                DiscoveredResource(
                    provider="kubernetes",
                    kind="node",
                    cpu=cpu_val,
                    mem_gb=mem_gb,
                    gpu="gpu" if gpu_count > 0 else "",
                    gpu_count=gpu_count,
                    cost_per_hour=0.0,
                )
            )
        return resources


def _parse_k8s_cpu(cpu_str: str) -> float:
    if cpu_str.endswith("m"):
        return float(cpu_str[:-1]) / 1000.0
    if cpu_str.endswith("n"):
        return float(cpu_str[:-1]) / 1_000_000_000.0
    return float(cpu_str)


def _parse_k8s_memory_gb(mem_str: str) -> float:
    mem_str = mem_str.strip()
    suffix = mem_str[-2:]
    if suffix == "Ki":
        return float(mem_str[:-2]) / (1024 * 1024)
    if suffix == "Mi":
        return float(mem_str[:-2]) / 1024.0
    if suffix == "Gi":
        return float(mem_str[:-2])
    if suffix == "Ti":
        return float(mem_str[:-2]) * 1024.0
    if suffix == "Pi":
        return float(mem_str[:-2]) * 1024.0 * 1024.0
    return float(mem_str) / (1024 * 1024 * 1024)


def discover_all(
    probes: list[ResourceProbe],
    timeout_s: float = 5.0,
) -> list[DiscoveredResource]:
    """Aggregate synchronous probes while isolating individual failures."""
    all_resources: list[DiscoveredResource] = []
    for probe in probes:
        try:
            all_resources.extend(probe.probe())
        except Exception:
            logger.warning("probe %s failed", type(probe).__name__, exc_info=True)
    return all_resources


def _build_vsphere_ssl_context(verify_ssl: bool) -> ssl.SSLContext | None:
    """Build an explicit vSphere TLS context using maintained public APIs."""
    if verify_ssl:
        return None
    logger.warning(
        "SECURITY: vSphere TLS certificate verification was explicitly disabled"
    )
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


class VSphereProbe:
    """Discover vSphere inventory parameters for Terraform generation.

    Walks the vSphere object hierarchy via pyvmomi to find the first
    datacenter, cluster, datastore and network.  Every method is fail-safe:
    missing pyvmomi, connection errors, and empty inventory all return
    ``None`` rather than raising.
    """

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        port: int = 443,
        verify_ssl: bool = True,
    ) -> None:
        """Store connection inputs without opening a network connection."""
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self.verify_ssl = verify_ssl

    def discover(self) -> dict[str, str] | None:
        """Walk the vSphere inventory and return first-found params.

        Returns a dict with keys ``datacenter``, ``cluster``, ``datastore``,
        ``network``, or ``None`` on any error (missing pyvmomi, connection
        failure, empty inventory).
        """
        try:
            from pyVim.connect import Disconnect, SmartConnect
            from pyVmomi import vim
        except ImportError:
            logger.warning("pyvmomi is not installed; vSphere inventory discovery is unavailable")
            return None

        ssl_context = _build_vsphere_ssl_context(self.verify_ssl)

        si = None
        try:
            si = SmartConnect(
                host=self.host,
                user=self.username,
                pwd=self.password,
                port=self.port,
                sslContext=ssl_context,
            )
        except Exception:
            logger.warning("Failed to connect to vSphere host %s", self.host, exc_info=True)
            return None

        try:
            content = si.RetrieveContent()
            root_folder = content.rootFolder

            datacenter: str | None = None
            cluster: str | None = None
            datastore: str | None = None
            network: str | None = None

            for child in root_folder.childEntity:
                if not isinstance(child, vim.Datacenter):
                    continue
                datacenter = child.name

                dc = child
                for c in dc.hostFolder.childEntity:
                    if isinstance(c, vim.ClusterComputeResource):
                        cluster = c.name
                        break

                for ds in dc.datastoreFolder.childEntity:
                    if isinstance(ds, vim.Datastore):
                        datastore = ds.name
                        break

                for net in dc.networkFolder.childEntity:
                    if isinstance(net, vim.Network):
                        network = net.name
                        break

                break

            if datacenter is None:
                logger.warning("No datacenter found in vSphere inventory")
                return None

            return {
                "datacenter": datacenter,
                "cluster": cluster or "Cluster0",
                "datastore": datastore or "datastore0",
                "network": network or "VM Network",
            }
        finally:
            with contextlib.suppress(Exception):
                Disconnect(si)
