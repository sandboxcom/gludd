"""Push-pull gossip protocol with rumor-mongering and failure detection.

Membership lifecycle:
    alive -> suspected -> (recovered -> alive) | failed -> dead

Gossip rounds use push-pull: nodes exchange a digest of known versions,
then push newer data and pull missing data in a single round-trip.
Rumors spread with a bounded hop count (fanout + TTL) and die after
they exceed their hop budget.

Failure detection uses an adaptive gossip interval: each round bumps a
per-node heartbeat counter; phi-accrual thresholds flag nodes as
suspected, then failed after a configurable grace period without ack.
"""

from __future__ import annotations

import enum
import random
import time as time_mod
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

# ── vocabulary ──────────────────────────────────────────────────────────────


class MemberStatus(enum.StrEnum):
    ALIVE = "alive"
    SUSPECTED = "suspected"
    FAILED = "failed"
    DEAD = "dead"


# ── data structures ─────────────────────────────────────────────────────────


@dataclass
class Member:
    node_id: str
    address: str
    status: MemberStatus = MemberStatus.ALIVE
    heartbeat: int = 0
    last_seen: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Rumor:
    key: str
    value: Any
    version: int
    origin: str
    ttl: int
    created_at: float = field(default_factory=time_mod.monotonic)


@dataclass
class DigestEntry:
    key: str
    version: int
    node_id: str


@dataclass
class GossipMessage:
    msg_type: str  # "digest", "push", "pull_request", "pull_response", "ack"
    sender_id: str
    round: int
    digest: list[DigestEntry] = field(default_factory=list)
    rumors: list[Rumor] = field(default_factory=list)
    pull_keys: list[str] = field(default_factory=list)
    members: list[Member] = field(default_factory=list)


# ── protocol ────────────────────────────────────────────────────────────────


