"""AG.15 — Reflexion loop core: episode recording, memory, and loop orchestrator.

The pattern follows the try → evaluate → reflect → retry cycle described in
Shinn et al. "Reflexion: Language Agents with Verbal Reinforcement Learning"
(NeurIPS 2023), adapted for the gludd agent framework.

Each attempt at a task produces an EpisodeRecord. A ReflexionMemory aggregates
records across attempts so the next try carries the accumulated feedback.
The ReflexionLoop orchestrates the full cycle with configurable termination
conditions (score threshold, max retries).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class EpisodeRecord:
    """A single attempt at a task with its evaluation and self-critique."""

    episode_id: str
    task_description: str
    actor_output: str
    evaluation_score: float
    reflexion_text: str = ""
    retry_count: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def is_success(self, threshold: float = 0.8) -> bool:
        return self.evaluation_score >= threshold


@dataclass
class ReflexionResult:
    """Terminal outcome of a reflexion loop — either success or max retries exhausted."""

    success: bool
    final_episode: EpisodeRecord
    episodes: list[EpisodeRecord] = field(default_factory=list)
    total_retries: int = 0


class ReflexionMemory:
    """In-memory store for reflexion episodes.

    Accumulates EpisodeRecords across attempts and surfaces recent feedback
    text for injection into the actor's context on retry.
    """

    def __init__(self, max_window: int = 5) -> None:
        self._episodes: list[EpisodeRecord] = []
        self._max_window = max_window

    @property
    def episodes(self) -> tuple[EpisodeRecord, ...]:
        return tuple(self._episodes)

    @property
    def episode_count(self) -> int:
        return len(self._episodes)

    def add(self, episode: EpisodeRecord) -> None:
        self._episodes.append(episode)
        if len(self._episodes) > self._max_window * 2:
            self._episodes = self._episodes[-self._max_window :]

    def recent_feedback(self, n: int = 3) -> list[str]:
        recent = self._episodes[-n:]
        return [ep.reflexion_text for ep in recent if ep.reflexion_text]

    def last_score(self) -> float | None:
        if not self._episodes:
            return None
        return self._episodes[-1].evaluation_score

    def clear(self) -> None:
        self._episodes.clear()


class ReflexionLoop:
    """Orchestrates the try → evaluate → reflect → retry cycle.

    Wraps an actor (generates output) and an evaluator (scores output) in a
    loop that continues until the score meets the threshold or max_retries
    is exhausted. Each iteration records an EpisodeRecord in memory and
    surfaces accumulated feedback to the actor.

    In production, the actor and evaluator are model-calling functions.
    In test, they can be simple callables returning canned responses.
    """

    def __init__(
        self,
        actor: Callable[[str, Sequence[str]], str],
        evaluator: Callable[[str, str], float],
        max_retries: int = 3,
        score_threshold: float = 0.8,
        memory_window: int = 5,
    ) -> None:
        if max_retries < 0:
            raise ValueError(f"max_retries must be >= 0, got {max_retries}")
        if not 0.0 <= score_threshold <= 1.0:
            raise ValueError(f"score_threshold must be 0.0–1.0, got {score_threshold}")

        self._actor = actor
        self._evaluator = evaluator
        self._max_retries = max_retries
        self._score_threshold = score_threshold
        self._memory = ReflexionMemory(max_window=memory_window)
        self._episode_counter = 0

    @property
    def memory(self) -> ReflexionMemory:
        return self._memory

    @property
    def max_retries(self) -> int:
        return self._max_retries

    @property
    def score_threshold(self) -> float:
        return self._score_threshold

    def run(self, task_description: str) -> ReflexionResult:
        episodes: list[EpisodeRecord] = []

        for attempt in range(self._max_retries + 1):
            self._episode_counter += 1
            episode_id = f"ep-{self._episode_counter}"

            feedback = self._memory.recent_feedback()
            actor_output = self._actor(task_description, feedback)

            score = self._evaluator(task_description, actor_output)

            reflexion_text = ""
            if score < self._score_threshold:
                reflexion_text = self._generate_reflexion(
                    task_description, actor_output, score, attempt
                )

            episode = EpisodeRecord(
                episode_id=episode_id,
                task_description=task_description,
                actor_output=actor_output,
                evaluation_score=score,
                reflexion_text=reflexion_text,
                retry_count=attempt,
            )
            episodes.append(episode)
            self._memory.add(episode)

            if episode.is_success(self._score_threshold):
                return ReflexionResult(
                    success=True,
                    final_episode=episode,
                    episodes=episodes,
                    total_retries=attempt,
                )

        return ReflexionResult(
            success=False,
            final_episode=episodes[-1],
            episodes=episodes,
            total_retries=self._max_retries,
        )

    @staticmethod
    def _generate_reflexion(
        task: str,
        output: str,
        score: float,
        attempt: int,
    ) -> str:
        gap = 1.0 - score
        return (
            f"Attempt {attempt + 1}: score {score:.2f} ({gap:.0%} below threshold). "
            f"Output may be incomplete or incorrect. Revise the approach "
            f"and address gaps in reasoning."
        )

    def reset(self) -> None:
        self._memory.clear()
        self._episode_counter = 0


def create_reflexion_loop(
    actor: Callable[[str, Sequence[str]], str],
    evaluator: Callable[[str, str], float],
    max_retries: int = 3,
    score_threshold: float = 0.8,
) -> ReflexionLoop:
    return ReflexionLoop(
        actor=actor,
        evaluator=evaluator,
        max_retries=max_retries,
        score_threshold=score_threshold,
    )
