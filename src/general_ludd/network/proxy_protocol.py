"""HAProxy PROXY protocol v1/v2 parser with TLV handling.

Core types:
  ProxyProtocolHeader — parsed header: command, transport, addresses, TLVs
  parse_proxy_header  — parse a raw bytes header into ProxyProtocolHeader
  build_proxy_header  — encode a ProxyProtocolHeader into raw bytes

Supports:
  v1 (text): TCP4, TCP6, UNKNOWN
  v2 (binary): LOCAL, PROXY with IPv4/IPv6/Unix + TLV extensions
"""

from __future__ import annotations

import ipaddress
import socket
import struct
from dataclasses import dataclass, field
from enum import IntEnum

# ── constants ─────────────────────────────────────────────────────────────────

V2_SIGNATURE = b"\x0d\x0a\x0d\x0a\x00\x0d\x0a\x51\x55\x49\x54\x0a"
V2_SIGNATURE_LEN = 12
V2_HEADER_BASE_LEN = 16
V1_MIN_LEN = 15
MAX_TOTAL_LEN = 65535


class ProxyCommand(IntEnum):
    LOCAL = 0x0
    PROXY = 0x1


class ProxyTransport(IntEnum):
    UNSPEC = 0x00
    STREAM = 0x01
    DGRAM = 0x02


class AddressFamily(IntEnum):
    AF_UNSPEC = 0x0
    AF_INET = 0x1
    AF_INET6 = 0x2
    AF_UNIX = 0x3


V2_PROTOCOL_TO_TRANSPORT = {
    0x11: (AddressFamily.AF_INET, ProxyTransport.STREAM),
    0x12: (AddressFamily.AF_INET, ProxyTransport.DGRAM),
    0x21: (AddressFamily.AF_INET6, ProxyTransport.STREAM),
    0x22: (AddressFamily.AF_INET6, ProxyTransport.DGRAM),
    0x31: (AddressFamily.AF_UNIX, ProxyTransport.STREAM),
    0x32: (AddressFamily.AF_UNIX, ProxyTransport.DGRAM),
    0x00: (AddressFamily.AF_UNSPEC, ProxyTransport.UNSPEC),
}

TRANSPORT_TO_V2_PROTOCOL = {v: k for k, v in V2_PROTOCOL_TO_TRANSPORT.items()}

V2_ADDR_SIZES = {
    0x11: 12,
    0x12: 12,
    0x21: 36,
    0x22: 36,
    0x31: 216,
    0x32: 216,
    0x00: 0,
}

# ── TLV types ─────────────────────────────────────────────────────────────────


class TLVType(IntEnum):
    ALPN = 0x01
    AUTHORITY = 0x02
    CRC32C = 0x03
    NOOP = 0x04
    UNIQUE_ID = 0x05
    SSL = 0x20
    SSL_VERSION = 0x21
    SSL_CN = 0x22
    SSL_CIPHER = 0x23
    SSL_SIG_ALG = 0x24
    SSL_KEY_ALG = 0x25
    NETNS = 0x30


_TLV_TYPE_NAMES = {
    0x01: "ALPN",
    0x02: "AUTHORITY",
    0x03: "CRC32C",
    0x04: "NOOP",
    0x05: "UNIQUE_ID",
    0x20: "SSL",
    0x21: "SSL_VERSION",
    0x22: "SSL_CN",
    0x23: "SSL_CIPHER",
    0x24: "SSL_SIG_ALG",
    0x25: "SSL_KEY_ALG",
    0x30: "NETNS",
}


@dataclass
class TLV:
    type: int
    value: bytes = b""

    @property
    def type_name(self) -> str:
        return _TLV_TYPE_NAMES.get(self.type, f"UNKNOWN_0x{self.type:02X}")

    @property
    def value_utf8(self) -> str:
        return self.value.decode("utf-8", errors="replace")

    def __repr__(self) -> str:
        return f"TLV(type={self.type_name}, len={len(self.value)})"


# ── header ────────────────────────────────────────────────────────────────────


