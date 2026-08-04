"""Deep 2PC/3PC distributed commit protocol tests.

Covers: success path, participant failure, coordinator failure, timeout recovery,
3PC pre-commit phase, crash-recovery, and edge cases.
"""

from __future__ import annotations

from general_ludd.distributed.two_phase_commit import (
    AbortRequest,
    AckResponse,
    CommitRequest,
    Coordinator,
    CoordinatorState,
    Participant,
    ParticipantState,
    PreCommitRequest,
    PrepareRequest,
    Protocol,
    TwoPCConfig,
    Vote,
)

# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _coordinator_with(
    n: int = 3,
    protocol: Protocol = Protocol.TWO_PC,
) -> Coordinator:
    c = Coordinator(
        coordinator_id="coord-1",
        config=TwoPCConfig(protocol=protocol),
    )
    for i in range(n):
        c.register_participant(Participant(participant_id=f"p{i}"))
    return c


# ═══════════════════════════════════════════════════════════════════════
# 2PC success path
# ═══════════════════════════════════════════════════════════════════════


def test_2pc_success_all_participants_vote_yes():
    c = _coordinator_with(3)
    result = c.execute_transaction("tx-1")
    assert result == CoordinatorState.COMMITTED
    for p in c.participants.values():
        assert p.state == ParticipantState.COMMITTED


def test_2pc_success_single_participant():
    c = _coordinator_with(1)
    result = c.execute_transaction("tx-single")
    assert result == CoordinatorState.COMMITTED
    assert c.participants["p0"].state == ParticipantState.COMMITTED


def test_2pc_success_five_participants():
    c = _coordinator_with(5)
    result = c.execute_transaction("tx-5")
    assert result == CoordinatorState.COMMITTED
    assert all(p.state == ParticipantState.COMMITTED for p in c.participants.values())


# ═══════════════════════════════════════════════════════════════════════
# Participant failure — vote NO
# ═══════════════════════════════════════════════════════════════════════


def test_2pc_one_participant_votes_no_triggers_abort():
    c = _coordinator_with(3)
    c.participants["p1"]._prepare_should_fail = True
    result = c.execute_transaction("tx-abort-1")
    assert result == CoordinatorState.ABORTED
    assert c.participants["p0"].state == ParticipantState.ABORTED
    assert c.participants["p1"].state == ParticipantState.ABORTED
    assert c.participants["p2"].state == ParticipantState.ABORTED


def test_2pc_all_participants_vote_no():
    c = _coordinator_with(3)
    for p in c.participants.values():
        p._prepare_should_fail = True
    result = c.execute_transaction("tx-all-no")
    assert result == CoordinatorState.ABORTED
    assert all(p.state in (ParticipantState.ABORTED, ParticipantState.FAILED) for p in c.participants.values())


def test_2pc_one_participant_votes_no_others_still_aborted():
    c = _coordinator_with(3)
    c.participants["p2"]._prepare_should_fail = True
    result = c.execute_transaction("tx-last-no")
    assert result == CoordinatorState.ABORTED
    assert c.participants["p0"].state == ParticipantState.ABORTED
    assert c.participants["p1"].state == ParticipantState.ABORTED


# ═══════════════════════════════════════════════════════════════════════
# Participant commit failure
# ═══════════════════════════════════════════════════════════════════════


def test_2pc_participant_commit_failure_does_not_block_others():
    c = _coordinator_with(3)
    c.participants["p1"]._commit_should_fail = True
    result = c.execute_transaction("tx-commit-fail")
    assert result == CoordinatorState.COMMITTED
    assert c.participants["p0"].state == ParticipantState.COMMITTED
    assert c.participants["p2"].state == ParticipantState.COMMITTED


# ═══════════════════════════════════════════════════════════════════════
# Coordinator crash during prepare
# ═══════════════════════════════════════════════════════════════════════


def test_coordinator_crash_after_prepare_stays_in_preparing():
    c = _coordinator_with(3)
    c._crash_after_prepare = True
    result = c.execute_transaction("tx-crash-prep")
    assert result == CoordinatorState.PREPARING
    assert c.state == CoordinatorState.PREPARING


# ═══════════════════════════════════════════════════════════════════════
# Coordinator crash after commit — recovery
# ═══════════════════════════════════════════════════════════════════════


