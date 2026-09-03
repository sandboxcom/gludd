"""Two-Phase Commit (2PC) and Three-Phase Commit (3PC) distributed transaction protocols.

2PC state machine:
    Coordinator: INIT -> PREPARING -> COMMITTING | ABORTING -> COMMITTED | ABORTED
    Participant: INIT -> PREPARED | FAILED -> COMMITTED | ABORTED

3PC adds a pre-commit phase to eliminate the blocking problem:
    Coordinator: INIT -> PREPARING -> PRE_COMMITTING -> COMMITTING | ABORTING -> COMMITTED | ABORTED
    Participant: INIT -> PREPARED | FAILED -> PRE_COMMITTED | ABORTED -> COMMITTED
"""

from __future__ import annotations

import enum
import time as time_mod
from collections.abc import Callable
from dataclasses import dataclass, field

# ── type aliases ──────────────────────────────────────────────────────────────

ParticipantId = str
TransactionId = str


# ── protocol enums ────────────────────────────────────────────────────────────


class CoordinatorState(enum.StrEnum):
    """Represent coordinator states for two- and three-phase commit."""

    INIT = "init"
    PREPARING = "preparing"
    PRE_COMMITTING = "pre_committing"
    COMMITTING = "committing"
    ABORTING = "aborting"
    COMMITTED = "committed"
    ABORTED = "aborted"


class ParticipantState(enum.StrEnum):
    """Represent participant states for two- and three-phase commit."""

    INIT = "init"
    PREPARED = "prepared"
    PRE_COMMITTED = "pre_committed"
    FAILED = "failed"
    COMMITTED = "committed"
    ABORTED = "aborted"


class Vote(enum.StrEnum):
    """Represent a participant's prepare vote."""

    YES = "yes"
    NO = "no"


class Protocol(enum.StrEnum):
    """Select the distributed commit protocol."""

    TWO_PC = "two_pc"
    THREE_PC = "three_pc"


# ── messages ──────────────────────────────────────────────────────────────────


@dataclass
class PrepareRequest:
    """Request a participant's vote for a transaction."""

    transaction_id: TransactionId
    coordinator_id: str


@dataclass
class PrepareResponse:
    """Carry a participant's prepare vote."""

    transaction_id: TransactionId
    participant_id: ParticipantId
    vote: Vote


@dataclass
class CommitRequest:
    """Request transaction commit."""

    transaction_id: TransactionId


@dataclass
class AbortRequest:
    """Request transaction rollback."""

    transaction_id: TransactionId


@dataclass
class PreCommitRequest:
    """Request the three-phase pre-commit transition."""

    transaction_id: TransactionId


@dataclass
class PreCommitResponse:
    """Carry a participant's pre-commit acknowledgement."""

    transaction_id: TransactionId
    participant_id: ParticipantId
    ack: bool


@dataclass
class AckResponse:
    """Acknowledge a commit or abort request."""

    transaction_id: TransactionId
    participant_id: ParticipantId


# ── configuration ─────────────────────────────────────────────────────────────


@dataclass
class TwoPCConfig:
    """Configure commit protocol selection and phase timeouts."""

    prepare_timeout: float = 5.0
    commit_timeout: float = 5.0
    pre_commit_timeout: float = 5.0
    protocol: Protocol = Protocol.TWO_PC


# ── participant ───────────────────────────────────────────────────────────────


