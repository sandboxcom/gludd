"""Unit tests for compute resource discovery and auto-select deploy wiring."""

from __future__ import annotations

import pytest

from general_ludd.infra.compute import ComputeProvider, GPUType
from general_ludd.infra.discovery import (
    DiscoveredResource,
    KubernetesProbe,
    LocalProbe,
    _parse_k8s_cpu,
    _parse_k8s_memory_gb,
    discover_all,
)
from general_ludd.infra.providers import ProviderRegistry


class FakeK8sTransport:
    def __call__(self, _method: str, _path: str) -> dict[str, object]:
        return {
            "items": [
                {
                    "metadata": {"name": "gpu-node-1"},
                    "status": {
                        "capacity": {
                            "cpu": "8",
                            "memory": "64Gi",
                            "nvidia.com/gpu": "4",
                        },
                    },
                },
                {
                    "metadata": {"name": "cpu-node-1"},
                    "status": {
                        "capacity": {
                            "cpu": "4",
                            "memory": "32Gi",
                        },
                    },
                },
            ]
        }


class TestDiscoveredResource:
    def test_fields(self):
        r = DiscoveredResource(
            provider="aws", kind="vm", cpu=4.0, mem_gb=16.0,
            gpu="t4", gpu_count=1, cost_per_hour=0.50,
        )
        assert r.provider == "aws"
        assert r.kind == "vm"
        assert r.cpu == 4.0
        assert r.mem_gb == 16.0
        assert r.gpu == "t4"
        assert r.gpu_count == 1
        assert r.cost_per_hour == 0.50

    def test_label(self):
        r = DiscoveredResource(provider="kubernetes", kind="node", gpu="a100", gpu_count=2)
        assert r.label() == "kubernetes:node:a100"

    def test_label_no_gpu(self):
        r = DiscoveredResource(provider="local", kind="process")
        assert r.label() == "local:process:cpu-only"

    def test_defaults(self):
        r = DiscoveredResource(provider="test", kind="test")
        assert r.cpu == 0.0
        assert r.mem_gb == 0.0
        assert r.gpu == ""
        assert r.gpu_count == 0
        assert r.cost_per_hour == 0.0

    def test_frozen(self):
        r = DiscoveredResource(provider="aws", kind="vm")
        with pytest.raises(AttributeError):
            r.provider = "gcp"  # type: ignore[misc]


class TestLocalProbe:
    def test_probe_returns_resource(self):
        r = LocalProbe().probe()
        assert len(r) == 1
        assert r[0].provider == "local"
        assert r[0].kind == "process"
        assert r[0].cpu >= 1
        assert r[0].cost_per_hour == 0.0
        assert r[0].gpu == ""
        assert r[0].gpu_count == 0


class TestKubernetesProbe:
    def test_no_transport_returns_empty(self):
        r = KubernetesProbe().probe()
        assert r == []

    def test_failing_transport_returns_empty(self):
        def fail(_m: str, _p: str) -> None:
            raise ConnectionError("boom")
        probe = KubernetesProbe(transport=fail)
        assert probe.probe() == []

    def test_parses_nodes(self):
        transport = FakeK8sTransport()
        r = KubernetesProbe(transport=transport).probe()
        assert len(r) == 2
        gpu_node = r[0]
        assert gpu_node.provider == "kubernetes"
        assert gpu_node.cpu == 8.0
        assert gpu_node.mem_gb == 64.0
        assert gpu_node.gpu == "gpu"
        assert gpu_node.gpu_count == 4
        cpu_node = r[1]
        assert cpu_node.gpu == ""
        assert cpu_node.gpu_count == 0
        assert cpu_node.cpu == 4.0


class TestParseK8sCpu:
    def test_plain_int(self):
        assert _parse_k8s_cpu("8") == 8.0

    def test_millicores(self):
        assert _parse_k8s_cpu("1500m") == 1.5

    def test_nanocores(self):
        assert _parse_k8s_cpu("1000000000n") == 1.0

    def test_zero(self):
        assert _parse_k8s_cpu("0") == 0.0


class TestParseK8sMemory:
    def test_kibibytes(self):
        assert _parse_k8s_memory_gb("1048576Ki") == 1.0

    def test_mebibytes(self):
        assert _parse_k8s_memory_gb("1024Mi") == 1.0

    def test_gibibytes(self):
        assert _parse_k8s_memory_gb("64Gi") == 64.0

    def test_tebibytes(self):
        assert _parse_k8s_memory_gb("1Ti") == 1024.0

    def test_plain_byte_count(self):
        result = _parse_k8s_memory_gb(str(1073741824))
        assert result == pytest.approx(1.0)

    def test_zero(self):
        assert _parse_k8s_memory_gb("0Ki") == 0.0


class TestDiscoverAll:
    def test_no_probes(self):
        assert discover_all([]) == []

    def test_single_probe(self):
        r = discover_all([LocalProbe()])
        assert len(r) == 1
        assert r[0].provider == "local"

    def test_failing_probe_isolated(self):
        class FailingProbe:
            def probe(self) -> list[DiscoveredResource]:
                raise RuntimeError("probe failure")

        r = discover_all([FailingProbe(), LocalProbe()])
        assert len(r) == 1
        assert r[0].provider == "local"

    def test_all_failing(self):
        class FailingProbe:
            def probe(self) -> list[DiscoveredResource]:
                raise RuntimeError("probe failure")

        r = discover_all([FailingProbe(), FailingProbe()])
        assert r == []


class TestProviderRegistryGetCheapest:
    def test_get_cheapest_a100_80(self):
        reg = ProviderRegistry()
        info = reg.get_cheapest_for_gpu(GPUType.A100_80)
        assert info.provider in (
            ComputeProvider.AWS,
            ComputeProvider.RUNPOD,
            ComputeProvider.VAST_AI,
            ComputeProvider.GCP,
            ComputeProvider.AZURE,
            ComputeProvider.LAMBDA_LABS,
            ComputeProvider.MODAL,
            ComputeProvider.COREWEAVE,
            ComputeProvider.DIGITAL_OCEAN,
            ComputeProvider.ORACLE,
            ComputeProvider.TOGETHER_AI,
            ComputeProvider.FIREWORKS_AI,
            ComputeProvider.HUGGINGFACE,
            ComputeProvider.REPLICATE,
        )

    def test_keyerror_unknown_gpu(self):
        reg = ProviderRegistry()
        with pytest.raises(KeyError, match="No provider supports GPU type"):
            reg.get_cheapest_for_gpu(GPUType.H200)


class TestResourceProbeProtocol:
    def test_localprobe_conforms(self):
        probe = LocalProbe()
        assert callable(getattr(probe, "probe", None))

    def test_kubernetesprobe_conforms(self):
        probe = KubernetesProbe(transport=FakeK8sTransport())
        assert callable(getattr(probe, "probe", None))
