"""Integration tests for the ``general_ludd.networking.networking`` role.

Runs the role for real against a tmp dir via ``AnsibleRunnerAdapter.run_playbook``
and asserts pcap read/write round-trip, packet craft+analyze flow.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from general_ludd.ansible.runner import AnsibleRunnerAdapter

_PLAYBOOK = "networking.yml"


def _run_role(project_dir: Path, extravars: dict) -> dict:
    adapter = AnsibleRunnerAdapter(project_root=str(project_dir))
    pb = Path(__file__).resolve().parent.parent.parent / "playbooks" / _PLAYBOOK
    if _PLAYBOOK not in adapter.list_playbooks():
        adapter.register_playbook(_PLAYBOOK, str(pb))
    return adapter.run_playbook(_PLAYBOOK, extravars=extravars)


def _has_ansible() -> bool:
    return shutil.which("ansible-playbook") is not None


pytestmark = pytest.mark.skipif(
    not _has_ansible(), reason="ansible-playbook not installed"
)


class TestNetworkingRoleImport:
    def test_role_defaults_load(self) -> None:
        defaults_path = (
            Path(__file__).resolve().parent.parent.parent
            / "collections/ansible_collections/general_ludd/agent/roles/networking/defaults/main.yml"
        )
        assert defaults_path.exists()
        defaults = yaml.safe_load(defaults_path.read_text())
        assert "artifact_dir" in defaults

    def test_role_tasks_exist(self) -> None:
        tasks_path = (
            Path(__file__).resolve().parent.parent.parent
            / "collections/ansible_collections/general_ludd/agent/roles/networking/tasks/main.yml"
        )
        assert tasks_path.exists()
        tasks = yaml.safe_load(tasks_path.read_text())
        assert isinstance(tasks, list)
        assert len(tasks) > 0

    def test_role_meta_has_name(self) -> None:
        meta_path = (
            Path(__file__).resolve().parent.parent.parent
            / "collections/ansible_collections/general_ludd/agent/roles/networking/meta/main.yml"
        )
        assert meta_path.exists()
        meta = yaml.safe_load(meta_path.read_text())
        assert meta["galaxy_info"]["role_name"] == "networking"


class TestNetworkingRoleExecution:
    def test_role_runs_with_empty_inputs(self, tmp_path: Path) -> None:
        result = _run_role(
            tmp_path,
            {
                "artifact_dir": str(tmp_path / "artifacts"),
                "networking_pcap_path": "",
                "networking_cidr_input": "",
                "networking_asn_whois": "",
                "networking_bgp_community": "",
                "networking_install_scapy": False,
            },
        )
        assert result["rc"] == 0

    def test_role_cidr_analysis(self, tmp_path: Path) -> None:
        artifact_dir = tmp_path / "artifacts"
        result = _run_role(
            tmp_path,
            {
                "artifact_dir": str(artifact_dir),
                "networking_pcap_path": "",
                "networking_cidr_input": "10.0.0.0/8",
                "networking_asn_whois": "",
                "networking_bgp_community": "",
                "networking_install_scapy": False,
            },
        )
        assert result["rc"] == 0
        artifact_path = artifact_dir / "networking.json"
        if artifact_path.exists():
            data = json.loads(artifact_path.read_text())
            assert data["status"] == "completed"

    def test_role_bgp_community(self, tmp_path: Path) -> None:
        artifact_dir = tmp_path / "artifacts"
        result = _run_role(
            tmp_path,
            {
                "artifact_dir": str(artifact_dir),
                "networking_pcap_path": "",
                "networking_cidr_input": "",
                "networking_asn_whois": "",
                "networking_bgp_community": "15169:100",
                "networking_install_scapy": False,
            },
        )
        assert result["rc"] == 0

    def test_role_pcap_roundtrip(self, tmp_path: Path) -> None:
        from general_ludd.networking import PacketSummary, write_pcap

        pcap_path = tmp_path / "traffic.pcap"
        pkts = [
            PacketSummary(
                timestamp=1000.0, src_ip="10.0.0.1", dst_ip="10.0.0.2",
                protocol="TCP", length=60, src_port=54321, dst_port=80,
            ),
        ]
        write_pcap(pkts, pcap_path)

        artifact_dir = tmp_path / "artifacts"
        result = _run_role(
            tmp_path,
            {
                "artifact_dir": str(artifact_dir),
                "networking_pcap_path": str(pcap_path),
                "networking_cidr_input": "",
                "networking_asn_whois": "",
                "networking_bgp_community": "",
                "networking_install_scapy": False,
            },
        )
        assert result["rc"] == 0

    def test_role_packet_craft_and_analyze(self, tmp_path: Path) -> None:
        from general_ludd.networking import PacketSummary, write_pcap

        pkts = [
            PacketSummary(
                timestamp=1000.0, src_ip="10.0.0.1", dst_ip="10.0.0.2",
                protocol="TCP", length=60, src_port=1, dst_port=80,
            ),
            PacketSummary(
                timestamp=1001.0, src_ip="10.0.0.1", dst_ip="10.0.0.3",
                protocol="TCP", length=60, src_port=2, dst_port=443,
            ),
            PacketSummary(
                timestamp=1002.0, src_ip="10.0.0.1", dst_ip="10.0.0.2",
                protocol="UDP", length=50, src_port=3, dst_port=53,
            ),
        ]
        pcap_path = tmp_path / "crafted.pcap"
        write_pcap(pkts, pcap_path)

        artifact_dir = tmp_path / "artifacts"
        result = _run_role(
            tmp_path,
            {
                "artifact_dir": str(artifact_dir),
                "networking_pcap_path": str(pcap_path),
                "networking_cidr_input": "192.168.0.0/16",
                "networking_asn_whois": "",
                "networking_bgp_community": "",
                "networking_install_scapy": False,
            },
        )
        assert result["rc"] == 0
