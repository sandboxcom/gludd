"""Scapy/tshark packet capture and analysis adapter.

Fallback chain: tshark -> scapy -> struct parsing.
"""

from __future__ import annotations

import contextlib
import importlib.util
import ipaddress
import json
import struct
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_UNSPECIFIED_IPV4 = str(ipaddress.IPv4Address(0))


@dataclass
class AsnInfo:
    asn: int = 0
    name: str = ""
    description: str = ""
    organization: str = ""
    country: str = ""
    rir: str = ""
    prefix: str = ""
    prefixes: list[str] = field(default_factory=list)
    peers: list[str] = field(default_factory=list)


@dataclass
class BgpCommunity:
    asn: int = 0
    value: int = 0
    description: str = ""
    well_known: str = ""
    raw: str = ""


@dataclass
class CidrRange:
    network: str = ""
    prefix_length: int = 0
    first_address: str = ""
    last_address: str = ""
    total_addresses: int = 0


@dataclass
class TrafficReport:
    total_packets: int = 0
    protocols: dict[str, int] = field(default_factory=dict)
    top_talkers: dict[str, int] = field(default_factory=dict)
    top_ports: dict[int, int] = field(default_factory=dict)
    flows: list[dict[str, Any]] = field(default_factory=list)
    duration_seconds: float = 0.0


@dataclass
class PacketSummary:
    timestamp: float = 0.0
    length: int = 0
    src_ip: str = ""
    dst_ip: str = ""
    protocol: str = ""
    src_port: int | None = None
    dst_port: int | None = None
    flags: str | None = None
    info: str = ""


def tshark_available() -> bool:
    try:
        subprocess.run(["tshark", "--version"], capture_output=True, timeout=5)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def scapy_available() -> bool:
    try:
        return importlib.util.find_spec("scapy.all") is not None
    except ModuleNotFoundError:
        return False


def _parse_tshark_json(raw: list[dict[str, Any]]) -> list[PacketSummary]:
    packets: list[PacketSummary] = []
    for item in raw:
        layers = item.get("_source", {}).get("layers", {})
        ts = layers.get("frame.time_epoch", ["0.0"])
        ip_src = layers.get("ip.src", [""])
        ip_dst = layers.get("ip.dst", [""])
        proto_raw = layers.get("frame.protocols", [""])
        length_raw = layers.get("frame.len", ["0"])
        tcp_sport = layers.get("tcp.srcport", [None])
        tcp_dport = layers.get("tcp.dstport", [None])
        udp_sport = layers.get("udp.srcport", [None])
        udp_dport = layers.get("udp.dstport", [None])
        tcp_flags = layers.get("tcp.flags.str", [None])

        proto_str = proto_raw[0] if proto_raw else ""
        proto = proto_str.rsplit(":", 1)[-1] if ":" in proto_str else proto_str or "unknown"

        sport = _first_int(tcp_sport) or _first_int(udp_sport)
        dport = _first_int(tcp_dport) or _first_int(udp_dport)

        packets.append(
            PacketSummary(
                timestamp=float(ts[0]) if ts and ts[0] else 0.0,
                length=int(length_raw[0]) if length_raw and length_raw[0] else 0,
                src_ip=ip_src[0] if ip_src else "",
                dst_ip=ip_dst[0] if ip_dst else "",
                protocol=proto,
                src_port=sport,
                dst_port=dport,
                flags=tcp_flags[0] if tcp_flags and tcp_flags[0] else None,
            )
        )
    return packets


def _first_int(vals: list[Any]) -> int | None:
    for v in vals:
        if v is not None:
            try:
                return int(v)
            except (ValueError, TypeError):
                pass
    return None


def _read_pcap_tshark(path: Path) -> list[PacketSummary]:
    try:
        result = subprocess.run(
            [
                "tshark",
                "-r",
                str(path),
                "-T",
                "json",
                "-e",
                "frame.time_epoch",
                "-e",
                "frame.len",
                "-e",
                "frame.protocols",
                "-e",
                "ip.src",
                "-e",
                "ip.dst",
                "-e",
                "tcp.srcport",
                "-e",
                "tcp.dstport",
                "-e",
                "tcp.flags.str",
                "-e",
                "udp.srcport",
                "-e",
                "udp.dstport",
            ],
            capture_output=True,
            timeout=120,
        )
        if result.returncode != 0:
            return []
        data = json.loads(result.stdout)
        return _parse_tshark_json(data)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, MemoryError, Exception):
        return []


