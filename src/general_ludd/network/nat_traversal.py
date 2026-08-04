"""NAT traversal primitives: STUN binding, reflexive address discovery,
NAT type classification, ICE candidate types, and UDP hole-punch state machines.

Core types:
  NatType        — full-cone, restricted, port-restricted, symmetric, open
  IceCandidate   — host, srflx (server reflexive), relay
  StunMessage    — RFC 5389 Binding Request / Response
  NatClassifier  — classifies NAT behaviour via STUN server responses
  HolePunchState — bidirectional UDP hole-punch state machine
"""

from __future__ import annotations

import contextlib
import enum
import os
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass, field


def _random_bytes(n: int) -> bytes:
    return os.urandom(n)


# ── Enumerations ─────────────────────────────────────────────────────────────


class NatType(enum.StrEnum):
    OPEN = "open"
    FULL_CONE = "full_cone"
    RESTRICTED = "restricted"
    PORT_RESTRICTED = "port_restricted"
    SYMMETRIC = "symmetric"
    UDP_BLOCKED = "udp_blocked"


class IceCandidateType(enum.StrEnum):
    HOST = "host"
    SRFLX = "srflx"
    RELAY = "relay"


class StunClass(int, enum.Enum):
    REQUEST = 0x0000
    INDICATION = 0x0010
    SUCCESS = 0x0100
    ERROR = 0x0110


class StunMethod(int, enum.Enum):
    BINDING = 0x0001


class StunAttribute(int, enum.Enum):
    MAPPED_ADDRESS = 0x0001
    XOR_MAPPED_ADDRESS = 0x0020
    USERNAME = 0x0006
    MESSAGE_INTEGRITY = 0x0008
    ERROR_CODE = 0x0009
    SOFTWARE = 0x8022
    FINGERPRINT = 0x8028
    ICE_CONTROLLING = 0x802A
    ICE_CONTROLLED = 0x8029
    PRIORITY = 0x0024
    USE_CANDIDATE = 0x0025


class StunErrorCode(enum.IntEnum):
    TRY_ALTERNATE = 300
    BAD_REQUEST = 400
    UNAUTHORIZED = 401
    UNKNOWN_ATTRIBUTE = 420
    STALE_NONCE = 438
    SERVER_ERROR = 500


STUN_MAGIC_COOKIE = 0x2112A442


# ── STUN Address ─────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class StunAddress:
    family: int
    port: int
    address: str

    @staticmethod
    def ipv4(host: str, port: int) -> StunAddress:
        return StunAddress(family=0x01, port=port, address=host)

    def pack(self) -> bytes:
        parts = self.address.split(".")
        return struct.pack("!BBH4s", 0, self.family, self.port, bytes(map(int, parts)))


# ── ICE Candidate ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class IceCandidate:
    kind: IceCandidateType
    address: str
    port: int
    priority: int
    foundation: str = ""
    transport: str = "udp"

    def candidate_line(self) -> str:
        typ = "host" if self.kind == IceCandidateType.HOST else self.kind.value
        return f"{self.foundation} 1 {self.transport} {self.priority} {self.address} {self.port} typ {typ}"

    @staticmethod
    def host(address: str, port: int, priority: int = 2130706431) -> IceCandidate:
        return IceCandidate(
            kind=IceCandidateType.HOST,
            address=address,
            port=port,
            priority=priority,
            foundation="1",
        )

    @staticmethod
    def srflx(address: str, port: int, priority: int = 1694498815) -> IceCandidate:
        return IceCandidate(
            kind=IceCandidateType.SRFLX,
            address=address,
            port=port,
            priority=priority,
            foundation="2",
        )

    @staticmethod
    def relay(address: str, port: int, priority: int = 16777215) -> IceCandidate:
        return IceCandidate(
            kind=IceCandidateType.RELAY,
            address=address,
            port=port,
            priority=priority,
            foundation="3",
        )


# ── STUN Message ─────────────────────────────────────────────────────────────


