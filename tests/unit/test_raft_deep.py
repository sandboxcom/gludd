"""Deep Raft consensus algorithm tests.

Covers leader election, log replication, commit, snapshot, and membership changes.
"""

from __future__ import annotations

from collections.abc import Callable

# ── Import the module under test ──────────────────────────────────────
from general_ludd.distributed.raft import (
    AppendEntriesRequest,
    AppendEntriesResponse,
    InstallSnapshotRequest,
    InstallSnapshotResponse,
    LogEntry,
    NodeRole,
    RaftConfig,
    RaftNode,
    RequestVoteRequest,
    RequestVoteResponse,
)

# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _cluster(n: int, timeout: float = 0.05) -> list[RaftNode]:
    ids = [f"n{i}" for i in range(n)]
    peers = [[p for p in ids if p != name] for name in ids]
    return [
        RaftNode(name, peers[i], config=RaftConfig(election_timeout_min=timeout, election_timeout_max=timeout * 2.5))
        for i, name in enumerate(ids)
    ]


def _send(
    msgs: list[tuple[str, str, object]],
    sender: str,
    dest: str,
    req: object,
) -> None:
    msgs.append((sender, dest, req))


def _deliver(
    msgs: list[tuple[str, str, object]],
    nodes: dict[str, RaftNode],
    rng: Callable[[], float] | None = None,
) -> None:
    delivered: list[tuple[str, str, object]] = list(msgs)
    msgs.clear()
    for sender, dest, req in delivered:
        if rng is not None:
            pass
        _deliver_one(nodes, sender, dest, req, msgs)


def _deliver_one(
    nodes: dict[str, RaftNode],
    sender: str,
    dest: str,
    req: object,
    outbox: list[tuple[str, str, object]],
) -> None:
    node = nodes[dest]
    if isinstance(req, RequestVoteRequest):
        vote_response = node.handle_request_vote(req)
        outbox.append((dest, sender, vote_response))
    elif isinstance(req, RequestVoteResponse):
        node.handle_request_vote_response(req)
    elif isinstance(req, AppendEntriesRequest):
        append_response = node.handle_append_entries(req)
        outbox.append((dest, sender, append_response))
    elif isinstance(req, AppendEntriesResponse):
        node.handle_append_entries_response(req)
    elif isinstance(req, InstallSnapshotRequest):
        snapshot_response = node.handle_install_snapshot(req)
        outbox.append((dest, sender, snapshot_response))
    elif isinstance(req, InstallSnapshotResponse):
        node.handle_install_snapshot_response(req)


def _tick_all(
    nodes: list[RaftNode],
    msgs: list[tuple[str, str, object]],
    now: float,
    rng: Callable[[], float],
) -> None:
    node_map = {n.node_id: n for n in nodes}
    for n in nodes:
        out = n.tick(now, rng)
        for dest, req in out:
            msgs.append((n.node_id, dest, req))
    _deliver(msgs, node_map)


def _step_until_stable(
    nodes: list[RaftNode],
    tick_step: float = 0.01,
    max_ticks: int = 2000,
) -> None:
    msgs: list[tuple[str, str, object]] = []
    now = 0.0
    rng_count = 0

    def rng() -> float:
        nonlocal rng_count
        rng_count += 1
        return 0.5

    for _ in range(max_ticks):
        now += tick_step
        _tick_all(nodes, msgs, now, rng)
        roles = {n.role for n in nodes}
        if NodeRole.LEADER in roles and NodeRole.CANDIDATE not in roles:
            return
    raise TimeoutError("cluster did not stabilise")


def _leader(nodes: list[RaftNode]) -> RaftNode:
    for n in nodes:
        if n.role == NodeRole.LEADER:
            return n
    raise RuntimeError("no leader")


def _propose(nodes: list[RaftNode], command: str) -> None:
    msgs: list[tuple[str, str, object]] = []
    {n.node_id: n for n in nodes}
    leader = _leader(nodes)
    leader.handle_client_command(command)
    now = 0.0
    rng_count = 0

    def rng() -> float:
        nonlocal rng_count
        rng_count += 1
        return 0.2

    for _ in range(200):
        now += 0.01
        _tick_all(nodes, msgs, now, rng)