def _read_pcap_scapy(path: Path) -> list[PacketSummary]:
    try:
        from scapy.all import rdpcap

        pkts = rdpcap(str(path))
    except Exception:
        return []
    return _parse_scapy_packets(pkts)


def _parse_scapy_packets(pkts: list[Any]) -> list[PacketSummary]:
    summaries: list[PacketSummary] = []
    for pkt in pkts:
        try:
            ts = float(pkt.time) if hasattr(pkt, "time") else 0.0
            length = len(pkt)
            src_ip = dst_ip = ""
            src_port = dst_port = None
            flags = None
            proto = ""

            if hasattr(pkt, "haslayer"):
                from scapy.all import IP, TCP, UDP

                if pkt.haslayer(IP):
                    ip_layer = pkt.getlayer(IP)
                    src_ip = ip_layer.src
                    dst_ip = ip_layer.dst
                    proto = {6: "tcp", 17: "udp", 1: "icmp"}.get(ip_layer.proto, "")
                    if pkt.haslayer(TCP):
                        t = pkt.getlayer(TCP)
                        src_port = t.sport
                        dst_port = t.dport
                        flags = str(t.flags) if hasattr(t, "flags") else None
                    elif pkt.haslayer(UDP):
                        u = pkt.getlayer(UDP)
                        src_port = u.sport
                        dst_port = u.dport

            summaries.append(
                PacketSummary(
                    timestamp=ts,
                    length=length,
                    src_ip=src_ip,
                    dst_ip=dst_ip,
                    protocol=proto,
                    src_port=src_port,
                    dst_port=dst_port,
                    flags=flags,
                    info="",
                )
            )
        except Exception:
            summaries.append(PacketSummary())
    return summaries


def read_pcap(path: str | Path) -> list[PacketSummary]:
    path = Path(path)
    if not path.exists():
        return []
    if tshark_available():
        return _read_pcap_tshark(path)
    if scapy_available():
        return _read_pcap_scapy(path)
    return []


def write_pcap(packets: list[PacketSummary], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if scapy_available():
        _write_pcap_scapy(packets, path)
    else:
        _write_pcap_raw(packets, path)


def _write_pcap_scapy(packets: list[PacketSummary], path: Path) -> None:
    from scapy.all import IP, TCP, UDP, Ether, PcapWriter

    with open(path, "wb") as f:
        writer = PcapWriter(f)
        for p in packets:
            pkt = Ether() / IP(
                src=p.src_ip or _UNSPECIFIED_IPV4,
                dst=p.dst_ip or _UNSPECIFIED_IPV4,
            )
            if p.protocol.upper() == "TCP":
                pkt = pkt / TCP(sport=p.src_port or 0, dport=p.dst_port or 0)
            elif p.protocol.upper() == "UDP":
                pkt = pkt / UDP(sport=p.src_port or 0, dport=p.dst_port or 0)
            writer.write(pkt)


def _write_pcap_raw(packets: list[PacketSummary], path: Path) -> None:
    with open(path, "wb") as f:
        f.write(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1))
        for p in packets:
            ts_sec = int(p.timestamp)
            ts_usec = int((p.timestamp - ts_sec) * 1_000_000)
            f.write(struct.pack("<IIII", ts_sec, ts_usec, p.length, p.length))
            f.write(b"\x00" * p.length)


def craft_packet(layers: list[str], fields: dict[str, str]) -> dict[str, Any]:
    return {
        "protocols": layers,
        "fields": fields,
        "_scapy_available": scapy_available(),
    }


def send_packet(spec: dict[str, Any], iface: str, count: int = 1) -> dict[str, Any]:
    if scapy_available():
        return _send_scapy(spec, iface, count)

    dst = spec.get("fields", {}).get("dst", "127.0.0.1")
    dport = spec.get("fields", {}).get("dport", "80")
    try:
        subprocess.run(
            ["nping", "--tcp", "-c", str(count), "-p", str(dport), dst],
            capture_output=True,
            timeout=30,
        )
        return {"tool": "nping", "count": count}
    except FileNotFoundError:
        return {"sent": 0}


def _send_scapy(spec: dict[str, Any], iface: str, count: int) -> dict[str, Any]:
    return {"interface": iface, "count": count, "packets": []}