@dataclass
class ProxyProtocolHeader:
    version: int
    command: ProxyCommand
    transport: ProxyTransport
    family: AddressFamily
    src_addr: str | None = None
    dst_addr: str | None = None
    src_port: int | None = None
    dst_port: int | None = None
    tlvs: list[TLV] = field(default_factory=list)

    @property
    def is_v1(self) -> bool:
        return self.version == 1

    @property
    def is_v2(self) -> bool:
        return self.version == 2

    @property
    def is_local(self) -> bool:
        return self.command == ProxyCommand.LOCAL

    @property
    def has_tlvs(self) -> bool:
        return len(self.tlvs) > 0

    def tlv_by_type(self, tlv_type: int) -> TLV | None:
        for tlv in self.tlvs:
            if tlv.type == tlv_type:
                return tlv
        return None

    def tlv_values_by_type(self, tlv_type: int) -> list[bytes]:
        return [tlv.value for tlv in self.tlvs if tlv.type == tlv_type]


# ── parsing ───────────────────────────────────────────────────────────────────


class ProxyProtocolError(ValueError):
    """Raised when a PROXY protocol header cannot be parsed."""


def parse_proxy_header(data: bytes) -> ProxyProtocolHeader:
    if len(data) == 0:
        raise ProxyProtocolError("empty data")

    if len(data) >= V2_SIGNATURE_LEN and data[:V2_SIGNATURE_LEN] == V2_SIGNATURE:
        return _parse_v2(data)
    elif data.startswith(b"PROXY "):
        return _parse_v1(data)
    else:
        raise ProxyProtocolError(f"unknown proxy header prefix: {data[:16]!r}")


# ── v1 parser ─────────────────────────────────────────────────────────────────


def _parse_v1(data: bytes) -> ProxyProtocolHeader:
    end = data.find(b"\r\n")
    if end == -1:
        raise ProxyProtocolError("v1 header missing CRLF terminator")

    line = data[:end].decode("ascii", errors="replace")
    parts = line.split(" ")

    if len(parts) < 2:
        raise ProxyProtocolError(f"v1 header too short: {line!r}")

    t = parts[1]

    if t == "UNKNOWN":
        if len(parts) < 2:
            raise ProxyProtocolError(f"v1 UNKNOWN header malformed: {line!r}")
        return ProxyProtocolHeader(
            version=1,
            command=ProxyCommand.PROXY,
            transport=ProxyTransport.UNSPEC,
            family=AddressFamily.AF_UNSPEC,
        )

    if t in ("TCP4", "TCP6"):
        if len(parts) != 6:
            raise ProxyProtocolError(f"v1 TCP4/TCP6 requires 6 fields, got {len(parts)}: {line!r}")

        family_str = parts[1]
        src_addr_str = parts[2]
        dst_addr_str = parts[3]
        src_port_str = parts[4]
        dst_port_str = parts[5]

        _validate_ip(src_addr_str, family_str)
        _validate_ip(dst_addr_str, family_str)

        try:
            src_port = int(src_port_str)
            dst_port = int(dst_port_str)
        except ValueError as err:
            raise ProxyProtocolError(f"v1 ports must be integers: {src_port_str!r}, {dst_port_str!r}") from err

        if not (0 <= src_port <= 65535) or not (0 <= dst_port <= 65535):
            raise ProxyProtocolError(f"v1 ports out of range: {src_port}, {dst_port}")

        family = AddressFamily.AF_INET if family_str == "TCP4" else AddressFamily.AF_INET6

        return ProxyProtocolHeader(
            version=1,
            command=ProxyCommand.PROXY,
            transport=ProxyTransport.STREAM,
            family=family,
            src_addr=src_addr_str,
            dst_addr=dst_addr_str,
            src_port=src_port,
            dst_port=dst_port,
        )

    if t.startswith("TCP") or t in ("UNKNOWN",):
        return ProxyProtocolHeader(
            version=1,
            command=ProxyCommand.PROXY,
            transport=ProxyTransport.UNSPEC,
            family=AddressFamily.AF_UNSPEC,
        )

    raise ProxyProtocolError(f"v1 unsupported protocol: {t!r}")