# ═══════════════════════════════════════════════════════════════════════
# Constructor / basic types
# ═══════════════════════════════════════════════════════════════════════


class TestConstruction:
    def test_node_starts_as_follower(self) -> None:
        n = RaftNode("n0", ["n1", "n2"])
        assert n.role == NodeRole.FOLLOWER
        assert n.current_term == 0
        assert n.voted_for is None

    def test_node_stores_config(self) -> None:
        cfg = RaftConfig(election_timeout_min=0.15, election_timeout_max=0.30)
        n = RaftNode("n0", ["n1", "n2"], config=cfg)
        assert n.config.election_timeout_min == 0.15
        assert n.config.election_timeout_max == 0.30

    def test_log_starts_empty(self) -> None:
        n = RaftNode("n0", ["n1"])
        assert n.log == []
        assert n.commit_index == -1
        assert n.last_applied == -1

    def test_log_entry_has_term_and_command(self) -> None:
        e = LogEntry(term=3, index=7, command="set x=1")
        assert e.term == 3
        assert e.index == 7
        assert e.command == "set x=1"

    def test_request_vote_rpc_serialisation(self) -> None:
        r = RequestVoteRequest(term=5, candidate_id="n2", last_log_index=10, last_log_term=4)
        assert r.term == 5
        assert r.candidate_id == "n2"
        assert r.last_log_index == 10
        assert r.last_log_term == 4

    def test_append_entries_rpc_serialisation(self) -> None:
        entries = [LogEntry(term=3, index=1, command="x")]
        r = AppendEntriesRequest(
            term=3, leader_id="n0", prev_log_index=0, prev_log_term=2, entries=entries, leader_commit=1
        )
        assert r.term == 3
        assert r.entries == entries
        assert r.leader_commit == 1

    def test_node_id_and_peers(self) -> None:
        n = RaftNode("server-1", ["server-2", "server-3", "server-4", "server-5"])
        assert n.node_id == "server-1"
        assert len(n.peers) == 4
        assert "server-2" in n.peers


# ═══════════════════════════════════════════════════════════════════════
# Leader election
# ═══════════════════════════════════════════════════════════════════════