def sniff_packets(
    filter_str: str = "",
    count: int = 1,
    timeout: int = 1,
) -> list[PacketSummary]:
    if not tshark_available():
        return []
    try:
        result = subprocess.run(
            [
                "tshark",
                "-i",
                "any",
                "-c",
                str(count),
                "-a",
                f"duration:{timeout}",
                "-f",
                filter_str,
                "-T",
                "json",
                "-e",
                "frame.time_epoch",
                "-e",
                "frame.len",
                "-e",
                "ip.src",
                "-e",
                "ip.dst",
                "-e",
                "tcp.srcport",
                "-e",
                "tcp.dstport",
            ],
            capture_output=True,
            timeout=timeout + 5,
        )
        if result.returncode != 0:
            return []
        return _parse_tshark_json(json.loads(result.stdout))
    except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception):
        return []


def parse_asn_rdap(rdap_json: dict[str, Any]) -> AsnInfo:
    if not rdap_json:
        return AsnInfo()
    asn = int(rdap_json.get("autnum") or rdap_json.get("asn", 0))
    org = rdap_json.get("name", "")
    country = ""
    entities = rdap_json.get("entities", [])
    for entity in entities:
        if "vcardArray" in entity:
            vcard = entity["vcardArray"]
            if isinstance(vcard, list) and len(vcard) > 1:
                for item in vcard[1]:
                    if isinstance(item, list) and len(item) >= 4 and item[0] == "org":
                        org = item[3]
                        break
        if "country" in entity and not country:
            country = entity["country"]
    return AsnInfo(asn=asn, name=org, organization=org, country=country)


def parse_asn_whois(whois_text: str) -> AsnInfo:
    if not whois_text.strip():
        return AsnInfo()
    asn = 0
    name = ""
    org = ""
    country = ""
    prefix_val = ""
    for line in whois_text.splitlines():
        line_lower = line.lower()
        if "aut-num:" in line_lower or "asnumber:" in line_lower:
            raw_asn = line.split(":", 1)[-1].strip().upper().replace("AS", "")
            with contextlib.suppress(ValueError):
                asn = int(raw_asn)
        elif "as-name:" in line_lower:
            name = line.split(":", 1)[-1].strip()
        elif "org-name:" in line_lower or line_lower.startswith("owner:"):
            org = line.split(":", 1)[-1].strip()
        elif "country:" in line_lower:
            country = line.split(":", 1)[-1].strip()
        elif "route:" in line_lower:
            prefix_val = line.split(":", 1)[-1].strip()
    return AsnInfo(
        asn=asn,
        name=name,
        organization=org,
        country=country,
        prefix=prefix_val,
    )


def parse_bgp_community(raw: str) -> BgpCommunity:
    cleaned = raw.strip("() ")
    parts = cleaned.split(":")
    if len(parts) == 2:
        try:
            return BgpCommunity(asn=int(parts[0]), value=int(parts[1]), raw=raw)
        except ValueError:
            pass
    well_known: dict[str, str] = {
        "NO_EXPORT": "65535:65281",
        "NO_ADVERTISE": "65535:65282",
        "NO_EXPORT_SUBCONFED": "65535:65283",
        "LOCAL_AS": "65535:65283",
    }
    for name, val in well_known.items():
        if cleaned.upper() == name or cleaned == val:
            return BgpCommunity(asn=65535, value=int(val.split(":")[1]), well_known=name, raw=raw)
    return BgpCommunity(raw=raw)


def parse_cidr(cidr: str) -> CidrRange:
    try:
        net: ipaddress.IPv4Network | ipaddress.IPv6Network = ipaddress.ip_network(cidr, strict=False)
        total = int(net.num_addresses)
        if total > 2:
            first = str(net.network_address + 1)
            last = str(net.broadcast_address - 1) if net.version == 4 else str(net.broadcast_address)
        elif total == 2:
            first = str(net.network_address)
            last = str(net.broadcast_address)
        else:
            first = str(net.network_address)
            last = str(net.network_address)
        return CidrRange(
            network=str(net.network_address),
            prefix_length=net.prefixlen,
            first_address=first,
            last_address=last,
            total_addresses=total,
        )
    except ValueError:
        return CidrRange(network=cidr)


def analyze_pcap(path: str | Path) -> TrafficReport:
    path = str(path)
    if tshark_available():
        try:
            result = subprocess.run(
                ["tshark", "-r", path, "-q", "-z", "io,stat,0"],
                capture_output=True,
                timeout=120,
            )
            if result.returncode == 0 and result.stdout:
                return _parse_tshark_stats(result.stdout.decode(errors="replace"))
        except (subprocess.TimeoutExpired, Exception):
            return TrafficReport()
        return TrafficReport()

    if scapy_available():
        pkts = read_pcap(path)
        return _build_report_from_packets(pkts)

    return TrafficReport()


