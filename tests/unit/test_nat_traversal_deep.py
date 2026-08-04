"""Deep algorithmic tests for NAT traversal: STUN message encode/decode,
NAT type classification, ICE candidate pairs, hole-punch state machine,
symmetric port prediction, and reflexive address discovery.

Tests the structural and algorithmic contracts without network I/O.
"""

from __future__ import annotations

import struct

import pytest

from general_ludd.network.nat_traversal import (
    STUN_MAGIC_COOKIE,
    HolePunchPhase,
    HolePunchState,
    IceCandidate,
    IceCandidatePair,
    IceCandidateType,
    IceGatherer,
    NatClassifier,
    NatTraversalOrchestrator,
    NatType,
    StunAddress,
    StunAttribute,
    StunBindingResult,
    StunClass,
    StunClient,
    StunErrorCode,
    StunMessage,
    StunMethod,
    SymmetricPortPredictor,
)

# ---------------------------------------------------------------------------
# 1 — STUN Message Encode / Decode
# ---------------------------------------------------------------------------


class TestStunMessageRoundTrip:
    """Verify STUN message encode → decode idempotency."""

    def test_binding_request_encode_decode(self) -> None:
        req = StunMessage.binding_request()
        raw = req.encode()
        parsed = StunMessage.parse(raw)
        assert parsed.msg_class == StunClass.REQUEST
        assert parsed.method == StunMethod.BINDING
        assert parsed.transaction_id == req.transaction_id

    def test_success_response_encode_decode(self) -> None:
        req = StunMessage.binding_request()
        client = StunClient(servers=[("1.2.3.4", 3478)])
        resp = client.binding_request_response(req, "5.6.7.8", 54321)
        raw = resp.encode()
        parsed = StunMessage.parse(raw)
        assert parsed.msg_class == StunClass.SUCCESS
        assert parsed.transaction_id == req.transaction_id

    def test_xor_mapped_address_preserved(self) -> None:
        req = StunMessage.binding_request()
        client = StunClient(servers=[("stun.example.com", 3478)])
        resp = client.binding_request_response(req, "10.20.30.40", 12345, xor=True)
        decoded = resp.get_xor_address()
        assert decoded == "10.20.30.40"

    def test_mapped_address_packed_correctly(self) -> None:
        addr = StunAddress.ipv4("192.168.1.1", 9999)
        packed = addr.pack()
        assert len(packed) == 8
        _zero, family, port, _ip = struct.unpack("!BBH4s", packed)
        assert family == 0x01
        assert port == 9999

    def test_reject_message_too_short(self) -> None:
        with pytest.raises(ValueError, match="too short"):
            StunMessage.parse(b"\x00" * 10)

    def test_unknown_attribute_skipped(self) -> None:
        req = StunMessage.binding_request()
        req.attributes.append((9999, b"junk"))  # type: ignore[arg-type]
        raw = req.encode()
        parsed = StunMessage.parse(raw)
        assert parsed.msg_class == StunClass.REQUEST

    def test_multiple_attributes_round_trip(self) -> None:
        req = StunMessage.binding_request()
        req.attributes = [
            (StunAttribute.SOFTWARE, b"gludd-stun/1.0"),
        ]
        raw = req.encode()
        parsed = StunMessage.parse(raw)
        assert parsed.msg_class == StunClass.REQUEST
        assert parsed.method == StunMethod.BINDING
        assert parsed.transaction_id == req.transaction_id

    def test_get_address_returns_none_for_missing(self) -> None:
        req = StunMessage.binding_request()
        assert req.get_address(StunAttribute.MAPPED_ADDRESS) is None


# ---------------------------------------------------------------------------
# 2 — NAT Type Classification
# ---------------------------------------------------------------------------


