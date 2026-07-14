"""Structural tests for general_ludd.infra.discovery — resource probes.

Tests LocalProbe, KubernetesProbe, DiscoveredResource, discover_all,
and the K8s unit parsers without depending on live infrastructure.
"""

from __future__ import annotations

import sys
from unittest import mock

import pytest

from general_ludd.infra.discovery import (
    DiscoveredResource,
    KubernetesProbe,
    LocalProbe,
    VSphereProbe,
    _parse_k8s_cpu,
    _parse_k8s_memory_gb,
    discover_all,
)


class TestDiscoveredResource:
    def test_has_all_fields(self) -> None:
        r = DiscoveredResource(provider="local", kind="process")
        assert r.provider == "local"
        assert r.kind == "process"
        assert r.cpu == 0.0
        assert r.mem_gb == 0.0
        assert r.gpu == ""
        assert r.gpu_count == 0
        assert r.cost_per_hour == 0.0

    def test_label_cpu_only(self) -> None:
        r = DiscoveredResource(provider="aws", kind="ec2", cpu=4.0)
        assert r.label() == "aws:ec2:cpu-only"

    def test_label_with_gpu(self) -> None:
        r = DiscoveredResource(provider="aws", kind="ec2", gpu="a100", gpu_count=1)
        assert r.label() == "aws:ec2:a100"

    def test_frozen_dataclass(self) -> None:
        r = DiscoveredResource(provider="x", kind="y")
        from dataclasses import FrozenInstanceError

        with pytest.raises(FrozenInstanceError):
            r.provider = "z"  # type: ignore[misc]


class TestLocalProbe:
    def test_probe_returns_one_resource(self) -> None:
        probe = LocalProbe()
        results = probe.probe()
        assert len(results) == 1
        r = results[0]
        assert r.provider == "local"
        assert r.kind == "process"
        assert r.cpu > 0
        assert r.gpu == ""

    def test_has_probe_method(self) -> None:
        probe = LocalProbe()
        assert hasattr(probe, "probe")
        assert callable(probe.probe)


class TestKubernetesProbe:
    def test_no_transport_returns_empty(self) -> None:
        probe = KubernetesProbe(transport=None)
        assert probe.probe() == []

    def test_transport_returns_dict_with_items(self) -> None:
        def fake_transport(method: str, path: str) -> dict:
            assert method == "GET"
            assert path == "/api/v1/nodes"
            return {
                "items": [
                    {
                        "status": {
                            "capacity": {
                                "cpu": "4",
                                "memory": "8192Mi",
                                "nvidia.com/gpu": "2",
                            }
                        }
                    }
                ]
            }

        probe = KubernetesProbe(transport=fake_transport)
        results = probe.probe()
        assert len(results) == 1
        r = results[0]
        assert r.provider == "kubernetes"
        assert r.kind == "node"
        assert r.cpu == 4.0
        assert r.mem_gb == pytest.approx(8.0, rel=0.1)
        assert r.gpu_count == 2
        assert r.gpu == "gpu"

    def test_transport_exception_returns_empty(self) -> None:
        def failing_transport(method: str, path: str) -> dict:
            raise RuntimeError("connection refused")

        probe = KubernetesProbe(transport=failing_transport)
        assert probe.probe() == []

    def test_non_dict_items_handled_gracefully(self) -> None:
        def fake_transport(method: str, path: str) -> dict:
            return {"items": ["not-a-dict"]}

        probe = KubernetesProbe(transport=fake_transport)
        assert probe.probe() == []

    def test_missing_status_handled(self) -> None:
        def fake_transport(method: str, path: str) -> dict:
            return {"items": [{"name": "no-status-node"}]}

        probe = KubernetesProbe(transport=fake_transport)
        results = probe.probe()
        assert len(results) == 1
        assert results[0].cpu == 0.0

    def test_has_probe_method(self) -> None:
        probe = KubernetesProbe()
        assert hasattr(probe, "probe")
        assert callable(probe.probe)


class TestParseK8sCpu:
    def test_millicores(self) -> None:
        assert _parse_k8s_cpu("500m") == 0.5

    def test_nanocores(self) -> None:
        assert _parse_k8s_cpu("1000000000n") == 1.0

    def test_plain_integer(self) -> None:
        assert _parse_k8s_cpu("2") == 2.0

    def test_plain_float(self) -> None:
        assert _parse_k8s_cpu("0.5") == 0.5

    def test_zero(self) -> None:
        assert _parse_k8s_cpu("0") == 0.0


class TestParseK8sMemoryGb:
    def test_kibibytes(self) -> None:
        gb = _parse_k8s_memory_gb("1048576Ki")
        assert gb == pytest.approx(1.0, rel=0.01)

    def test_mebibytes(self) -> None:
        gb = _parse_k8s_memory_gb("1024Mi")
        assert gb == pytest.approx(1.0, rel=0.01)

    def test_gibibytes(self) -> None:
        assert _parse_k8s_memory_gb("8Gi") == 8.0

    def test_tebibytes(self) -> None:
        assert _parse_k8s_memory_gb("1Ti") == 1024.0

    def test_pebibytes(self) -> None:
        assert _parse_k8s_memory_gb("1Pi") == 1024.0 * 1024.0

    def test_no_suffix_assumes_bytes(self) -> None:
        gb = _parse_k8s_memory_gb(str(1024 * 1024 * 1024))
        assert gb == pytest.approx(1.0, rel=0.01)

    def test_strips_whitespace(self) -> None:
        assert _parse_k8s_memory_gb(" 4Gi ") == 4.0


class TestDiscoverAll:
    def test_aggregates_all_probes(self) -> None:
        probes = [LocalProbe()]
        results = discover_all(probes)
        assert len(results) == 1

    def test_failing_probe_does_not_abort_fanout(self) -> None:
        class FailingProbe:
            def probe(self) -> list[DiscoveredResource]:
                raise RuntimeError("boom")

        probes = [FailingProbe(), LocalProbe()]
        results = discover_all(probes)
        assert len(results) == 1

    def test_empty_probes_returns_empty(self) -> None:
        assert discover_all([]) == []


class TestVSphereProbe:
    def test_can_instantiate_with_required_args(self) -> None:
        probe = VSphereProbe(
            host="vcenter.example.com",
            username="admin",
            password="secret",
        )
        assert probe.host == "vcenter.example.com"
        assert probe.port == 443
        assert probe.verify_ssl is True

    def test_default_port_is_443(self) -> None:
        probe = VSphereProbe(host="vc", username="u", password="p")
        assert probe.port == 443

    def test_discover_returns_none_when_pyvmomi_missing(self) -> None:
        with mock.patch.dict(
            sys.modules, {"pyVim.connect": None}
        ):
            probe = VSphereProbe(host="h", username="u", password="p")
            result = probe.discover()
        assert result is None
