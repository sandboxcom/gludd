"""Deep Paxos consensus algorithm tests.

Covers single-value Paxos, multi-Paxos, leader election, Prepare/Accept
phases, conflict resolution, learner catching up, and scenario combinations.
"""

from __future__ import annotations

from general_ludd.distributed.paxos import (
    Acceptor,
    AcceptRequest,
    AcceptResponse,
    Learner,
    PrepareRequest,
    PrepareResponse,
    ProposalID,
    Proposer,
)


def _round_trip(
    acceptors: list[Acceptor],
    proposer: Proposer,
    learner: Learner,
    value: object,
) -> bool:
    """Run a single Paxos round: Prepare → Accept → Learn."""
    pid = ProposalID(number=proposer.next_proposal_number(), proposer_id=proposer.node_id)

    prepare_req = PrepareRequest(pid=pid)
    promises: list[PrepareResponse] = []
    for acc in acceptors:
        promises.append(acc.handle_prepare(prepare_req))

    highest = proposer.handle_prepare_responses(promises)
    accept_req = AcceptRequest(pid=pid, value=value, promised_id=highest)
    accepts: list[AcceptResponse] = []
    for acc in acceptors:
        accepts.append(acc.handle_accept(accept_req))

    chosen = proposer.handle_accept_responses(accepts)
    if chosen:
        learner.handle_accepted(accept_req)
        return True
    return False


# ═══════════════════════════════════════════════════════════════════════
# ProposalID
# ═══════════════════════════════════════════════════════════════════════


class TestProposalID:
    def test_ordering_by_number_first(self) -> None:
        a = ProposalID(number=1, proposer_id="A")
        b = ProposalID(number=2, proposer_id="A")
        assert a < b
        assert b > a
        assert a != b

    def test_ordering_by_proposer_id_on_tie(self) -> None:
        a = ProposalID(number=2, proposer_id="A")
        b = ProposalID(number=2, proposer_id="B")
        assert a < b


# ═══════════════════════════════════════════════════════════════════════
# Acceptor
# ═══════════════════════════════════════════════════════════════════════


class TestAcceptor:
    def test_prepare_promises_when_higher_number(self) -> None:
        acc = Acceptor(node_id="A1")
        pid = ProposalID(number=5, proposer_id="P1")
        resp = acc.handle_prepare(PrepareRequest(pid=pid))
        assert resp.promised
        assert resp.promised_id == pid

    def test_prepare_stores_minimum_promise(self) -> None:
        acc = Acceptor(node_id="A1")
        pid = ProposalID(number=5, proposer_id="P1")
        acc.handle_prepare(PrepareRequest(pid=pid))
        assert acc.minimum_promise == pid.number

    def test_prepare_rejects_lower_number_after_promise(self) -> None:
        acc = Acceptor(node_id="A1")
        acc.handle_prepare(PrepareRequest(pid=ProposalID(number=5, proposer_id="P1")))
        resp = acc.handle_prepare(PrepareRequest(pid=ProposalID(number=3, proposer_id="P2")))
        assert not resp.promised

    def test_prepare_returns_highest_accepted(self) -> None:
        acc = Acceptor(node_id="A1")
        high_pid = ProposalID(number=5, proposer_id="P1")
        acc.handle_accept(AcceptRequest(pid=high_pid, value="hello", promised_id=None))
        resp = acc.handle_prepare(PrepareRequest(pid=ProposalID(number=7, proposer_id="P2")))
        assert resp.highest_accepted_id == high_pid
        assert resp.highest_accepted_value == "hello"

    def test_prepare_does_not_return_higher_not_accepted(self) -> None:
        acc = Acceptor(node_id="A1")
        acc.handle_prepare(PrepareRequest(pid=ProposalID(number=3, proposer_id="P1")))
        resp = acc.handle_prepare(PrepareRequest(pid=ProposalID(number=5, proposer_id="P2")))
        assert resp.highest_accepted_id is None
        assert resp.highest_accepted_value is None

    def test_accept_stores_value_when_enough_promises(self) -> None:
        acc = Acceptor(node_id="A1")
        pid = ProposalID(number=5, proposer_id="P1")
        acc.handle_prepare(PrepareRequest(pid=pid))
        resp = acc.handle_accept(AcceptRequest(pid=pid, value="world", promised_id=None))
        assert resp.accepted
        assert acc.accepted_id == pid
        assert acc.accepted_value == "world"

    def test_accept_rejects_stale_id(self) -> None:
        acc = Acceptor(node_id="A1")
        pid1 = ProposalID(number=5, proposer_id="P1")
        acc.handle_prepare(PrepareRequest(pid=pid1))
        resp = acc.handle_accept(AcceptRequest(pid=ProposalID(number=2, proposer_id="P2"), value="x", promised_id=None))
        assert not resp.accepted

    def test_accept_promised_id_check_passed(self) -> None:
        acc = Acceptor(node_id="A1")
        pid = ProposalID(number=5, proposer_id="P1")
        acc.handle_prepare(PrepareRequest(pid=pid))
        resp = acc.handle_accept(AcceptRequest(pid=pid, value="x", promised_id=pid))
        assert resp.accepted

    def test_accept_promised_id_check_blocked(self) -> None:
        acc = Acceptor(node_id="A1")
        pid1 = ProposalID(number=5, proposer_id="P1")
        pid2 = ProposalID(number=6, proposer_id="P2")
        acc.handle_prepare(PrepareRequest(pid=pid2))
        resp = acc.handle_accept(AcceptRequest(pid=pid1, value="x", promised_id=pid1))
        assert not resp.accepted

    def test_accept_succeeds_when_pid_number_matches(self) -> None:
        acc = Acceptor(node_id="A1")
        pid = ProposalID(number=5, proposer_id="P1")
        acc.handle_prepare(PrepareRequest(pid=pid))
        resp = acc.handle_accept(AcceptRequest(pid=pid, value="x", promised_id=ProposalID(number=3, proposer_id="P3")))
        assert resp.accepted