@dataclass
class StunMessage:
    msg_class: StunClass
    method: StunMethod
    transaction_id: bytes
    attributes: list[tuple[StunAttribute, bytes]] = field(default_factory=list)

    STUN_HEADER_FMT = "!HHI12s"

    def encode(self, password: str = "") -> bytes:
        msg_type = self.msg_class.value | self.method.value
        header = struct.pack(
            StunMessage.STUN_HEADER_FMT,
            msg_type,
            0,
            STUN_MAGIC_COOKIE,
            self.transaction_id,
        )
        body = b""
        for attr_kind, attr_value in self.attributes:
            if isinstance(attr_kind, StunAttribute) and attr_kind == StunAttribute.MESSAGE_INTEGRITY:
                continue
            kind_val = attr_kind.value if isinstance(attr_kind, StunAttribute) else attr_kind
            padded = attr_value + b"\x00" * ((4 - len(attr_value) % 4) % 4)
            body += struct.pack("!HH", kind_val, len(attr_value)) + padded

        length = len(body)
        header = struct.pack(
            "!HHI12s",
            msg_type,
            length,
            STUN_MAGIC_COOKIE,
            self.transaction_id,
        )
        return header + body

    @staticmethod
    def parse(raw: bytes) -> StunMessage:
        if len(raw) < 20:
            raise ValueError("STUN message too short")
        msg_type, length, _magic, tx_id = struct.unpack("!HHI12s", raw[:20])
        msg_class = StunClass(msg_type & 0x0110)
        method = StunMethod(msg_type & 0x000F)
        cursor = 20
        attrs: list[tuple[StunAttribute, bytes]] = []
        while cursor + 4 <= len(raw) and cursor < 20 + length:
            attr_type, attr_len = struct.unpack("!HH", raw[cursor : cursor + 4])
            cursor += 4
            value = raw[cursor : cursor + attr_len]
            cursor += attr_len
            pad = (4 - attr_len % 4) % 4
            cursor += pad
            with contextlib.suppress(ValueError):
                attrs.append((StunAttribute(attr_type), value))
        return StunMessage(
            msg_class=msg_class,
            method=method,
            transaction_id=tx_id,
            attributes=attrs,
        )

    @staticmethod
    def binding_request() -> StunMessage:
        return StunMessage(
            msg_class=StunClass.REQUEST,
            method=StunMethod.BINDING,
            transaction_id=_random_bytes(12),
        )

    def get_address(self, attr: StunAttribute) -> str | None:
        for a, v in self.attributes:
            if a == attr and len(v) >= 6:
                _port, ip_bytes = struct.unpack("!H4s", v[:6])
                return ".".join(map(str, ip_bytes))
        return None

    def get_xor_address(self) -> str | None:
        for a, v in self.attributes:
            if a == StunAttribute.XOR_MAPPED_ADDRESS and len(v) >= 6:
                xport, xip = struct.unpack("!H4s", v[:6])
                xport ^ (STUN_MAGIC_COOKIE >> 16)
                ip = struct.unpack("!I", xip)[0] ^ STUN_MAGIC_COOKIE
                return ".".join(str((ip >> shift) & 0xFF) for shift in (24, 16, 8, 0))
        return None


# ── STUN Binding Client ──────────────────────────────────────────────────────


@dataclass
class StunBindingResult:
    success: bool
    mapped_address: str | None = None
    mapped_port: int | None = None
    nat_type: NatType = NatType.UDP_BLOCKED
    rtt_ms: float = 0.0
    error_code: int | None = None

    def have_connectivity(self) -> bool:
        return self.success and self.mapped_address is not None


@dataclass
class StunClient:
    servers: list[tuple[str, int]]
    _clock: Callable[[], float] = field(default=time.monotonic, repr=False)

    def binding_request_response(
        self,
        request: StunMessage,
        mapped_address: str,
        mapped_port: int,
        xor: bool = True,
    ) -> StunMessage:
        response = StunMessage(
            msg_class=StunClass.SUCCESS,
            method=StunMethod.BINDING,
            transaction_id=request.transaction_id,
        )
        attr = StunAttribute.XOR_MAPPED_ADDRESS if xor else StunAttribute.MAPPED_ADDRESS
        if xor:
            xport = mapped_port ^ (STUN_MAGIC_COOKIE >> 16)
            ip_int = struct.unpack("!I", bytes(map(int, mapped_address.split("."))))[0]
            xip = ip_int ^ STUN_MAGIC_COOKIE
            value = struct.pack("!H4s", xport, struct.pack("!I", xip))
        else:
            value = struct.pack(
                "!H4s",
                mapped_port,
                bytes(map(int, mapped_address.split("."))),
            )
        response.attributes.append((attr, value))
        return response

    def classify(self, responses: list[StunMessage]) -> NatType:
        if not responses:
            return NatType.UDP_BLOCKED
        mapped_addrs: set[str] = set()
        for r in responses:
            addr = r.get_xor_address() or r.get_address(StunAttribute.MAPPED_ADDRESS)
            if addr:
                mapped_addrs.add(addr)
        if len(mapped_addrs) == 0:
            return NatType.UDP_BLOCKED
        if len(mapped_addrs) > 1:
            return NatType.SYMMETRIC
        return NatType.FULL_CONE