@dataclass
class Participant:
    """Model one participant in a distributed transaction."""

    participant_id: ParticipantId
    state: ParticipantState = ParticipantState.INIT
    current_transaction_id: TransactionId | None = None
    _prepare_should_fail: bool = False
    _commit_should_fail: bool = False
    _lag_seconds: float = 0.0
    _on_commit: Callable[[TransactionId], None] | None = None
    _on_abort: Callable[[TransactionId], None] | None = None
    _on_prepare: Callable[[Participant], Vote] | None = None
    _on_pre_commit: Callable[[Participant], bool] | None = None

    def handle_prepare(self, req: PrepareRequest) -> PrepareResponse:
        """Process a prepare request and return this participant's vote."""
        if self._lag_seconds > 0:
            time_mod.sleep(self._lag_seconds)

        vote = Vote.NO if self._prepare_should_fail else Vote.YES
        if vote == Vote.YES and self._on_prepare is not None:
            vote = self._on_prepare(self)
        self.state = ParticipantState.PREPARED if vote == Vote.YES else ParticipantState.FAILED
        if vote == Vote.YES:
            self.current_transaction_id = req.transaction_id
        return PrepareResponse(
            transaction_id=req.transaction_id,
            participant_id=self.participant_id,
            vote=vote,
        )

    def handle_commit(self, req: CommitRequest) -> AckResponse:
        """Commit a prepared transaction and acknowledge the request."""
        if not self._commit_should_fail:
            self.state = ParticipantState.COMMITTED
            if self._on_commit is not None:
                self._on_commit(req.transaction_id)
        return AckResponse(
            transaction_id=req.transaction_id,
            participant_id=self.participant_id,
        )

    def handle_abort(self, req: AbortRequest) -> AckResponse:
        """Abort a transaction and acknowledge the request."""
        self.state = ParticipantState.ABORTED
        if self._on_abort is not None:
            self._on_abort(req.transaction_id)
        return AckResponse(
            transaction_id=req.transaction_id,
            participant_id=self.participant_id,
        )

    def handle_pre_commit(self, req: PreCommitRequest) -> PreCommitResponse:
        """Process the three-phase pre-commit transition."""
        ack = self._on_pre_commit(self) if self._on_pre_commit is not None else True
        self.state = ParticipantState.PRE_COMMITTED if ack else ParticipantState.ABORTED
        return PreCommitResponse(
            transaction_id=req.transaction_id,
            participant_id=self.participant_id,
            ack=ack,
        )

    def reset(self) -> None:
        """Restore the participant to its initial state."""
        self.state = ParticipantState.INIT
        self.current_transaction_id = None

    def sim_crash(self) -> None:
        """Simulate participant failure."""
        self.state = ParticipantState.FAILED

    def sim_recover(self) -> ParticipantState:
        """Recover participant state from retained transaction context."""
        if self.state == ParticipantState.FAILED:
            self.state = ParticipantState.INIT
        if self.current_transaction_id is not None:
            self.state = ParticipantState.PREPARED
        return self.state


# ── coordinator ───────────────────────────────────────────────────────────────


