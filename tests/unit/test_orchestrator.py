"""Unit tests for ChatOrchestrator."""

from __future__ import annotations

from general_ludd.ag16_orchestration.conversation import (
    Conversation,
    RoundRobinSelector,
    Turn,
    TurnKind,
)
from general_ludd.ag16_orchestration.orchestrator import ChatOrchestrator


class AlwaysTerminate:
    def should_terminate(self, conversation: Conversation) -> bool:
        return True


class NeverTerminate:
    def should_terminate(self, conversation: Conversation) -> bool:
        return False


class FixedSelector:
    def __init__(self, speakers: list[str]) -> None:
        self._speakers = speakers
        self._i = 0

    def select_next(self, conversation: Conversation) -> str:
        s = self._speakers[self._i % len(self._speakers)]
        self._i += 1
        return s


class TestChatOrchestrator:
    def test_initialization(self) -> None:
        conv = Conversation(participants=["agent_a"])
        selector = RoundRobinSelector()
        termination = NeverTerminate()
        orch = ChatOrchestrator(conv, selector, termination)

        assert orch.conversation is conv

    def test_register_agent(self) -> None:
        conv = Conversation(participants=["agent_a"])
        selector = FixedSelector(["agent_a"])
        termination = NeverTerminate()

        orch = ChatOrchestrator(conv, selector, termination)

        def agent_fn(turn: Turn | None) -> Turn:
            return Turn(speaker="agent_a", kind=TurnKind.TERMINATE)

        orch.register_agent("agent_a", agent_fn)
        assert "agent_a" in orch._agents

    async def test_seed_turn_yielded(self) -> None:
        conv = Conversation(participants=["agent_a"])
        selector = FixedSelector(["agent_a"])
        termination = AlwaysTerminate()

        orch = ChatOrchestrator(conv, selector, termination)

        def agent_fn(turn: Turn | None) -> Turn:
            return Turn(speaker="agent_a", kind=TurnKind.TERMINATE)

        orch.register_agent("agent_a", agent_fn)
        seed = Turn(speaker="user", kind=TurnKind.REQUEST, payload="hello")

        turns = [t async for t in orch.run(seed_turn=seed)]
        assert len(turns) == 1
        assert turns[0] is seed

    async def test_seed_turn_added_to_conversation(self) -> None:
        conv = Conversation(participants=["agent_a"])
        selector = FixedSelector(["agent_a"])
        termination = AlwaysTerminate()

        orch = ChatOrchestrator(conv, selector, termination)
        seed = Turn(speaker="user", kind=TurnKind.REQUEST, payload="hello")

        _ = [t async for t in orch.run(seed_turn=seed)]
        assert conv.turn_count == 1

    async def test_termination_stops_loop(self) -> None:
        conv = Conversation(participants=["agent_a"])
        selector = FixedSelector(["agent_a"])
        termination = AlwaysTerminate()

        orch = ChatOrchestrator(conv, selector, termination)

        def agent_fn(turn: Turn | None) -> Turn:
            return Turn(speaker="agent_a", kind=TurnKind.TERMINATE)

        orch.register_agent("agent_a", agent_fn)
        turns = [t async for t in orch.run()]
        assert len(turns) == 0

    async def test_termination_error_handled(self) -> None:
        conv = Conversation(participants=["agent_a"])

        class BrokenTermination:
            def should_terminate(self, conversation: Conversation) -> bool:
                raise RuntimeError("boom")

        selector = FixedSelector(["agent_a"])
        orch = ChatOrchestrator(conv, selector, BrokenTermination())

        def agent_fn(turn: Turn | None) -> Turn:
            return Turn(speaker="agent_a", kind=TurnKind.TERMINATE)

        orch.register_agent("agent_a", agent_fn)
        turns = [t async for t in orch.run()]
        assert len(turns) == 0

    async def test_speaker_not_in_participants_skipped(self) -> None:
        conv = Conversation(participants=["agent_a"])
        selector = FixedSelector(["agent_b", "agent_a"])
        termination = NeverTerminate()

        orch = ChatOrchestrator(conv, selector, termination)
        counter: list[int] = [0]

        def agent_fn(turn: Turn | None) -> Turn:
            counter[0] += 1
            return Turn(speaker="agent_a", kind=TurnKind.TERMINATE)

        orch.register_agent("agent_a", agent_fn)
        turns = [t async for t in orch.run()]
        assert len(turns) == 1
        assert counter[0] == 1

    async def test_agent_not_registered_skipped(self) -> None:
        conv = Conversation(participants=["agent_a", "agent_b"])
        selector = RoundRobinSelector()
        termination = NeverTerminate()

        orch = ChatOrchestrator(conv, selector, termination, max_iterations=3)

        def agent_fn(turn: Turn | None) -> Turn:
            return Turn(speaker="agent_a", kind=TurnKind.TERMINATE)

        orch.register_agent("agent_a", agent_fn)
        turns = [t async for t in orch.run()]
        assert len(turns) <= 3

    async def test_turn_kind_terminate_stops_early(self) -> None:
        conv = Conversation(participants=["agent_a"])
        selector = FixedSelector(["agent_a"])
        termination = NeverTerminate()

        orch = ChatOrchestrator(conv, selector, termination)

        def agent_fn(turn: Turn | None) -> Turn:
            return Turn(speaker="agent_a", kind=TurnKind.TERMINATE)

        orch.register_agent("agent_a", agent_fn)
        turns = [t async for t in orch.run()]
        assert len(turns) == 1
        assert turns[0].kind == TurnKind.TERMINATE

    async def test_max_iterations_enforced(self) -> None:
        conv = Conversation(participants=["agent_a"])
        selector = FixedSelector(["agent_a"])
        termination = NeverTerminate()

        orch = ChatOrchestrator(conv, selector, termination, max_iterations=3)

        def agent_fn(turn: Turn | None) -> Turn:
            return Turn(speaker="agent_a", kind=TurnKind.REPORT, payload="ok")

        orch.register_agent("agent_a", agent_fn)
        turns = [t async for t in orch.run()]
        assert len(turns) == 3

    async def test_multiple_agents_round_robin(self) -> None:
        conv = Conversation(participants=["a", "b"])
        selector = RoundRobinSelector()
        termination = NeverTerminate()

        orch = ChatOrchestrator(conv, selector, termination, max_iterations=4)
        order: list[str] = []

        def make_agent(name: str):
            def fn(turn: Turn | None) -> Turn:
                order.append(name)
                return Turn(speaker=name, kind=TurnKind.REPORT, payload=f"from {name}")
            return fn

        orch.register_agent("a", make_agent("a"))
        orch.register_agent("b", make_agent("b"))
        _ = [t async for t in orch.run()]
        assert len(order) == 4
        assert order[:2] == ["a", "b"]

    async def test_agent_receives_prior_turn(self) -> None:
        conv = Conversation(participants=["agent_a"])
        selector = FixedSelector(["agent_a"])
        termination = NeverTerminate()

        orch = ChatOrchestrator(conv, selector, termination)
        received: list[Turn | None] = []

        def agent_fn(turn: Turn | None) -> Turn:
            received.append(turn)
            return Turn(speaker="agent_a", kind=TurnKind.TERMINATE)

        orch.register_agent("agent_a", agent_fn)
        seed = Turn(speaker="user", kind=TurnKind.REQUEST, payload="start")
        _ = [t async for t in orch.run(seed_turn=seed)]
        assert received[0] is seed

    async def test_selector_error_handled_gracefully(self) -> None:
        conv = Conversation(participants=["agent_a"])

        class BrokenSelector:
            def select_next(self, conversation: Conversation) -> str:
                raise ValueError("no speakers")

        orch = ChatOrchestrator(conv, BrokenSelector(), NeverTerminate())

        def agent_fn(turn: Turn | None) -> Turn:
            return Turn(speaker="agent_a", kind=TurnKind.TERMINATE)

        orch.register_agent("agent_a", agent_fn)
        turns = [t async for t in orch.run()]
        assert len(turns) == 0

    async def test_empty_agents_no_turns(self) -> None:
        conv = Conversation(participants=["agent_a"])
        selector = FixedSelector(["agent_a"])
        termination = NeverTerminate()

        orch = ChatOrchestrator(conv, selector, termination, max_iterations=2)
        turns = [t async for t in orch.run()]
        assert len(turns) == 0

    async def test_conversation_participants_detected(self) -> None:
        conv = Conversation(participants=["bot1", "bot2"])
        selector = FixedSelector(["bot1"])
        termination = NeverTerminate()

        orch = ChatOrchestrator(conv, selector, termination, max_iterations=2)

        def agent_fn(turn: Turn | None) -> Turn:
            return Turn(speaker="bot1", kind=TurnKind.TERMINATE)

        orch.register_agent("bot1", agent_fn)
        turns = [t async for t in orch.run()]
        assert len(turns) == 1

    async def test_without_seed_turn_none_passed_to_agent(self) -> None:
        conv = Conversation(participants=["agent_a"])
        selector = FixedSelector(["agent_a"])
        termination = NeverTerminate()

        orch = ChatOrchestrator(conv, selector, termination)
        received: list[Turn | None] = []

        def agent_fn(turn: Turn | None) -> Turn:
            received.append(turn)
            return Turn(speaker="agent_a", kind=TurnKind.TERMINATE)

        orch.register_agent("agent_a", agent_fn)
        _ = [t async for t in orch.run()]
        assert received[0] is None
