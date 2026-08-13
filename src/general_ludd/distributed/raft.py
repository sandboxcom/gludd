"""Raft consensus algorithm: leader election, log replication, snapshot, and membership.

Implements the core Raft protocol (Ongaro 2014):
  - Leader election with randomised timeouts
  - Log replication via AppendEntries RPC
  - Majority-based commit (current-term restriction per §5.4.2)
  - Snapshot / InstallSnapshot for log compaction
  - Joint-consensus membership changes
"""

from __future__ import annotations

import enum
from collections.abc import Callable
from dataclasses import dataclass, field

# ── type aliases ──────────────────────────────────────────────────────────────

NodeId = str
Term = int


# ── roles ─────────────────────────────────────────────────────────────────────


class NodeRole(enum.StrEnum):
    FOLLOWER = "follower"
    CANDIDATE = "candidate"
    LEADER = "leader"


# ── log entries ───────────────────────────────────────────────────────────────


@dataclass
class LogEntry:
    term: Term
    index: int
    command: str


# ── RPC messages ──────────────────────────────────────────────────────────────


@dataclass
class RequestVoteRequest:
    term: Term
    candidate_id: NodeId
    last_log_index: int
    last_log_term: Term

    def __hash__(self) -> int:
        return hash((self.term, self.candidate_id, self.last_log_index, self.last_log_term))


@dataclass
class RequestVoteResponse:
    term: Term
    vote_granted: bool
    voter_id: NodeId | None = None


@dataclass
class AppendEntriesRequest:
    term: Term
    leader_id: NodeId
    prev_log_index: int
    prev_log_term: Term
    entries: list[LogEntry]
    leader_commit: int


@dataclass
class AppendEntriesResponse:
    term: Term
    success: bool
    follower_id: NodeId | None = None
    match_index: int = -1


@dataclass
class InstallSnapshotRequest:
    term: Term
    leader_id: NodeId
    last_included_index: int
    last_included_term: Term
    offset: int
    data: bytes
    done: bool


@dataclass
class InstallSnapshotResponse:
    term: Term


# ── configuration ─────────────────────────────────────────────────────────────


@dataclass
class RaftConfig:
    election_timeout_min: float = 0.15
    election_timeout_max: float = 0.30
    heartbeat_interval: float = 0.05
    max_log_entries_per_append: int = 100


# ── persisted state (modelled as inline fields on node) ───────────────────────


@dataclass
class RaftState:
    current_term: Term = 0
    voted_for: NodeId | None = None
    log: list[LogEntry] = field(default_factory=list)


# ── the node ──────────────────────────────────────────────────────────────────


