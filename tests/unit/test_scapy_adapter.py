"""Structural tests for scapy/tshark networking adapter."""

from __future__ import annotations

from general_ludd.networking.scapy_adapter import (
    AsnInfo,
    BgpCommunity,
    CidrRange,
    PacketSummary,
    TrafficReport,
    _first_int,
    _parse_tshark_json,
    craft_packet,
    dissect_packet,
    parse_asn_rdap,
    parse_asn_whois,
    parse_bgp_community,
    parse_cidr,
    scapy_available,
    tshark_available,
)


def test_asn_info_defaults() -> None:
    info = AsnInfo()
    assert info.asn == 0
    assert info.name == ""
    assert info.organization == ""
    assert info.country == ""
    assert info.prefixes == []
    assert info.peers == []


def test_asn_info_explicit() -> None:
    info = AsnInfo(
        asn=15169,
        name="GOOGLE",
        organization="Google LLC",
        country="US",
        rir="ARIN",
        prefix="8.8.8.0/24",
        prefixes=["8.8.8.0/24", "8.8.4.0/24"],
        peers=["AS1299"],
    )
    assert info.asn == 15169
    assert info.name == "GOOGLE"
    assert info.organization == "Google LLC"
    assert info.country == "US"
    assert info.rir == "ARIN"
    assert info.prefix == "8.8.8.0/24"
    assert info.prefixes == ["8.8.8.0/24", "8.8.4.0/24"]
    assert info.peers == ["AS1299"]


def test_bgp_community_defaults() -> None:
    c = BgpCommunity()
    assert c.asn == 0
    assert c.value == 0
    assert c.description == ""
    assert c.well_known == ""
    assert c.raw == ""


def test_bgp_community_explicit() -> None:
    c = BgpCommunity(asn=65001, value=100, description="Local pref", raw="65001:100")
    assert c.asn == 65001
    assert c.value == 100
    assert c.description == "Local pref"
    assert c.well_known == ""
    assert c.raw == "65001:100"


def test_cidr_range_defaults() -> None:
    r = CidrRange()
    assert r.network == ""
    assert r.prefix_length == 0
    assert r.first_address == ""
    assert r.last_address == ""
    assert r.total_addresses == 0


def test_cidr_range_explicit() -> None:
    r = CidrRange(
        network="10.0.0.0",
        prefix_length=24,
        first_address="10.0.0.1",
        last_address="10.0.0.254",
        total_addresses=256,
    )
    assert r.network == "10.0.0.0"
    assert r.prefix_length == 24
    assert r.first_address == "10.0.0.1"
    assert r.last_address == "10.0.0.254"
    assert r.total_addresses == 256


def test_traffic_report_defaults() -> None:
    r = TrafficReport()
    assert r.total_packets == 0
    assert r.protocols == {}
    assert r.top_talkers == {}
    assert r.top_ports == {}
    assert r.flows == []
    assert r.duration_seconds == 0.0


def test_traffic_report_explicit() -> None:
    r = TrafficReport(
        total_packets=42,
        protocols={"tcp": 30, "udp": 12},
        top_talkers={"10.0.0.1": 20, "10.0.0.2": 15},
        top_ports={80: 25, 443: 10},
        flows=[{"src": "10.0.0.1", "dst": "10.0.0.2", "bytes": 4096}],
        duration_seconds=1.5,
    )
    assert r.total_packets == 42
    assert r.protocols == {"tcp": 30, "udp": 12}
    assert r.top_talkers["10.0.0.1"] == 20
    assert r.top_ports[80] == 25
    assert len(r.flows) == 1
    assert r.flows[0]["bytes"] == 4096
    assert r.duration_seconds == 1.5


def test_packet_summary_defaults() -> None:
    p = PacketSummary()
    assert p.timestamp == 0.0
    assert p.length == 0
    assert p.src_ip == ""
    assert p.dst_ip == ""
    assert p.protocol == ""
    assert p.src_port is None
    assert p.dst_port is None
    assert p.flags is None
    assert p.info == ""


def test_packet_summary_explicit() -> None:
    p = PacketSummary(
        timestamp=1700000000.123456,
        length=1500,
        src_ip="192.168.1.1",
        dst_ip="10.0.0.1",
        protocol="tcp",
        src_port=54321,
        dst_port=443,
        flags="SA",
        info="HTTP request",
    )
    assert p.timestamp == 1700000000.123456
    assert p.length == 1500
    assert p.src_ip == "192.168.1.1"
    assert p.dst_ip == "10.0.0.1"
    assert p.protocol == "tcp"
    assert p.src_port == 54321
    assert p.dst_port == 443
    assert p.flags == "SA"
    assert p.info == "HTTP request"