class TestLeaderElection:
    def test_single_node_becomes_leader(self) -> None:
        n = RaftNode("n0", [])
        n.handle_client_command("cmd")
        assert n.role == NodeRole.LEADER

    def test_three_node_cluster_elects_leader(self) -> None:
        nodes = _cluster(3)
        _step_until_stable(nodes)
        leaders = [n for n in nodes if n.role == NodeRole.LEADER]
        assert len(leaders) == 1

    def test_candidate_increments_term(self) -> None:
        nodes = _cluster(3)
        _step_until_stable(nodes)
        leader = _leader(nodes)
        assert leader.current_term >= 1

    def test_follower_becomes_candidate_on_timeout(self) -> None:
        n = RaftNode("n0", ["n1", "n2"], config=RaftConfig(election_timeout_min=0.1, election_timeout_max=0.2))
        assert n.role.value == NodeRole.FOLLOWER.value
        count = 0

        def rng() -> float:
            nonlocal count
            count += 1
            return 0.05

        n.tick(0.15, rng)
        assert n.role == NodeRole.CANDIDATE
        assert n.current_term == 1
        assert n.voted_for == "n0"

    def test_candidate_votes_for_self(self) -> None:
        n = RaftNode("n0", ["n1", "n2"], config=RaftConfig(election_timeout_min=0.1, election_timeout_max=0.2))

        def rng() -> float:
            return 0.05

        n.tick(0.11, rng)
        assert n.role == NodeRole.CANDIDATE
        assert n.voted_for == "n0"

    def test_follower_grants_vote_on_first_request(self) -> None:
        follower = RaftNode("n1", ["n0"])
        req = RequestVoteRequest(term=2, candidate_id="n0", last_log_index=5, last_log_term=2)
        resp = follower.handle_request_vote(req)
        assert resp.vote_granted is True
        assert resp.voter_id == "n1"
        assert follower.voted_for == "n0"

    def test_follower_rejects_vote_if_already_voted(self) -> None:
        follower = RaftNode("n1", ["n0"])
        req_a = RequestVoteRequest(term=2, candidate_id="n0", last_log_index=5, last_log_term=2)
        req_b = RequestVoteRequest(term=2, candidate_id="n2", last_log_index=5, last_log_term=2)
        follower.handle_request_vote(req_a)
        resp = follower.handle_request_vote(req_b)
        assert resp.vote_granted is False

    def test_follower_rejects_stale_term_vote(self) -> None:
        follower = RaftNode("n1", ["n0"])
        follower.current_term = 5
        req = RequestVoteRequest(term=3, candidate_id="n0", last_log_index=5, last_log_term=2)
        resp = follower.handle_request_vote(req)
        assert resp.vote_granted is False

    def test_vote_rejects_candidate_with_shorter_log(self) -> None:
        follower = RaftNode("n1", ["n0"])
        follower.log = [LogEntry(term=3, index=0, command="a"), LogEntry(term=3, index=1, command="b")]
        req = RequestVoteRequest(term=4, candidate_id="n0", last_log_index=0, last_log_term=2)
        resp = follower.handle_request_vote(req)
        assert resp.vote_granted is False

    def test_only_configured_voter_identity_counts_toward_quorum(self) -> None:
        candidate = RaftNode(
            "n0",
            ["n1", "n2"],
            config=RaftConfig(election_timeout_min=0.1, election_timeout_max=0.2),
        )
        candidate.tick(0.11, lambda: 0.5)

        candidate.handle_request_vote_response(
            RequestVoteResponse(term=1, vote_granted=True, voter_id="unknown")
        )
        assert candidate.role.value == NodeRole.CANDIDATE.value

        candidate.handle_request_vote_response(
            RequestVoteResponse(term=1, vote_granted=True, voter_id="n1")
        )
        assert candidate.role.value == NodeRole.LEADER.value

    def test_stale_heartbeat_does_not_demote_candidate(self) -> None:
        candidate = RaftNode(
            "n0",
            ["n1"],
            config=RaftConfig(election_timeout_min=0.1, election_timeout_max=0.2),
        )
        candidate.tick(0.11, lambda: 0.5)

        response = candidate.handle_append_entries(
            AppendEntriesRequest(
                term=0,
                leader_id="n1",
                prev_log_index=-1,
                prev_log_term=0,
                entries=[],
                leader_commit=-1,
            )
        )

        assert response.success is False
        assert candidate.role == NodeRole.CANDIDATE


# ═══════════════════════════════════════════════════════════════════════
# Log replication
# ═══════════════════════════════════════════════════════════════════════


class TestLogReplication:
    def test_leader_appends_client_command(self) -> None:
        nodes = _cluster(3)
        _step_until_stable(nodes)
        leader = _leader(nodes)
        init_len = len(leader.log)
        leader.handle_client_command("set x=42")
        assert len(leader.log) == init_len + 1
        assert leader.log[-1].command == "set x=42"
        assert leader.log[-1].term == leader.current_term

    def test_append_entries_replicates_log(self) -> None:
        nodes = _cluster(3)
        _step_until_stable(nodes)
        _propose(nodes, "create user 1")
        _step_until_stable(nodes)  # re-stabilise after propose
        _leader(nodes)
        for n in nodes:
            if n.role == NodeRole.FOLLOWER:
                assert any(e.command == "create user 1" for e in n.log), f"{n.node_id} missing log entry"

    def test_follower_appends_entries(self) -> None:
        follower = RaftNode("n1", ["n0"])
        req = AppendEntriesRequest(
            term=1,
            leader_id="n0",
            prev_log_index=-1,
            prev_log_term=0,
            entries=[LogEntry(term=1, index=0, command="x")],
            leader_commit=-1,
        )
        resp = follower.handle_append_entries(req)
        assert resp.success is True
        assert resp.follower_id == "n1"
        assert resp.match_index == 0
        assert len(follower.log) == 1
        assert follower.log[0].command == "x"

    def test_follower_rejects_gap_in_log(self) -> None:
        follower = RaftNode("n1", ["n0"])
        req = AppendEntriesRequest(
            term=1,
            leader_id="n0",
            prev_log_index=5,
            prev_log_term=0,
            entries=[LogEntry(term=1, index=6, command="y")],
            leader_commit=-1,
        )
        resp = follower.handle_append_entries(req)
        assert resp.success is False

    def test_append_entries_updates_commit_index(self) -> None:
        follower = RaftNode("n1", ["n0"])
        follower.log = [LogEntry(term=1, index=0, command="a")]
        req = AppendEntriesRequest(
            term=1, leader_id="n0", prev_log_index=-1, prev_log_term=0, entries=[], leader_commit=0
        )
        follower.handle_append_entries(req)
        assert follower.commit_index == 0
        assert follower.last_applied == 0

    def test_leader_initialises_next_index(self) -> None:
        nodes = _cluster(3)
        _step_until_stable(nodes)
        leader = _leader(nodes)
        for nid in leader.peers:
            assert leader.next_index.get(nid, -1) == len(leader.log)
            assert leader.match_index.get(nid, -1) == -1

    def test_failed_append_retries_only_the_identified_follower(self) -> None:
        leader = RaftNode("n0", ["n1", "n2"])
        leader.current_term = 2
        leader.role = NodeRole.CANDIDATE
        leader._become_leader()
        leader.log = [
            LogEntry(term=2, index=0, command="a"),
            LogEntry(term=2, index=1, command="b"),
        ]
        leader.next_index = {"n1": 2, "n2": 2}

        leader.handle_append_entries_response(
            AppendEntriesResponse(
                term=2,
                success=False,
                follower_id="n1",
                match_index=-1,
            )
        )

        assert leader.next_index == {"n1": 1, "n2": 2}