# ── ICE Candidate Pair ───────────────────────────────────────────────────────


@dataclass
class IceCandidatePair:
    local: IceCandidate
    remote: IceCandidate
    state: str = "frozen"
    priority: int = 0
    nominated: bool = False

    def __post_init__(self) -> None:
        if self.priority == 0:
            self.priority = min(self.local.priority, self.remote.priority) * 2**32 + max(
                self.local.priority, self.remote.priority
            )

    def pair_id(self) -> str:
        return f"{self.local.candidate_line()}:{self.remote.candidate_line()}"


# ── NAT Classifier — RFC 3489 / RFC 5780 test procedures ─────────────────────


@dataclass
class NatClassifier:
    _responses: list[StunBindingResult] = field(default_factory=list)

    def add_response(self, result: StunBindingResult) -> None:
        self._responses.append(result)

    def classify(self) -> NatType:
        if not self._responses:
            return NatType.UDP_BLOCKED
        successful = [r for r in self._responses if r.success]
        if not successful:
            return NatType.UDP_BLOCKED

        primary = successful[0]
        primary_addr = (primary.mapped_address, primary.mapped_port)

        other_servers = successful[1:]
        for r in other_servers:
            other_addr = (r.mapped_address, r.mapped_port)
            if other_addr != primary_addr:
                return NatType.SYMMETRIC
        return NatType.FULL_CONE


# ── ICE Gathering ────────────────────────────────────────────────────────────


@dataclass
class IceGatherer:
    host_candidates: list[IceCandidate] = field(default_factory=list)
    srflx_candidates: list[IceCandidate] = field(default_factory=list)
    relay_candidates: list[IceCandidate] = field(default_factory=list)
    local_ufrag: str = field(default="")
    local_pwd: str = field(default="")

    def __post_init__(self) -> None:
        if not self.local_ufrag:
            self.local_ufrag = _random_bytes(4).hex()
        if not self.local_pwd:
            self.local_pwd = _random_bytes(22).hex()

    def add_host(self, address: str, port: int) -> None:
        self.host_candidates.append(IceCandidate.host(address, port))

    def add_srflx(self, address: str, port: int) -> None:
        self.srflx_candidates.append(IceCandidate.srflx(address, port))

    def add_relay(self, address: str, port: int) -> None:
        self.relay_candidates.append(IceCandidate.relay(address, port))

    def all_candidates(self) -> list[IceCandidate]:
        return self.host_candidates + self.srflx_candidates + self.relay_candidates

    def pair_with(
        self,
        remote: IceGatherer,
    ) -> list[IceCandidatePair]:
        pairs: list[IceCandidatePair] = []
        for local in self.all_candidates():
            for remote_cand in remote.all_candidates():
                pairs.append(IceCandidatePair(local=local, remote=remote_cand))
        pairs.sort(key=lambda p: p.priority, reverse=True)
        return pairs


# ── Hole Punch State Machine ─────────────────────────────────────────────────


class HolePunchPhase(enum.StrEnum):
    IDLE = "idle"
    BINDING_REQUEST_SENT = "binding_request_sent"
    BINDING_RESPONSE_RECEIVED = "binding_response_received"
    CONNECTIVITY_CHECK_SENT = "connectivity_check_sent"
    CONNECTIVITY_CHECK_PASSED = "connectivity_check_passed"
    NOMINATED = "nominated"
    ESTABLISHED = "established"
    FAILED = "failed"


