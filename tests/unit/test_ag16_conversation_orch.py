"""Unit tests for AG.13/16: Conversation-Driven Orchestration primitives.

Tests Turn, Conversation, SpeakerSelector variants, TerminationCondition
variants, and the ChatOrchestrator conversation loop.
"""

from __future__ import annotations

import pytest

from general_ludd.ag16_orchestration.conversation import (
    CompositeTermination,
    Conversation,
    KeywordTermination,
    MaxTurnsTermination,
    PrioritySelector,
    RoundRobinSelector,
    Turn,
    TurnKind,
)
from general_ludd.ag16_orchestration.orchestrator import ChatOrchestrator


class TestTurn:
    def test_turn_construction_defaults(self):
        t = Turn(speaker="agent-1", kind=TurnKind.DELEGATE)
        assert t.speaker == "agent-1"
        assert t.kind == TurnKind.DELEGATE
        assert t.payload is None
        assert t.targets == []
        assert t.turn_index == 0
        assert t.metadata == {}

    def test_turn_construction_full(self):
        t = Turn(
            speaker="agent-2",
            kind=TurnKind.REQUEST,
            payload={"query": "status"},
            targets=["agent-1"],
            metadata={"priority": "high"},
        )
        assert t.speaker == "agent-2"
        assert t.kind == TurnKind.REQUEST
        assert t.payload == {"query": "status"}
        assert t.targets == ["agent-1"]
        assert t.metadata == {"priority": "high"}


class TestConversation:
    def test_empty_conversation(self):
        conv = Conversation(participants=["a", "b"])
        assert conv.participants == ["a", "b"]
        assert conv.turn_count == 0
        assert conv.history == []
        assert conv.last_turn() is None

    def test_add_turn_increments_count(self):
        conv = Conversation(participants=["a", "b"])
        conv.add_turn(Turn(speaker="a", kind=TurnKind.BROADCAST))
        assert conv.turn_count == 1

    def test_turn_index_assigned_on_add(self):
        conv = Conversation(participants=["a"])
        t1 = Turn(speaker="a", kind=TurnKind.DELEGATE)
        t2 = Turn(speaker="a", kind=TurnKind.REPORT)
        conv.add_turn(t1)
        conv.add_turn(t2)
        assert t1.turn_index == 0
        assert t2.turn_index == 1

    def test_last_turn_returns_most_recent(self):
        conv = Conversation(participants=["a", "b"])
        conv.add_turn(Turn(speaker="a", kind=TurnKind.BROADCAST))
        conv.add_turn(Turn(speaker="b", kind=TurnKind.REPORT, payload="done"))
        last = conv.last_turn()
        assert last is not None
        assert last.speaker == "b"
        assert last.payload == "done"

    def test_turns_by_speaker_filters_correctly(self):
        conv = Conversation(participants=["a", "b"])
        conv.add_turn(Turn(speaker="a", kind=TurnKind.DELEGATE))
        conv.add_turn(Turn(speaker="b", kind=TurnKind.REPORT))
        conv.add_turn(Turn(speaker="a", kind=TurnKind.TERMINATE))
        a_turns = conv.turns_by_speaker("a")
        assert len(a_turns) == 2
        assert all(t.speaker == "a" for t in a_turns)
        b_turns = conv.turns_by_speaker("b")
        assert len(b_turns) == 1

    def test_turns_by_speaker_unknown_returns_empty(self):
        conv = Conversation(participants=["a"])
        conv.add_turn(Turn(speaker="a", kind=TurnKind.DELEGATE))
        assert conv.turns_by_speaker("nonexistent") == []


class TestRoundRobinSelector:
    def test_round_robin_cycles_through_participants(self):
        conv = Conversation(participants=["a", "b", "c"])
        sel = RoundRobinSelector()
        assert sel.select_next(conv) == "a"
        assert sel.select_next(conv) == "b"
        assert sel.select_next(conv) == "c"
        assert sel.select_next(conv) == "a"

    def test_round_robin_single_participant(self):
        conv = Conversation(participants=["only"])
        sel = RoundRobinSelector()
        assert sel.select_next(conv) == "only"
        assert sel.select_next(conv) == "only"

    def test_round_robin_empty_participants_raises(self):
        conv = Conversation(participants=[])
        sel = RoundRobinSelector()
        with pytest.raises(ValueError, match="No participants"):
            sel.select_next(conv)


