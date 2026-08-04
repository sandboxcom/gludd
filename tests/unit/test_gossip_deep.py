"""Deep gossip protocol tests — 15+ tests covering:
- Push-pull round exchange
- Digest diff: push-ahead, pull-behind, missing keys
- Rumor creation, fanout spread, TTL expiry
- Membership lifecycle: alive → suspected → failed → dead → recovered
- Failure detection timeouts
- Multi-node convergence
- Concurrent rumors
- Edge cases: empty digest, zero peers, duplicate handling
"""

from __future__ import annotations

from general_ludd.distributed.gossip import (
    GossipMessage,
    GossipProtocol,
    MemberStatus,
    Rumor,
    ensure_convergence,
    run_gossip_round,
    spread_rumor,
)
from general_ludd.distributed.gossip import (
    Member as _MemberType,
)

# ═══════════════════════════════════════════════════════════════════════════
# Construction
# ═══════════════════════════════════════════════════════════════════════════


class TestConstruction:
    def test_default_construction(self):
        g = GossipProtocol("n1", "10.0.0.1:7000")
        assert g.node_id == "n1"
        assert g.address == "10.0.0.1:7000"
        assert g.round == 0
        assert g.alive_count == 1
        assert g.store_size() == 0

    def test_custom_timeouts(self):
        g = GossipProtocol(
            "n1",
            "10.0.0.1:7000",
            suspect_timeout=5.0,
            fail_timeout=15.0,
            dead_timeout=60.0,
        )
        assert g.suspect_timeout == 5.0
        assert g.fail_timeout == 15.0
        assert g.dead_timeout == 60.0

    def test_seeded_determinism(self):
        g1 = GossipProtocol("n1", "a", seed=42)
        g1.add_peer("p1", "addr1")
        g1.add_peer("p2", "addr2")
        g1.add_peer("p3", "addr3")
        peers1 = [g1.select_peer() for _ in range(10)]

        g2 = GossipProtocol("n1", "a", seed=42)
        g2.add_peer("p1", "addr1")
        g2.add_peer("p2", "addr2")
        g2.add_peer("p3", "addr3")
        peers2 = [g2.select_peer() for _ in range(10)]

        assert peers1 == peers2


# ═══════════════════════════════════════════════════════════════════════════
# Data access
# ═══════════════════════════════════════════════════════════════════════════


class TestDataAccess:
    def test_put_and_get(self):
        g = GossipProtocol("n1", "a")
        v1 = g.put("x", 10)
        v2 = g.put("x", 20)
        assert v1 == 1
        assert v2 == 2
        assert g.get("x") == 20
        assert g.get("missing") is None

    def test_store_size(self):
        g = GossipProtocol("n1", "a")
        g.put("a", 1)
        g.put("b", 2)
        g.put("c", 3)
        assert g.store_size() == 3

    def test_list_keys(self):
        g = GossipProtocol("n1", "a")
        g.put("a", 1)
        g.put("b", 2)
        assert sorted(g.list_keys()) == ["a", "b"]


# ═══════════════════════════════════════════════════════════════════════════
# Peer management
# ═══════════════════════════════════════════════════════════════════════════


class TestPeerManagement:
    def test_add_peer(self):
        g = GossipProtocol("n1", "addr1")
        g.add_peer("n2", "addr2")
        assert "n2" in g.members
        assert g.members["n2"].address == "addr2"
        assert g.members["n2"].status == MemberStatus.ALIVE

    def test_set_peers(self):
        g = GossipProtocol("n1", "addr1")
        g.set_peers(["n2", "n3", "n4"])
        assert g.select_peer() is not None

    def test_remove_peer(self):
        g = GossipProtocol("n1", "addr1")
        g.add_peer("n2", "addr2")
        g.remove_peer("n2")
        assert g.select_peer() is None

    def test_select_peer_none_when_empty(self):
        g = GossipProtocol("n1", "a")
        assert g.select_peer() is None

    def test_select_peer_respects_exclude(self):
        g = GossipProtocol("n1", "a", seed=1)
        g.add_peer("n2", "addr2")
        g.add_peer("n3", "addr3")
        result = g.select_peer(exclude={"n2"})
        assert result == "n3"


# ═══════════════════════════════════════════════════════════════════════════
# Digest round
# ═══════════════════════════════════════════════════════════════════════════