class TestNatClassification:
    """Verify NatType classification from STUN responses / binding results."""

    def test_no_responses_is_udp_blocked(self) -> None:
        classifier = NatClassifier()
        assert classifier.classify() == NatType.UDP_BLOCKED

    def test_failed_responses_is_udp_blocked(self) -> None:
        classifier = NatClassifier()
        classifier.add_response(StunBindingResult(success=False))
        assert classifier.classify() == NatType.UDP_BLOCKED

    def test_single_success_is_full_cone(self) -> None:
        classifier = NatClassifier()
        classifier.add_response(
            StunBindingResult(
                success=True,
                mapped_address="1.2.3.4",
                mapped_port=5555,
            )
        )
        assert classifier.classify() == NatType.FULL_CONE

    def test_different_mapped_addresses_is_symmetric(self) -> None:
        classifier = NatClassifier()
        classifier.add_response(
            StunBindingResult(
                success=True,
                mapped_address="1.2.3.4",
                mapped_port=5555,
            )
        )
        classifier.add_response(
            StunBindingResult(
                success=True,
                mapped_address="5.6.7.8",
                mapped_port=6666,
            )
        )
        assert classifier.classify() == NatType.SYMMETRIC

    def test_same_mapped_address_from_multiple_servers(self) -> None:
        classifier = NatClassifier()
        classifier.add_response(
            StunBindingResult(
                success=True,
                mapped_address="1.2.3.4",
                mapped_port=5555,
            )
        )
        classifier.add_response(
            StunBindingResult(
                success=True,
                mapped_address="1.2.3.4",
                mapped_port=5555,
            )
        )
        assert classifier.classify() == NatType.FULL_CONE

    def test_stun_client_classify_single_response(self) -> None:
        client = StunClient(servers=[("stun.l.google.com", 19302)])
        req = StunMessage.binding_request()
        resp = client.binding_request_response(req, "8.8.8.8", 12345)
        assert client.classify([resp]) == NatType.FULL_CONE

    def test_stun_client_classify_two_different_mapped(self) -> None:
        client = StunClient(
            servers=[
                ("stun1.example.com", 3478),
                ("stun2.example.com", 3479),
            ]
        )
        req1 = StunMessage.binding_request()
        resp1 = client.binding_request_response(req1, "8.8.8.8", 12345)
        req2 = StunMessage.binding_request()
        resp2 = client.binding_request_response(req2, "9.9.9.9", 54321)
        assert client.classify([resp1, resp2]) == NatType.SYMMETRIC

    def test_stun_client_classify_empty(self) -> None:
        client = StunClient(servers=[])
        assert client.classify([]) == NatType.UDP_BLOCKED

    def test_stun_binding_result_have_connectivity(self) -> None:
        good = StunBindingResult(success=True, mapped_address="1.2.3.4", mapped_port=9999)
        assert good.have_connectivity() is True
        bad = StunBindingResult(success=False, mapped_address="1.2.3.4", mapped_port=9999)
        assert bad.have_connectivity() is False
        no_addr = StunBindingResult(success=True, mapped_address=None, mapped_port=9999)
        assert no_addr.have_connectivity() is False


# ---------------------------------------------------------------------------
# 3 — ICE Candidate Types and Pairing
# ---------------------------------------------------------------------------


class TestIceCandidates:
    """Verify ICE candidate types, candidate-line formatting, and pairing."""

    def test_host_candidate_factory(self) -> None:
        c = IceCandidate.host("192.168.1.5", 50000)
        assert c.kind == IceCandidateType.HOST
        assert c.address == "192.168.1.5"
        assert c.port == 50000
        assert c.priority == 2130706431
        assert c.transport == "udp"

    def test_srflx_candidate_factory(self) -> None:
        c = IceCandidate.srflx("203.0.113.5", 40000)
        assert c.kind == IceCandidateType.SRFLX
        assert c.address == "203.0.113.5"

    def test_relay_candidate_factory(self) -> None:
        c = IceCandidate.relay("turn.example.com", 3478)
        assert c.kind == IceCandidateType.RELAY

    def test_candidate_line_format(self) -> None:
        c = IceCandidate.host("10.0.0.1", 5000)
        line = c.candidate_line()
        assert "10.0.0.1" in line
        assert "5000" in line
        assert "typ host" in line

    def test_srflx_candidate_line(self) -> None:
        c = IceCandidate.srflx("203.0.113.5", 40000)
        line = c.candidate_line()
        assert "typ srflx" in line

    def test_ice_candidate_pair_priority(self) -> None:
        local = IceCandidate.host("10.0.0.1", 5000, priority=100)
        remote = IceCandidate.host("10.0.0.2", 6000, priority=200)
        pair = IceCandidatePair(local=local, remote=remote)
        assert pair.priority > 0
        assert pair.state == "frozen"

    def test_pair_id_unique(self) -> None:
        a = IceCandidate.host("10.0.0.1", 5000)
        b = IceCandidate.host("10.0.0.2", 6000)
        pair = IceCandidatePair(local=a, remote=b)
        assert ":" in pair.pair_id()
        assert len(pair.pair_id()) > 0

    def test_pair_nominated_flag(self) -> None:
        a = IceCandidate.host("10.0.0.1", 5000)
        b = IceCandidate.host("10.0.0.2", 6000)
        pair = IceCandidatePair(local=a, remote=b)
        assert pair.nominated is False
        pair.nominated = True
        assert pair.nominated is True