class GossipProtocol:
    """Push-pull gossip with rumor-mongering and failure detection.

    Each node maintains a partial view of the cluster (member list) and a
    key-value data store with per-entry version vectors.  A gossip round:

    1. The initiator sends a *digest* (list of ``(key, version)`` pairs) to
       a randomly selected peer.
    2. The peer compares the digest against its local store:
       - Keys it is ahead on → push back to initiator.
       - Keys the initiator is ahead on → request pull.
       - Members not yet seen → piggyback membership data.
    3. The initiator updates its local store from the peer's push, then
       responds with the requested pull data.

    Rumors: a rumor is a key-value pair tagged with an origin and a TTL.
    When a rumor is created it fans out to ``rumor_fanout`` random peers.
    Each peer decrements the TTL and re-gossips while TTL > 0.  The rumor
    dies when TTL reaches 0 or a duplicate is received.

    Failure detection: every gossip round bumps the initiator's heartbeat.
    A node that hasn't been heard from for ``suspect_timeout`` seconds is
    marked SUSPECTED.  If still silent after ``fail_timeout`` seconds it is
    marked FAILED.  After ``dead_timeout`` additional seconds the entry is
    pruned (DEAD).
    """

    def __init__(
        self,
        node_id: str,
        address: str,
        *,
        gossip_interval: float = 1.0,
        rumor_fanout: int = 3,
        rumor_ttl: int = 10,
        suspect_timeout: float = 3.0,
        fail_timeout: float = 9.0,
        dead_timeout: float = 30.0,
        max_digest_size: int = 50,
        max_members_per_message: int = 10,
        seed: int | None = None,
    ) -> None:
        self.node_id = node_id
        self.address = address
        self.gossip_interval = gossip_interval
        self.rumor_fanout = rumor_fanout
        self.rumor_ttl = rumor_ttl
        self.suspect_timeout = suspect_timeout
        self.fail_timeout = fail_timeout
        self.dead_timeout = dead_timeout
        self.max_digest_size = max_digest_size
        self.max_members_per_message = max_members_per_message

        self._round: int = 0
        self._rng = random.Random(seed)

        # local key-value store: {key: (value, version)}
        self._store: dict[str, tuple[Any, int]] = {}

        # membership table
        self._members: OrderedDict[str, Member] = OrderedDict()
        self._members[node_id] = Member(
            node_id=node_id,
            address=address,
            status=MemberStatus.ALIVE,
            heartbeat=0,
            last_seen=time_mod.monotonic(),
        )

        # rumor tracking — origin + key → highest version seen
        self._rumor_versions: dict[tuple[str, str], int] = {}

        # peer list for random selection (set by caller or bootstrap)
        self._peers: list[str] = []

    # ── public API ──────────────────────────────────────────────────────

    @property
    def round(self) -> int:
        return self._round

    @property
    def members(self) -> dict[str, Member]:
        return dict(self._members)

    def set_peers(self, peers: list[str]) -> None:
        self._peers = list(peers)

    def add_peer(self, peer_id: str, address: str) -> None:
        if peer_id not in self._members:
            self._members[peer_id] = Member(
                node_id=peer_id,
                address=address,
                status=MemberStatus.ALIVE,
                heartbeat=0,
                last_seen=time_mod.monotonic(),
            )
        if peer_id not in self._peers:
            self._peers.append(peer_id)

    def remove_peer(self, peer_id: str) -> None:
        if peer_id in self._peers:
            self._peers.remove(peer_id)

    # ── data access ─────────────────────────────────────────────────────

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        return entry[0] if entry else None

    def put(self, key: str, value: Any) -> int:
        prev = self._store.get(key)
        version = (prev[1] + 1) if prev else 1
        self._store[key] = (value, version)
        return version

    def put_rumor(self, key: str, value: Any, ttl: int | None = None) -> Rumor:
        version = self.put(key, value)
        rumor = Rumor(
            key=key,
            value=value,
            version=version,
            origin=self.node_id,
            ttl=ttl if ttl is not None else self.rumor_ttl,
        )
        self._rumor_versions[(self.node_id, key)] = version
        return rumor

    def list_keys(self) -> list[str]:
        return list(self._store.keys())

    def all_data(self) -> dict[str, tuple[Any, int]]:
        return dict(self._store)

    def store_size(self) -> int:
        return len(self._store)

    # ── gossip round ────────────────────────────────────────────────────

    def create_digest(self) -> GossipMessage:
        self.bump_heartbeat()
        self._round += 1
        digest_entries = [
            DigestEntry(key=key, version=ver, node_id=self.node_id) for key, (_, ver) in self._store.items()
        ][: self.max_digest_size]
        return GossipMessage(
            msg_type="digest",
            sender_id=self.node_id,
            round=self._round,
            digest=digest_entries,
            members=self._pick_members_to_share(),
        )

    def handle_digest(self, msg: GossipMessage) -> GossipMessage:
        self._merge_remote_members(msg.members)
        push_rumors, pull_keys = self._diff_digest(msg)
        return GossipMessage(
            msg_type="push",
            sender_id=self.node_id,
            round=msg.round,
            rumors=push_rumors,
            pull_keys=pull_keys,
            members=self._pick_members_to_share(),
        )

    def handle_push(self, msg: GossipMessage) -> GossipMessage | None:
        self._merge_remote_members(msg.members)
        for rumor in msg.rumors:
            self._apply_rumor_if_fresh(rumor)
        if msg.pull_keys:
            pull_response = []
            for key in msg.pull_keys:
                entry = self._store.get(key)
                if entry is not None:
                    r = Rumor(
                        key=key,
                        value=entry[0],
                        version=entry[1],
                        origin=self.node_id,
                        ttl=1,
                    )
                    pull_response.append(r)
            return GossipMessage(
                msg_type="pull_response",
                sender_id=self.node_id,
                round=msg.round,
                rumors=pull_response,
                members=self._pick_members_to_share(),
            )
        return None

    def handle_pull_response(self, msg: GossipMessage) -> None:
        self._merge_remote_members(msg.members)
        for rumor in msg.rumors:
            self._apply_rumor_if_fresh(rumor)

    def handle_ack(self, msg: GossipMessage) -> None:
        self._merge_remote_members(msg.members)

    def handle_message(self, msg: GossipMessage) -> GossipMessage | None:
        handler = {
            "digest": self.handle_digest,
            "push": self.handle_push,
            "pull_response": self.handle_pull_response,
            "ack": self.handle_ack,
        }
        fn = handler.get(msg.msg_type)
        if fn is None:
            return None
        if msg.msg_type == "pull_response" or msg.msg_type == "ack":
            fn(msg)
            return None
        return fn(msg)

    # ── random peer selection ───────────────────────────────────────────

    def select_peer(self, exclude: set[str] | None = None) -> str | None:
        candidates = [p for p in self._peers if p != self.node_id and (exclude is None or p not in exclude)]
        if not candidates:
            return None
        return self._rng.choice(candidates)

    def select_rumor_peers(self, rumor: Rumor, exclude: set[str] | None = None) -> list[str]:
        alive_peers = [
            p
            for p in self._peers
            if p != self.node_id
            and p != rumor.origin
            and self._members.get(p, Member(p, "")).status in (MemberStatus.ALIVE, MemberStatus.SUSPECTED)
            and (exclude is None or p not in exclude)
        ]
        count = min(self.rumor_fanout, len(alive_peers))
        if count == 0:
            return []
        return self._rng.sample(alive_peers, count)

    # ── heartbeat / failure detection ───────────────────────────────────

    def bump_heartbeat(self) -> None:
        me = self._members.get(self.node_id)
        if me is not None:
            me.heartbeat += 1
            me.last_seen = time_mod.monotonic()
            me.status = MemberStatus.ALIVE

    def detect_failures(self, now: float | None = None) -> list[Member]:
        if now is None:
            now = time_mod.monotonic()
        changes: list[Member] = []
        for member in self._members.values():
            if member.node_id == self.node_id:
                continue
            elapsed = now - member.last_seen
            if elapsed > self.dead_timeout and member.status == MemberStatus.FAILED:
                member.status = MemberStatus.DEAD
                changes.append(member)
            elif elapsed > self.fail_timeout and member.status in (
                MemberStatus.ALIVE,
                MemberStatus.SUSPECTED,
            ):
                member.status = MemberStatus.FAILED
                changes.append(member)
            elif elapsed > self.suspect_timeout and member.status == MemberStatus.ALIVE:
                member.status = MemberStatus.SUSPECTED
                changes.append(member)
        return changes

    def mark_recovered(self, node_id: str) -> bool:
        member = self._members.get(node_id)
        if member is not None and member.status in (MemberStatus.SUSPECTED, MemberStatus.FAILED):
            member.status = MemberStatus.ALIVE
            member.last_seen = time_mod.monotonic()
            member.heartbeat += 1
            return True
        return False

    @property
    def alive_count(self) -> int:
        return sum(1 for m in self._members.values() if m.status == MemberStatus.ALIVE)

    @property
    def suspected_count(self) -> int:
        return sum(1 for m in self._members.values() if m.status == MemberStatus.SUSPECTED)

    @property
    def failed_count(self) -> int:
        return sum(1 for m in self._members.values() if m.status == MemberStatus.FAILED)

    # ── convergence helpers ─────────────────────────────────────────────

    def data_matches(self, other: GossipProtocol) -> bool:
        for key, (_, ver) in self._store.items():
            oe = other._store.get(key)
            if oe is None or oe[1] != ver:
                return False
        for key, (_, ver) in other._store.items():
            se = self._store.get(key)
            if se is None or se[1] != ver:
                return False
        return True

    def member_status_matches(self, other: GossipProtocol, node_id: str) -> bool:
        sm = self._members.get(node_id)
        om = other._members.get(node_id)
        if sm is None or om is None:
            return sm is om
        return sm.status == om.status

    def rumor_hop_count(self, key: str) -> int:
        entry = self._store.get(key)
        if entry is None:
            return -1
        return max(self.rumor_ttl - entry[1], 0) if entry[1] <= self.rumor_ttl else 0

    # ── internal ────────────────────────────────────────────────────────

    def _diff_digest(self, msg: GossipMessage) -> tuple[list[Rumor], list[str]]:
        push_rumors: list[Rumor] = []
        pull_keys: list[str] = []
        digest_keys = {d.key for d in msg.digest}
        for d in msg.digest:
            local = self._store.get(d.key)
            if local is None:
                pull_keys.append(d.key)
            elif local[1] > d.version:
                r = Rumor(
                    key=d.key,
                    value=local[0],
                    version=local[1],
                    origin=self.node_id,
                    ttl=1,
                )
                push_rumors.append(r)
            elif local[1] < d.version:
                pull_keys.append(d.key)
        for key, (value, version) in self._store.items():
            if key not in digest_keys:
                r = Rumor(
                    key=key,
                    value=value,
                    version=version,
                    origin=self.node_id,
                    ttl=1,
                )
                push_rumors.append(r)
        return push_rumors, pull_keys

    def _apply_rumor_if_fresh(self, rumor: Rumor) -> bool:
        dup_key = (rumor.origin, rumor.key)
        highest = self._rumor_versions.get(dup_key, 0)
        if rumor.version <= highest:
            return False
        self._rumor_versions[dup_key] = rumor.version
        local = self._store.get(rumor.key)
        if local is None or rumor.version > local[1]:
            self._store[rumor.key] = (rumor.value, rumor.version)
            return True
        return False

    def _pick_members_to_share(self) -> list[Member]:
        others = [m for mid, m in self._members.items() if mid != self.node_id and m.status != MemberStatus.DEAD]
        if len(others) > self.max_members_per_message:
            others = self._rng.sample(others, self.max_members_per_message)
        return others

    def _merge_remote_members(self, remote: list[Member]) -> None:
        for rm in remote:
            local = self._members.get(rm.node_id)
            if local is None:
                self._members[rm.node_id] = Member(
                    node_id=rm.node_id,
                    address=rm.address,
                    status=rm.status,
                    heartbeat=rm.heartbeat,
                    last_seen=rm.last_seen,
                    metadata=dict(rm.metadata),
                )
            else:
                if rm.heartbeat > local.heartbeat:
                    local.heartbeat = rm.heartbeat
                    local.last_seen = rm.last_seen
                    local.address = rm.address
                    local.metadata = dict(rm.metadata)
                    if local.status == MemberStatus.SUSPECTED and rm.status == MemberStatus.ALIVE:
                        local.status = MemberStatus.ALIVE