class TestDigestRound:
    def test_create_digest_bumps_round(self):
        g = GossipProtocol("n1", "a")
        assert g.round == 0
        g.create_digest()
        assert g.round == 1
        g.create_digest()
        assert g.round == 2

    def test_create_digest_bumps_heartbeat(self):
        g = GossipProtocol("n1", "a")
        hb_before = g.members["n1"].heartbeat
        g.create_digest()
        assert g.members["n1"].heartbeat == hb_before + 1

    def test_empty_digest(self):
        g = GossipProtocol("n1", "a")
        msg = g.create_digest()
        assert msg.msg_type == "digest"
        assert msg.sender_id == "n1"
        assert msg.digest == []

    def test_digest_includes_stored_keys(self):
        g = GossipProtocol("n1", "a")
        g.put("x", 10)
        g.put("y", 20)
        msg = g.create_digest()
        assert len(msg.digest) == 2
        keys = {d.key for d in msg.digest}
        assert keys == {"x", "y"}


# ═══════════════════════════════════════════════════════════════════════════
# Push-pull exchange
# ═══════════════════════════════════════════════════════════════════════════


class TestPushPullExchange:
    def test_push_ahead_keys(self):
        g1 = GossipProtocol("n1", "a")
        g2 = GossipProtocol("n2", "b")
        g1.add_peer("n2", "b")
        g2.add_peer("n1", "a")

        g1.put("x", 10)
        run_gossip_round({g1.node_id: g1, g2.node_id: g2}, "n1")

        assert g2.get("x") == 10

    def test_pull_behind_keys(self):
        g1 = GossipProtocol("n1", "a")
        g2 = GossipProtocol("n2", "b")
        g1.add_peer("n2", "b")
        g2.add_peer("n1", "a")

        g2.put("y", 99)
        run_gossip_round({g1.node_id: g1, g2.node_id: g2}, "n1")

        assert g1.get("y") == 99

    def test_bidirectional_sync(self):
        g1 = GossipProtocol("n1", "a")
        g2 = GossipProtocol("n2", "b")
        g1.add_peer("n2", "b")
        g2.add_peer("n1", "a")

        g1.put("a", 1)
        g2.put("b", 2)

        run_gossip_round({g1.node_id: g1, g2.node_id: g2}, "n1")

        assert g1.get("b") == 2
        assert g2.get("a") == 1

    def test_newer_version_wins(self):
        g1 = GossipProtocol("n1", "a")
        g2 = GossipProtocol("n2", "b")
        g1.add_peer("n2", "b")
        g2.add_peer("n1", "a")

        g1.put("k", 100)
        g2.put("k", 200)
        g2.put("k", 300)

        run_gossip_round({g1.node_id: g1, g2.node_id: g2}, "n1")

        assert g1.get("k") == 300
        assert g2.get("k") == 300

    def test_missing_key_pull(self):
        g1 = GossipProtocol("n1", "a")
        g2 = GossipProtocol("n2", "b")
        g1.add_peer("n2", "b")
        g2.add_peer("n1", "a")

        g2.put("secret", "xyz")

        run_gossip_round({g1.node_id: g1, g2.node_id: g2}, "n1")

        assert g1.get("secret") == "xyz"


# ═══════════════════════════════════════════════════════════════════════════
# Rumor-mongering
# ═══════════════════════════════════════════════════════════════════════════


class TestRumorMongering:
    def test_create_rumor(self):
        g = GossipProtocol("n1", "a", rumor_ttl=8)
        rumor = g.put_rumor("event", {"type": "join"})
        assert rumor.key == "event"
        assert rumor.value == {"type": "join"}
        assert rumor.version == 1
        assert rumor.origin == "n1"
        assert rumor.ttl == 8

    def test_rumor_spreads_to_peers(self):
        g1 = GossipProtocol("n1", "a", rumor_fanout=2, seed=1)
        g2 = GossipProtocol("n2", "b")
        g3 = GossipProtocol("n3", "c")
        g1.set_peers(["n2", "n3"])

        rumor = g1.put_rumor("alert", "fire")
        nodes = {"n1": g1, "n2": g2, "n3": g3}

        count = spread_rumor(nodes, rumor, "n1")
        assert count >= 1
        assert g2.get("alert") == "fire" or g3.get("alert") == "fire"

    def test_rumor_duplicate_rejected(self):
        g = GossipProtocol("n1", "a")
        rumor = Rumor(key="k", value="v1", version=1, origin="n2", ttl=5)
        assert g._apply_rumor_if_fresh(rumor) is True
        assert g._apply_rumor_if_fresh(rumor) is False

    def test_rumor_lower_version_rejected(self):
        g = GossipProtocol("n1", "a")
        r1 = Rumor(key="k", value="v2", version=2, origin="n2", ttl=5)
        r2 = Rumor(key="k", value="v1", version=1, origin="n2", ttl=5)
        g._apply_rumor_if_fresh(r1)
        assert g._apply_rumor_if_fresh(r2) is False

    def test_custom_ttl(self):
        g = GossipProtocol("n1", "a")
        rumor = g.put_rumor("k", "v", ttl=2)
        assert rumor.ttl == 2