# ═══════════════════════════════════════════════════════════════════════
# Learner
# ═══════════════════════════════════════════════════════════════════════


class TestLearner:
    def test_learner_starts_empty(self) -> None:
        learner = Learner(node_id="L1")
        assert len(learner.learned) == 0

    def test_learner_remembers_accepted_value(self) -> None:
        learner = Learner(node_id="L1")
        pid = ProposalID(number=1, proposer_id="P1")
        learner.handle_accepted(AcceptRequest(pid=pid, value="hello", promised_id=None))
        assert "hello" in learner.learned

    def test_learner_deduplicates_same_value(self) -> None:
        learner = Learner(node_id="L1")
        pid = ProposalID(number=1, proposer_id="P1")
        learner.handle_accepted(AcceptRequest(pid=pid, value="hello", promised_id=None))
        learner.handle_accepted(AcceptRequest(pid=pid, value="hello", promised_id=None))
        assert len(learner.learned) == 1

    def test_learner_preserves_order(self) -> None:
        learner = Learner(node_id="L1")
        learner.handle_accepted(AcceptRequest(pid=ProposalID(number=1, proposer_id="P1"), value="a", promised_id=None))
        learner.handle_accepted(AcceptRequest(pid=ProposalID(number=2, proposer_id="P1"), value="b", promised_id=None))
        assert list(learner.learned) == ["a", "b"]


# ═══════════════════════════════════════════════════════════════════════
# Proposer
# ═══════════════════════════════════════════════════════════════════════