@dataclass
class Coordinator:
    """Coordinate participants through two- or three-phase commit."""

    coordinator_id: str
    participants: dict[ParticipantId, Participant] = field(default_factory=dict)
    config: TwoPCConfig = field(default_factory=TwoPCConfig)
    state: CoordinatorState = CoordinatorState.INIT
    current_transaction_id: TransactionId | None = None
    _clock: Callable[[], float] = field(default=time_mod.monotonic)
    _crash_after_prepare: bool = False
    _crash_after_pre_commit: bool = False
    _crash_after_commit: bool = False
    _skip_prepare_to: ParticipantId | None = None

    def register_participant(self, participant: Participant) -> None:
        """Register or replace a participant by identifier."""
        self.participants[participant.participant_id] = participant

    def _broadcast_prepare(self, transaction_id: TransactionId) -> dict[ParticipantId, Vote]:
        self.state = CoordinatorState.PREPARING
        self.current_transaction_id = transaction_id

        votes: dict[ParticipantId, Vote] = {}
        req = PrepareRequest(transaction_id=transaction_id, coordinator_id=self.coordinator_id)

        start = self._clock()
        for pid, participant in list(self.participants.items()):
            if self._skip_prepare_to == pid or self._clock() - start > self.config.prepare_timeout:
                votes[pid] = Vote.NO
                continue
            votes[pid] = participant.handle_prepare(req).vote

        return votes

    def _all_voted_yes(self, votes: dict[ParticipantId, Vote], total_expected: int) -> bool:
        return len(votes) == total_expected and all(v == Vote.YES for v in votes.values())

    def execute_transaction(self, transaction_id: TransactionId) -> CoordinatorState:
        """Execute the configured commit protocol for a transaction."""
        self.reset()

        expected_count = len(self.participants)
        skipped_set = {self._skip_prepare_to} if self._skip_prepare_to else set()
        expected_voters = expected_count - len(skipped_set)

        votes = self._broadcast_prepare(transaction_id)

        if self._crash_after_prepare:
            self.state = CoordinatorState.PREPARING
            return self.state

        if self.config.protocol == Protocol.THREE_PC:
            return self._execute_three_pc(transaction_id, votes, expected_voters)
        return self._execute_two_pc(transaction_id, votes, expected_voters)

    def _execute_two_pc(
        self,
        transaction_id: TransactionId,
        votes: dict[ParticipantId, Vote],
        expected_total: int,
    ) -> CoordinatorState:
        if self._all_voted_yes(votes, expected_total):
            self.state = CoordinatorState.COMMITTING
            for pid in self.participants:
                if pid not in votes:
                    continue
                self.participants[pid].handle_commit(CommitRequest(transaction_id=transaction_id))

            if self._crash_after_commit:
                return self.state

            self.state = CoordinatorState.COMMITTED
        else:
            self.state = CoordinatorState.ABORTING
            for pid in self.participants:
                self.participants[pid].handle_abort(AbortRequest(transaction_id=transaction_id))
            self.state = CoordinatorState.ABORTED

        return self.state

    def _execute_three_pc(
        self,
        transaction_id: TransactionId,
        votes: dict[ParticipantId, Vote],
        expected_total: int,
    ) -> CoordinatorState:
        if not self._all_voted_yes(votes, expected_total):
            self.state = CoordinatorState.ABORTING
            for pid in self.participants:
                self.participants[pid].handle_abort(AbortRequest(transaction_id=transaction_id))
            self.state = CoordinatorState.ABORTED
            return self.state

        self.state = CoordinatorState.PRE_COMMITTING
        pre_commit_req = PreCommitRequest(transaction_id=transaction_id)
        pre_commit_acks: dict[ParticipantId, bool] = {}

        start = self._clock()
        for pid in self.participants:
            if pid not in votes:
                continue
            elapsed = self._clock() - start
            if elapsed > self.config.pre_commit_timeout:
                pre_commit_acks[pid] = False
                continue
            resp = self.participants[pid].handle_pre_commit(pre_commit_req)
            pre_commit_acks[pid] = resp.ack

        if self._crash_after_pre_commit:
            return self.state

        if all(pre_commit_acks.values()) and len(pre_commit_acks) == expected_total:
            self.state = CoordinatorState.COMMITTING
            for pid in self.participants:
                if pid in votes:
                    self.participants[pid].handle_commit(CommitRequest(transaction_id=transaction_id))
            self.state = CoordinatorState.COMMITTED
        else:
            self.state = CoordinatorState.ABORTING
            for pid in self.participants:
                self.participants[pid].handle_abort(AbortRequest(transaction_id=transaction_id))
            self.state = CoordinatorState.ABORTED

        return self.state

    def recover(self) -> CoordinatorState:
        """Recover a transaction from the participants' durable states."""
        txid = self.current_transaction_id
        if txid is None:
            self.state = CoordinatorState.INIT
            return self.state

        participant_states = [p.state for p in self.participants.values()]

        if any(s == ParticipantState.COMMITTED for s in participant_states):
            for p in self.participants.values():
                if p.state in (ParticipantState.PREPARED, ParticipantState.PRE_COMMITTED):
                    p.handle_commit(CommitRequest(transaction_id=txid))
            self.state = CoordinatorState.COMMITTED

        elif any(s == ParticipantState.ABORTED for s in participant_states):
            self.state = CoordinatorState.ABORTING
            for p in self.participants.values():
                if p.state not in (ParticipantState.COMMITTED, ParticipantState.ABORTED):
                    p.handle_abort(AbortRequest(transaction_id=txid))
            self.state = CoordinatorState.ABORTED

        elif any(s == ParticipantState.PRE_COMMITTED for s in participant_states):
            self.state = CoordinatorState.PRE_COMMITTING
            pre_commit_req = PreCommitRequest(transaction_id=txid)
            for p in self.participants.values():
                if p.state == ParticipantState.PREPARED:
                    p.handle_pre_commit(pre_commit_req)
            self.state = CoordinatorState.COMMITTING
            for p in self.participants.values():
                if p.state in (ParticipantState.PREPARED, ParticipantState.PRE_COMMITTED):
                    p.handle_commit(CommitRequest(transaction_id=txid))
            self.state = CoordinatorState.COMMITTED

        elif any(s == ParticipantState.PREPARED for s in participant_states):
            self.state = CoordinatorState.COMMITTING
            for p in self.participants.values():
                if p.state == ParticipantState.PREPARED:
                    p.handle_commit(CommitRequest(transaction_id=txid))
            self.state = CoordinatorState.COMMITTED

        else:
            self.state = CoordinatorState.ABORTING
            for p in self.participants.values():
                p.handle_abort(AbortRequest(transaction_id=txid))
            self.state = CoordinatorState.ABORTED

        return self.state

    def reset(self) -> None:
        """Reset the coordinator and every registered participant."""
        self.state = CoordinatorState.INIT
        self.current_transaction_id = None
        for p in self.participants.values():
            p.reset()

    def sim_crash(self) -> None:
        """Simulate a coordinator crash during preparation."""
        self.state = CoordinatorState.PREPARING