# ═══════════════════════════════════════════════════════════════════════════
# Membership lifecycle
# ═══════════════════════════════════════════════════════════════════════════


class TestMembershipLifecycle:
    def test_member_merging_adds_new(self):
        g = GossipProtocol("n1", "a")
        remote = [MemberStub("n2", "addr2", MemberStatus.ALIVE, 5, 100.0)]
        g._merge_remote_members(remote)
        assert "n2" in g.members
        assert g.members["n2"].heartbeat == 5

    def test_member_merging_updates_existing(self):
        g = GossipProtocol("n1", "a")
        g.add_peer("n2", "addr2")
        remote = [MemberStub("n2", "addr2-new", MemberStatus.ALIVE, 10, 200.0)]
        g._merge_remote_members(remote)
        assert g.members["n2"].heartbeat == 10
        assert g.members["n2"].address == "addr2-new"

    def test_suspected_node_recovers_on_heartbeat(self):
        g = GossipProtocol("n1", "a")
        g.add_peer("n2", "b")
        g.members["n2"].status = MemberStatus.SUSPECTED
        remote = [MemberStub("n2", "b", MemberStatus.ALIVE, 5, 500.0)]
        g._merge_remote_members(remote)
        assert g.members["n2"].status == MemberStatus.ALIVE

    def test_mark_recovered(self):
        g = GossipProtocol("n1", "a")
        g.add_peer("n2", "b")
        g.members["n2"].status = MemberStatus.SUSPECTED
        assert g.mark_recovered("n2") is True
        assert g.members["n2"].status == MemberStatus.ALIVE

    def test_mark_recovered_already_alive(self):
        g = GossipProtocol("n1", "a")
        g.add_peer("n2", "b")
        assert g.mark_recovered("n2") is False

    def test_mark_recovered_unknown_node(self):
        g = GossipProtocol("n1", "a")
        assert g.mark_recovered("n99") is False


# ═══════════════════════════════════════════════════════════════════════════
# Failure detection
# ═══════════════════════════════════════════════════════════════════════════


class TestFailureDetection:
    def test_suspect_after_timeout(self):
        g = GossipProtocol("n1", "a", suspect_timeout=1.0, fail_timeout=5.0)
        g.add_peer("n2", "b")
        g.members["n2"].last_seen = 0.0
        changes = g.detect_failures(now=2.0)
        assert len(changes) == 1
        assert changes[0].status == MemberStatus.SUSPECTED
        assert g.members["n2"].status == MemberStatus.SUSPECTED

    def test_failed_after_longer_timeout(self):
        g = GossipProtocol("n1", "a", suspect_timeout=1.0, fail_timeout=5.0)
        g.add_peer("n2", "b")
        g.members["n2"].last_seen = 0.0
        g.detect_failures(now=2.0)
        assert g.members["n2"].status == MemberStatus.SUSPECTED
        changes = g.detect_failures(now=6.0)
        assert changes[0].status == MemberStatus.FAILED
        assert g.members["n2"].status == MemberStatus.FAILED

    def test_dead_after_dead_timeout(self):
        g = GossipProtocol("n1", "a", suspect_timeout=1.0, fail_timeout=5.0, dead_timeout=10.0)
        g.add_peer("n2", "b")
        g.members["n2"].last_seen = 0.0
        g.members["n2"].status = MemberStatus.FAILED
        changes = g.detect_failures(now=20.0)
        assert changes[0].status == MemberStatus.DEAD

    def test_self_is_never_suspected(self):
        g = GossipProtocol("n1", "a", suspect_timeout=1.0)
        g.members["n1"].last_seen = 0.0
        changes = g.detect_failures(now=10.0)
        assert len(changes) == 0

    def test_counts_reflect_status(self):
        g = GossipProtocol("n1", "a", suspect_timeout=1.0, fail_timeout=3.0)
        g.add_peer("n2", "b")
        g.add_peer("n3", "c")
        g.members["n2"].last_seen = 0.0
        g.members["n3"].last_seen = 0.0
        g.detect_failures(now=2.0)
        assert g.suspected_count == 2
        assert g.failed_count == 0
        g.detect_failures(now=4.0)
        assert g.suspected_count == 0
        assert g.failed_count == 2


