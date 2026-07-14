"""Unit tests for the networking Scapy/tshark adapter.

Covers: pcap read/write, packet craft, pcap analysis, raw-byte dissection,
ASN WHOIS/RDAP parsing, CIDR subnet calculation, BGP community parsing,
and edge cases.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from general_ludd.networking import (
    PacketSummary,
    TrafficReport,
    analyze_pcap,
    craft_packet,
    dissect_packet,
    parse_asn_rdap,
    parse_asn_whois,
    parse_bgp_community,
    parse_cidr,
    read_pcap,
    scapy_available,
    write_pcap,
)


class TestReadPcap:
    def test_read_nonexistent_returns_empty(self) -> None:
        result = read_pcap("/nonexistent/path.pcap")
        assert result == []

    def test_read_pcap_tshark_or_scapy(self, tmp_path: Path) -> None:
        if scapy_available():
            self._roundtrip_via_scapy(tmp_path)
        else:
            self._roundtrip_via_raw(tmp_path)

    def _roundtrip_via_scapy(self, tmp_path: Path) -> None:
        from scapy.all import IP, TCP, Ether, wrpcap

        pcap_path = tmp_path / "test.pcap"
        pkt = Ether() / IP(src="10.0.0.1", dst="10.0.0.2") / TCP(sport=12345, dport=80)
        wrpcap(str(pcap_path), [pkt])

        pkts = read_pcap(pcap_path)
        assert len(pkts) > 0
        assert isinstance(pkts[0], PacketSummary)

    def _roundtrip_via_raw(self, tmp_path: Path) -> None:
        pcap_path = tmp_path / "raw.pcap"
        pcap_path.write_bytes(_minimal_pcap_header() + b"\x00" * 100)
        result = read_pcap(pcap_path)
        assert isinstance(result, list)


class TestWritePcap:
    def test_write_creates_file(self, tmp_path: Path) -> None:
        pcap_path = tmp_path / "out.pcap"
        pkts = [PacketSummary(
            timestamp=1000.0, src_ip="10.0.0.1", dst_ip="10.0.0.2",
            protocol="TCP", length=100,
        )]
        write_pcap(pkts, pcap_path)
        assert pcap_path.exists()
        assert pcap_path.stat().st_size > 0

    def test_write_creates_nested_dirs(self, tmp_path: Path) -> None:
        pcap_path = tmp_path / "a" / "b" / "out.pcap"
        write_pcap([], pcap_path)
        assert pcap_path.exists()

    def test_write_read_roundtrip(self, tmp_path: Path) -> None:
        pkts = [
            PacketSummary(
                timestamp=1000.0, src_ip="10.0.0.1", dst_ip="10.0.0.2",
                protocol="TCP", length=60, src_port=5555, dst_port=80,
            ),
            PacketSummary(
                timestamp=1001.0, src_ip="10.0.0.1", dst_ip="10.0.0.3",
                protocol="UDP", length=50, src_port=5555, dst_port=53,
            ),
        ]
        pcap_path = tmp_path / "roundtrip.pcap"
        write_pcap(pkts, pcap_path)
        if scapy_available():
            result = read_pcap(pcap_path)
            assert len(result) >= len(pkts)


class TestPacketSummary:
    def test_fields(self) -> None:
        ps = PacketSummary(
            timestamp=1234.5, src_ip="192.168.1.1", dst_ip="192.168.1.2",
            protocol="TCP", length=1500, src_port=443, dst_port=54321,
            flags="SYN ACK", info="test packet",
        )
        assert ps.src_ip == "192.168.1.1"
        assert ps.dst_ip == "192.168.1.2"
        assert ps.protocol == "TCP"
        assert ps.length == 1500
        assert ps.src_port == 443
        assert ps.dst_port == 54321
        assert ps.flags == "SYN ACK"

    def test_defaults(self) -> None:
        ps = PacketSummary(timestamp=0.0, src_ip="", dst_ip="", protocol="", length=0)
        assert ps.src_port is None
        assert ps.dst_port is None
        assert ps.flags is None
        assert ps.info == ""


class TestTrafficReport:
    def test_defaults(self) -> None:
        tr = TrafficReport()
        assert tr.total_packets == 0
        assert tr.protocols == {}
        assert tr.top_talkers == {}
        assert tr.top_ports == {}
        assert tr.flows == []

    def test_can_set_fields(self) -> None:
        tr = TrafficReport(
            total_packets=100,
            protocols={"TCP": 80, "UDP": 20},
            top_talkers={"10.0.0.1": 50},
            top_ports={80: 40, 443: 30},
            duration_seconds=15.5,
        )
        assert tr.total_packets == 100
        assert tr.protocols["TCP"] == 80
        assert tr.top_talkers["10.0.0.1"] == 50


class TestCraftPacket:
    def test_craft_tcp_packet(self) -> None:
        spec = craft_packet(["IP", "TCP"], {"src": "10.0.0.1", "dst": "10.0.0.2", "dport": "80"})
        assert "protocols" in spec
        assert "IP" in spec["protocols"]
        assert "TCP" in spec["protocols"]
        assert spec["fields"]["src"] == "10.0.0.1"

    def test_craft_udp_packet(self) -> None:
        spec = craft_packet(["IP", "UDP"], {"src": "10.0.0.1", "dst": "10.0.0.3", "dport": "53"})
        assert "UDP" in spec["protocols"]

    def test_craft_empty_stack(self) -> None:
        spec = craft_packet([], {})
        assert spec["protocols"] == []


class TestAnalyzePcap:
    def test_analyze_nonexistent_returns_empty(self) -> None:
        report = analyze_pcap("/nonexistent.pcap")
        assert isinstance(report, TrafficReport)
        assert report.total_packets == 0

    def test_analyze_valid_pcap(self, tmp_path: Path) -> None:
        if not scapy_available():
            pytest.skip("scapy not available")
        from scapy.all import IP, TCP, UDP, Ether, wrpcap

        pcap_path = tmp_path / "analyze.pcap"
        pkts = [
            Ether() / IP(src="10.0.0.1", dst="10.0.0.2") / TCP(sport=1, dport=80),
            Ether() / IP(src="10.0.0.1", dst="10.0.0.3") / TCP(sport=2, dport=443),
            Ether() / IP(src="10.0.0.2", dst="10.0.0.1") / UDP(sport=3, dport=53),
        ]
        wrpcap(str(pcap_path), pkts)

        report = analyze_pcap(pcap_path)
        assert isinstance(report, TrafficReport)
        assert report.total_packets == 3


class TestDissectPacket:
    def test_dissect_ip_tcp_bytes(self) -> None:
        if not scapy_available():
            pytest.skip("scapy not available")
        from scapy.all import IP, TCP
        from scapy.all import raw as scapy_raw
        pkt = IP(src="10.0.0.1", dst="10.0.0.2") / TCP(sport=12345, dport=80, flags="S")
        raw_bytes = scapy_raw(pkt)

        result = dissect_packet(raw_bytes)
        assert "layers" in result
        layers = result["layers"]
        assert "IP" in layers
        assert "TCP" in layers

    def test_dissect_short_bytes(self) -> None:
        result = dissect_packet(b"\x45\x00")
        assert "raw_hex" in result
        assert result["length"] == 2

    def test_dissect_struct_fallback(self) -> None:
        result = dissect_packet(b"\xff" * 64)
        assert "raw_hex" in result or "error" in result

    def test_dissect_empty_bytes(self) -> None:
        result = dissect_packet(b"")
        assert result["length"] == 0


class TestAsnParsing:
    def test_parse_asn_whois_ripe(self) -> None:
        whois = (
            "aut-num:        AS15169\n"
            "as-name:        GOOGLE\n"
            "org-name:       Google LLC\n"
            "country:        US\n"
            "route:          8.8.8.0/24\n"
        )
        result = parse_asn_whois(whois)
        assert result.asn == 15169
        assert result.organization == "Google LLC"
        assert result.country == "US"
        assert result.prefix == "8.8.8.0/24"

    def test_parse_asn_whois_arin(self) -> None:
        whois = (
            "ASNumber:       16509\n"
            "owner:          Amazon.com, Inc.\n"
            "country:        US\n"
        )
        result = parse_asn_whois(whois)
        assert result.asn == 16509
        assert result.organization == "Amazon.com, Inc."
        assert result.country == "US"

    def test_parse_asn_whois_empty(self) -> None:
        result = parse_asn_whois("")
        assert result.asn == 0
        assert result.organization == ""

    def test_parse_asn_rdap_basic(self) -> None:
        rdap = {"autnum": 15169, "name": "GOOGLE", "entities": [{"country": "US"}]}
        result = parse_asn_rdap(rdap)
        assert result.asn == 15169
        assert result.organization == "GOOGLE"
        assert result.country == "US"

    def test_parse_asn_rdap_vcard(self) -> None:
        rdap = {
            "asn": 13335,
            "entities": [{
                "vcardArray": [
                    "vcard",
                    [["version", {}, "text", "4.0"], ["org", {}, "text", "Cloudflare, Inc."]],
                ]
            }],
        }
        result = parse_asn_rdap(rdap)
        assert result.asn == 13335
        assert result.organization == "Cloudflare, Inc."

    def test_parse_asn_rdap_empty(self) -> None:
        result = parse_asn_rdap({})
        assert result.asn == 0


class TestCidrParsing:
    def test_parse_cidr_v4_24(self) -> None:
        result = parse_cidr("192.168.1.0/24")
        assert result.network == "192.168.1.0"
        assert result.prefix_length == 24
        assert result.first_address == "192.168.1.1"
        assert result.last_address == "192.168.1.254"
        assert result.total_addresses == 256

    def test_parse_cidr_v4_32(self) -> None:
        result = parse_cidr("10.0.0.1/32")
        assert result.network == "10.0.0.1"
        assert result.prefix_length == 32
        assert result.total_addresses == 1

    def test_parse_cidr_v6(self) -> None:
        result = parse_cidr("2001:db8::/32")
        assert result.network == "2001:db8::"
        assert result.prefix_length == 32

    def test_parse_cidr_invalid(self) -> None:
        result = parse_cidr("not-a-cidr")
        assert result.network == "not-a-cidr"
        assert result.prefix_length == 0

    def test_parse_cidr_class_a(self) -> None:
        result = parse_cidr("10.0.0.0/8")
        assert result.network == "10.0.0.0"
        assert result.total_addresses == 16777216


class TestBgpParsing:
    def test_parse_standard_community(self) -> None:
        result = parse_bgp_community("15169:100")
        assert result.asn == 15169
        assert result.value == 100
        assert result.raw == "15169:100"

    def test_parse_with_parens(self) -> None:
        result = parse_bgp_community("(13335:200)")
        assert result.asn == 13335
        assert result.value == 200

    def test_parse_noexport(self) -> None:
        result = parse_bgp_community("65535:65281")
        assert result.asn == 65535
        assert result.value == 65281

    def test_parse_invalid(self) -> None:
        result = parse_bgp_community("not-valid")
        assert result.asn == 0
        assert result.value == 0
        assert result.raw == "not-valid"

    def test_parse_empty(self) -> None:
        result = parse_bgp_community("")
        assert result.asn == 0


class TestScapyAvailable:
    def test_returns_bool(self) -> None:
        available = scapy_available()
        assert isinstance(available, bool)


def _minimal_pcap_header() -> bytes:
    import struct
    magic = 0xA1B2C3D4
    return struct.pack("<IHHiIII", magic, 2, 4, 0, 0, 65535, 1)