class TestPrioritySelector:
    def test_priority_selector_follows_order(self):
        conv = Conversation(participants=["a", "b"])
        sel = PrioritySelector(priority_order=["b", "a"])
        assert sel.select_next(conv) == "b"
        assert sel.select_next(conv) == "a"
        assert sel.select_next(conv) == "b"

    def test_priority_selector_empty_order_raises(self):
        conv = Conversation(participants=["a"])
        sel = PrioritySelector(priority_order=[])
        with pytest.raises(ValueError, match="No priority order"):
            sel.select_next(conv)


class TestMaxTurnsTermination:
    def test_terminates_after_max_turns(self):
        cond = MaxTurnsTermination(max_turns=3)
        conv = Conversation(participants=["a"])
        assert not cond.should_terminate(conv)
        conv.add_turn(Turn(speaker="a", kind=TurnKind.DELEGATE))
        conv.add_turn(Turn(speaker="a", kind=TurnKind.DELEGATE))
        assert not cond.should_terminate(conv)
        conv.add_turn(Turn(speaker="a", kind=TurnKind.DELEGATE))
        assert cond.should_terminate(conv)

    def test_max_turns_zero_raises(self):
        with pytest.raises(ValueError, match="max_turns must be >= 1"):
            MaxTurnsTermination(max_turns=0)

    def test_max_turns_negative_raises(self):
        with pytest.raises(ValueError, match="max_turns must be >= 1"):
            MaxTurnsTermination(max_turns=-1)


class TestKeywordTermination:
    def test_terminate_turn_kind_triggers(self):
        cond = KeywordTermination(keywords=["done"])
        conv = Conversation(participants=["a"])
        conv.add_turn(Turn(speaker="a", kind=TurnKind.TERMINATE))
        assert cond.should_terminate(conv)

    def test_keyword_in_payload_triggers(self):
        cond = KeywordTermination(keywords=["complete", "finished"])
        conv = Conversation(participants=["a"])
        conv.add_turn(Turn(speaker="a", kind=TurnKind.REPORT, payload="Task is finished"))
        assert cond.should_terminate(conv)

    def test_no_keyword_no_terminate(self):
        cond = KeywordTermination(keywords=["done"])
        conv = Conversation(participants=["a"])
        conv.add_turn(Turn(speaker="a", kind=TurnKind.REPORT, payload="Working on it"))
        assert not cond.should_terminate(conv)

    def test_empty_conversation_no_terminate(self):
        cond = KeywordTermination(keywords=["done"])
        conv = Conversation(participants=["a"])
        assert not cond.should_terminate(conv)

    def test_none_payload_no_terminate(self):
        cond = KeywordTermination(keywords=["done"])
        conv = Conversation(participants=["a"])
        conv.add_turn(Turn(speaker="a", kind=TurnKind.REPORT, payload=None))
        assert not cond.should_terminate(conv)


class TestCompositeTermination:
    def test_any_mode_terminates_on_first_match(self):
        c1 = MaxTurnsTermination(max_turns=1)
        c2 = MaxTurnsTermination(max_turns=100)
        comp = CompositeTermination([c1, c2], mode="any")
        conv = Conversation(participants=["a"])
        conv.add_turn(Turn(speaker="a", kind=TurnKind.DELEGATE))
        assert comp.should_terminate(conv)

    def test_all_mode_requires_both(self):
        conv = Conversation(participants=["a"])
        kw = KeywordTermination(keywords=["done"])
        mt = MaxTurnsTermination(max_turns=1)
        comp = CompositeTermination([kw, mt], mode="all")
        assert not comp.should_terminate(conv)
        conv.add_turn(Turn(speaker="a", kind=TurnKind.REPORT, payload="done"))
        assert comp.should_terminate(conv)

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError, match="mode must be"):
            CompositeTermination([], mode="invalid")