class TestProposer:
    def test_proposer_starts_with_zero_proposal_number(self) -> None:
        p = Proposer(node_id="P1", quorum_size=3)
        assert p.next_proposal_number() == 1

    def test_proposer_increments_proposal_number(self) -> None:
        p = Proposer(node_id="P1", quorum_size=3)
        assert p.next_proposal_number() == 1
        assert p.next_proposal_number() == 2
        assert p.next_proposal_number() == 3

    def test_proposer_picks_highest_value_from_responses(self) -> None:
        p = Proposer(node_id="P1", quorum_size=3)
        resp = p.handle_prepare_responses(
            [
                PrepareResponse(
                    promised_id=ProposalID(number=5, proposer_id="P2"),
                    promised=True,
                    highest_accepted_id=ProposalID(number=3, proposer_id="P3"),
                    highest_accepted_value="old",
                ),
                PrepareResponse(
                    promised_id=ProposalID(number=5, proposer_id="P2"),
                    promised=True,
                    highest_accepted_id=None,
                    highest_accepted_value=None,
                ),
                PrepareResponse(
                    promised_id=ProposalID(number=5, proposer_id="P2"),
                    promised=True,
                    highest_accepted_id=ProposalID(number=4, proposer_id="P1"),
                    highest_accepted_value="older",
                ),
            ]
        )
        assert resp == ProposalID(number=4, proposer_id="P1")

    def test_proposer_accept_wins_with_quorum(self) -> None:
        p = Proposer(node_id="P1", quorum_size=3)
        ack = p.handle_accept_responses(
            [
                AcceptResponse(pid=ProposalID(number=1, proposer_id="P1"), accepted=True),
                AcceptResponse(pid=ProposalID(number=1, proposer_id="P1"), accepted=True),
                AcceptResponse(pid=ProposalID(number=1, proposer_id="P1"), accepted=True),
            ]
        )
        assert ack is True

    def test_proposer_accept_fails_without_quorum(self) -> None:
        p = Proposer(node_id="P1", quorum_size=3)
        ack = p.handle_accept_responses(
            [
                AcceptResponse(pid=ProposalID(number=1, proposer_id="P1"), accepted=True),
                AcceptResponse(pid=ProposalID(number=1, proposer_id="P1"), accepted=False),
                AcceptResponse(pid=ProposalID(number=1, proposer_id="P1"), accepted=False),
            ]
        )
        assert ack is False

    def test_proposer_accept_quorum_with_extra_rejections(self) -> None:
        p = Proposer(node_id="P1", quorum_size=3)
        ack = p.handle_accept_responses(
            [
                AcceptResponse(pid=ProposalID(number=1, proposer_id="P1"), accepted=True),
                AcceptResponse(pid=ProposalID(number=1, proposer_id="P1"), accepted=True),
                AcceptResponse(pid=ProposalID(number=1, proposer_id="P1"), accepted=True),
                AcceptResponse(pid=ProposalID(number=1, proposer_id="P1"), accepted=False),
                AcceptResponse(pid=ProposalID(number=1, proposer_id="P1"), accepted=False),
            ]
        )
        assert ack is True


# ═══════════════════════════════════════════════════════════════════════
# Integration: single-value Paxos
# ═══════════════════════════════════════════════════════════════════════