def test_tshark_available_returns_bool() -> None:
    result = tshark_available()
    assert isinstance(result, bool)


def test_scapy_available_returns_bool() -> None:
    result = scapy_available()
    assert isinstance(result, bool)


def test_parse_tshark_json_with_sample_data() -> None:
    raw = [
        {
            "_source": {
                "layers": {
                    "frame.time_epoch": ["1700000000.123456"],
                    "frame.len": ["1514"],
                    "frame.protocols": ["eth:ethertype:ip:tcp:http"],
                    "ip.src": ["192.168.1.100"],
                    "ip.dst": ["93.184.216.34"],
                    "tcp.srcport": ["52341"],
                    "tcp.dstport": ["80"],
                    "tcp.flags.str": ["PA"],
                }
            }
        }
    ]
    results = _parse_tshark_json(raw)
    assert len(results) == 1
    pkt = results[0]
    assert pkt.src_ip == "192.168.1.100"
    assert pkt.dst_ip == "93.184.216.34"
    assert pkt.protocol == "http"
    assert pkt.length == 1514
    assert pkt.src_port == 52341
    assert pkt.dst_port == 80
    assert pkt.flags == "PA"
    assert pkt.timestamp == 1700000000.123456


def test_parse_tshark_json_udp_without_tcp() -> None:
    raw = [
        {
            "_source": {
                "layers": {
                    "frame.time_epoch": ["1699999999.0"],
                    "frame.len": ["256"],
                    "frame.protocols": ["eth:ethertype:ip:udp:dns"],
                    "ip.src": ["10.0.0.1"],
                    "ip.dst": ["8.8.8.8"],
                    "udp.srcport": ["5353"],
                    "udp.dstport": ["53"],
                }
            }
        }
    ]
    results = _parse_tshark_json(raw)
    assert len(results) == 1
    pkt = results[0]
    assert pkt.protocol == "dns"
    assert pkt.src_port == 5353
    assert pkt.dst_port == 53
    assert pkt.flags is None


def test_parse_tshark_json_missing_fields() -> None:
    raw = [{"_source": {"layers": {}}}]
    results = _parse_tshark_json(raw)
    assert len(results) == 1
    pkt = results[0]
    assert pkt.src_ip == ""
    assert pkt.dst_ip == ""
    assert pkt.length == 0
    assert pkt.src_port is None
    assert pkt.dst_port is None


def test_parse_tshark_json_empty_list() -> None:
    results = _parse_tshark_json([])
    assert results == []


def test_first_int_with_valid_values() -> None:
    assert _first_int(["42"]) == 42
    assert _first_int(["0"]) == 0
    assert _first_int([None, "99"]) == 99


def test_first_int_with_invalid_values() -> None:
    assert _first_int([None]) is None
    assert _first_int([]) is None
    assert _first_int(["abc"]) is None


def test_parse_cidr_ipv4_class_c() -> None:
    result = parse_cidr("192.168.1.0/24")
    assert result.network == "192.168.1.0"
    assert result.prefix_length == 24
    assert result.first_address == "192.168.1.1"
    assert result.last_address == "192.168.1.254"
    assert result.total_addresses == 256


def test_parse_cidr_ipv4_subnet() -> None:
    result = parse_cidr("10.0.0.0/8")
    assert result.network == "10.0.0.0"
    assert result.prefix_length == 8
    assert result.total_addresses == 16777216
    assert result.first_address == "10.0.0.1"
    assert result.last_address == "10.255.255.254"


def test_parse_cidr_ipv4_single_host() -> None:
    result = parse_cidr("192.168.1.42/32")
    assert result.network == "192.168.1.42"
    assert result.prefix_length == 32
    assert result.first_address == "192.168.1.42"
    assert result.last_address == "192.168.1.42"
    assert result.total_addresses == 1


def test_parse_cidr_ipv4_point_to_point() -> None:
    result = parse_cidr("10.0.0.0/31")
    assert result.network == "10.0.0.0"
    assert result.prefix_length == 31
    assert result.total_addresses == 2
    assert result.first_address == "10.0.0.0"
    assert result.last_address == "10.0.0.1"


def test_parse_cidr_ipv6() -> None:
    result = parse_cidr("2001:db8::/32")
    assert result.network == "2001:db8::"
    assert result.prefix_length == 32
    assert result.total_addresses > 0


def test_parse_cidr_invalid_returns_cidr_field() -> None:
    result = parse_cidr("not-a-cidr")
    assert result.network == "not-a-cidr"
    assert result.prefix_length == 0


def test_parse_asn_whois_valid() -> None:
    whois = (
        "aut-num:        AS15169\n"
        "as-name:        GOOGLE\n"
        "org-name:       Google LLC\n"
        "country:        US\n"
        "route:          8.8.8.0/24\n"
    )
    result = parse_asn_whois(whois)
    assert result.asn == 15169
    assert result.name == "GOOGLE"
    assert result.organization == "Google LLC"
    assert result.country == "US"
    assert result.prefix == "8.8.8.0/24"


