"""Deep tests for proxy_protocol: v1/v2 parsing, TLV handling, build round-trip,
edge cases, error paths, and HAProxy compliance.

≥15 test methods across multiple classes.
"""

from __future__ import annotations

import struct

import pytest

from general_ludd.network.proxy_protocol import (
    TLV,
    V2_SIGNATURE,
    AddressFamily,
    ProxyCommand,
    ProxyProtocolError,
    ProxyProtocolHeader,
    ProxyTransport,
    build_proxy_header,
    parse_proxy_header,
)

# ── helpers ──────────────────────────────────────────────────────────────────


def _build_v2_header(
    ver_cmd: int = 0x21,
    protocol: int = 0x11,
    addr_data: bytes = b"",
    addr_len: int | None = None,
) -> bytes:
    sig = V2_SIGNATURE
    pl = addr_len if addr_len is not None else len(addr_data)
    return sig + struct.pack("!BBH", ver_cmd, protocol, pl) + addr_data


# ── v1 parsing ────────────────────────────────────────────────────────────────


class TestV1Parsing:
    def test_tcp4(self) -> None:
        h = parse_proxy_header(b"PROXY TCP4 192.168.0.1 10.0.0.1 443 8080\r\n")
        assert h.version == 1
        assert h.command == ProxyCommand.PROXY
        assert h.family == AddressFamily.AF_INET
        assert h.transport == ProxyTransport.STREAM
        assert h.src_addr == "192.168.0.1"
        assert h.dst_addr == "10.0.0.1"
        assert h.src_port == 443
        assert h.dst_port == 8080
        assert h.tlvs == []

    def test_tcp6(self) -> None:
        h = parse_proxy_header(b"PROXY TCP6 2001:db8::1 fe80::1 443 8080\r\n")
        assert h.version == 1
        assert h.family == AddressFamily.AF_INET6
        assert h.src_addr == "2001:db8::1"
        assert h.dst_addr == "fe80::1"
        assert h.src_port == 443
        assert h.dst_port == 8080

    def test_unknown(self) -> None:
        h = parse_proxy_header(b"PROXY UNKNOWN\r\n")
        assert h.version == 1
        assert h.command == ProxyCommand.PROXY
        assert h.family == AddressFamily.AF_UNSPEC

    def test_extra_unknown_args(self) -> None:
        h = parse_proxy_header(b"PROXY UNKNOWN extra stuff here\r\n")
        assert h.version == 1
        assert h.family == AddressFamily.AF_UNSPEC

    def test_trailing_data_after_crlf(self) -> None:
        h = parse_proxy_header(b"PROXY TCP4 10.0.0.1 10.0.0.2 80 443\r\nGET / HTTP/1.1...")
        assert h.version == 1
        assert h.src_addr == "10.0.0.1"

    def test_invalid_address_rejected(self) -> None:
        with pytest.raises(ProxyProtocolError, match="invalid"):
            parse_proxy_header(b"PROXY TCP4 999.999.999.999 10.0.0.2 80 443\r\n")

    def test_missing_crlf_rejected(self) -> None:
        with pytest.raises(ProxyProtocolError, match="CRLF"):
            parse_proxy_header(b"PROXY TCP4 1.2.3.4 5.6.7.8 80 443")

    def test_wrong_field_count_rejected(self) -> None:
        with pytest.raises(ProxyProtocolError, match="6 fields"):
            parse_proxy_header(b"PROXY TCP4 1.2.3.4 5.6.7.8 80\r\n")

    def test_invalid_port_rejected(self) -> None:
        with pytest.raises(ProxyProtocolError, match="port"):
            parse_proxy_header(b"PROXY TCP4 1.2.3.4 5.6.7.8 abc 443\r\n")


# ── v2 parsing ────────────────────────────────────────────────────────────────