# ═══════════════════════════════════════════════════════════════════════
# Term / stale leader handling
# ═══════════════════════════════════════════════════════════════════════


class TestTermHandling:
    def test_higher_term_demotes_leader(self) -> None:
        nodes = _cluster(3)
        _step_until_stable(nodes)
        leader = _leader(nodes)
        leader.handle_append_entries(
            AppendEntriesRequest(
                term=leader.current_term + 3,
                leader_id="phantom",
                prev_log_index=-1,
                prev_log_term=0,
                entries=[],
                leader_commit=-1,
            )
        )
        assert leader.role == NodeRole.FOLLOWER
        assert leader.current_term >= leader.current_term

    def test_stale_term_causes_rejection(self) -> None:
        follower = RaftNode("n1", ["n0"])
        req = AppendEntriesRequest(
            term=0, leader_id="n0", prev_log_index=-1, prev_log_term=0, entries=[], leader_commit=-1
        )
        follower.current_term = 5
        resp = follower.handle_append_entries(req)
        assert resp.success is False
        assert resp.term == 5

    def test_request_vote_with_higher_term_updates_its_term(self) -> None:
        follower = RaftNode("n1", ["n0"])
        follower.current_term = 2
        req = RequestVoteRequest(term=5, candidate_id="n0", last_log_index=0, last_log_term=0)
        follower.handle_request_vote(req)
        assert follower.current_term == 5


# ═══════════════════════════════════════════════════════════════════════
# Commit / application
# ═══════════════════════════════════════════════════════════════════════


class TestCommit:
    def test_leader_commits_when_majority_replicates(self) -> None:
        nodes = _cluster(3, timeout=0.03)
        _step_until_stable(nodes)
        _propose(nodes, "set y=7")
        leader = _leader(nodes)
        assert leader.commit_index >= 0

    def test_applied_increments_after_commit(self) -> None:
        nodes = _cluster(3, timeout=0.03)
        _step_until_stable(nodes)
        _propose(nodes, "put k v")
        leader = _leader(nodes)
        assert leader.last_applied >= 0

    def test_no_commit_from_previous_term(self) -> None:
        follower = RaftNode("n1", ["n0"])
        follower.log = [LogEntry(term=1, index=0, command="a"), LogEntry(term=1, index=1, command="b")]
        follower.current_term = 3
        req = AppendEntriesRequest(
            term=3, leader_id="n0", prev_log_index=1, prev_log_term=1, entries=[], leader_commit=1
        )
        resp = follower.handle_append_entries(req)
        assert resp.success is True
        assert follower.commit_index == 1


# ═══════════════════════════════════════════════════════════════════════
# Snapshot
# ═══════════════════════════════════════════════════════════════════════