def test_parse_asn_whois_lowercase_keys() -> None:
    whois = (
        "aut-num:        as32934\n"
        "as-name:        FACEBOOK\n"
        "org-name:       Meta Platforms, Inc.\n"
    )
    result = parse_asn_whois(whois)
    assert result.asn == 32934
    assert result.name == "FACEBOOK"
    assert result.organization == "Meta Platforms, Inc."


def test_parse_asn_whois_empty() -> None:
    result = parse_asn_whois("")
    assert result.asn == 0
    assert result.name == ""


def test_parse_asn_whois_whitespace_only() -> None:
    result = parse_asn_whois("   \n  \n")
    assert result.asn == 0


def test_parse_asn_rdap_valid() -> None:
    rdap = {
        "autnum": 15169,
        "name": "GOOGLE",
        "entities": [
            {
                "vcardArray": [
                    "vcard",
                    [["version", {}, "text", "4.0"], ["org", {}, "text", "Google LLC"]],
                ]
            },
            {"country": "US"},
        ],
    }
    result = parse_asn_rdap(rdap)
    assert result.asn == 15169
    assert result.name == "Google LLC"
    assert result.organization == "Google LLC"
    assert result.country == "US"


def test_parse_asn_rdap_fallback_asn_key() -> None:
    rdap = {"asn": 13335, "name": "CLOUDFLARENET"}
    result = parse_asn_rdap(rdap)
    assert result.asn == 13335
    assert result.name == "CLOUDFLARENET"


def test_parse_asn_rdap_empty() -> None:
    result = parse_asn_rdap({})
    assert result.asn == 0
    assert result.name == ""


def test_parse_bgp_community_standard() -> None:
    result = parse_bgp_community("65001:100")
    assert result.asn == 65001
    assert result.value == 100
    assert result.raw == "65001:100"
    assert result.well_known == ""


def test_parse_bgp_community_with_parentheses() -> None:
    result = parse_bgp_community("(65001:200)")
    assert result.asn == 65001
    assert result.value == 200
    assert result.raw == "(65001:200)"


def test_parse_bgp_community_well_known_no_export() -> None:
    result = parse_bgp_community("NO_EXPORT")
    assert result.asn == 65535
    assert result.value == 65281
    assert result.well_known == "NO_EXPORT"
    assert result.raw == "NO_EXPORT"


def test_parse_bgp_community_well_known_no_advertise() -> None:
    result = parse_bgp_community("NO_ADVERTISE")
    assert result.asn == 65535
    assert result.value == 65282
    assert result.well_known == "NO_ADVERTISE"


def test_parse_bgp_community_numeric_parsed_as_standard() -> None:
    result = parse_bgp_community("65535:65281")
    assert result.asn == 65535
    assert result.value == 65281
    assert result.well_known == ""
    assert result.raw == "65535:65281"


def test_parse_bgp_community_non_numeric_parts() -> None:
    result = parse_bgp_community("abc:def")
    assert result.asn == 0
    assert result.value == 0
    assert result.raw == "abc:def"


def test_parse_bgp_community_single_part() -> None:
    result = parse_bgp_community("somevalue")
    assert result.asn == 0
    assert result.value == 0
    assert result.raw == "somevalue"


def test_craft_packet_basic() -> None:
    result = craft_packet(layers=["eth", "ip", "tcp"], fields={"src": "10.0.0.1", "dst": "10.0.0.2"})
    assert result["protocols"] == ["eth", "ip", "tcp"]
    assert result["fields"]["src"] == "10.0.0.1"
    assert result["fields"]["dst"] == "10.0.0.2"
    assert "_scapy_available" in result


def test_craft_packet_empty() -> None:
    result = craft_packet(layers=[], fields={})
    assert result["protocols"] == []
    assert result["fields"] == {}


def test_dissect_packet_minimal_bytes() -> None:
    result = dissect_packet(b"\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x08\x00")
    assert result["length"] == 14
    layers = result.get("layers", {})
    assert "Ethernet" in layers
    assert layers["Ethernet"]["dst"]
    assert layers["Ethernet"]["src"]
    assert layers["Ethernet"]["type"] == "0x0800"


def test_dissect_packet_empty_bytes() -> None:
    result = dissect_packet(b"")
    assert result["raw_hex"] == ""
    assert result["length"] == 0


def test_dissect_packet_short_bytes() -> None:
    result = dissect_packet(b"\x00\x01\x02\x03")
    assert result["raw_hex"] == "00010203"
    assert result["length"] == 4