# ---------------------------------------------------------------------------
# 4 — ICE Gatherer
# ---------------------------------------------------------------------------


class TestIceGatherer:
    """Verify ICE gathering: host, srflx, relay candidates and pairing."""

    def test_gatherer_adds_host_candidates(self) -> None:
        g = IceGatherer()
        g.add_host("192.168.0.10", 50000)
        assert len(g.host_candidates) == 1
        assert g.host_candidates[0].kind == IceCandidateType.HOST

    def test_gatherer_adds_srflx(self) -> None:
        g = IceGatherer()
        g.add_srflx("203.0.113.5", 40000)
        assert len(g.srflx_candidates) == 1

    def test_gatherer_adds_relay(self) -> None:
        g = IceGatherer()
        g.add_relay("turn.example.com", 5349)
        assert len(g.relay_candidates) == 1

    def test_all_candidates_ordered(self) -> None:
        g = IceGatherer()
        g.add_host("10.0.0.1", 1)
        g.add_srflx("203.0.113.1", 2)
        g.add_relay("turn.example.com", 3)
        all_c = g.all_candidates()
        assert all_c[0].kind == IceCandidateType.HOST
        assert all_c[1].kind == IceCandidateType.SRFLX
        assert all_c[2].kind == IceCandidateType.RELAY

    def test_pair_with_produces_cross_product(self) -> None:
        local = IceGatherer()
        local.add_host("10.0.0.1", 5000)
        local.add_srflx("203.0.113.1", 4000)
        remote = IceGatherer()
        remote.add_host("10.0.0.2", 6000)
        remote.add_srflx("203.0.113.2", 5000)
        pairs = local.pair_with(remote)
        assert len(pairs) == 4

    def test_pair_with_sorted_by_priority_desc(self) -> None:
        local = IceGatherer()
        local.add_host("10.0.0.1", 5000)
        remote = IceGatherer()
        remote.add_host("10.0.0.2", 6000)
        remote.add_srflx("203.0.113.2", 5000)
        pairs = local.pair_with(remote)
        assert pairs[0].priority >= pairs[1].priority

    def test_ufrag_and_pwd_auto_generated(self) -> None:
        g = IceGatherer()
        assert len(g.local_ufrag) == 8
        assert len(g.local_pwd) == 44

    def test_ufrag_and_pwd_can_be_set(self) -> None:
        g = IceGatherer(local_ufrag="abcd1234", local_pwd="secret")
        assert g.local_ufrag == "abcd1234"
        assert g.local_pwd == "secret"


# ---------------------------------------------------------------------------
# 5 — Hole Punch State Machine
# ---------------------------------------------------------------------------