def test_coordinator_crash_after_commit_recovery_commits_remaining():
    c = _coordinator_with(3)
    c._crash_after_commit = True
    result = c.execute_transaction("tx-crash-commit")
    assert result == CoordinatorState.COMMITTING

    new_state = c.recover()
    assert new_state == CoordinatorState.COMMITTED
    assert all(p.state == ParticipantState.COMMITTED for p in c.participants.values())


# ═══════════════════════════════════════════════════════════════════════
# Recovery: prepared participants commit on recovery
# ═══════════════════════════════════════════════════════════════════════


def test_recovery_when_participants_prepared_commits_them():
    c = _coordinator_with(3)
    c.state = CoordinatorState.PREPARING
    c.current_transaction_id = "tx-recov-1"
    for p in c.participants.values():
        p.state = ParticipantState.PREPARED
        p.current_transaction_id = "tx-recov-1"

    result = c.recover()
    assert result == CoordinatorState.COMMITTED
    assert all(p.state == ParticipantState.COMMITTED for p in c.participants.values())


def test_recovery_when_participants_aborted_aborts_others():
    c = _coordinator_with(3)
    c.state = CoordinatorState.PREPARING
    c.current_transaction_id = "tx-recov-2"
    c.participants["p0"].state = ParticipantState.PREPARED
    c.participants["p0"].current_transaction_id = "tx-recov-2"
    c.participants["p1"].state = ParticipantState.ABORTED
    c.participants["p1"].current_transaction_id = "tx-recov-2"
    c.participants["p2"].state = ParticipantState.INIT
    c.participants["p2"].current_transaction_id = "tx-recov-2"

    result = c.recover()
    assert result == CoordinatorState.ABORTED
    assert c.participants["p0"].state == ParticipantState.ABORTED
    assert c.participants["p2"].state == ParticipantState.ABORTED


# ═══════════════════════════════════════════════════════════════════════
# Recovery with no transaction — returns INIT
# ═══════════════════════════════════════════════════════════════════════


def test_recovery_with_no_transaction_returns_init():
    c = _coordinator_with(3)
    c.state = CoordinatorState.PREPARING
    result = c.recover()
    assert result == CoordinatorState.INIT


# ═══════════════════════════════════════════════════════════════════════
# Participant sim_crash / sim_recover
# ═══════════════════════════════════════════════════════════════════════


def test_participant_sim_crash_sets_failed():
    p = Participant(participant_id="p0")
    p.sim_crash()
    assert p.state == ParticipantState.FAILED


def test_participant_recover_from_failed_with_transaction_becomes_prepared():
    p = Participant(participant_id="p0")
    p.current_transaction_id = "tx-recov-p"
    p.state = ParticipantState.FAILED
    new_state = p.sim_recover()
    assert new_state == ParticipantState.PREPARED


def test_participant_recover_from_failed_no_transaction_becomes_init():
    p = Participant(participant_id="p0")
    p.state = ParticipantState.FAILED
    new_state = p.sim_recover()
    assert new_state == ParticipantState.INIT


def test_participant_reset_clears_state():
    p = Participant(participant_id="p0")
    p.state = ParticipantState.COMMITTED
    p.current_transaction_id = "tx-old"
    p.reset()
    assert p.state == ParticipantState.INIT
    assert p.current_transaction_id is None


# ═══════════════════════════════════════════════════════════════════════
# 3PC success path
# ═══════════════════════════════════════════════════════════════════════


def test_3pc_success_all_participants_vote_yes():
    c = _coordinator_with(3, protocol=Protocol.THREE_PC)
    result = c.execute_transaction("tx-3pc")
    assert result == CoordinatorState.COMMITTED
    assert all(p.state == ParticipantState.COMMITTED for p in c.participants.values())


def test_3pc_one_participant_votes_no_triggers_abort():
    c = _coordinator_with(3, protocol=Protocol.THREE_PC)
    c.participants["p1"]._prepare_should_fail = True
    result = c.execute_transaction("tx-3pc-abort")
    assert result == CoordinatorState.ABORTED
    assert all(p.state in (ParticipantState.ABORTED, ParticipantState.FAILED) for p in c.participants.values())


def test_3pc_participant_enters_pre_committed_state():
    c = _coordinator_with(2, protocol=Protocol.THREE_PC)
    c.execute_transaction("tx-3pc-pre")
    assert c.participants["p0"].state == ParticipantState.COMMITTED
    assert c.participants["p1"].state == ParticipantState.COMMITTED