class TestV2Parsing:
    def test_local_command(self) -> None:
        hdr = _build_v2_header(ver_cmd=(2 << 4) | ProxyCommand.LOCAL, protocol=0x00)
        h = parse_proxy_header(hdr)
        assert h.version == 2
        assert h.command == ProxyCommand.LOCAL
        assert h.family == AddressFamily.AF_UNSPEC
        assert h.src_addr is None

    def test_proxy_ipv4_tcp(self) -> None:
        addr = struct.pack("!4s4sHH", b"\x7f\x00\x00\x01", b"\x0a\x00\x00\x01", 443, 8080)
        hdr = _build_v2_header(ver_cmd=0x21, protocol=0x11, addr_data=addr)
        h = parse_proxy_header(hdr)
        assert h.command == ProxyCommand.PROXY
        assert h.family == AddressFamily.AF_INET
        assert h.transport == ProxyTransport.STREAM
        assert h.src_addr == "127.0.0.1"
        assert h.dst_addr == "10.0.0.1"
        assert h.src_port == 443
        assert h.dst_port == 8080

    def test_proxy_ipv6_tcp(self) -> None:
        src = b"\x20\x01" + b"\x00" * 13 + b"\x01"
        dst = b"\xfe\x80" + b"\x00" * 13 + b"\x01"
        addr = struct.pack("!16s16sHH", src, dst, 80, 443)
        hdr = _build_v2_header(ver_cmd=0x21, protocol=0x21, addr_data=addr)
        h = parse_proxy_header(hdr)
        assert h.family == AddressFamily.AF_INET6
        assert h.src_addr == "2001::1"
        assert h.dst_addr == "fe80::1"
        assert h.src_port == 80
        assert h.dst_port == 443

    def test_proxy_unix_tcp(self) -> None:
        src = b"/tmp/foo.sock".ljust(108, b"\x00")
        dst = b"/tmp/bar.sock".ljust(108, b"\x00")
        addr = struct.pack("!108s108s", src, dst)
        hdr = _build_v2_header(ver_cmd=0x21, protocol=0x31, addr_data=addr)
        h = parse_proxy_header(hdr)
        assert h.family == AddressFamily.AF_UNIX
        assert h.src_addr == "/tmp/foo.sock"
        assert h.dst_addr == "/tmp/bar.sock"

    def test_short_header_rejected(self) -> None:
        with pytest.raises(ProxyProtocolError, match="too short"):
            parse_proxy_header(V2_SIGNATURE + b"\x21\x11\x00")

    def test_wrong_version_rejected(self) -> None:
        hdr = _build_v2_header(ver_cmd=(3 << 4) | ProxyCommand.PROXY, protocol=0x11)
        with pytest.raises(ProxyProtocolError, match="version"):
            parse_proxy_header(hdr)

    def test_unknown_command_rejected(self) -> None:
        hdr = _build_v2_header(ver_cmd=(2 << 4) | 0x0F, protocol=0x11)
        with pytest.raises(ProxyProtocolError, match="command"):
            parse_proxy_header(hdr)

    def test_truncated_address_data(self) -> None:
        addr = struct.pack("!4s4sHH", b"\x7f\x00\x00\x01", b"\x0a\x00\x00\x01", 443, 8080)
        hdr = _build_v2_header(ver_cmd=0x21, protocol=0x11, addr_data=addr, addr_len=len(addr) + 10)
        with pytest.raises(ProxyProtocolError, match="truncated"):
            parse_proxy_header(hdr)


# ── TLV parsing ───────────────────────────────────────────────────────────────