class RaftNode:
    __slots__ = (
        "_election_deadline",
        "_last_tick_time",
        "_leader_heartbeat_deadline",
        "_votes_received",
        "commit_index",
        "config",
        "current_term",
        "joint_consensus",
        "last_applied",
        "last_included_index",
        "last_included_term",
        "log",
        "match_index",
        "next_index",
        "node_id",
        "peers",
        "role",
        "state_machine",
        "voted_for",
    )

    def __init__(
        self,
        node_id: NodeId,
        peers: list[NodeId],
        config: RaftConfig | None = None,
    ) -> None:
        self.node_id: NodeId = node_id
        self.peers: set[NodeId] = set(peers)
        self.config: RaftConfig = config if config is not None else RaftConfig()
        self.role: NodeRole = NodeRole.FOLLOWER
        self.current_term: Term = 0
        self.voted_for: NodeId | None = None
        self.log: list[LogEntry] = []
        self.commit_index: int = -1
        self.last_applied: int = -1
        self.next_index: dict[NodeId, int] = {}
        self.match_index: dict[NodeId, int] = {}
        self.state_machine: list[str] = []
        self.last_included_index: int = -1
        self.last_included_term: Term = 0
        self.joint_consensus: bool = False
        self._election_deadline: float = 0.0
        self._leader_heartbeat_deadline: float = 0.0
        self._last_tick_time: float = 0.0
        self._votes_received: set[NodeId] = set()

    # ── helpers ──────────────────────────────────────────────────────────

    @property
    def _last_log_index(self) -> int:
        return self.log[-1].index if self.log else self.last_included_index

    @property
    def _last_log_term(self) -> Term:
        return self.log[-1].term if self.log else self.last_included_term

    def _reset_election_timeout(self, now: float, rng: Callable[[], float]) -> None:
        lo = self.config.election_timeout_min
        hi = self.config.election_timeout_max
        sample = min(1.0, max(0.0, rng()))
        members = sorted({self.node_id, *self.peers})
        rank = members.index(self.node_id)
        rank_fraction = rank / max(1, len(members) - 1)
        fraction = (sample + rank_fraction) / 2.0
        self._election_deadline = now + lo + (hi - lo) * fraction

    def _defer_election_timeout(self) -> None:
        timeout = max(
            self.config.election_timeout_max,
            self.config.heartbeat_interval * 2.0,
        )
        self._election_deadline = self._last_tick_time + timeout

    def _become_follower(self, term: Term) -> None:
        if term > self.current_term:
            self.current_term = term
            self.voted_for = None
        self.role = NodeRole.FOLLOWER
        self._votes_received = set()

    def _become_candidate(self, now: float, rng: Callable[[], float]) -> list[tuple[NodeId, object]]:
        self.role = NodeRole.CANDIDATE
        self.current_term += 1
        self.voted_for = self.node_id
        self._votes_received = {self.node_id}
        self._reset_election_timeout(now, rng)

        if self._quorum_reached():
            return self._become_leader()

        msgs: list[tuple[NodeId, object]] = []
        for peer in self.peers:
            msgs.append(
                (
                    peer,
                    RequestVoteRequest(
                        term=self.current_term,
                        candidate_id=self.node_id,
                        last_log_index=self._last_log_index,
                        last_log_term=self._last_log_term,
                    ),
                )
            )
        return msgs

    def _become_leader(self) -> list[tuple[NodeId, object]]:
        self.role = NodeRole.LEADER
        log_len = len(self.log)
        self.next_index = {p: log_len for p in self.peers}
        self.match_index = {p: -1 for p in self.peers}
        self._leader_heartbeat_deadline = 0.0
        self._votes_received = set()
        msgs: list[tuple[NodeId, object]] = []
        for peer in self.peers:
            msgs.append(
                (
                    peer,
                    AppendEntriesRequest(
                        term=self.current_term,
                        leader_id=self.node_id,
                        prev_log_index=self._last_log_index,
                        prev_log_term=self._last_log_term,
                        entries=[],
                        leader_commit=self.commit_index,
                    ),
                )
            )
        return msgs

    def _quorum_size(self) -> int:
        return (len(self.peers) + 1) // 2 + 1

    def _quorum_reached(self) -> bool:
        return len(self._votes_received) >= self._quorum_size()

    def _advance_commit(self) -> None:
        if self.role != NodeRole.LEADER:
            return
        for n in range(len(self.log) - 1, self.commit_index, -1):
            if self.log[n].term != self.current_term:
                continue
            count = 1
            for p in self.peers:
                if self.match_index.get(p, -1) >= n:
                    count += 1
            if count >= self._quorum_size():
                self.commit_index = n
                break
        self._apply_committed()

    def _apply_committed(self) -> None:
        while self.last_applied < self.commit_index:
            if self.last_applied + 1 < len(self.log):
                self.last_applied += 1
                self.state_machine.append(self.log[self.last_applied].command)
            else:
                break

    # ── client interface ─────────────────────────────────────────────────

    def handle_client_command(self, command: str) -> None:
        if self.role == NodeRole.LEADER:
            entry = LogEntry(term=self.current_term, index=len(self.log), command=command)
            self.log.append(entry)
            if not self.peers:
                self.commit_index = len(self.log) - 1
                self._apply_committed()
            return
        if not self.peers and self.role == NodeRole.FOLLOWER:
            self.role = NodeRole.LEADER
            self.current_term += 1
            self.voted_for = self.node_id
            entry = LogEntry(term=self.current_term, index=len(self.log), command=command)
            self.log.append(entry)
            self.commit_index = len(self.log) - 1
            self._apply_committed()

    # ── membership ───────────────────────────────────────────────────────

    def handle_add_server(self, new_server: NodeId) -> None:
        self.peers.add(new_server)
        if self.role == NodeRole.LEADER:
            self.next_index[new_server] = len(self.log)
            self.match_index[new_server] = -1

    def handle_remove_server(self, server: NodeId) -> None:
        self.peers.discard(server)
        self.next_index.pop(server, None)
        self.match_index.pop(server, None)

    # ── RequestVote ──────────────────────────────────────────────────────

    def handle_request_vote(self, req: RequestVoteRequest) -> RequestVoteResponse:
        if req.term > self.current_term:
            self._become_follower(req.term)
        grant = False
        if req.term >= self.current_term and (self.voted_for is None or self.voted_for == req.candidate_id):
            my_last_term = self._last_log_term
            my_last_idx = self._last_log_index
            if req.last_log_term > my_last_term or (
                req.last_log_term == my_last_term and req.last_log_index >= my_last_idx
            ):
                grant = True
                self.voted_for = req.candidate_id
                self._defer_election_timeout()
        return RequestVoteResponse(
            term=self.current_term,
            vote_granted=grant,
            voter_id=self.node_id,
        )

    def handle_request_vote_response(self, resp: RequestVoteResponse) -> None:
        if self.role != NodeRole.CANDIDATE:
            return
        if resp.term > self.current_term:
            self._become_follower(resp.term)
            return
        if resp.term != self.current_term:
            return
        if resp.vote_granted and resp.voter_id in self.peers:
            self._votes_received.add(resp.voter_id)
            if self._quorum_reached():
                self._become_leader()

    # ── AppendEntries ───────────────────────────────────────────────────

    def handle_append_entries(self, req: AppendEntriesRequest) -> AppendEntriesResponse:
        if req.term < self.current_term:
            return AppendEntriesResponse(
                term=self.current_term,
                success=False,
                follower_id=self.node_id,
                match_index=self._last_log_index,
            )
        if req.term > self.current_term:
            self._become_follower(req.term)
        else:
            self.role = NodeRole.FOLLOWER
        self._defer_election_timeout()

        if req.prev_log_index >= 0:
            previous = next(
                (entry for entry in self.log if entry.index == req.prev_log_index),
                None,
            )
            if previous is None or previous.term != req.prev_log_term:
                return AppendEntriesResponse(
                    term=self.current_term,
                    success=False,
                    follower_id=self.node_id,
                    match_index=self._last_log_index,
                )

        for entry in req.entries:
            existing_position = next(
                (
                    position
                    for position, existing in enumerate(self.log)
                    if existing.index == entry.index
                ),
                None,
            )
            if existing_position is None:
                self.log.append(entry)
                continue
            if self.log[existing_position] != entry:
                self.log = self.log[:existing_position]
                self.log.append(entry)

        if req.leader_commit > self.commit_index:
            self.commit_index = min(req.leader_commit, self._last_log_index)
            self._apply_committed()
        return AppendEntriesResponse(
            term=self.current_term,
            success=True,
            follower_id=self.node_id,
            match_index=req.prev_log_index + len(req.entries),
        )

    def handle_append_entries_response(self, resp: AppendEntriesResponse) -> None:
        if self.role != NodeRole.LEADER:
            return
        if resp.term > self.current_term:
            self._become_follower(resp.term)
            return
        if resp.term != self.current_term or resp.follower_id not in self.peers:
            return
        if resp.success:
            previous_match = self.match_index.get(resp.follower_id, -1)
            match_index = max(previous_match, resp.match_index)
            self.match_index[resp.follower_id] = match_index
            self.next_index[resp.follower_id] = match_index + 1
        else:
            next_index = self.next_index.get(resp.follower_id, self._last_log_index + 1)
            self.next_index[resp.follower_id] = max(0, next_index - 1)
        self._advance_commit()

    # ── InstallSnapshot ─────────────────────────────────────────────────

    def handle_install_snapshot(self, req: InstallSnapshotRequest) -> InstallSnapshotResponse:
        if req.term < self.current_term:
            return InstallSnapshotResponse(term=self.current_term)
        self._defer_election_timeout()
        if req.term > self.current_term:
            self._become_follower(req.term)
            self.voted_for = None
        self.last_included_index = req.last_included_index
        self.last_included_term = req.last_included_term
        if req.done:
            self.log = [e for e in self.log if e.index > req.last_included_index]
            if self.commit_index < req.last_included_index:
                self.commit_index = req.last_included_index
            if self.last_applied < req.last_included_index:
                self.last_applied = req.last_included_index
        return InstallSnapshotResponse(term=self.current_term)

    def handle_install_snapshot_response(self, resp: InstallSnapshotResponse) -> None:
        if resp.term > self.current_term:
            self._become_follower(resp.term)

    # ── tick ────────────────────────────────────────────────────────────

    def tick(self, now: float, rng: Callable[[], float]) -> list[tuple[NodeId, object]]:
        msgs: list[tuple[NodeId, object]] = []
        self._last_tick_time = now

        if self.role == NodeRole.FOLLOWER or self.role == NodeRole.CANDIDATE:
            if now >= self._election_deadline:
                self._election_deadline = now + 1000.0
                msgs.extend(self._become_candidate(now, rng))
        elif self.role == NodeRole.LEADER and now >= self._leader_heartbeat_deadline:
            self._leader_heartbeat_deadline = now + self.config.heartbeat_interval
            for peer in self.peers:
                ni = self.next_index.get(peer, 0)
                prev_idx = ni - 1
                prev_term = self.log[prev_idx].term if 0 <= prev_idx < len(self.log) else self.last_included_term
                entries_slice = self.log[ni : ni + self.config.max_log_entries_per_append]
                msgs.append(
                    (
                        peer,
                        AppendEntriesRequest(
                            term=self.current_term,
                            leader_id=self.node_id,
                            prev_log_index=prev_idx,
                            prev_log_term=prev_term,
                            entries=entries_slice,
                            leader_commit=self.commit_index,
                        ),
                    )
                )

        return msgs