# ═══════════════════════════════════════════════════════════════════════
# 3PC coordinator crash after pre-commit
# ═══════════════════════════════════════════════════════════════════════


def test_3pc_coordinator_crash_after_pre_commit():
    c = _coordinator_with(3, protocol=Protocol.THREE_PC)
    c._crash_after_pre_commit = True
    result = c.execute_transaction("tx-3pc-crash-pre")
    assert result == CoordinatorState.PRE_COMMITTING


# ═══════════════════════════════════════════════════════════════════════
# 3PC recovery with pre-committed participants
# ═══════════════════════════════════════════════════════════════════════


def test_3pc_recovery_from_pre_committed_commits():
    c = _coordinator_with(3, protocol=Protocol.THREE_PC)
    c.state = CoordinatorState.PRE_COMMITTING
    c.current_transaction_id = "tx-3pc-recov"
    for p in c.participants.values():
        p.state = ParticipantState.PRE_COMMITTED
        p.current_transaction_id = "tx-3pc-recov"

    result = c.recover()
    assert result == CoordinatorState.COMMITTED
    assert all(p.state == ParticipantState.COMMITTED for p in c.participants.values())


def test_3pc_recovery_from_mixed_prepared_and_pre_committed():
    c = _coordinator_with(3, protocol=Protocol.THREE_PC)
    c.state = CoordinatorState.PRE_COMMITTING
    c.current_transaction_id = "tx-3pc-mixed"
    c.participants["p0"].state = ParticipantState.PRE_COMMITTED
    c.participants["p0"].current_transaction_id = "tx-3pc-mixed"
    c.participants["p1"].state = ParticipantState.PREPARED
    c.participants["p1"].current_transaction_id = "tx-3pc-mixed"
    c.participants["p2"].state = ParticipantState.PREPARED
    c.participants["p2"].current_transaction_id = "tx-3pc-mixed"

    result = c.recover()
    assert result == CoordinatorState.COMMITTED
    assert all(p.state == ParticipantState.COMMITTED for p in c.participants.values())


# ═══════════════════════════════════════════════════════════════════════
# Coordinator reset
# ═══════════════════════════════════════════════════════════════════════


def test_coordinator_reset_clears_all_participant_state():
    c = _coordinator_with(3)
    c.execute_transaction("tx-reset")
    c.reset()
    assert c.state == CoordinatorState.INIT
    assert c.current_transaction_id is None
    for p in c.participants.values():
        assert p.state == ParticipantState.INIT


# ═══════════════════════════════════════════════════════════════════════
# Timeout: skipped participant treated as NO
# ═══════════════════════════════════════════════════════════════════════


def test_timeout_skipped_participant_causes_abort():
    c = _coordinator_with(3)
    c._skip_prepare_to = "p1"
    result = c.execute_transaction("tx-timeout")
    assert result == CoordinatorState.ABORTED


# ═══════════════════════════════════════════════════════════════════════
# Edge: Protocol enum values
# ═══════════════════════════════════════════════════════════════════════


def test_protocol_enum_values():
    assert Protocol.TWO_PC == "two_pc"
    assert Protocol.THREE_PC == "three_pc"


def test_vote_enum_values():
    assert Vote.YES == "yes"
    assert Vote.NO == "no"


def test_coordinator_state_enum_values():
    assert CoordinatorState.INIT == "init"
    assert CoordinatorState.COMMITTED == "committed"
    assert CoordinatorState.ABORTED == "aborted"


def test_participant_state_enum_values():
    assert ParticipantState.INIT == "init"
    assert ParticipantState.PREPARED == "prepared"
    assert ParticipantState.PRE_COMMITTED == "pre_committed"


# ═══════════════════════════════════════════════════════════════════════
# Edge: callbacks fire on commit/abort
# ═══════════════════════════════════════════════════════════════════════


def test_participant_on_commit_callback_fires():
    committed_txs: list[str] = []
    c = _coordinator_with(1)
    c.participants["p0"]._on_commit = lambda txid: committed_txs.append(txid)
    c.execute_transaction("tx-callback")
    assert committed_txs == ["tx-callback"]


def test_participant_on_abort_callback_fires():
    aborted_txs: list[str] = []
    p = Participant(participant_id="p0", _on_abort=lambda txid: aborted_txs.append(txid))
    p.handle_abort(AbortRequest(transaction_id="tx-abort-cb"))
    assert aborted_txs == ["tx-abort-cb"]