# ═══════════════════════════════════════════════════════════════════════════
# Convergence
# ═══════════════════════════════════════════════════════════════════════════


class TestConvergence:
    def test_two_nodes_converge(self):
        g1 = GossipProtocol("n1", "a")
        g2 = GossipProtocol("n2", "b")
        g1.set_peers(["n2"])
        g2.set_peers(["n1"])
        g1.put("a", 1)
        g2.put("b", 2)

        nodes = {"n1": g1, "n2": g2}
        converged = ensure_convergence(nodes, rounds=20)
        assert converged
        assert g1.get("b") == 2
        assert g2.get("a") == 1

    def test_three_nodes_converge(self):
        nodes: dict[str, GossipProtocol] = {}
        for i in range(3):
            nid = f"n{i}"
            nodes[nid] = GossipProtocol(nid, f"addr{i}")
        all_ids = list(nodes.keys())
        for nid in nodes:
            peers = [p for p in all_ids if p != nid]
            nodes[nid].set_peers(peers)

        nodes["n0"].put("k0", "v0")
        nodes["n1"].put("k1", "v1")
        nodes["n2"].put("k2", "v2")

        converged = ensure_convergence(nodes, rounds=50)
        assert converged
        for nid in nodes:
            assert nodes[nid].get("k0") == "v0"
            assert nodes[nid].get("k1") == "v1"
            assert nodes[nid].get("k2") == "v2"

    def test_convergence_with_rumors(self):
        g1 = GossipProtocol("n1", "a", rumor_fanout=3, seed=1)
        g2 = GossipProtocol("n2", "b")
        g3 = GossipProtocol("n3", "c")
        g1.set_peers(["n2", "n3"])
        g2.set_peers(["n1", "n3"])
        g3.set_peers(["n1", "n2"])

        g1.put_rumor("config", {"db": "postgres"})
        nodes = {"n1": g1, "n2": g2, "n3": g3}

        converged = ensure_convergence(nodes, rounds=30)
        assert converged
        assert g2.get("config") == {"db": "postgres"}
        assert g3.get("config") == {"db": "postgres"}


# ═══════════════════════════════════════════════════════════════════════════
# Edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    def test_round_with_no_peers(self):
        g = GossipProtocol("n1", "a")
        msg_count = run_gossip_round({"n1": g}, "n1")
        assert msg_count == 0

    def test_handle_unknown_message_type(self):
        g = GossipProtocol("n1", "a")
        msg = GossipMessage(msg_type="bogus", sender_id="x", round=0)
        result = g.handle_message(msg)
        assert result is None

    def test_peer_not_in_nodes_list(self):
        g1 = GossipProtocol("n1", "a")
        g1.add_peer("ghost", "g")
        msg_count = run_gossip_round({"n1": g1}, "n1")
        assert msg_count == 0

    def test_digest_size_capped(self):
        g = GossipProtocol("n1", "a", max_digest_size=3)
        for i in range(10):
            g.put(f"k{i}", i)
        msg = g.create_digest()
        assert len(msg.digest) == 3

    def test_members_per_message_capped(self):
        g = GossipProtocol("n1", "a", max_members_per_message=2)
        for i in range(5):
            g.add_peer(f"p{i}", f"addr{i}")
        shared = g._pick_members_to_share()
        assert len(shared) <= 2

    def test_handle_push_with_no_pull_keys(self):
        g = GossipProtocol("n1", "a")
        msg = GossipMessage(
            msg_type="push",
            sender_id="n2",
            round=1,
            rumors=[],
            pull_keys=[],
        )
        result = g.handle_push(msg)
        assert result is None

    def test_multiple_rounds_bidirectional(self):
        g1 = GossipProtocol("n1", "a", seed=5)
        g2 = GossipProtocol("n2", "b", seed=5)
        g1.set_peers(["n2"])
        g2.set_peers(["n1"])

        g1.put("a", 1)
        g2.put("b", 2)

        nodes = {"n1": g1, "n2": g2}
        for _ in range(10):
            run_gossip_round(nodes, "n1")
            run_gossip_round(nodes, "n2")

        assert g1.data_matches(g2)

    def test_all_data_returns_copy(self):
        g = GossipProtocol("n1", "a")
        g.put("k", "v")
        d = g.all_data()
        d["k"] = ("modified", 99)
        assert g.get("k") == "v"


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


def MemberStub(
    node_id: str,
    address: str,
    status: MemberStatus,
    heartbeat: int,
    last_seen: float,
) -> _MemberType:

    return _MemberType(
        node_id=node_id,
        address=address,
        status=status,
        heartbeat=heartbeat,
        last_seen=last_seen,
    )