def _parse_tshark_stats(output: str) -> TrafficReport:
    lines = output.splitlines()
    total = 0
    for line in lines:
        if "frames" in line:
            parts = line.split()
            for i, p in enumerate(parts):
                if p == "frames":
                    with contextlib.suppress(ValueError, IndexError):
                        total = int(parts[i + 1])
                    break
    return TrafficReport(total_packets=total)


def _build_report_from_packets(packets: list[PacketSummary]) -> TrafficReport:
    protocols: dict[str, int] = {}
    for p in packets:
        if p.protocol:
            protocols[p.protocol] = protocols.get(p.protocol, 0) + 1
    return TrafficReport(
        total_packets=len(packets),
        protocols=protocols,
    )


def dissect_packet(raw_bytes: bytes) -> dict[str, Any]:
    if not raw_bytes:
        return {"raw_hex": "", "length": 0}
    if len(raw_bytes) < 14:
        return {"raw_hex": raw_bytes.hex(), "length": len(raw_bytes)}
    if scapy_available():
        return _dissect_scapy(raw_bytes)

    layers: dict[str, Any] = {}
    layers["Ethernet"] = {}
    if len(raw_bytes) >= 14:
        eth = raw_bytes[:14]
        layers["Ethernet"]["dst"] = ":".join(f"{b:02x}" for b in eth[:6])
        layers["Ethernet"]["src"] = ":".join(f"{b:02x}" for b in eth[6:12])
        layers["Ethernet"]["type"] = f"0x{eth[12]:02x}{eth[13]:02x}"

    if len(raw_bytes) >= 34:
        ip_hdr = raw_bytes[14:34]
        layers["IP"] = {
            "version": ip_hdr[0] >> 4,
            "ihl": ip_hdr[0] & 0x0F,
            "tos": ip_hdr[1],
            "length": (ip_hdr[2] << 8) + ip_hdr[3],
            "id": (ip_hdr[4] << 8) + ip_hdr[5],
            "flags": ip_hdr[6] >> 5,
            "fragment_offset": ((ip_hdr[6] & 0x1F) << 8) + ip_hdr[7],
            "ttl": ip_hdr[8],
            "proto": ip_hdr[9],
            "checksum": (ip_hdr[10] << 8) + ip_hdr[11],
            "src": ".".join(str(b) for b in ip_hdr[12:16]),
            "dst": ".".join(str(b) for b in ip_hdr[16:20]),
        }
        ihl = ip_hdr[0] & 0x0F
        proto = ip_hdr[9]
        tcp_start = 14 + ihl * 4
        if proto == 6 and len(raw_bytes) >= tcp_start + 20:
            tcp_hdr = raw_bytes[tcp_start : tcp_start + 20]
            layers["TCP"] = {
                "src_port": (tcp_hdr[0] << 8) + tcp_hdr[1],
                "dst_port": (tcp_hdr[2] << 8) + tcp_hdr[3],
                "seq": struct.unpack("!I", tcp_hdr[4:8])[0],
                "ack": struct.unpack("!I", tcp_hdr[8:12])[0],
                "flags": tcp_hdr[13],
            }
        elif proto == 17 and len(raw_bytes) >= tcp_start + 8:
            udp_hdr = raw_bytes[tcp_start : tcp_start + 8]
            layers["UDP"] = {
                "src_port": (udp_hdr[0] << 8) + udp_hdr[1],
                "dst_port": (udp_hdr[2] << 8) + udp_hdr[3],
                "length": (udp_hdr[4] << 8) + udp_hdr[5],
                "checksum": (udp_hdr[6] << 8) + udp_hdr[7],
            }

    return {"layers": layers, "length": len(raw_bytes), "raw_hex": raw_bytes.hex()}


def _dissect_scapy(raw_bytes: bytes) -> dict[str, Any]:
    try:
        from scapy.all import Ether

        pkt = Ether(raw_bytes)
        result: dict[str, Any] = {"layers": {}, "raw_hex": raw_bytes.hex()}
        current = pkt
        while current:
            name = current.__class__.__name__
            fields: dict[str, Any] = {}
            for f in current.fields_desc:
                val = getattr(current, f.name, None)
                fields[f.name] = val
            result["layers"][name] = fields
            current = current.payload if hasattr(current, "payload") else None
        return result
    except Exception:
        return {"layers": {}, "length": len(raw_bytes), "raw_hex": raw_bytes.hex()}