class TestTLVParsing:
    def test_single_tlv(self) -> None:
        tlv = struct.pack("!BH5s", 0x01, 5, b"h2-14")
        hdr = _build_v2_header(ver_cmd=0x21, protocol=0x11, addr_data=b"\x00" * 12 + tlv)
        h = parse_proxy_header(hdr)
        assert len(h.tlvs) == 1
        t = h.tlvs[0]
        assert t.type == 0x01
        assert t.type_name == "ALPN"
        assert t.value == b"h2-14"

    def test_multiple_tlvs(self) -> None:
        alpn = struct.pack("!BH5s", 0x01, 5, b"h2-14")
        auth = struct.pack("!BH11s", 0x02, 11, b"example.com")
        hdr = _build_v2_header(
            ver_cmd=0x21,
            protocol=0x11,
            addr_data=b"\x00" * 12 + alpn + auth,
        )
        h = parse_proxy_header(hdr)
        assert len(h.tlvs) == 2
        assert h.tlvs[0].type_name == "ALPN"
        assert h.tlvs[1].type_name == "AUTHORITY"
        assert h.tlv_values_by_type(0x02) == [b"example.com"]

    def test_unknown_tlv_type(self) -> None:
        tlv = struct.pack("!BH3s", 0xFF, 3, b"abc")
        hdr = _build_v2_header(ver_cmd=0x21, protocol=0x11, addr_data=b"\x00" * 12 + tlv)
        h = parse_proxy_header(hdr)
        assert h.tlvs[0].type_name == "UNKNOWN_0xFF"

    def test_noop_tlv(self) -> None:
        tlv = struct.pack("!BH", 0x04, 0)
        hdr = _build_v2_header(ver_cmd=0x21, protocol=0x11, addr_data=b"\x00" * 12 + tlv)
        h = parse_proxy_header(hdr)
        assert len(h.tlvs) == 1
        assert h.tlvs[0].type_name == "NOOP"
        assert h.tlvs[0].value == b""

    def test_ssl_tlv(self) -> None:
        version = b"TLSv1.3"
        version_tlv = struct.pack("!BH8s", 0x21, len(version), version)
        ssl_data = struct.pack("!BI", 0x01, 0) + version_tlv
        tlv = struct.pack(f"!BH{len(ssl_data)}s", 0x20, len(ssl_data), ssl_data)
        hdr = _build_v2_header(ver_cmd=0x21, protocol=0x11, addr_data=b"\x00" * 12 + tlv)
        h = parse_proxy_header(hdr)
        assert h.tlvs[0].type_name == "SSL"
        assert b"TLSv1.3" in h.tlvs[0].value

    def test_tlv_utf8_accessor(self) -> None:
        tlv = struct.pack("!BH11s", 0x02, 11, b"example.com")
        hdr = _build_v2_header(ver_cmd=0x21, protocol=0x11, addr_data=b"\x00" * 12 + tlv)
        h = parse_proxy_header(hdr)
        assert h.tlvs[0].value_utf8 == "example.com"

    def test_tlv_lookup_by_type(self) -> None:
        a = struct.pack("!BH5s", 0x01, 5, b"h2-17")
        b = struct.pack("!BH12s", 0x02, 12, b"myhost.local")
        hdr = _build_v2_header(ver_cmd=0x21, protocol=0x11, addr_data=b"\x00" * 12 + a + b)
        h = parse_proxy_header(hdr)
        assert h.tlv_by_type(0x01) is not None
        assert h.tlv_by_type(0x01).value == b"h2-17"  # type: ignore[union-attr]
        assert h.tlv_by_type(0x99) is None

    def test_truncated_tlv_handled(self) -> None:
        partial = struct.pack("!BH", 0x01, 100) + b"short"
        hdr = _build_v2_header(ver_cmd=0x21, protocol=0x11, addr_data=b"\x00" * 12 + partial)
        h = parse_proxy_header(hdr)
        assert h.tlvs == []

    def test_tlv_repr(self) -> None:
        t = TLV(type=0x01, value=b"h2")
        r = repr(t)
        assert "ALPN" in r
        assert "len=2" in r


# ── build & round-trip ────────────────────────────────────────────────────────