# ── simulation helpers ──────────────────────────────────────────────────────


def run_gossip_round(
    nodes: dict[str, GossipProtocol],
    node_id: str,
) -> int:
    """Execute one push-pull round from *node_id*.  Return number of
    messages exchanged (0 if no peer available)."""
    node = nodes[node_id]
    peer_id = node.select_peer()
    if peer_id is None or peer_id not in nodes:
        return 0
    peer = nodes[peer_id]

    msg_count = 0
    # 1. initiator sends digest
    dig = node.create_digest()
    msg_count += 1

    # 2. peer handles digest → push + pull_request
    push_resp = peer.handle_digest(dig)
    msg_count += 1

    # 3. initiator handles push → maybe pull_response
    pull_resp = node.handle_push(push_resp)
    msg_count += 1

    # 4. if the initiator had missing keys, forward pull_response to peer
    if pull_resp is not None:
        # peer is "getting the pull_response" — actually the response to its pull_request
        # In this simplified simulation the push already carries the pull_keys,
        # and the initiator's handle_push returns the pull_response.
        # The peer consumes it.
        peer.handle_pull_response(pull_resp)
        msg_count += 1

    # 5. send ack
    ack = GossipMessage(
        msg_type="ack",
        sender_id=node.node_id,
        round=node.round,
        members=node._pick_members_to_share(),
    )
    peer.handle_ack(ack)
    msg_count += 1

    return msg_count


