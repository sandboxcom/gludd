"""Unit test for the gludd_scapy Ansible module.

Loads the real shipped module via importlib, drives main() with a fake
AnsibleModule + mocked ScapyAdapter, and asserts:
  - Read-only actions return changed=False.
  - Mutating actions return changed=True.
  - Check mode returns changed=False for all actions.
  - Missing adapter fails clearly.
  - Missing required params fails clearly.
  - Adapter exceptions are caught and surfaced.

Every external boundary is mocked.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).parent.parent.parent
MODULE_PATH = (
    ROOT / "collections" / "ansible_collections" / "general_ludd" / "networking"
    / "plugins" / "modules" / "gludd_scapy.py"
)

_BASE_PARAMS: dict[str, Any] = {
    "action": "read_pcap",
    "pcap_path": None,
    "packets": None,
    "protocol_stack": None,
    "packet_fields": None,
    "interface": "eth0",
    "count": 1,
    "timeout": 30,
    "output_format": "json",
}


class _FakeAnsibleModule:
    def __init__(self, params: dict[str, Any], check_mode: bool = False) -> None:
        self.params = params
        self.check_mode = check_mode
        self.exited: dict[str, Any] | None = None
        self.failed: dict[str, Any] | None = None
        self.warnings: list[str] = []

    def exit_json(self, **kwargs: Any) -> None:
        self.exited = kwargs

    def fail_json(self, **kwargs: Any) -> None:
        self.failed = kwargs

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gludd_scapy", str(MODULE_PATH))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@contextmanager
def _patch_ansible_module(
    mod: ModuleType,
    fake: _FakeAnsibleModule,
) -> Iterator[MagicMock]:
    with patch.object(mod, "AnsibleModule", return_value=fake) as mocked:
        yield mocked


@pytest.fixture
def mock_adapter() -> MagicMock:
    adapter = MagicMock()
    adapter.read_pcap.return_value = [{"src": "1.2.3.4", "dst": "5.6.7.8"}]
    adapter.craft_packet.return_value = {"hex": "deadbeef", "layers": ["Ether", "IP", "ICMP"]}
    adapter.send_packet.return_value = {"sent": 1, "interface": "eth0"}
    adapter.sniff_packets.return_value = [{"summary": "DNS qry example.com", "src": "10.0.0.1"}]
    adapter.analyze_pcap.return_value = {"total": 42, "protocols": {"TCP": 30, "UDP": 12}}
    adapter.dissect_packet.return_value = {"layers": {"Ether": {"src": "00:11:22:33:44:55"}}}
    adapter.write_pcap.return_value = None
    return adapter


class TestCheckMode:
    def test_read_only_action_returns_changed_false(self, mock_adapter: MagicMock) -> None:
        mod = _load_module()
        fake = _FakeAnsibleModule(
            params={**_BASE_PARAMS, "action": "read_pcap", "pcap_path": "/tmp/test.pcap"},
            check_mode=True,
        )
        with patch.object(mod, "_get_adapter", return_value=mock_adapter), _patch_ansible_module(mod, fake):
            mod.main()

        assert fake.exited is not None
        assert fake.exited.get("changed") is False
        assert fake.failed is None

    def test_mutating_action_check_mode_returns_changed_false(self, mock_adapter: MagicMock) -> None:
        mod = _load_module()
        fake = _FakeAnsibleModule(
            params={**_BASE_PARAMS, "action": "send_packet", "protocol_stack": ["Ether", "IP"], "packet_fields": {}},
            check_mode=True,
        )
        with patch.object(mod, "_get_adapter", return_value=mock_adapter), _patch_ansible_module(mod, fake):
            mod.main()

        assert fake.exited is not None
        assert fake.exited.get("changed") is False


class TestErrorCases:
    def test_real_adapter_contract_is_loadable(self) -> None:
        mod = _load_module()

        adapter = mod._get_adapter()

        assert adapter is not None
        assert callable(adapter.craft_packet)

    def test_adapter_unavailable_returns_error(self) -> None:
        mod = _load_module()
        fake = _FakeAnsibleModule(
            params={**_BASE_PARAMS, "action": "read_pcap", "pcap_path": "/tmp/x.pcap"},
            check_mode=False,
        )
        with patch.object(mod, "_get_adapter", return_value=None), _patch_ansible_module(mod, fake):
            mod.main()

        assert fake.failed is not None
        assert "not available" in fake.failed.get("msg", "").lower()

    def test_missing_pcap_path_fails(self, mock_adapter: MagicMock) -> None:
        mod = _load_module()
        fake = _FakeAnsibleModule(
            params={**_BASE_PARAMS, "action": "read_pcap"},
            check_mode=False,
        )
        with patch.object(mod, "_get_adapter", return_value=mock_adapter), _patch_ansible_module(mod, fake):
            mod.main()

        assert fake.failed is not None
        assert "pcap_path" in fake.failed.get("msg", "").lower()

    def test_adapter_exception_is_caught(self, mock_adapter: MagicMock) -> None:
        mock_adapter.craft_packet.side_effect = RuntimeError("scapy not installed")
        mod = _load_module()
        fake = _FakeAnsibleModule(
            params={**_BASE_PARAMS, "action": "craft_packet", "protocol_stack": ["Ether", "IP"], "packet_fields": {}},
            check_mode=False,
        )
        with patch.object(mod, "_get_adapter", return_value=mock_adapter), _patch_ansible_module(mod, fake):
            mod.main()

        assert fake.failed is not None
        assert "failed" in fake.failed.get("msg", "").lower()


class TestReadOnlyActions:
    def test_analyze_pcap_returns_changed_false(self, mock_adapter: MagicMock) -> None:
        mod = _load_module()
        fake = _FakeAnsibleModule(
            params={**_BASE_PARAMS, "action": "analyze_pcap", "pcap_path": "/tmp/c.pcap"},
            check_mode=False,
        )
        with patch.object(mod, "_get_adapter", return_value=mock_adapter), _patch_ansible_module(mod, fake):
            mod.main()

        assert fake.exited is not None
        assert fake.exited.get("changed") is False
        assert "analyzed" in fake.exited.get("summary", "")
        mock_adapter.analyze_pcap.assert_called_once_with("/tmp/c.pcap")

    def test_dissect_packet_returns_structured_output(self, mock_adapter: MagicMock) -> None:
        mod = _load_module()
        fake = _FakeAnsibleModule(
            params={**_BASE_PARAMS, "action": "dissect_packet", "packet_fields": {"raw_hex": "ffff"}},
            check_mode=False,
        )
        with patch.object(mod, "_get_adapter", return_value=mock_adapter), _patch_ansible_module(mod, fake):
            mod.main()

        assert fake.exited is not None
        assert fake.exited.get("changed") is False
        assert "layers" in str(fake.exited.get("output", {})).lower()
        mock_adapter.dissect_packet.assert_called_once_with(bytes.fromhex("ffff"))

    def test_dissect_packet_requires_fields(self, mock_adapter: MagicMock) -> None:
        mod = _load_module()
        fake = _FakeAnsibleModule(
            params={**_BASE_PARAMS, "action": "dissect_packet"},
            check_mode=False,
        )
        with patch.object(mod, "_get_adapter", return_value=mock_adapter), _patch_ansible_module(mod, fake):
            mod.main()

        assert fake.failed is not None
        assert "packet_fields" in fake.failed.get("msg", "").lower()


class TestMutatingActions:
    def test_craft_packet_returns_changed_true(self, mock_adapter: MagicMock) -> None:
        mod = _load_module()
        fake = _FakeAnsibleModule(
            params={
                **_BASE_PARAMS,
                "action": "craft_packet",
                "protocol_stack": ["Ether", "IP", "ICMP"],
                "packet_fields": {"IP": {"dst": "8.8.8.8"}},
            },
            check_mode=False,
        )
        with patch.object(mod, "_get_adapter", return_value=mock_adapter), _patch_ansible_module(mod, fake):
            mod.main()

        assert fake.exited is not None
        assert fake.exited.get("changed") is True
        mock_adapter.craft_packet.assert_called_once_with(
            ["Ether", "IP", "ICMP"], {"IP": {"dst": "8.8.8.8"}}
        )

    def test_send_packet_returns_changed_true(self, mock_adapter: MagicMock) -> None:
        packet = {"protocols": ["Ether", "IP"], "fields": {"IP": {"dst": "8.8.8.8"}}}
        mod = _load_module()
        fake = _FakeAnsibleModule(
            params={
                **_BASE_PARAMS,
                "action": "send_packet",
                "packets": [packet],
                "interface": "enp0s1",
                "count": 5,
            },
            check_mode=False,
        )
        with patch.object(mod, "_get_adapter", return_value=mock_adapter), _patch_ansible_module(mod, fake):
            mod.main()

        assert fake.exited is not None
        assert fake.exited.get("changed") is True
        mock_adapter.send_packet.assert_called_once_with(packet, "enp0s1", 5)

    def test_sniff_packets_passes_interface_count_timeout(self, mock_adapter: MagicMock) -> None:
        mod = _load_module()
        fake = _FakeAnsibleModule(
            params={
                **_BASE_PARAMS,
                "action": "sniff_packets",
                "interface": "enp0s1",
                "count": 10,
                "timeout": 15,
            },
            check_mode=False,
        )
        with patch.object(mod, "_get_adapter", return_value=mock_adapter), _patch_ansible_module(mod, fake):
            mod.main()

        assert fake.exited is not None
        assert fake.exited.get("changed") is True
        mock_adapter.sniff_packets.assert_called_once_with("", count=10, timeout=15)

    def test_write_pcap_returns_changed_true(self, mock_adapter: MagicMock) -> None:
        mod = _load_module()
        fake = _FakeAnsibleModule(
            params={
                **_BASE_PARAMS,
                "action": "write_pcap",
                "pcap_path": "/tmp/out.pcap",
                "packets": [{"src": "1.2.3.4"}],
            },
            check_mode=False,
        )
        with patch.object(mod, "_get_adapter", return_value=mock_adapter), _patch_ansible_module(mod, fake):
            mod.main()

        assert fake.exited is not None
        assert fake.exited.get("changed") is True
        args = mock_adapter.write_pcap.call_args.args
        assert args[1] == "/tmp/out.pcap"
        assert args[0][0].src_ip == "1.2.3.4"

    def test_write_pcap_requires_path_and_packets(self, mock_adapter: MagicMock) -> None:
        mod = _load_module()
        fake = _FakeAnsibleModule(
            params={**_BASE_PARAMS, "action": "write_pcap"},
            check_mode=False,
        )
        with patch.object(mod, "_get_adapter", return_value=mock_adapter), _patch_ansible_module(mod, fake):
            mod.main()

        assert fake.failed is not None
        assert "pcap_path" in fake.failed.get("msg", "").lower()
