"""AG.13/16: ChatOrchestrator — drives the conversation loop."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable

from general_ludd.ag16_orchestration.conversation import (
    Conversation,
    SpeakerSelector,
    TerminationCondition,
    Turn,
    TurnKind,
)

AgentFn = Callable[[Turn | None], Turn]


class ChatOrchestrator:
    def __init__(
        self,
        conversation: Conversation,
        speaker_selector: SpeakerSelector,
        termination: TerminationCondition,
        agents: dict[str, AgentFn] | None = None,
        max_iterations: int = 100,
    ) -> None:
        self._conversation = conversation
        self._selector = speaker_selector
        self._termination = termination
        self._agents: dict[str, AgentFn] = agents or {}
        self._max_iterations = max_iterations

    @property
    def conversation(self) -> Conversation:
        return self._conversation

    def register_agent(self, name: str, fn: AgentFn) -> None:
        self._agents[name] = fn

    async def run(self, seed_turn: Turn | None = None) -> AsyncGenerator[Turn, None]:
        if seed_turn is not None:
            self._conversation.add_turn(seed_turn)
            yield seed_turn

        for _ in range(self._max_iterations):
            try:
                if self._termination.should_terminate(self._conversation):
                    return
            except Exception:
                return

            try:
                speaker = self._selector.select_next(self._conversation)
            except (ValueError, IndexError):
                return

            if speaker not in self._participant_set():
                continue

            agent_fn = self._agents.get(speaker)
            if agent_fn is None:
                continue

            previous_turn = self._conversation.last_turn() if self._conversation.history else None
            turn = agent_fn(previous_turn)
            turn.speaker = speaker
            self._conversation.add_turn(turn)
            yield turn

            if turn.kind == TurnKind.TERMINATE:
                return

    def _participant_set(self) -> set[str]:
        return set(self._conversation.participants)
