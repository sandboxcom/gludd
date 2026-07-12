"""Unit tests for scapy_adapter module."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from general_ludd.networking.scapy_adapter import (
    PacketSummary,
    TrafficReport,
    _parse_tshark_json,
    analyze_pcap,
    craft_packet,
    dissect_packet,
    read_pcap,
    scapy_available,
    send_packet,
    sniff_packets,
    tshark_available,
    write_pcap,
)


class TestScapyAvailable:
    def test_returns_true_when_scapy_is_importable(self):
        with patch.dict("sys.modules", {"scapy": MagicMock(), "scapy.all": MagicMock()}):
            assert scapy_available() is True

    def test_returns_false_when_not_importable(self):
        with patch.dict("sys.modules", {"scapy.all": None}, clear=True):
            assert scapy_available() is False


class TestTsharkAvailable:
    def test_returns_true_when_tshark_on_path(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock()
            assert tshark_available() is True

    def test_returns_false_when_tshark_not_found(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert tshark_available() is False

    def test_returns_false_on_timeout(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("tshark", 5)):
            assert tshark_available() is False


class TestReadPcap:
    def test_returns_empty_when_file_not_found(self, tmp_path):
        result = read_pcap(tmp_path / "nonexistent.pcap")
        assert result == []

    def test_reads_with_tshark_when_available(self, tmp_path):
        pcap = tmp_path / "test.pcap"
        pcap.write_text("")
        with patch(
            "general_ludd.networking.scapy_adapter.tshark_available",
            return_value=True,
        ), patch(
            "general_ludd.networking.scapy_adapter._read_pcap_tshark",
            return_value=[
                PacketSummary(timestamp=1.0, src_ip="1.1.1.1", dst_ip="2.2.2.2", protocol="TCP", length=100)
            ],
        ):
            result = read_pcap(pcap)
            assert len(result) == 1
            assert result[0].src_ip == "1.1.1.1"

    def test_falls_back_to_scapy(self, tmp_path):
        pcap = tmp_path / "test.pcap"
        pcap.write_text("")
        with patch(
            "general_ludd.networking.scapy_adapter.tshark_available",
            return_value=False,
        ), patch(
            "general_ludd.networking.scapy_adapter.scapy_available",
            return_value=True,
        ), patch(
            "general_ludd.networking.scapy_adapter._read_pcap_scapy",
            return_value=[
                PacketSummary(timestamp=2.0, src_ip="3.3.3.3", dst_ip="4.4.4.4", protocol="UDP", length=50)
            ],
        ):
            result = read_pcap(pcap)
            assert len(result) == 1
            assert result[0].protocol == "UDP"

    def test_returns_empty_when_no_backend_available(self, tmp_path):
        pcap = tmp_path / "test.pcap"
        pcap.write_text("")
        with patch(
            "general_ludd.networking.scapy_adapter.tshark_available",
            return_value=False,
        ), patch(
            "general_ludd.networking.scapy_adapter.scapy_available",
            return_value=False,
        ):
            result = read_pcap(pcap)
            assert result == []


class TestParseTsharkJson:
    def test_parses_valid_json(self):
        raw = [
            {
                "_source": {
                    "layers": {
                        "frame.time_epoch": ["1620000000.123"],
                        "ip.src": ["10.0.0.1"],
                        "ip.dst": ["10.0.0.2"],
                        "frame.protocols": ["eth:ethertype:ip:tcp"],
                        "frame.len": ["256"],
                        "tcp.srcport": ["443"],
                        "tcp.dstport": ["8080"],
                        "tcp.flags.str": ["ACK"],
                    }
                }
            }
        ]
        result = _parse_tshark_json(raw)
        assert len(result) == 1
        pkt = result[0]
        assert pkt.src_ip == "10.0.0.1"
        assert pkt.dst_ip == "10.0.0.2"
        assert pkt.protocol == "tcp"
        assert pkt.length == 256
        assert pkt.src_port == 443
        assert pkt.dst_port == 8080
        assert pkt.flags == "ACK"

    def test_parses_udp_packet(self):
        raw = [
            {
                "_source": {
                    "layers": {
                        "frame.time_epoch": ["1620000000.000"],
                        "ip.src": ["192.168.1.1"],
                        "ip.dst": ["192.168.1.2"],
                        "frame.protocols": ["eth:ethertype:ip:udp:dns"],
                        "frame.len": ["80"],
                        "udp.srcport": ["53"],
                        "udp.dstport": ["12345"],
                    }
                }
            }
        ]
        result = _parse_tshark_json(raw)
        assert result[0].src_port == 53
        assert result[0].dst_port == 12345
        assert result[0].protocol == "dns"

    def test_handles_missing_fields(self):
        raw = [{"_source": {"layers": {}}}]
        result = _parse_tshark_json(raw)
        assert len(result) == 1
        pkt = result[0]
        assert pkt.src_ip == ""
        assert pkt.protocol == "unknown"
        assert pkt.length == 0

    def test_handles_empty_list(self):
        assert _parse_tshark_json([]) == []


class TestWritePcap:
    def test_creates_directories(self, tmp_path):
        path = tmp_path / "sub" / "dir" / "out.pcap"
        with patch(
            "general_ludd.networking.scapy_adapter.scapy_available",
            return_value=False,
        ):
            write_pcap([], path)
            assert path.parent.exists()

    def test_writes_with_scapy_when_available(self, tmp_path):
        path = tmp_path / "out.pcap"
        pkt = PacketSummary(
            timestamp=1.0, src_ip="10.0.0.1", dst_ip="10.0.0.2",
            protocol="TCP", length=100, src_port=80, dst_port=443,
        )
        with patch(
            "general_ludd.networking.scapy_adapter.scapy_available",
            return_value=True,
        ), patch(
            "general_ludd.networking.scapy_adapter._write_pcap_scapy",
        ) as mock_write:
            write_pcap([pkt], path)
            mock_write.assert_called_once()

    def test_falls_back_to_raw_writer(self, tmp_path):
        path = tmp_path / "out.pcap"
        with patch(
            "general_ludd.networking.scapy_adapter.scapy_available",
            return_value=False,
        ):
            write_pcap([], path)
            assert path.exists()


class TestCraftPacket:
    def test_returns_spec_with_scapy_available(self):
        with patch(
            "general_ludd.networking.scapy_adapter.scapy_available",
            return_value=True,
        ):
            spec = craft_packet(["Ether", "IP", "TCP"], {"src": "10.0.0.1", "dport": "80"})
            assert spec["protocols"] == ["Ether", "IP", "TCP"]
            assert spec["fields"]["src"] == "10.0.0.1"
            assert spec["_scapy_available"] is True

    def test_returns_spec_without_scapy(self):
        with patch(
            "general_ludd.networking.scapy_adapter.scapy_available",
            return_value=False,
        ):
            spec = craft_packet(["Ether", "IP"], {"dst": "10.0.0.2"})
            assert spec["_scapy_available"] is False


class TestSendPacket:
    def test_sends_via_nping_fallback(self):
        spec = {"protocols": ["IP", "TCP"], "fields": {"dst": "127.0.0.1", "dport": "80"}}
        with patch(
            "general_ludd.networking.scapy_adapter.scapy_available",
            return_value=False,
        ), patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = send_packet(spec, "eth0", count=2)
            assert result["tool"] == "nping"
            assert result["count"] == 2

    def test_returns_error_when_no_tool_available(self):
        spec = {"protocols": ["IP"], "fields": {}}
        with patch(
            "general_ludd.networking.scapy_adapter.scapy_available",
            return_value=False,
        ), patch("subprocess.run", side_effect=FileNotFoundError):
            result = send_packet(spec, "eth0")
            assert result["sent"] == 0

    def test_sends_via_scapy_when_available(self):
        spec = {"protocols": ["IP", "TCP"], "fields": {"src": "127.0.0.1", "dst": "127.0.0.1", "dport": "443"}}
        with patch(
            "general_ludd.networking.scapy_adapter.scapy_available",
            return_value=True,
        ), patch(
            "general_ludd.networking.scapy_adapter._send_scapy",
            return_value={"interface": "eth0", "count": 3, "packets": []},
        ) as mock_send:
            result = send_packet(spec, "eth0", count=3)
            assert result["count"] == 3
            mock_send.assert_called_once()


class TestSniffPackets:
    def test_returns_empty_when_no_tshark(self):
        with patch(
            "general_ludd.networking.scapy_adapter.tshark_available",
            return_value=False,
        ):
            assert sniff_packets("tcp port 80") == []

    def test_returns_empty_on_subprocess_error(self):
        with patch(
            "general_ludd.networking.scapy_adapter.tshark_available",
            return_value=True,
        ), patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr=b"no interface")
            result = sniff_packets("tcp port 80", count=1, timeout=1)
            assert result == []


class TestAnalyzePcap:
    def test_returns_empty_report_when_file_missing(self):
        report = analyze_pcap(Path("/nonexistent/file.pcap"))
        assert report.total_packets == 0

    def test_analyzes_with_tshark(self, tmp_path):
        pcap = tmp_path / "test.pcap"
        pcap.write_text("")
        mock_io = MagicMock(returncode=0, stdout="frame\n10.0.0.1\n", stderr="")
        with patch(
            "general_ludd.networking.scapy_adapter.tshark_available",
            return_value=True,
        ), patch("subprocess.run", return_value=mock_io):
            report = analyze_pcap(pcap)
            assert isinstance(report, TrafficReport)

    def test_falls_back_when_tshark_unavailable(self, tmp_path):
        pcap = tmp_path / "test.pcap"
        pcap.write_text("")
        with patch(
            "general_ludd.networking.scapy_adapter.tshark_available",
            return_value=False,
        ), patch(
            "general_ludd.networking.scapy_adapter.scapy_available",
            return_value=False,
        ):
            report = analyze_pcap(pcap)
            assert report.total_packets == 0


class TestDissectPacket:
    def test_dissects_ip_tcp_struct(self):
        raw = (
            b"\x00\x11\x22\x33\x44\x55"
            b"\x66\x77\x88\x99\xaa\xbb"
            b"\x08\x00"
            b"\x45\x00\x00\x28"
            b"\x00\x01\x00\x00"
            b"\x40\x06\x00\x00"
            b"\x0a\x00\x00\x01"
            b"\x0a\x00\x00\x02"
            b"\x00\x50\x01\xbb"
            b"\x00\x00\x00\x00"
            b"\x00\x00\x00\x00"
            b"\x50\x02\x00\x00"
            b"\x00\x00\x00\x00"
        )
        with patch(
            "general_ludd.networking.scapy_adapter.scapy_available",
            return_value=False,
        ):
            result = dissect_packet(raw)
        layers = result.get("layers", {})
        assert "Ethernet" in layers
        assert "IP" in layers
        assert layers["IP"]["src"] == "10.0.0.1"
        tcp = layers.get("TCP", {})
        assert tcp.get("src_port") == 80
        assert tcp.get("dst_port") == 443

    def test_dissects_ip_udp_struct(self):
        raw = (
            b"\x00\x11\x22\x33\x44\x55"
            b"\x66\x77\x88\x99\xaa\xbb"
            b"\x08\x00"
            b"\x45\x00\x00\x1c"
            b"\x00\x01\x00\x00"
            b"\x40\x11\x00\x00"
            b"\x0a\x00\x00\x01"
            b"\x0a\x00\x00\x02"
            b"\x00\x35\xc4\x04"
            b"\x00\x08\x00\x00"
        )
        with patch(
            "general_ludd.networking.scapy_adapter.scapy_available",
            return_value=False,
        ):
            result = dissect_packet(raw)
        udp = result.get("layers", {}).get("UDP", {})
        assert udp.get("src_port") == 53
        assert udp.get("dst_port") == 50180

    def test_handles_short_packet(self):
        with patch(
            "general_ludd.networking.scapy_adapter.scapy_available",
            return_value=False,
        ):
            result = dissect_packet(b"\x00" * 10)
        assert result["length"] == 10

    def test_uses_scapy_when_available(self):
        raw = b"\x00" * 64
        with patch(
            "general_ludd.networking.scapy_adapter.scapy_available",
            return_value=True,
        ), patch(
            "general_ludd.networking.scapy_adapter._dissect_scapy",
            return_value={"layers": {"Ethernet": {}}},
        ) as mock_fn:
            result = dissect_packet(raw)
            assert result["layers"] == {"Ethernet": {}}
            mock_fn.assert_called_once()


class TestTrafficReport:
    def test_default_values(self):
        report = TrafficReport()
        assert report.total_packets == 0
        assert report.protocols == {}
        assert report.top_talkers == {}
        assert report.top_ports == {}
        assert report.flows == []
        assert report.duration_seconds == 0.0

    def test_can_set_fields(self):
        report = TrafficReport(
            total_packets=42,
            protocols={"TCP": 30, "UDP": 12},
            top_talkers={"10.0.0.1": 20},
        )
        assert report.total_packets == 42
        assert report.protocols["TCP"] == 30


class TestPacketSummary:
    def test_defaults(self):
        pkt = PacketSummary(timestamp=1.0, src_ip="1.2.3.4", dst_ip="5.6.7.8", protocol="TCP", length=100)
        assert pkt.src_port is None
        assert pkt.dst_port is None
        assert pkt.flags is None
        assert pkt.info == ""