class TestHolePunchStateMachine:
    """Verify the hole-punch state machine transitions and invariants."""

    def test_initial_state_is_idle(self) -> None:
        hp = HolePunchState()
        assert hp.phase == HolePunchPhase.IDLE

    def test_gather_transitions_from_idle(self) -> None:
        hp = HolePunchState()
        local = IceGatherer()
        hp.gather(local)
        assert hp.phase == HolePunchPhase.BINDING_REQUEST_SENT
        assert hp.local is local

    def test_binding_response_transitions(self) -> None:
        hp = HolePunchState()
        hp.gather(IceGatherer())
        hp.on_binding_response("203.0.113.5", 40000)
        assert hp.phase == HolePunchPhase.BINDING_RESPONSE_RECEIVED
        assert len(hp.local.srflx_candidates) == 1  # type: ignore[union-attr]

    def test_remote_gathered_transitions_to_connectivity_check(self) -> None:
        hp = HolePunchState()
        hp.gather(IceGatherer())
        hp.on_binding_response("203.0.113.5", 40000)
        remote = IceGatherer()
        remote.add_host("10.0.0.2", 6000)
        hp.on_remote_gathered(remote)
        assert hp.phase == HolePunchPhase.CONNECTIVITY_CHECK_SENT

    def test_connectivity_passes_transitions(self) -> None:
        hp = HolePunchState()
        local = IceGatherer()
        local.add_host("10.0.0.1", 5000)
        hp.gather(local)
        hp.on_binding_response("203.0.113.5", 40000)
        remote = IceGatherer()
        remote.add_host("10.0.0.2", 6000)
        hp.on_remote_gathered(remote)
        pair = IceCandidatePair(
            local=IceCandidate.host("10.0.0.1", 5000),
            remote=IceCandidate.host("10.0.0.2", 6000),
        )
        hp.connectivity_passes(pair)
        assert hp.phase == HolePunchPhase.CONNECTIVITY_CHECK_PASSED
        assert hp.selected_pair is not None

    def test_nominate_transitions(self) -> None:
        hp = HolePunchState()
        local = IceGatherer()
        local.add_host("10.0.0.1", 5000)
        hp.gather(local)
        hp.on_binding_response("203.0.113.5", 40000)
        remote = IceGatherer()
        remote.add_host("10.0.0.2", 6000)
        hp.on_remote_gathered(remote)
        pair = IceCandidatePair(
            local=IceCandidate.host("10.0.0.1", 5000),
            remote=IceCandidate.host("10.0.0.2", 6000),
        )
        hp.connectivity_passes(pair)
        hp.nominate()
        assert hp.phase == HolePunchPhase.NOMINATED

    def test_establish_transitions(self) -> None:
        hp = HolePunchState()
        local = IceGatherer()
        local.add_host("10.0.0.1", 5000)
        hp.gather(local)
        hp.on_binding_response("203.0.113.5", 40000)
        remote = IceGatherer()
        remote.add_host("10.0.0.2", 6000)
        hp.on_remote_gathered(remote)
        pair = IceCandidatePair(
            local=IceCandidate.host("10.0.0.1", 5000),
            remote=IceCandidate.host("10.0.0.2", 6000),
        )
        hp.connectivity_passes(pair)
        hp.nominate()
        hp.establish()
        assert hp.phase == HolePunchPhase.ESTABLISHED

    def test_fail_from_idle(self) -> None:
        hp = HolePunchState()
        hp.fail()
        assert hp.phase == HolePunchPhase.FAILED

    def test_fail_from_connectivity_check(self) -> None:
        hp = HolePunchState()
        hp.gather(IceGatherer())
        hp.on_binding_response("203.0.113.5", 40000)
        remote = IceGatherer()
        remote.add_host("10.0.0.2", 6000)
        hp.on_remote_gathered(remote)
        hp.fail()
        assert hp.phase == HolePunchPhase.FAILED

    def test_relay_pair_rejected_by_check(self) -> None:
        hp = HolePunchState()
        lp = IceCandidate.relay("turn.example.com", 5349)
        rp = IceCandidate.relay("turn2.example.com", 5349)
        pair = IceCandidatePair(local=lp, remote=rp)
        assert hp.check_pair(pair) is False

    def test_host_to_host_check_passes(self) -> None:
        hp = HolePunchState()
        lp = IceCandidate.host("10.0.0.1", 5000)
        rp = IceCandidate.host("10.0.0.2", 6000)
        pair = IceCandidatePair(local=lp, remote=rp)
        assert hp.check_pair(pair) is True

    def test_srflx_to_host_check_passes(self) -> None:
        hp = HolePunchState()
        lp = IceCandidate.srflx("203.0.113.1", 4000)
        rp = IceCandidate.host("10.0.0.2", 6000)
        pair = IceCandidatePair(local=lp, remote=rp)
        assert hp.check_pair(pair) is True

    def test_connectivity_passes_rejects_relay_pair(self) -> None:
        hp = HolePunchState()
        hp.gather(IceGatherer())
        hp.on_binding_response("203.0.113.5", 40000)
        remote = IceGatherer()
        remote.add_relay("turn.example.com", 5349)
        hp.on_remote_gathered(remote)
        pair = IceCandidatePair(
            local=IceCandidate.host("10.0.0.1", 5000),
            remote=IceCandidate.relay("turn.example.com", 5349),
        )
        assert hp.connectivity_passes(pair) is False


# ---------------------------------------------------------------------------
# 6 — Symmetric Port Predictor
# ---------------------------------------------------------------------------