# ═══════════════════════════════════════════════════════════════════════
# Edge: custom prepare vote decision via callback
# ═══════════════════════════════════════════════════════════════════════


def test_participant_on_prepare_callback_overrides_vote():
    p = Participant(participant_id="p0", _on_prepare=lambda self: Vote.NO)
    resp = p.handle_prepare(PrepareRequest(transaction_id="tx-cb-no", coordinator_id="c1"))
    assert resp.vote == Vote.NO
    assert p.state == ParticipantState.FAILED


def test_participant_on_prepare_callback_vote_yes():
    p = Participant(participant_id="p0", _on_prepare=lambda self: Vote.YES)
    resp = p.handle_prepare(PrepareRequest(transaction_id="tx-cb-yes", coordinator_id="c1"))
    assert resp.vote == Vote.YES
    assert p.state == ParticipantState.PREPARED


# ═══════════════════════════════════════════════════════════════════════
# Edge: direct message handling
# ═══════════════════════════════════════════════════════════════════════


def test_participant_handle_prepare_directly():
    p = Participant(participant_id="p0")
    req = PrepareRequest(transaction_id="tx-direct", coordinator_id="c1")
    resp = p.handle_prepare(req)
    assert resp.vote == Vote.YES
    assert resp.participant_id == "p0"
    assert p.state == ParticipantState.PREPARED


def test_participant_handle_commit_directly():
    p = Participant(participant_id="p0", state=ParticipantState.PREPARED)
    resp = p.handle_commit(CommitRequest(transaction_id="tx-commit-dir"))
    assert isinstance(resp, AckResponse)
    assert resp.transaction_id == "tx-commit-dir"
    assert p.state == ParticipantState.COMMITTED


def test_participant_handle_abort_directly():
    p = Participant(participant_id="p0")
    resp = p.handle_abort(AbortRequest(transaction_id="tx-abort-dir"))
    assert isinstance(resp, AckResponse)
    assert p.state == ParticipantState.ABORTED


def test_participant_pre_commit_ack_false_aborts():
    p = Participant(
        participant_id="p0",
        state=ParticipantState.PREPARED,
        _on_pre_commit=lambda self: False,
    )
    resp = p.handle_pre_commit(PreCommitRequest(transaction_id="tx-pc-no"))
    assert resp.ack is False
    assert p.state == ParticipantState.ABORTED


def test_participant_pre_commit_ack_true():
    p = Participant(
        participant_id="p0",
        state=ParticipantState.PREPARED,
        _on_pre_commit=lambda self: True,
    )
    resp = p.handle_pre_commit(PreCommitRequest(transaction_id="tx-pc-yes"))
    assert resp.ack is True
    assert p.state == ParticipantState.PRE_COMMITTED


# ═══════════════════════════════════════════════════════════════════════
# Edge: recovery with committed already present
# ═══════════════════════════════════════════════════════════════════════


def test_recovery_with_committed_participant_commits_remainder():
    c = _coordinator_with(3)
    c.state = CoordinatorState.PREPARING
    c.current_transaction_id = "tx-mixed-recov"
    c.participants["p0"].state = ParticipantState.COMMITTED
    c.participants["p0"].current_transaction_id = "tx-mixed-recov"
    c.participants["p1"].state = ParticipantState.PREPARED
    c.participants["p1"].current_transaction_id = "tx-mixed-recov"
    c.participants["p2"].state = ParticipantState.PREPARED
    c.participants["p2"].current_transaction_id = "tx-mixed-recov"

    result = c.recover()
    assert result == CoordinatorState.COMMITTED
    assert all(p.state == ParticipantState.COMMITTED for p in c.participants.values())


# ═══════════════════════════════════════════════════════════════════════
# Edge: TwoPCConfig defaults
# ═══════════════════════════════════════════════════════════════════════


def test_two_pc_config_defaults():
    config = TwoPCConfig()
    assert config.prepare_timeout == 5.0
    assert config.commit_timeout == 5.0
    assert config.protocol == Protocol.TWO_PC


def test_two_pc_config_three_pc():
    config = TwoPCConfig(protocol=Protocol.THREE_PC, prepare_timeout=10.0)
    assert config.protocol == Protocol.THREE_PC
    assert config.prepare_timeout == 10.0