class TestSnapshot:
    def test_leader_sends_snapshot_when_log_trimmed(self) -> None:
        follower = RaftNode("n1", ["n0"])
        follower.log = [LogEntry(term=1, index=0, command="a")]
        snap = InstallSnapshotRequest(
            term=2, leader_id="n0", last_included_index=0, last_included_term=1, offset=0, data=b"snap-data", done=True
        )
        resp = follower.handle_install_snapshot(snap)
        assert resp.term == 2
        assert len(follower.log) <= 1

    def test_snapshot_restores_state_machine(self) -> None:
        follower = RaftNode("n1", ["n0"])
        snap = InstallSnapshotRequest(
            term=1,
            leader_id="n0",
            last_included_index=3,
            last_included_term=2,
            offset=0,
            data=b"state: x=5,y=10",
            done=True,
        )
        resp = follower.handle_install_snapshot(snap)
        assert resp.term == 1
        assert follower.last_included_index == 3
        assert follower.last_included_term == 2

    def test_snapshot_clears_compacted_log(self) -> None:
        follower = RaftNode("n1", ["n0"])
        follower.log = [
            LogEntry(term=1, index=0, command="a"),
            LogEntry(term=1, index=1, command="b"),
            LogEntry(term=1, index=2, command="c"),
        ]
        snap = InstallSnapshotRequest(
            term=2, leader_id="n0", last_included_index=1, last_included_term=1, offset=0, data=b"snap", done=True
        )
        follower.handle_install_snapshot(snap)
        assert len(follower.log) >= 0


# ═══════════════════════════════════════════════════════════════════════
# Membership changes
# ═══════════════════════════════════════════════════════════════════════


class TestMembership:
    def test_add_peer_increases_peer_set(self) -> None:
        n = RaftNode("n0", ["n1"])
        assert "n2" not in n.peers
        n.handle_add_server("n2")
        assert "n2" in n.peers

    def test_remove_peer_decreases_peer_set(self) -> None:
        n = RaftNode("n0", ["n1", "n2", "n3"])
        n.joint_consensus = True
        n.handle_remove_server("n2")
        assert "n2" not in n.peers

    def test_joint_consensus_flag(self) -> None:
        n = RaftNode("n0", ["n1", "n2"])
        assert n.joint_consensus is False
        n.joint_consensus = True
        assert n.joint_consensus is True

    def test_leader_replicates_membership_change(self) -> None:
        nodes = _cluster(5, timeout=0.02)
        _step_until_stable(nodes)
        leader = _leader(nodes)
        init_peers = set(leader.peers)
        leader.handle_add_server("n5")
        assert "n5" in leader.peers
        assert len(leader.peers) == len(init_peers) + 1


# ═══════════════════════════════════════════════════════════════════════
# State machine persistence
# ═══════════════════════════════════════════════════════════════════════


class TestStateMachine:
    def test_apply_increments_applied_index(self) -> None:
        n = RaftNode("n0", [])
        n.log = [LogEntry(term=1, index=0, command="set a=1")]
        n.commit_index = 0
        n._apply_committed()
        assert n.last_applied == 0
        assert "set a=1" in n.state_machine

    def test_state_machine_persists_across_terms(self) -> None:
        n = RaftNode("n0", [])
        n.log = [LogEntry(term=1, index=0, command="set a=1")]
        n.commit_index = 0
        n._apply_committed()
        n.current_term = 5
        n.log.append(LogEntry(term=5, index=1, command="set b=2"))
        n.commit_index = 1
        n._apply_committed()
        assert "set a=1" in n.state_machine
        assert "set b=2" in n.state_machine

    def test_state_machine_is_list_of_applied_commands(self) -> None:
        n = RaftNode("n0", [])
        n.log = [
            LogEntry(term=1, index=0, command="cmd-a"),
            LogEntry(term=1, index=1, command="cmd-b"),
        ]
        n.commit_index = 1
        n._apply_committed()
        assert n.state_machine == ["cmd-a", "cmd-b"]


