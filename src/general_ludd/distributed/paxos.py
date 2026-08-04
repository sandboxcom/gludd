"""Paxos consensus algorithm: Proposer, Acceptor, Learner.

Single-decree and multi-Paxos with Prepare/Accept phases.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProposalID:
    number: int
    proposer_id: str

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, ProposalID):
            return NotImplemented
        return (self.number, self.proposer_id) < (other.number, other.proposer_id)

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, ProposalID):
            return NotImplemented
        return (self.number, self.proposer_id) > (other.number, other.proposer_id)


@dataclass
class PrepareRequest:
    pid: ProposalID


@dataclass
class PrepareResponse:
    promised_id: ProposalID
    promised: bool
    highest_accepted_id: ProposalID | None
    highest_accepted_value: object


@dataclass
class AcceptRequest:
    pid: ProposalID
    value: object
    promised_id: ProposalID | None


@dataclass
class AcceptResponse:
    pid: ProposalID
    accepted: bool


class Acceptor:
    def __init__(self, node_id: str) -> None:
        self.node_id = node_id
        self.minimum_promise: int = -1
        self.accepted_id: ProposalID | None = None
        self.accepted_value: object = None

    def handle_prepare(self, req: PrepareRequest) -> PrepareResponse:
        pid = req.pid
        if pid.number > self.minimum_promise:
            self.minimum_promise = pid.number
            return PrepareResponse(
                promised_id=pid,
                promised=True,
                highest_accepted_id=self.accepted_id,
                highest_accepted_value=self.accepted_value,
            )
        return PrepareResponse(
            promised_id=pid,
            promised=False,
            highest_accepted_id=None,
            highest_accepted_value=None,
        )

    def handle_accept(self, req: AcceptRequest) -> AcceptResponse:
        pid = req.pid
        if pid.number >= self.minimum_promise:
            self.minimum_promise = pid.number
            self.accepted_id = pid
            self.accepted_value = req.value
            return AcceptResponse(pid=pid, accepted=True)
        return AcceptResponse(pid=pid, accepted=False)


class Proposer:
    def __init__(self, node_id: str, quorum_size: int) -> None:
        self.node_id = node_id
        self.quorum_size = quorum_size
        self._proposal_counter: int = 0

    def next_proposal_number(self) -> int:
        self._proposal_counter += 1
        return self._proposal_counter

    def handle_prepare_responses(self, responses: list[PrepareResponse]) -> ProposalID | None:
        promised = [r for r in responses if r.promised]
        if len(promised) < self.quorum_size:
            return None

        highest: ProposalID | None = None
        for r in promised:
            if r.highest_accepted_id is not None and (highest is None or r.highest_accepted_id > highest):
                highest = r.highest_accepted_id
        return highest

    def handle_accept_responses(self, responses: list[AcceptResponse]) -> bool:
        accepted_count = sum(1 for r in responses if r.accepted)
        return accepted_count >= self.quorum_size


class Learner:
    def __init__(self, node_id: str) -> None:
        self.node_id = node_id
        self.learned: list[object] = []

    def handle_accepted(self, req: AcceptRequest) -> None:
        if req.value not in self.learned:
            self.learned.append(req.value)