class TestChatOrchestrator:
    @pytest.mark.asyncio
    async def test_single_agent_round_robin_terminates_on_max_turns(self):
        conv = Conversation(participants=["agent-a"])
        selector = RoundRobinSelector()
        termination = MaxTurnsTermination(max_turns=3)

        def agent_a(prev_turn):
            return Turn(speaker="agent-a", kind=TurnKind.REPORT, payload="step")

        orch = ChatOrchestrator(
            conversation=conv,
            speaker_selector=selector,
            termination=termination,
            agents={"agent-a": agent_a},
        )
        turns = [t async for t in orch.run()]
        assert len(turns) == 3
        for t in turns:
            assert t.speaker == "agent-a"
            assert t.kind == TurnKind.REPORT

    @pytest.mark.asyncio
    async def test_multi_agent_round_robin(self):
        conv = Conversation(participants=["planner", "coder", "reviewer"])
        selector = RoundRobinSelector()
        termination = MaxTurnsTermination(max_turns=6)

        def make_agent(name: str):
            def agent(prev_turn):
                return Turn(speaker=name, kind=TurnKind.REPORT, payload=f"{name}-msg")
            return agent

        orch = ChatOrchestrator(
            conversation=conv,
            speaker_selector=selector,
            termination=termination,
            agents={
                "planner": make_agent("planner"),
                "coder": make_agent("coder"),
                "reviewer": make_agent("reviewer"),
            },
        )
        turns = [t async for t in orch.run()]
        assert len(turns) == 6
        speakers = [t.speaker for t in turns]
        assert speakers == ["planner", "coder", "reviewer", "planner", "coder", "reviewer"]

    @pytest.mark.asyncio
    async def test_terminate_turn_stops_early(self):
        conv = Conversation(participants=["a", "b"])
        selector = RoundRobinSelector()
        termination = MaxTurnsTermination(max_turns=10)

        call_count = {"a": 0, "b": 0}

        def agent_a(prev_turn):
            call_count["a"] += 1
            if call_count["a"] >= 2:
                return Turn(speaker="a", kind=TurnKind.TERMINATE)
            return Turn(speaker="a", kind=TurnKind.DELEGATE, payload="work")

        def agent_b(prev_turn):
            call_count["b"] += 1
            return Turn(speaker="b", kind=TurnKind.REPORT, payload="done")

        orch = ChatOrchestrator(
            conversation=conv,
            speaker_selector=selector,
            termination=termination,
            agents={"a": agent_a, "b": agent_b},
        )
        turns = [t async for t in orch.run()]
        assert len(turns) == 3
        assert turns[-1].kind == TurnKind.TERMINATE
        assert call_count["a"] == 2
        assert call_count["b"] == 1

    @pytest.mark.asyncio
    async def test_seed_turn_added_first(self):
        conv = Conversation(participants=["a"])
        selector = RoundRobinSelector()
        termination = MaxTurnsTermination(max_turns=2)

        def agent_a(prev_turn):
            return Turn(speaker="a", kind=TurnKind.REPORT, payload="response")

        orch = ChatOrchestrator(
            conversation=conv,
            speaker_selector=selector,
            termination=termination,
            agents={"a": agent_a},
        )
        seed = Turn(speaker="human", kind=TurnKind.REQUEST, payload="start")
        turns = [t async for t in orch.run(seed_turn=seed)]
        assert len(turns) == 2
        assert turns[0].speaker == "human"
        assert turns[0].payload == "start"
        assert turns[1].speaker == "a"

    @pytest.mark.asyncio
    async def test_unregistered_speaker_skipped(self):
        conv = Conversation(participants=["a", "b", "c"])
        selector = RoundRobinSelector()
        termination = MaxTurnsTermination(max_turns=4)

        def agent_a(prev_turn):
            return Turn(speaker="a", kind=TurnKind.REPORT, payload="a-msg")

        orch = ChatOrchestrator(
            conversation=conv,
            speaker_selector=selector,
            termination=termination,
            agents={"a": agent_a},
        )
        turns = [t async for t in orch.run()]
        assert len(turns) == 4
        for t in turns:
            assert t.speaker == "a"

    @pytest.mark.asyncio
    async def test_empty_participants_no_turns(self):
        conv = Conversation(participants=[])
        selector = RoundRobinSelector()
        termination = MaxTurnsTermination(max_turns=5)

        orch = ChatOrchestrator(
            conversation=conv,
            speaker_selector=selector,
            termination=termination,
            agents={},
        )
        turns = [t async for t in orch.run()]
        assert len(turns) == 0

    @pytest.mark.asyncio
    async def test_keyword_termination_ends_conversation(self):
        conv = Conversation(participants=["a"])
        selector = RoundRobinSelector()
        termination = KeywordTermination(keywords=["finished"])

        call_count = 0

        def agent_a(prev_turn):
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                return Turn(speaker="a", kind=TurnKind.REPORT, payload="all finished")
            return Turn(speaker="a", kind=TurnKind.REPORT, payload="working")

        orch = ChatOrchestrator(
            conversation=conv,
            speaker_selector=selector,
            termination=termination,
            agents={"a": agent_a},
        )
        turns = [t async for t in orch.run()]
        assert len(turns) == 3
        assert turns[-1].payload == "all finished"