class TestBuildAndRoundTrip:
    def test_v1_build_tcp4(self) -> None:
        h = ProxyProtocolHeader(
            version=1,
            command=ProxyCommand.PROXY,
            transport=ProxyTransport.STREAM,
            family=AddressFamily.AF_INET,
            src_addr="1.2.3.4",
            dst_addr="5.6.7.8",
            src_port=80,
            dst_port=443,
        )
        data = build_proxy_header(h)
        assert data.startswith(b"PROXY TCP4")
        parsed = parse_proxy_header(data)
        assert parsed.src_addr == "1.2.3.4"
        assert parsed.src_port == 80

    def test_v2_ipv4_roundtrip(self) -> None:
        h = ProxyProtocolHeader(
            version=2,
            command=ProxyCommand.PROXY,
            transport=ProxyTransport.STREAM,
            family=AddressFamily.AF_INET,
            src_addr="10.0.0.1",
            dst_addr="10.0.0.2",
            src_port=8080,
            dst_port=9090,
            tlvs=[TLV(type=0x02, value=b"myhost")],
        )
        data = build_proxy_header(h)
        parsed = parse_proxy_header(data)
        assert parsed.src_addr == "10.0.0.1"
        assert parsed.dst_port == 9090
        assert parsed.tlv_by_type(0x02) is not None
        assert parsed.tlv_by_type(0x02).value == b"myhost"  # type: ignore[union-attr]

    def test_v2_ipv6_roundtrip(self) -> None:
        h = ProxyProtocolHeader(
            version=2,
            command=ProxyCommand.PROXY,
            transport=ProxyTransport.STREAM,
            family=AddressFamily.AF_INET6,
            src_addr="::1",
            dst_addr="fe80::2",
            src_port=443,
            dst_port=8443,
        )
        data = build_proxy_header(h)
        parsed = parse_proxy_header(data)
        assert parsed.src_addr == "::1"
        assert parsed.dst_addr == "fe80::2"

    def test_v2_local_build(self) -> None:
        h = ProxyProtocolHeader(
            version=2,
            command=ProxyCommand.LOCAL,
            transport=ProxyTransport.UNSPEC,
            family=AddressFamily.AF_UNSPEC,
        )
        data = build_proxy_header(h)
        parsed = parse_proxy_header(data)
        assert parsed.command == ProxyCommand.LOCAL

    def test_v2_dgram_roundtrip(self) -> None:
        h = ProxyProtocolHeader(
            version=2,
            command=ProxyCommand.PROXY,
            transport=ProxyTransport.DGRAM,
            family=AddressFamily.AF_INET,
            src_addr="192.168.1.1",
            dst_addr="192.168.1.2",
            src_port=53,
            dst_port=5353,
        )
        data = build_proxy_header(h)
        parsed = parse_proxy_header(data)
        assert parsed.transport == ProxyTransport.DGRAM
        assert parsed.src_addr == "192.168.1.1"

    def test_v2_unix_roundtrip(self) -> None:
        h = ProxyProtocolHeader(
            version=2,
            command=ProxyCommand.PROXY,
            transport=ProxyTransport.STREAM,
            family=AddressFamily.AF_UNIX,
            src_addr="/run/app.sock",
            dst_addr="/run/backend.sock",
        )
        data = build_proxy_header(h)
        parsed = parse_proxy_header(data)
        assert parsed.family == AddressFamily.AF_UNIX
        assert parsed.src_addr == "/run/app.sock"

    def test_empty_data_rejected(self) -> None:
        with pytest.raises(ProxyProtocolError, match="empty"):
            parse_proxy_header(b"")


# ── header helpers ────────────────────────────────────────────────────────────


class TestHeaderHelpers:
    def test_is_v1_v2(self) -> None:
        h1 = ProxyProtocolHeader(
            version=1,
            command=ProxyCommand.PROXY,
            transport=ProxyTransport.STREAM,
            family=AddressFamily.AF_INET,
        )
        h2 = ProxyProtocolHeader(
            version=2,
            command=ProxyCommand.LOCAL,
            transport=ProxyTransport.UNSPEC,
            family=AddressFamily.AF_UNSPEC,
        )
        assert h1.is_v1 is True
        assert h1.is_v2 is False
        assert h2.is_v2 is True

    def test_is_local(self) -> None:
        h = ProxyProtocolHeader(
            version=2,
            command=ProxyCommand.LOCAL,
            transport=ProxyTransport.UNSPEC,
            family=AddressFamily.AF_UNSPEC,
        )
        assert h.is_local is True

    def test_has_tlvs_empty(self) -> None:
        h = ProxyProtocolHeader(
            version=2,
            command=ProxyCommand.PROXY,
            transport=ProxyTransport.STREAM,
            family=AddressFamily.AF_INET,
        )
        assert h.has_tlvs is False

    def test_has_tlvs_populated(self) -> None:
        h = ProxyProtocolHeader(
            version=2,
            command=ProxyCommand.PROXY,
            transport=ProxyTransport.STREAM,
            family=AddressFamily.AF_INET,
            tlvs=[TLV(type=0x01, value=b"h2")],
        )
        assert h.has_tlvs is True

    def test_build_unsupported_version(self) -> None:
        h = ProxyProtocolHeader(
            version=99,
            command=ProxyCommand.PROXY,
            transport=ProxyTransport.STREAM,
            family=AddressFamily.AF_INET,
        )
        with pytest.raises(ProxyProtocolError, match="version"):
            build_proxy_header(h)


# ── garbage input ─────────────────────────────────────────────────────────────


class TestGarbageInput:
    def test_random_bytes_rejected(self) -> None:
        with pytest.raises(ProxyProtocolError, match="unknown proxy"):
            parse_proxy_header(b"GET / HTTP/1.1\r\n")

    def test_non_ascii_binary(self) -> None:
        with pytest.raises(ProxyProtocolError, match="unknown proxy"):
            parse_proxy_header(b"\x00\x01\x02\x03" * 4)

    def test_partial_v1_rejected(self) -> None:
        with pytest.raises(ProxyProtocolError, match="CRLF"):
            parse_proxy_header(b"PROXY TCP4 1.2.3.4")