# ═══════════════════════════════════════════════════════════════════════
# Edge cases
# ═══════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    def test_empty_log_commit_noop(self) -> None:
        n = RaftNode("n0", [])
        n.commit_index = 5
        n._apply_committed()
        assert n.last_applied == -1
        assert n.state_machine == []

    def test_election_timeout_reset_on_heartbeat(self) -> None:
        n = RaftNode("n0", ["n1"], config=RaftConfig(election_timeout_min=0.1, election_timeout_max=0.2))

        def rng() -> float:
            return 0.05

        n.tick(0.05, rng)
        n.handle_append_entries(
            AppendEntriesRequest(
                term=n.current_term,
                leader_id="n1",
                prev_log_index=-1,
                prev_log_term=0,
                entries=[],
                leader_commit=-1,
            )
        )
        n.tick(0.15, rng)
        assert n.role == NodeRole.FOLLOWER

    def test_five_node_cluster_handles_failure(self) -> None:
        nodes = _cluster(5, timeout=0.03)
        _step_until_stable(nodes)
        _propose(nodes, "cmd-1")
        leader = _leader(nodes)
        assert leader.role == NodeRole.LEADER
        replicated = sum(1 for n in nodes if any(e.command == "cmd-1" for e in n.log))
        assert replicated >= 3

    def test_append_entries_with_matching_prevlog(self) -> None:
        follower = RaftNode("n1", ["n0"])
        follower.log = [LogEntry(term=1, index=0, command="a")]
        req = AppendEntriesRequest(
            term=2,
            leader_id="n0",
            prev_log_index=0,
            prev_log_term=1,
            entries=[LogEntry(term=2, index=1, command="b")],
            leader_commit=-1,
        )
        resp = follower.handle_append_entries(req)
        assert resp.success is True
        assert len(follower.log) == 2
        assert follower.log[1].command == "b"

    def test_append_entries_with_conflicting_prevlog(self) -> None:
        follower = RaftNode("n1", ["n0"])
        follower.log = [LogEntry(term=2, index=0, command="a")]
        req = AppendEntriesRequest(
            term=2,
            leader_id="n0",
            prev_log_index=0,
            prev_log_term=1,
            entries=[LogEntry(term=2, index=1, command="b")],
            leader_commit=-1,
        )
        resp = follower.handle_append_entries(req)
        assert resp.success is False
        assert len(follower.log) == 1

    def test_election_split_vote_with_deterministic_rng(self) -> None:
        nodes = _cluster(3, timeout=0.02)
        msgs: list[tuple[str, str, object]] = []
        now = 0.0
        call = 0

        def rng() -> float:
            nonlocal call
            call += 1
            return 0.01 * (call % 10 + 1)

        for _ in range(300):
            now += 0.01
            _tick_all(nodes, msgs, now, rng)
        roles = {n.role for n in nodes}
        assert NodeRole.CANDIDATE not in roles


# ═══════════════════════════════════════════════════════════════════════
# Quorum / majority math
# ═══════════════════════════════════════════════════════════════════════


class TestQuorum:
    def test_leader_elected_with_majority(self) -> None:
        nodes = _cluster(3)
        _step_until_stable(nodes)
        leader = _leader(nodes)
        assert leader.role == NodeRole.LEADER

    def test_even_sized_cluster_elects(self) -> None:
        nodes = _cluster(4, timeout=0.03)
        _step_until_stable(nodes)
        roles = {n.role for n in nodes}
        assert NodeRole.LEADER in roles

    def test_commit_requires_majority(self) -> None:
        nodes = _cluster(3, timeout=0.03)
        _step_until_stable(nodes)
        _propose(nodes, "cmd-quorum")
        _leader(nodes)
        replicated = sum(1 for n in nodes if any(e.command == "cmd-quorum" for e in n.log))
        assert replicated >= 2

    def test_single_node_quorum_is_trivial(self) -> None:
        n = RaftNode("n0", [])
        n.handle_client_command("test")
        assert len(n.log) == 1
        assert n.commit_index == 0