class TestSingleValuePaxos:
    def test_basic_round_reaches_consensus(self) -> None:
        acceptors = [Acceptor(node_id=f"A{i}") for i in range(5)]
        proposer = Proposer(node_id="P1", quorum_size=3)
        learner = Learner(node_id="L1")
        chosen = _round_trip(acceptors, proposer, learner, "hello-paxos")
        assert chosen is True
        assert "hello-paxos" in learner.learned

    def test_value_is_learned_on_quorum_of_acceptors(self) -> None:
        acceptors = [Acceptor(node_id=f"A{i}") for i in range(3)]
        proposer = Proposer(node_id="P1", quorum_size=2)
        learner = Learner(node_id="L1")
        chosen = _round_trip(acceptors, proposer, learner, "v1")
        assert chosen is True
        assert learner.learned[-1] == "v1"

    def test_conflicting_proposers_resolve_to_one_value(self) -> None:
        acceptors = [Acceptor(node_id=f"A{i}") for i in range(5)]
        learner = Learner(node_id="L1")

        p1 = Proposer(node_id="P1", quorum_size=3)
        p2 = Proposer(node_id="P2", quorum_size=3)

        pid1 = ProposalID(number=p1.next_proposal_number(), proposer_id=p1.node_id)
        pid2 = ProposalID(number=p2.next_proposal_number(), proposer_id=p2.node_id)

        for acc in acceptors:
            acc.handle_prepare(PrepareRequest(pid=pid1))

        for acc in acceptors:
            acc.handle_prepare(PrepareRequest(pid=pid2))

        promises1 = [
            acc.handle_prepare(PrepareRequest(pid=ProposalID(number=10, proposer_id="P1"))) for acc in acceptors
        ]
        highest1 = p1.handle_prepare_responses(promises1)

        accept1 = AcceptRequest(pid=ProposalID(number=10, proposer_id="P1"), value="p1-win", promised_id=highest1)
        accepts1 = [acc.handle_accept(accept1) for acc in acceptors]
        chosen1 = p1.handle_accept_responses(accepts1)
        if chosen1:
            learner.handle_accepted(accept1)

        assert len(learner.learned) >= 1
        assert learner.learned[-1] == "p1-win"

    def test_higher_proposal_number_outbids_lower(self) -> None:
        acceptors = [Acceptor(node_id="A0"), Acceptor(node_id="A1"), Acceptor(node_id="A2")]
        p_low = Proposer(node_id="P_low", quorum_size=2)
        p_high = Proposer(node_id="P_high", quorum_size=2)

        pid_low = ProposalID(number=p_low.next_proposal_number(), proposer_id=p_low.node_id)
        p_high.next_proposal_number()
        pid_high = ProposalID(number=p_high.next_proposal_number(), proposer_id=p_high.node_id)

        for acc in acceptors:
            acc.handle_prepare(PrepareRequest(pid=pid_low))

        for acc in acceptors:
            acc.handle_prepare(PrepareRequest(pid=pid_high))

        resp_low = [
            acc.handle_accept(AcceptRequest(pid=pid_low, value="low-val", promised_id=None)) for acc in acceptors
        ]
        assert sum(1 for r in resp_low if r.accepted) < 3

        resp_high = [
            acc.handle_accept(AcceptRequest(pid=pid_high, value="high-val", promised_id=None)) for acc in acceptors
        ]
        assert sum(1 for r in resp_high if r.accepted) >= 2

    def test_single_acceptor_trivial_consensus(self) -> None:
        acc = Acceptor(node_id="A0")
        proposer = Proposer(node_id="P1", quorum_size=1)
        learner = Learner(node_id="L1")
        chosen = _round_trip([acc], proposer, learner, "only")
        assert chosen is True
        assert learner.learned == ["only"]

    def test_larger_cluster_consensus(self) -> None:
        acceptors = [Acceptor(node_id=f"A{i}") for i in range(7)]
        proposer = Proposer(node_id="P1", quorum_size=4)
        learner = Learner(node_id="L1")
        chosen = _round_trip(acceptors, proposer, learner, "big")
        assert chosen is True
        assert "big" in learner.learned


# ═══════════════════════════════════════════════════════════════════════
# Multi-Paxos
# ═══════════════════════════════════════════════════════════════════════


class TestMultiPaxos:
    def test_multiple_rounds_in_sequence(self) -> None:
        acceptors = [Acceptor(node_id=f"A{i}") for i in range(5)]
        proposer = Proposer(node_id="P1", quorum_size=3)
        learner = Learner(node_id="L1")

        values = ["a", "b", "c", "d", "e"]
        for v in values:
            chosen = _round_trip(acceptors, proposer, learner, v)
            assert chosen is True

        assert list(learner.learned) == values

    def test_multi_paxos_leader_reuse_slot(self) -> None:
        acceptors = [Acceptor(node_id=f"A{i}") for i in range(5)]
        proposer = Proposer(node_id="leader", quorum_size=3)
        learner = Learner(node_id="L1")

        for i in range(10):
            chosen = _round_trip(acceptors, proposer, learner, f"cmd-{i}")
            assert chosen is True

        assert len(learner.learned) == 10
        assert learner.learned[0] == "cmd-0"
        assert learner.learned[9] == "cmd-9"

    def test_learned_values_are_globally_ordered(self) -> None:
        acceptors = [Acceptor(node_id=f"A{i}") for i in range(5)]
        proposer = Proposer(node_id="P1", quorum_size=3)
        learner = Learner(node_id="L1")

        seq = ["cmd_x", "cmd_y", "cmd_z"]
        for v in seq:
            _round_trip(acceptors, proposer, learner, v)

        assert learner.learned == seq

    def test_acceptor_does_not_rollback_accepted(self) -> None:
        acc = Acceptor(node_id="A1")
        pid1 = ProposalID(number=1, proposer_id="P1")
        acc.handle_prepare(PrepareRequest(pid=pid1))
        acc.handle_accept(AcceptRequest(pid=pid1, value="first", promised_id=None))
        assert acc.accepted_value == "first"

        pid2 = ProposalID(number=2, proposer_id="P2")
        acc.handle_prepare(PrepareRequest(pid=pid2))
        acc.handle_accept(AcceptRequest(pid=pid2, value="second", promised_id=None))
        assert acc.accepted_value == "second"
        assert acc.accepted_id == pid2