class TestSymmetricPortPredictor:
    """Verify port prediction for symmetric NAT traversal."""

    def test_constant_delta_prediction(self) -> None:
        p = SymmetricPortPredictor()
        p.observe(40000)
        p.observe(40001)
        p.observe(40002)
        assert p.predict_next() == 40003

    def test_average_delta_prediction(self) -> None:
        p = SymmetricPortPredictor()
        p.observe(40000)
        p.observe(40003)
        p.observe(40005)
        p.observe(40008)
        predicted = p.predict_next()
        assert predicted in (40011, 40010, 40009)

    def test_two_samples_uses_last_delta(self) -> None:
        p = SymmetricPortPredictor()
        p.observe(40000)
        p.observe(40005)
        assert p.predict_next() == 40010

    def test_single_sample_returns_zero(self) -> None:
        p = SymmetricPortPredictor()
        p.observe(40000)
        assert p.predict_next() == 0

    def test_empty_returns_zero(self) -> None:
        p = SymmetricPortPredictor()
        assert p.predict_next() == 0

    def test_window_slides_at_4(self) -> None:
        p = SymmetricPortPredictor()
        p.observe(40000)
        p.observe(40001)
        p.observe(40002)
        p.observe(40003)
        assert len(p.observed_ports) == 4
        p.observe(40004)
        assert p.observed_ports[0] == 40001


# ---------------------------------------------------------------------------
# 7 — NAT Traversal Orchestrator
# ---------------------------------------------------------------------------


class TestNatTraversalOrchestrator:
    """Verify the orchestrator integrates stun, gathering, and best-pair selection."""

    def test_discover_reflexive_adds_srflx(self) -> None:
        local = IceGatherer()
        local.add_host("10.0.0.1", 5000)
        client = StunClient(servers=[("stun.example.com", 3478)])
        orch = NatTraversalOrchestrator(local_gatherer=local, stun_client=client)
        orch.discover_reflexive({"stun.example.com:3478": ("203.0.113.5", 40000)})
        assert len(local.srflx_candidates) == 1
        assert local.srflx_candidates[0].address == "203.0.113.5"

    def test_compute_best_pair_prefers_host_to_host(self) -> None:
        local = IceGatherer()
        local.add_host("10.0.0.1", 5000)
        local.add_srflx("203.0.113.1", 4000)
        client = StunClient(servers=[])
        orch = NatTraversalOrchestrator(local_gatherer=local, stun_client=client)
        remote = IceGatherer()
        remote.add_host("10.0.0.2", 6000)
        remote.add_srflx("203.0.113.2", 5000)
        pairs = local.pair_with(remote)
        best = orch.compute_best_pair(pairs)
        assert best is not None
        assert best.local.kind == IceCandidateType.HOST
        assert best.remote.kind == IceCandidateType.HOST

    def test_compute_best_pair_returns_none_for_relay_only(self) -> None:
        local = IceGatherer()
        local.add_relay("turn.example.com", 5349)
        client = StunClient(servers=[])
        orch = NatTraversalOrchestrator(local_gatherer=local, stun_client=client)
        remote = IceGatherer()
        remote.add_relay("turn2.example.com", 5349)
        pairs = local.pair_with(remote)
        assert orch.compute_best_pair(pairs) is None


# ---------------------------------------------------------------------------
# 8 — Enumerations and Constants
# ---------------------------------------------------------------------------


class TestEnumerations:
    def test_nat_type_values(self) -> None:
        assert NatType.OPEN == "open"
        assert NatType.SYMMETRIC == "symmetric"
        assert NatType.UDP_BLOCKED == "udp_blocked"

    def test_ice_candidate_type_values(self) -> None:
        assert IceCandidateType.HOST == "host"
        assert IceCandidateType.SRFLX == "srflx"
        assert IceCandidateType.RELAY == "relay"

    def test_stun_class_encoding(self) -> None:
        assert StunClass.REQUEST.value == 0x0000
        assert StunClass.SUCCESS.value == 0x0100
        assert StunClass.ERROR.value == 0x0110

    def test_stun_method_binding(self) -> None:
        assert StunMethod.BINDING.value == 0x0001

    def test_stun_magic_cookie_constant(self) -> None:
        assert STUN_MAGIC_COOKIE == 0x2112A442

    def test_stun_error_codes(self) -> None:
        assert StunErrorCode.UNAUTHORIZED == 401
        assert StunErrorCode.SERVER_ERROR == 500
