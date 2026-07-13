"""AG.13/16: Conversation primitives — turns, speaker selection, termination."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class TurnKind(StrEnum):
    DELEGATE = "DELEGATE"
    REPORT = "REPORT"
    REQUEST = "REQUEST"
    BROADCAST = "BROADCAST"
    TERMINATE = "TERMINATE"


@dataclass
class Turn:
    speaker: str
    kind: TurnKind
    payload: Any = None
    targets: list[str] = field(default_factory=list)
    turn_index: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Conversation:
    participants: list[str]
    history: list[Turn] = field(default_factory=list)
    _speaker_index: int = 0

    @property
    def turn_count(self) -> int:
        return len(self.history)

    def add_turn(self, turn: Turn) -> None:
        turn.turn_index = self.turn_count
        self.history.append(turn)

    def last_turn(self) -> Turn | None:
        return self.history[-1] if self.history else None

    def turns_by_speaker(self, speaker: str) -> list[Turn]:
        return [t for t in self.history if t.speaker == speaker]


class SpeakerSelector(ABC):
    @abstractmethod
    def select_next(self, conversation: Conversation) -> str:
        ...


class RoundRobinSelector(SpeakerSelector):
    def __init__(self, start_index: int = 0) -> None:
        self._index = start_index

    def select_next(self, conversation: Conversation) -> str:
        if not conversation.participants:
            raise ValueError("No participants in conversation")
        speaker = conversation.participants[self._index % len(conversation.participants)]
        self._index += 1
        return speaker


class PrioritySelector(SpeakerSelector):
    def __init__(self, priority_order: list[str]) -> None:
        self._order = priority_order
        self._index = 0

    def select_next(self, conversation: Conversation) -> str:
        if not self._order:
            raise ValueError("No priority order configured")
        speaker = self._order[self._index % len(self._order)]
        self._index += 1
        return speaker


class TerminationCondition(ABC):
    @abstractmethod
    def should_terminate(self, conversation: Conversation) -> bool:
        ...


class MaxTurnsTermination(TerminationCondition):
    def __init__(self, max_turns: int) -> None:
        if max_turns < 1:
            raise ValueError("max_turns must be >= 1")
        self._max_turns = max_turns

    def should_terminate(self, conversation: Conversation) -> bool:
        return conversation.turn_count >= self._max_turns


class KeywordTermination(TerminationCondition):
    def __init__(self, keywords: list[str]) -> None:
        self._keywords = keywords

    def should_terminate(self, conversation: Conversation) -> bool:
        last = conversation.last_turn()
        if last is None:
            return False
        if last.kind == TurnKind.TERMINATE:
            return True
        payload_str = str(last.payload).lower() if last.payload else ""
        return any(kw.lower() in payload_str for kw in self._keywords)


class CompositeTermination(TerminationCondition):
    def __init__(self, conditions: list[TerminationCondition], mode: str = "any") -> None:
        if mode not in ("any", "all"):
            raise ValueError("mode must be 'any' or 'all'")
        self._conditions = conditions
        self._mode = mode

    def should_terminate(self, conversation: Conversation) -> bool:
        results = [c.should_terminate(conversation) for c in self._conditions]
        return any(results) if self._mode == "any" else all(results)