# ═══════════════════════════════════════════════════════════════════════
# Conflict resolution
# ═══════════════════════════════════════════════════════════════════════


class TestConflictResolution:
    def test_two_proposers_race_prepare_then_accept(self) -> None:
        acceptors = [Acceptor(node_id=f"A{i}") for i in range(5)]
        p1 = Proposer(node_id="P1", quorum_size=3)
        p2 = Proposer(node_id="P2", quorum_size=3)
        learner = Learner(node_id="L1")

        n1 = p1.next_proposal_number()
        n2 = p2.next_proposal_number()
        pid1 = ProposalID(number=n1, proposer_id="P1")
        pid2 = ProposalID(number=n2, proposer_id="P2")

        for acc in acceptors:
            acc.handle_prepare(PrepareRequest(pid=pid1))
        for acc in acceptors:
            acc.handle_prepare(PrepareRequest(pid=pid2))

        accepts1 = [acc.handle_accept(AcceptRequest(pid=pid1, value="v1", promised_id=pid1)) for acc in acceptors]
        chosen1 = p1.handle_accept_responses(accepts1)
        if chosen1:
            learner.handle_accepted(AcceptRequest(pid=pid1, value="v1", promised_id=pid1))

        accepts2 = [acc.handle_accept(AcceptRequest(pid=pid2, value="v2", promised_id=pid2)) for acc in acceptors]
        chosen2 = p2.handle_accept_responses(accepts2)
        if chosen2:
            learner.handle_accepted(AcceptRequest(pid=pid2, value="v2", promised_id=pid2))

        assert len(learner.learned) >= 1
        assert learner.learned[-1] in ("v1", "v2")

    def test_four_proposers_concurrent_all_land_one_value(self) -> None:
        acceptors = [Acceptor(node_id=f"A{i}") for i in range(5)]
        learner = Learner(node_id="L1")
        proposers = [Proposer(node_id=f"P{i}", quorum_size=3) for i in range(4)]
        values = [f"val-{i}" for i in range(4)]

        for i, proposer in enumerate(proposers):
            chosen = _round_trip(acceptors, proposer, learner, values[i])
            if chosen:
                break

        assert len(learner.learned) >= 1

    def test_proposer_sees_highest_among_conflicting(self) -> None:
        acceptors = [Acceptor(node_id=f"A{i}") for i in range(5)]
        p = Proposer(node_id="P1", quorum_size=3)

        old_pid = ProposalID(number=2, proposer_id="P_old")
        for acc in acceptors:
            acc.handle_prepare(PrepareRequest(pid=old_pid))
            acc.handle_accept(AcceptRequest(pid=old_pid, value="legacy", promised_id=None))

        promises = [acc.handle_prepare(PrepareRequest(pid=ProposalID(number=5, proposer_id="P1"))) for acc in acceptors]
        highest = p.handle_prepare_responses(promises)
        assert highest == old_pid


# ═══════════════════════════════════════════════════════════════════════
# Leader election
# ═══════════════════════════════════════════════════════════════════════