@dataclass
class HolePunchState:
    phase: HolePunchPhase = HolePunchPhase.IDLE
    local: IceGatherer | None = None
    remote: IceGatherer | None = None
    selected_pair: IceCandidatePair | None = None
    _pairs: list[IceCandidatePair] = field(default_factory=list)
    _tick: int = 0

    def gather(self, local: IceGatherer) -> None:
        self.local = local
        if self.phase == HolePunchPhase.IDLE:
            self.phase = HolePunchPhase.BINDING_REQUEST_SENT

    def on_binding_response(self, srflx_addr: str, srflx_port: int) -> None:
        if self.local is not None:
            self.local.add_srflx(srflx_addr, srflx_port)
        if self.phase == HolePunchPhase.BINDING_REQUEST_SENT:
            self.phase = HolePunchPhase.BINDING_RESPONSE_RECEIVED

    def on_remote_gathered(self, remote: IceGatherer) -> None:
        self.remote = remote
        if self.local is not None and self.phase == HolePunchPhase.BINDING_RESPONSE_RECEIVED:
            self._pairs = self.local.pair_with(remote)
            self.phase = HolePunchPhase.CONNECTIVITY_CHECK_SENT

    def check_pair(self, pair: IceCandidatePair) -> bool:
        direct = pair.local.kind == IceCandidateType.HOST and pair.remote.kind == IceCandidateType.HOST
        srflx = (pair.local.kind == IceCandidateType.SRFLX and pair.remote.kind == IceCandidateType.HOST) or (
            pair.local.kind == IceCandidateType.HOST and pair.remote.kind == IceCandidateType.SRFLX
        )
        return direct or srflx

    def connectivity_passes(self, pair: IceCandidatePair) -> bool:
        if not self.check_pair(pair):
            return False
        self.selected_pair = pair
        if self.phase == HolePunchPhase.CONNECTIVITY_CHECK_SENT:
            self.phase = HolePunchPhase.CONNECTIVITY_CHECK_PASSED
        return True

    def nominate(self) -> None:
        if self.phase == HolePunchPhase.CONNECTIVITY_CHECK_PASSED:
            self.phase = HolePunchPhase.NOMINATED

    def establish(self) -> None:
        if self.phase == HolePunchPhase.NOMINATED and self.selected_pair is not None:
            self.phase = HolePunchPhase.ESTABLISHED

    def fail(self) -> None:
        self.phase = HolePunchPhase.FAILED


# ── Symmetric NAT hole-punch predictor ───────────────────────────────────────


@dataclass
class SymmetricPortPredictor:
    observed_ports: list[int] = field(default_factory=list)

    def observe(self, port: int) -> None:
        if len(self.observed_ports) >= 4:
            self.observed_ports.pop(0)
        self.observed_ports.append(port)

    def predict_next(self) -> int:
        if len(self.observed_ports) < 2:
            return 0
        deltas = [self.observed_ports[i] - self.observed_ports[i - 1] for i in range(1, len(self.observed_ports))]
        if all(d == deltas[0] for d in deltas):
            return self.observed_ports[-1] + deltas[0]
        if len(deltas) >= 3:
            return self.observed_ports[-1] + round(sum(deltas) / len(deltas))
        return self.observed_ports[-1] + deltas[-1]


# ── Connectivity test harness ────────────────────────────────────────────────


@dataclass
class ConnectivityResult:
    reachable: bool
    nat_type: NatType
    local_candidates: list[IceCandidate]
    remote_candidates: list[IceCandidate]
    selected_pair: IceCandidatePair | None = None
    rtt_ms: float = 0.0

    def established(self) -> bool:
        return self.reachable and self.selected_pair is not None


# ── NAT traversal orchestrator ───────────────────────────────────────────────


@dataclass
class NatTraversalOrchestrator:
    local_gatherer: IceGatherer
    stun_client: StunClient
    predictor: SymmetricPortPredictor = field(default_factory=SymmetricPortPredictor)
    _results: list[ConnectivityResult] = field(default_factory=list)

    def discover_reflexive(
        self,
        binding_mapped: dict[str, tuple[str, int]],
    ) -> None:
        for _server_addr, (mapped_addr, mapped_port) in binding_mapped.items():
            self.local_gatherer.add_srflx(mapped_addr, mapped_port)

    def compute_best_pair(
        self,
        pairs: list[IceCandidatePair],
    ) -> IceCandidatePair | None:
        reachable = [
            p
            for p in pairs
            if (
                p.local.kind == IceCandidateType.HOST
                and p.remote.kind in (IceCandidateType.HOST, IceCandidateType.SRFLX)
            )
            or (p.local.kind == IceCandidateType.SRFLX and p.remote.kind == IceCandidateType.HOST)
        ]
        return reachable[0] if reachable else None
