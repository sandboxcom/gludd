"""Deep tests for untested networking subsystem surfaces.

Covers: sniff_packets config/fallback, send_packet paths,
_parse_scapy_packets, _build_report_from_packets, _parse_tshark_stats,
dissect fallback, _write_pcap_raw header, _first_int edge cases,
and parse_asn_whois ASNumber edge cases.
"""

from __future__ import annotations

import struct
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.networking.scapy_adapter import (
    PacketSummary,
    _build_report_from_packets,
    _first_int,
    _parse_scapy_packets,
    _parse_tshark_json,
    _parse_tshark_stats,
    _write_pcap_raw,
    dissect_packet,
    parse_asn_whois,
    read_pcap,
    send_packet,
    sniff_packets,
    write_pcap,
)


class TestSniffPackets:
    def test_sniff_returns_empty_when_tshark_unavailable(self) -> None:
        with patch("general_ludd.networking.scapy_adapter.tshark_available", return_value=False):
            result = sniff_packets(filter_str="tcp port 80", count=5, timeout=3)
        assert result == []

    def test_sniff_config_passed_to_subprocess(self) -> None:
        with (
            patch(
                "general_ludd.networking.scapy_adapter.tshark_available",
                return_value=True,
            ),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=1, stdout=b"[]", stderr=b"")
            result = sniff_packets(filter_str="tcp port 443", count=10, timeout=2)
            assert result == []
            call_args = mock_run.call_args[0][0]
            assert "-c" in call_args
            assert "10" in call_args
            assert "-f" in call_args
            assert "tcp port 443" in call_args
            timeout_idx = call_args.index("-a")
            assert "duration:2" in call_args[timeout_idx + 1]

    def test_sniff_timeout_returns_empty(self) -> None:
        import subprocess

        with (
            patch(
                "general_ludd.networking.scapy_adapter.tshark_available",
                return_value=True,
            ),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.side_effect = subprocess.TimeoutExpired(cmd=["tshark"], timeout=5)
            result = sniff_packets(count=1, timeout=1)
        assert result == []

    def test_sniff_json_error_returns_empty(self) -> None:
        import json

        with (
            patch(
                "general_ludd.networking.scapy_adapter.tshark_available",
                return_value=True,
            ),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout=b"not-json", stderr=b"")
            mock_run.side_effect = None
            with patch.object(mock_run, "returncode", 0), patch.object(mock_run, "stdout", b"not-json"):
                mock_run.return_value = MagicMock(returncode=0, stdout=b"not-json", stderr=b"")
                with patch("json.loads", side_effect=json.JSONDecodeError("bad", "", 0)):
                    result = sniff_packets(count=1, timeout=1)
        assert result == []

    def test_sniff_default_filter_empty_string(self) -> None:
        with (
            patch(
                "general_ludd.networking.scapy_adapter.tshark_available",
                return_value=True,
            ),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=1, stdout=b"[]", stderr=b"")
            result = sniff_packets()
            assert result == []
            call_args = mock_run.call_args[0][0]
            assert "" in call_args

    def test_sniff_generic_exception_returns_empty(self) -> None:
        with (
            patch(
                "general_ludd.networking.scapy_adapter.tshark_available",
                return_value=True,
            ),
            patch("subprocess.run", side_effect=OSError("no such binary")),
        ):
            result = sniff_packets()
        assert result == []


class TestSendPacket:
    def test_send_without_scapy_uses_nping(self) -> None:

        spec = {"protocols": ["IP", "TCP"], "fields": {"dst": "10.0.0.1", "dport": "8080"}}
        with (
            patch(
                "general_ludd.networking.scapy_adapter.scapy_available",
                return_value=False,
            ),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            result = send_packet(spec, iface="eth0", count=3)
        assert result["tool"] == "nping"
        assert result["count"] == 3
        call_args = mock_run.call_args[0][0]
        assert "8080" in str(call_args)
        assert "3" in str(call_args)
        assert "10.0.0.1" in str(call_args)

    def test_send_without_scapy_nping_not_found(self) -> None:
        spec = {"protocols": ["IP", "TCP"], "fields": {"dst": "10.0.0.1", "dport": "80"}}
        with (
            patch(
                "general_ludd.networking.scapy_adapter.scapy_available",
                return_value=False,
            ),
            patch("subprocess.run", side_effect=FileNotFoundError),
        ):
            result = send_packet(spec, iface="eth0", count=1)
        assert result == {"sent": 0}

    def test_send_with_scapy_returns_interface_info(self) -> None:
        spec = {"protocols": ["IP"], "fields": {}}
        with patch(
            "general_ludd.networking.scapy_adapter.scapy_available",
            return_value=True,
        ):
            result = send_packet(spec, iface="en0", count=5)
        assert result["interface"] == "en0"
        assert result["count"] == 5
        assert "packets" in result


class TestParseScapyPackets:
    def test_empty_list_returns_empty(self) -> None:
        result = _parse_scapy_packets([])
        assert result == []

    def test_parse_without_scapy_layer_attrs(self) -> None:
        pkt = MagicMock()
        pkt.time = 1234.5
        pkt.__len__ = MagicMock(return_value=100)
        del pkt.haslayer
        result = _parse_scapy_packets([pkt])
        assert len(result) == 1
        assert result[0].timestamp == 1234.5
        assert result[0].length == 100
        assert result[0].protocol == ""
        assert result[0].src_port is None

    def test_parse_packet_without_time_attribute(self) -> None:
        pkt = MagicMock(spec_set=["__len__"])
        pkt.__len__ = MagicMock(return_value=200)
        result = _parse_scapy_packets([pkt])
        assert len(result) == 1
        assert result[0].timestamp == 0.0
        assert result[0].length == 200

    def test_parse_exception_per_packet_produces_default(self) -> None:
        pkt = MagicMock()
        pkt.time = "not-a-float"
        pkt.__len__ = MagicMock(side_effect=Exception("bad packet"))
        result = _parse_scapy_packets([pkt])
        assert len(result) == 1
        assert result[0].src_ip == ""
        assert result[0].length == 0


class TestBuildReportFromPackets:
    def test_empty_packets_returns_zero_report(self) -> None:
        report = _build_report_from_packets([])
        assert report.total_packets == 0
        assert report.protocols == {}

    def test_counts_protocols_from_packet_summaries(self) -> None:
        pkts = [
            PacketSummary(protocol="tcp", length=100),
            PacketSummary(protocol="tcp", length=200),
            PacketSummary(protocol="udp", length=80),
            PacketSummary(protocol="", length=50),
        ]
        report = _build_report_from_packets(pkts)
        assert report.total_packets == 4
        assert report.protocols == {"tcp": 2, "udp": 1}


class TestParseTsharkStats:
    def test_parses_frame_count_from_output(self) -> None:
        output = (
            "===================================================================\n"
            "IO Statistics\n"
            "Interval: 0.000 secs\n"
            "Number of frames 42\n"
            "===================================================================\n"
        )
        report = _parse_tshark_stats(output)
        assert report.total_packets == 42

    def test_no_frames_line_returns_zero(self) -> None:
        report = _parse_tshark_stats("some random output\nno frames here")
        assert report.total_packets == 0

    def test_empty_output_returns_zero(self) -> None:
        report = _parse_tshark_stats("")
        assert report.total_packets == 0

    def test_malformed_frames_line_returns_zero(self) -> None:
        report = _parse_tshark_stats("Number of frames: abc")
        assert report.total_packets == 0


class TestDissectFallback:
    def test_dissect_ipv4_tcp_struct_fallback(self) -> None:
        eth = (
            b"\xff\xff\xff\xff\xff\xff"  # dst mac
            b"\x00\x11\x22\x33\x44\x55"  # src mac
            b"\x08\x00"  # EtherType IPv4
        )
        ip = (
            b"\x45\x00\x00\x28"  # version=4, ihl=5, dscp=0, len=40
            b"\x00\x01\x00\x00"  # id=1, flags=0, offset=0
            b"\x40\x06\x00\x00"  # ttl=64, proto=6(TCP)
            b"\xc0\xa8\x01\x01"  # src=192.168.1.1
            b"\xc0\xa8\x01\x02"  # dst=192.168.1.2
        )
        tcp = (
            b"\x04\xd2\x00\x50"  # sport=1234, dport=80
            b"\x00\x00\x00\x01"  # seq=1
            b"\x00\x00\x00\x02"  # ack=2
            b"\x50\x10\xff\xff"  # data_offset=5, flags=0x10(ACK)
            b"\x00\x00\x00\x00"  # window=65535, checksum=0, urgent=0
        )
        raw = eth + ip + tcp
        with patch(
            "general_ludd.networking.scapy_adapter.scapy_available",
            return_value=False,
        ):
            result = dissect_packet(raw)
        layers = result["layers"]
        assert "IP" in layers
        assert layers["IP"]["src"] == "192.168.1.1"
        assert layers["IP"]["dst"] == "192.168.1.2"
        assert layers["IP"]["proto"] == 6
        assert "TCP" in layers
        assert layers["TCP"]["src_port"] == 1234
        assert layers["TCP"]["dst_port"] == 80

    def test_dissect_ipv4_udp_struct_fallback(self) -> None:
        eth = b"\xff\xff\xff\xff\xff\xff\x00\x11\x22\x33\x44\x55\x08\x00"
        ip = (
            b"\x45\x00\x00\x1c"  # len=28
            b"\x00\x01\x00\x00"
            b"\x40\x11\x00\x00"  # ttl=64, proto=17(UDP)
            b"\x0a\x00\x00\x01"
            b"\x08\x08\x08\x08"
        )
        udp = (
            b"\x00\x35\x00\x35"  # sport=53, dport=53
            b"\x00\x08\x00\x00"  # len=8, checksum=0
        )
        raw = eth + ip + udp
        with patch(
            "general_ludd.networking.scapy_adapter.scapy_available",
            return_value=False,
        ):
            result = dissect_packet(raw)
        layers = result["layers"]
        assert "UDP" in layers
        assert layers["UDP"]["src_port"] == 53
        assert layers["UDP"]["dst_port"] == 53
        assert layers["UDP"]["length"] == 8

    def test_dissect_short_ethernet_only(self) -> None:
        eth = (
            b"\xff\xff\xff\xff\xff\xff"
            b"\x00\x11\x22\x33\x44\x55"
            b"\x08\x06"  # ARP
        )
        result = dissect_packet(eth)
        assert result["length"] == 14
        assert "Ethernet" in result["layers"]
        assert "IP" not in result["layers"]


class TestWritePcapRaw:
    def test_raw_header_contains_pcap_magic(self, tmp_path: Path) -> None:
        pcap_path = tmp_path / "raw.pcap"
        pkt = PacketSummary(timestamp=10.0, length=64, protocol="TCP")
        _write_pcap_raw([pkt], pcap_path)
        content = pcap_path.read_bytes()
        magic = struct.unpack("<I", content[:4])[0]
        assert magic == 0xA1B2C3D4

    def test_raw_writes_timestamp_correctly(self, tmp_path: Path) -> None:
        pcap_path = tmp_path / "ts.pcap"
        pkt = PacketSummary(timestamp=1600000000.500000, length=128, protocol="UDP")
        _write_pcap_raw([pkt], pcap_path)
        content = pcap_path.read_bytes()
        ts_sec = struct.unpack("<I", content[24:28])[0]
        ts_usec = struct.unpack("<I", content[28:32])[0]
        assert ts_sec == 1600000000
        assert ts_usec == 500000
        incl_len = struct.unpack("<I", content[32:36])[0]
        assert incl_len == 128


class TestFirstIntEdgeCases:
    def test_mixed_none_and_valid(self) -> None:
        assert _first_int([None, None, "64"]) == 64

    def test_float_string_rejects(self) -> None:
        assert _first_int(["3.14"]) is None

    def test_negative_int_string(self) -> None:
        assert _first_int(["-5"]) == -5

    def test_large_int(self) -> None:
        assert _first_int(["2147483647"]) == 2147483647

    def test_object_not_int_convertible(self) -> None:
        assert _first_int([object()]) is None


class TestParseTsharkJsonEdgeCases:
    def test_multiple_packets(self) -> None:
        raw = [
            {
                "_source": {
                    "layers": {
                        "frame.time_epoch": ["1.0"],
                        "frame.len": ["100"],
                        "frame.protocols": ["eth:ip:tcp"],
                        "ip.src": ["1.1.1.1"],
                        "ip.dst": ["2.2.2.2"],
                        "tcp.srcport": ["100"],
                        "tcp.dstport": ["200"],
                        "tcp.flags.str": ["S"],
                    }
                }
            },
            {
                "_source": {
                    "layers": {
                        "frame.time_epoch": ["2.0"],
                        "frame.len": ["200"],
                        "frame.protocols": ["eth:ip:udp"],
                        "ip.src": ["3.3.3.3"],
                        "ip.dst": ["4.4.4.4"],
                        "udp.srcport": ["300"],
                        "udp.dstport": ["400"],
                    }
                }
            },
        ]
        results = _parse_tshark_json(raw)
        assert len(results) == 2
        assert results[0].protocol == "tcp"
        assert results[1].protocol == "udp"

    def test_protocol_no_colon(self) -> None:
        raw = [
            {
                "_source": {
                    "layers": {
                        "frame.protocols": ["http"],
                        "frame.time_epoch": ["0.0"],
                        "frame.len": ["0"],
                    }
                }
            }
        ]
        results = _parse_tshark_json(raw)
        assert results[0].protocol == "http"

    def test_protocol_empty_string(self) -> None:
        raw = [
            {
                "_source": {
                    "layers": {
                        "frame.protocols": [""],
                        "frame.time_epoch": ["0.0"],
                        "frame.len": ["0"],
                    }
                }
            }
        ]
        results = _parse_tshark_json(raw)
        assert results[0].protocol == "unknown"


class TestWritePcapEdgeCases:
    def test_write_empty_packet_list(self, tmp_path: Path) -> None:
        pcap_path = tmp_path / "empty.pcap"
        write_pcap([], pcap_path)
        assert pcap_path.exists()
        assert pcap_path.stat().st_size > 0

    def test_write_packet_with_unset_ip_defaults_to_unspec(self, tmp_path: Path) -> None:
        if not _scapy_available_check():
            pytest.skip("scapy not available")
        pkt = PacketSummary(protocol="TCP", length=60)
        write_pcap([pkt], tmp_path / "unspec.pcap")
        assert (tmp_path / "unspec.pcap").exists()


class TestReadPcapFallback:
    def test_tshark_fallback_nonzero_exit(self, tmp_path: Path) -> None:
        pcap_path = tmp_path / "broken.pcap"
        pcap_path.write_bytes(b"\x00" * 64)
        with patch(
            "general_ludd.networking.scapy_adapter.tshark_available",
            return_value=True,
        ), patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout=b"", stderr=b"error")
            result = read_pcap(pcap_path)
        assert result == []

    def test_scapy_fallback_when_tshark_unavailable(self, tmp_path: Path) -> None:
        if not _scapy_available_check():
            pytest.skip("scapy not available")
        from scapy.all import IP, TCP, Ether, wrpcap

        pcap_path = tmp_path / "scapy_fallback.pcap"
        pkt = Ether() / IP(src="10.0.0.1", dst="10.0.0.2") / TCP(sport=1, dport=80)
        wrpcap(str(pcap_path), [pkt])
        with patch(
            "general_ludd.networking.scapy_adapter.tshark_available",
            return_value=False,
        ):
            result = read_pcap(pcap_path)
        assert len(result) > 0
        assert isinstance(result[0], PacketSummary)


class TestParseAsnWhoisEdgeCases:
    def test_asnumber_format(self) -> None:
        whois = "ASNumber:       16509\nowner:          Amazon.com, Inc.\n"
        result = parse_asn_whois(whois)
        assert result.asn == 16509
        assert result.organization == "Amazon.com, Inc."

    def test_asn_non_numeric(self) -> None:
        result = parse_asn_whois("aut-num: ASXYZ\n")
        assert result.asn == 0

    def test_truncated_line(self) -> None:
        result = parse_asn_whois("aut-num:")
        assert result.asn == 0


def _scapy_available_check() -> bool:
    from general_ludd.networking.scapy_adapter import scapy_available

    return scapy_available()