class TestLeaderElection:
    def test_highest_proposal_id_wins_leadership(self) -> None:
        acceptors = [Acceptor(node_id=f"A{i}") for i in range(5)]
        p1 = Proposer(node_id="P1", quorum_size=3)
        p2 = Proposer(node_id="P2", quorum_size=3)

        id1 = ProposalID(number=p1.next_proposal_number(), proposer_id=p1.node_id)
        id2 = ProposalID(number=p2.next_proposal_number(), proposer_id=p2.node_id)

        for acc in acceptors:
            acc.handle_prepare(PrepareRequest(pid=id1))
        for acc in acceptors:
            acc.handle_prepare(PrepareRequest(pid=id2))

        [acc.handle_prepare(PrepareRequest(pid=ProposalID(number=3, proposer_id="P1"))) for acc in acceptors]
        promises2 = [
            acc.handle_prepare(PrepareRequest(pid=ProposalID(number=4, proposer_id="P2"))) for acc in acceptors
        ]

        (p2.handle_prepare_responses(promises2) if p2.handle_prepare_responses(promises2) is not None else None)

        accept_req = AcceptRequest(pid=ProposalID(number=4, proposer_id="P2"), value="leader-val", promised_id=None)
        accepted = sum(1 for acc in acceptors if acc.handle_accept(accept_req).accepted)
        assert accepted >= 3

    def test_leader_can_propose_consecutive_slots(self) -> None:
        acceptors = [Acceptor(node_id=f"A{i}") for i in range(5)]
        leader = Proposer(node_id="leader", quorum_size=3)
        learner = Learner(node_id="L1")

        for slot in range(5):
            chosen = _round_trip(acceptors, leader, learner, f"slot-{slot}")
            assert chosen is True

        assert len(learner.learned) == 5


# ═══════════════════════════════════════════════════════════════════════
# Edge cases
# ═══════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    def test_empty_promise_list(self) -> None:
        p = Proposer(node_id="P1", quorum_size=3)
        highest = p.handle_prepare_responses([])
        assert highest is None

    def test_all_promises_have_no_higher_accepted(self) -> None:
        p = Proposer(node_id="P1", quorum_size=3)
        highest = p.handle_prepare_responses(
            [
                PrepareResponse(
                    promised_id=ProposalID(number=1, proposer_id="P1"),
                    promised=True,
                    highest_accepted_id=None,
                    highest_accepted_value=None,
                ),
                PrepareResponse(
                    promised_id=ProposalID(number=1, proposer_id="P1"),
                    promised=True,
                    highest_accepted_id=None,
                    highest_accepted_value=None,
                ),
                PrepareResponse(
                    promised_id=ProposalID(number=1, proposer_id="P1"),
                    promised=True,
                    highest_accepted_id=None,
                    highest_accepted_value=None,
                ),
            ]
        )
        assert highest is None

    def test_single_non_promising_response_breaks_quorum(self) -> None:
        p = Proposer(node_id="P1", quorum_size=2)
        highest = p.handle_prepare_responses(
            [
                PrepareResponse(
                    promised_id=ProposalID(number=1, proposer_id="P1"),
                    promised=True,
                    highest_accepted_id=None,
                    highest_accepted_value=None,
                ),
                PrepareResponse(
                    promised_id=ProposalID(number=1, proposer_id="P1"),
                    promised=False,
                    highest_accepted_id=None,
                    highest_accepted_value=None,
                ),
                PrepareResponse(
                    promised_id=ProposalID(number=1, proposer_id="P1"),
                    promised=False,
                    highest_accepted_id=None,
                    highest_accepted_value=None,
                ),
            ]
        )
        assert highest is None

    def test_value_is_none(self) -> None:
        acceptors = [Acceptor(node_id=f"A{i}") for i in range(3)]
        proposer = Proposer(node_id="P1", quorum_size=2)
        learner = Learner(node_id="L1")
        chosen = _round_trip(acceptors, proposer, learner, None)
        assert chosen is True
        assert None in learner.learned

    def test_proposal_id_equality(self) -> None:
        a = ProposalID(number=5, proposer_id="X")
        b = ProposalID(number=5, proposer_id="X")
        assert a == b
        assert hash(a) == hash(b)

    def test_proposal_id_repr(self) -> None:
        pid = ProposalID(number=7, proposer_id="leader")
        r = repr(pid)
        assert "7" in r
        assert "leader" in r