def _validate_ip(addr: str, family: str) -> None:
    try:
        if family == "TCP4":
            ipaddress.IPv4Address(addr)
        elif family == "TCP6":
            ipaddress.IPv6Address(addr)
    except ipaddress.AddressValueError as exc:
        raise ProxyProtocolError(f"v1 invalid {family} address: {addr!r}") from exc


# ── v2 parser ─────────────────────────────────────────────────────────────────


def _parse_v2(data: bytes) -> ProxyProtocolHeader:
    if len(data) < V2_HEADER_BASE_LEN:
        raise ProxyProtocolError(f"v2 header too short: {len(data)} bytes, need >= {V2_HEADER_BASE_LEN}")

    ver_cmd = data[12]
    version = (ver_cmd >> 4) & 0xF
    command = ver_cmd & 0xF

    if version != 2:
        raise ProxyProtocolError(f"v2 version mismatch: expected 2, got {version}")
    if command not in (ProxyCommand.LOCAL, ProxyCommand.PROXY):
        raise ProxyProtocolError(f"v2 unknown command: 0x{command:02X}")

    protocol = data[13]
    addr_len = struct.unpack_from("!H", data, 14)[0]

    total_expected = V2_HEADER_BASE_LEN + addr_len
    if len(data) < total_expected:
        raise ProxyProtocolError(f"v2 header truncated: {len(data)} bytes, need >= {total_expected}")

    addr_data = data[V2_HEADER_BASE_LEN:total_expected]

    transport_info = V2_PROTOCOL_TO_TRANSPORT.get(protocol)
    if transport_info is None:
        raise ProxyProtocolError(f"v2 unsupported protocol byte: 0x{protocol:02X}")

    family, transport = transport_info

    cmd = ProxyCommand(command)
    header = ProxyProtocolHeader(
        version=2,
        command=cmd,
        transport=transport,
        family=family,
    )

    if cmd == ProxyCommand.LOCAL:
        return header

    expiry = V2_ADDR_SIZES.get(protocol, 0)
    addr_block = addr_data[:expiry]
    tlvs_block = addr_data[expiry:]

    header = _parse_v2_addresses(header, addr_block, protocol)
    header.tlvs = _parse_v2_tlvs(tlvs_block)

    return header


V2_ADDR_FORMATS = {
    0x11: "!4s4sHH",  # AF_INET + STREAM: src4, dst4, srcport, dstport
    0x12: "!4s4sHH",  # AF_INET + DGRAM
    0x21: "!16s16sHH",  # AF_INET6 + STREAM: src16, dst16, srcport, dstport
    0x22: "!16s16sHH",  # AF_INET6 + DGRAM
    0x31: "!108s108s",  # AF_UNIX + STREAM: src_unix, dst_unix
    0x32: "!108s108s",  # AF_UNIX + DGRAM
}


def _parse_v2_addresses(header: ProxyProtocolHeader, data: bytes, protocol: int) -> ProxyProtocolHeader:
    fmt = V2_ADDR_FORMATS.get(protocol)
    if fmt is None or len(data) == 0:
        return header

    try:
        fields = struct.unpack(fmt, data)
    except struct.error as exc:
        raise ProxyProtocolError(f"v2 address unpack failed for protocol 0x{protocol:02X}: {exc}") from exc

    if protocol in (0x11, 0x12):
        header.src_addr = socket.inet_ntop(socket.AF_INET, fields[0])
        header.dst_addr = socket.inet_ntop(socket.AF_INET, fields[1])
        header.src_port = fields[2]
        header.dst_port = fields[3]
    elif protocol in (0x21, 0x22):
        header.src_addr = socket.inet_ntop(socket.AF_INET6, fields[0])
        header.dst_addr = socket.inet_ntop(socket.AF_INET6, fields[1])
        header.src_port = fields[2]
        header.dst_port = fields[3]
    elif protocol in (0x31, 0x32):
        header.src_addr = fields[0].rstrip(b"\x00").decode("utf-8", errors="replace") or None
        header.dst_addr = fields[1].rstrip(b"\x00").decode("utf-8", errors="replace") or None

    return header