def spread_rumor(
    nodes: dict[str, GossipProtocol],
    rumor: Rumor,
    origin_id: str,
    visited: set[str] | None = None,
) -> int:
    """Spread a rumor from *origin_id* to its fanout peers.  Return
    the number of nodes that received the rumor."""
    if visited is None:
        visited = set()
    visited.add(origin_id)

    node = nodes[origin_id]
    peers = node.select_rumor_peers(rumor, exclude=visited)
    spread = 0
    GossipMessage(
        msg_type="push",
        sender_id=node.node_id,
        round=node.round,
        rumors=[rumor],
    )
    for peer_id in peers:
        if peer_id in nodes:
            accepted = nodes[peer_id]._apply_rumor_if_fresh(rumor)
            if accepted:
                spread += 1
            visited.add(peer_id)
    return spread


def ensure_convergence(
    nodes: dict[str, GossipProtocol],
    rounds: int = 50,
    check_every: int = 1,
) -> bool:
    """Run gossip rounds until all nodes agree on data or *rounds* exhausted."""
    node_ids = list(nodes.keys())
    for _ in range(rounds):
        for nid in node_ids:
            run_gossip_round(nodes, nid)
        if check_every > 0 and _ % check_every == 0:
            first = nodes[node_ids[0]]
            if all(first.data_matches(nodes[nid]) for nid in node_ids[1:]):
                return True
    first = nodes[node_ids[0]]
    return all(first.data_matches(nodes[nid]) for nid in node_ids[1:])