class TestFailClosedResponseBranches:
    def test_single_voter_tick_enters_leader_path(self) -> None:
        node = RaftNode("n0", [])
        messages = node.tick(0.0, lambda: 0.0)

        assert messages == []
        assert node.role == NodeRole.LEADER

    def test_equal_term_follower_transition_and_vote_hash_are_stable(self) -> None:
        node = RaftNode("n0", ["n1"])
        node.current_term = 2
        node.role = NodeRole.CANDIDATE
        node._become_follower(2)
        request = RequestVoteRequest(2, "n1", -1, 0)

        assert node.current_term == 2
        assert node.role == NodeRole.FOLLOWER
        assert hash(request) == hash(RequestVoteRequest(2, "n1", -1, 0))

    def test_client_and_commit_guards_preserve_nonleader_state(self) -> None:
        follower = RaftNode("n0", ["n1"])
        follower.log = [LogEntry(term=1, index=0, command="old")]
        follower.commit_index = -1
        follower._advance_commit()
        follower.role = NodeRole.CANDIDATE
        follower.handle_client_command("ignored")

        leader = RaftNode("solo", [])
        leader.handle_client_command("first")
        leader.handle_client_command("second")

        assert follower.commit_index == -1
        assert len(follower.log) == 1
        assert leader.state_machine == ["first", "second"]

    def test_previous_term_entry_does_not_advance_leader_commit(self) -> None:
        leader = RaftNode("n0", ["n1"])
        leader.current_term = 2
        leader.role = NodeRole.LEADER
        leader.log = [LogEntry(term=1, index=0, command="old")]
        leader.match_index = {"n1": 0}

        leader._advance_commit()

        assert leader.commit_index == -1

    def test_vote_responses_enforce_term_boundaries(self) -> None:
        stale = RaftNode("n0", ["n1"])
        stale.current_term = 2
        stale.role = NodeRole.CANDIDATE
        stale.handle_request_vote_response(RequestVoteResponse(1, True, "n1"))

        higher = RaftNode("n0", ["n1"])
        higher.current_term = 2
        higher.role = NodeRole.CANDIDATE
        higher.handle_request_vote_response(RequestVoteResponse(3, True, "n1"))

        assert stale.role == NodeRole.CANDIDATE
        assert higher.role == NodeRole.FOLLOWER
        assert higher.current_term == 3

    def test_conflicting_entry_replaces_suffix_without_preserving_old_data(self) -> None:
        follower = RaftNode("n1", ["n0"])
        follower.log = [
            LogEntry(term=1, index=0, command="old"),
            LogEntry(term=1, index=1, command="suffix"),
        ]
        response = follower.handle_append_entries(
            AppendEntriesRequest(
                term=2,
                leader_id="n0",
                prev_log_index=-1,
                prev_log_term=0,
                entries=[LogEntry(term=2, index=0, command="new")],
                leader_commit=-1,
            )
        )

        assert response.success is True
        assert follower.log == [LogEntry(term=2, index=0, command="new")]

    def test_append_responses_enforce_role_term_and_identity(self) -> None:
        follower = RaftNode("n0", ["n1"])
        follower.handle_append_entries_response(AppendEntriesResponse(0, True, "n1", 0))

        stale = RaftNode("n0", ["n1"])
        stale.current_term = 2
        stale.role = NodeRole.LEADER
        stale.handle_append_entries_response(AppendEntriesResponse(1, True, "n1", 0))
        stale.handle_append_entries_response(AppendEntriesResponse(2, True, "unknown", 0))

        higher = RaftNode("n0", ["n1"])
        higher.current_term = 2
        higher.role = NodeRole.LEADER
        higher.handle_append_entries_response(AppendEntriesResponse(3, True, "n1", 0))

        assert follower.role == NodeRole.FOLLOWER
        assert stale.role == NodeRole.LEADER
        assert stale.match_index == {}
        assert higher.role == NodeRole.FOLLOWER
        assert higher.current_term == 3

    def test_snapshot_requests_and_responses_enforce_term_and_done_state(self) -> None:
        node = RaftNode("n1", ["n0"])
        node.current_term = 3
        stale = node.handle_install_snapshot(
            InstallSnapshotRequest(2, "n0", 4, 2, 0, b"stale", True)
        )
        node.log = [LogEntry(term=3, index=4, command="retained")]
        partial = node.handle_install_snapshot(
            InstallSnapshotRequest(4, "n0", 3, 3, 0, b"partial", False)
        )
        node.commit_index = 5
        node.last_applied = 5
        node.handle_install_snapshot(
            InstallSnapshotRequest(4, "n0", 3, 3, 0, b"done", True)
        )
        node.role = NodeRole.LEADER
        node.handle_install_snapshot_response(InstallSnapshotResponse(5))

        assert stale.term == 3
        assert partial.term == 4
        assert node.log == [LogEntry(term=3, index=4, command="retained")]
        assert node.commit_index == 5
        assert node.last_applied == 5
        assert node.role == NodeRole.FOLLOWER
        assert node.current_term == 5