def _parse_v2_tlvs(data: bytes) -> list[TLV]:
    tlvs: list[TLV] = []
    pos = 0
    while pos + 3 <= len(data):
        tlv_type = data[pos]
        tlv_len = struct.unpack_from("!H", data, pos + 1)[0]
        pos += 3
        if pos + tlv_len > len(data):
            break
        value = data[pos : pos + tlv_len]
        tlvs.append(TLV(type=tlv_type, value=value))
        pos += tlv_len
    return tlvs


# ── building ──────────────────────────────────────────────────────────────────


def build_proxy_header(header: ProxyProtocolHeader) -> bytes:
    if header.version == 1:
        return _build_v1(header)
    elif header.version == 2:
        return _build_v2(header)
    else:
        raise ProxyProtocolError(f"unsupported version: {header.version}")


def _build_v1(header: ProxyProtocolHeader) -> bytes:
    if header.command == ProxyCommand.LOCAL or header.family == AddressFamily.AF_UNSPEC:
        return b"PROXY UNKNOWN\r\n"

    family_str = "TCP4" if header.family == AddressFamily.AF_INET else "TCP6"

    if header.src_addr is None or header.dst_addr is None:
        raise ProxyProtocolError("v1 build requires src_addr and dst_addr")

    return (
        f"PROXY {family_str} {header.src_addr} {header.dst_addr} {header.src_port or 0} {header.dst_port or 0}\r\n"
    ).encode("ascii")


def _build_v2(header: ProxyProtocolHeader) -> bytes:
    if header.command == ProxyCommand.LOCAL:
        return _build_v2_local()
    return _build_v2_proxy(header)


def _build_v2_local() -> bytes:
    sig = V2_SIGNATURE
    ver_cmd = (2 << 4) | ProxyCommand.LOCAL
    protocol = 0x00
    addr_len = struct.pack("!H", 0)
    return sig + bytes([ver_cmd, protocol]) + addr_len


def _build_v2_proxy(header: ProxyProtocolHeader) -> bytes:
    key = (header.family, header.transport)
    protocol = TRANSPORT_TO_V2_PROTOCOL.get(key)
    if protocol is None:
        raise ProxyProtocolError(
            f"cannot map family={header.family.name} transport={header.transport.name} to v2 protocol byte"
        )

    addr_block = _encode_v2_addresses(header, protocol)
    tlv_block = _encode_v2_tlvs(header.tlvs)
    addr_data = addr_block + tlv_block

    sig = V2_SIGNATURE
    ver_cmd = (2 << 4) | header.command.value
    addr_len = struct.pack("!H", len(addr_data))

    return sig + bytes([ver_cmd, protocol]) + addr_len + addr_data


def _encode_v2_addresses(header: ProxyProtocolHeader, protocol: int) -> bytes:
    if protocol in (0x11, 0x12):
        src_bytes = socket.inet_pton(socket.AF_INET, header.src_addr or "0.0.0.0")
        dst_bytes = socket.inet_pton(socket.AF_INET, header.dst_addr or "0.0.0.0")
        return struct.pack("!4s4sHH", src_bytes, dst_bytes, header.src_port or 0, header.dst_port or 0)
    elif protocol in (0x21, 0x22):
        src_bytes = socket.inet_pton(socket.AF_INET6, header.src_addr or "::")
        dst_bytes = socket.inet_pton(socket.AF_INET6, header.dst_addr or "::")
        return struct.pack("!16s16sHH", src_bytes, dst_bytes, header.src_port or 0, header.dst_port or 0)
    elif protocol in (0x31, 0x32):
        src_padded = (header.src_addr or "").encode("utf-8").ljust(108, b"\x00")
        dst_padded = (header.dst_addr or "").encode("utf-8").ljust(108, b"\x00")
        return struct.pack("!108s108s", src_padded, dst_padded)
    return b""


def _encode_v2_tlvs(tlvs: list[TLV]) -> bytes:
    result = bytearray()
    for tlv in tlvs:
        result.append(tlv.type & 0xFF)
        result.extend(struct.pack("!H", len(tlv.value)))
        result.extend(tlv.value)
    return bytes(result)
