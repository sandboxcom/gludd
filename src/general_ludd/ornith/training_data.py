"""Training-data collector for Ornith self-improvement pairs.

Captures (instruction, response, outcome) triples from agent interactions
and coordinates the collection pipeline: record invocation, resolve outcome,
export dataset for fine-tuning. Higher-level orchestrator over
:class:`OrnithTrainingRepo` and :class:`OutcomeObserver`.
"""

from __future__ import annotations

import json as _json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from general_ludd.db.models import OrnithTrainingPairModel
from general_ludd.ornith.training_repo import (
    OrnithInvocation,
    OrnithTrainingRepo,
    compute_reward,
)

logger = logging.getLogger(__name__)

#: Statuses considered "resolved" (terminal outcome known).
TERMINAL_STATUSES = frozenset({
    "succeeded",
    "rejected_by_gate",
    "rejected_by_review",
    "reverted",
})

#: Statuses that yield a positive reward (usable for fine-tuning).
POSITIVE_STATUSES = frozenset({"succeeded", "applied"})


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class TrainingExample:
    """A (instruction, response, outcome) triple for fine-tuning.

    Fields mirror the OrnithTrainingPairModel but express the data in
    fine-tuning terminology: ``instruction`` is what was asked (the task),
    ``response`` is what the agent produced (the scaffold), and ``outcome``
    is what happened when it was applied.
    """

    instruction: str
    response: str
    outcome: str
    reward: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "instruction": self.instruction,
            "response": self.response,
            "outcome": self.outcome,
            "reward": self.reward,
            "metadata": self.metadata,
        }

    @classmethod
    def from_pair(
        cls, pair: OrnithTrainingPairModel
    ) -> TrainingExample:
        return cls(
            instruction=pair.task_description,
            response=pair.scaffold_content,
            outcome=pair.outcome_status,
            reward=compute_reward(pair.outcome_status),
            metadata={
                "pair_id": pair.id,
                "scaffold_kind": pair.scaffold_kind,
                "scaffold_hash": pair.scaffold_hash,
                "model_sha": pair.model_sha,
                "agent_id": pair.agent_id,
                "project_id": pair.project_id,
                "iterations_used": pair.iterations_used,
                "tokens_consumed": pair.tokens_consumed,
                "target_files": pair.target_files,
                "invoked_at": pair.invoked_at.isoformat() if pair.invoked_at else None,
                "outcome_details": pair.outcome_details,
            },
        )


class TrainingDataCollector:
    """Orchestrates the collection pipeline for Ornith training data.

    Wraps :class:`OrnithTrainingRepo` with higher-level batch operations,
    data quality checks, and export transformations suitable for
    fine-tuning pipelines.

    Usage::

        collector = TrainingDataCollector(session)
        await collector.capture(
            instruction="Refactor loop.py to use tenacity",
            response="--- a/loop.py\\n+++ b/loop.py\\n...",
            scaffold_kind="patch",
            agent_id="agent-1",
        )
        await collector.resolve_outcome(pair_id, "succeeded")
        out = await collector.export_finetuning_dataset(
            out_path="/tmp/ft.jsonl",
            only_positive=True,
        )
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = OrnithTrainingRepo(session)

    # -- capture ----------------------------------------------------------

    async def capture(
        self,
        instruction: str,
        response: str,
        scaffold_kind: str = "patch",
        agent_id: str = "",
        target_files: list[str] | None = None,
        model_sha: str = "",
        project_id: str | None = None,
        iterations_used: int = 0,
        tokens_consumed: int = 0,
    ) -> OrnithTrainingPairModel:
        """Capture a (instruction, response) pair as a pending training example.

        This is the canonical entry point for recording a training example.
        The outcome starts as ``pending`` and is resolved later via
        :meth:`resolve_outcome` or by the :class:`OutcomeObserver`.

        Parameters
        ----------
        instruction:
            The task description or prompt given to the agent.
        response:
            The scaffold content produced (patch, playbook, module, etc.).
        scaffold_kind:
            Type of artifact (patch, playbook, module, plugin, rego).
        agent_id:
            Identifier of the agent that produced the scaffold.
        target_files:
            File paths the scaffold targets.
        model_sha:
            SHA of the model that produced the scaffold.
        project_id:
            Project scope for the invocation.
        iterations_used:
            Number of iterations the agent used.
        tokens_consumed:
            Token cost of the invocation.
        """
        invocation = OrnithInvocation(
            task_description=instruction,
            target_files=target_files or [],
            scaffold_kind=scaffold_kind,
            scaffold_content=response,
            agent_id=agent_id,
            iterations_used=iterations_used,
            tokens_consumed=tokens_consumed,
            model_sha=model_sha,
            project_id=project_id,
        )
        return await self._repo.record_pair(invocation)

    async def capture_many(
        self,
        examples: Sequence[OrnithInvocation],
    ) -> list[OrnithTrainingPairModel]:
        """Batch-capture multiple training examples in one transaction."""
        rows: list[OrnithTrainingPairModel] = []
        for inv in examples:
            row = await self._repo.record_pair(inv)
            rows.append(row)
        await self._session.flush()
        return rows

    # -- outcome resolution -----------------------------------------------

    async def resolve_outcome(
        self,
        pair_id: str,
        status: str,
        details: dict[str, Any] | None = None,
    ) -> OrnithTrainingPairModel:
        """Resolve the outcome of a training pair.

        Wraps :meth:`OrnithTrainingRepo.set_outcome`.
        """
        return await self._repo.set_outcome(pair_id, status, details)

    async def resolve_pending(
        self,
        older_than_minutes: int = 0,
        limit: int = 100,
    ) -> list[OrnithTrainingPairModel]:
        """Fetch pairs whose outcome is still ``pending``.

        Useful for batch-processing stale or unresolved examples.
        """
        return await self._repo.get_pending_outcomes(
            limit=limit, older_than_minutes=older_than_minutes
        )

    async def batch_resolve(
        self,
        pairs: Sequence[tuple[str, str, dict[str, Any]]],
    ) -> list[OrnithTrainingPairModel]:
        """Resolve multiple outcomes atomically.

        Each element of ``pairs`` is ``(pair_id, status, details_dict)``.
        Runs in a single flush.
        """
        rows: list[OrnithTrainingPairModel] = []
        for pair_id, status, details in pairs:
            row = await self._repo.set_outcome(pair_id, status, details)
            rows.append(row)
        await self._session.flush()
        return rows

    # -- querying ---------------------------------------------------------

    async def get(self, pair_id: str) -> OrnithTrainingPairModel | None:
        return await self._repo.get(pair_id)

    async def list_examples(
        self,
        status: str | None = None,
        project_id: str | None = None,
        limit: int = 100,
    ) -> list[TrainingExample]:
        """List training examples as ``TrainingExample`` objects."""
        pairs = await self._repo.list_pairs(
            status=status, project_id=project_id, limit=limit
        )
        return [TrainingExample.from_pair(p) for p in pairs]

    async def list_by_statuses(
        self,
        statuses: list[str],
        project_id: str | None = None,
        limit: int = 100,
        lookback_days: int = 0,
    ) -> list[TrainingExample]:
        """List examples filtered by outcome statuses."""
        pairs = await self._repo.list_pairs_by_statuses(
            statuses=statuses,
            project_id=project_id,
            limit=limit,
            lookback_days=lookback_days,
        )
        return [TrainingExample.from_pair(p) for p in pairs]

    # -- export -----------------------------------------------------------

    async def export_finetuning_dataset(
        self,
        out_path: str | Path | None = None,
        since: datetime | None = None,
        project_id: str | None = None,
        only_positive: bool = False,
    ) -> Path:
        """Export resolved training examples as a JSONL dataset for fine-tuning.

        Each line is a fine-tuning-ready (instruction, response) pair with
        reward signal. When ``only_positive=True``, only examples with
        positive reward (``succeeded``, ``applied``) are included.

        Returns the path to the written file.
        """
        if out_path is None:
            ts = int(_now().timestamp())
            out_path = Path(".") / f"finetuning-dataset-{ts}.jsonl"

        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        pairs = await self._repo.list_pairs(
            status=None, project_id=project_id, limit=10000
        )

        with out.open("w", encoding="utf-8") as fh:
            for pair in pairs:
                reward = compute_reward(pair.outcome_status)
                if reward is None:
                    continue
                if only_positive and reward <= 0:
                    continue
                example = TrainingExample.from_pair(pair)
                fh.write(_json.dumps(example.to_dict(), default=str) + "\n")

        logger.info("Exported %d training examples to %s", _count_lines(out), out)
        return out

    async def export_rollout_log(
        self,
        out_path: str | Path | None = None,
        limit: int = 1000,
    ) -> Path:
        """Export a chronological rollout log suitable for RL training.

        Unlike ``export_finetuning_dataset``, this includes ALL statuses
        (including pending and negative-reward examples) so the RL trainer
        sees the full rollout distribution.
        """
        if out_path is None:
            ts = int(_now().timestamp())
            out_path = Path(".") / f"rollout-log-{ts}.jsonl"

        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        pairs = await self._repo.list_pairs(limit=limit, status=None)

        with out.open("w", encoding="utf-8") as fh:
            for pair in pairs:
                reward = compute_reward(pair.outcome_status)
                entry = {
                    "pair_id": pair.id,
                    "task_description": pair.task_description,
                    "scaffold_content": pair.scaffold_content,
                    "scaffold_hash": pair.scaffold_hash,
                    "scaffold_kind": pair.scaffold_kind,
                    "model_sha": pair.model_sha,
                    "outcome_status": pair.outcome_status,
                    "reward": reward,
                    "tokens_consumed": pair.tokens_consumed,
                    "iterations_used": pair.iterations_used,
                    "invoked_at": pair.invoked_at.isoformat() if pair.invoked_at else None,
                    "outcome_set_at": pair.outcome_set_at.isoformat() if pair.outcome_set_at else None,
                    "agent_id": pair.agent_id,
                    "project_id": pair.project_id,
                }
                fh.write(_json.dumps(entry, default=str) + "\n")

        logger.info("Exported %d rollout records to %s", _count_lines(out), out)
        return out

    # -- quality / statistics ---------------------------------------------

    async def stats(self) -> dict[str, Any]:
        """Aggregate statistics across all collected pairs."""
        return await self._repo.stats()

    async def quality_report(self) -> dict[str, Any]:
        """Produce a data quality report for the collected dataset.

        Reports:
        - Total pairs collected
        - Count per outcome status
        - Resolved vs. pending ratio
        - Positive vs. negative example counts
        - Token distribution (min, max, avg)
        - Scaffold kind distribution
        """
        pairs = await self._repo.list_pairs(limit=10000)

        total = len(pairs)
        by_status: dict[str, int] = {}
        by_kind: dict[str, int] = {}
        tokens_list: list[int] = []
        resolved = 0
        positive = 0

        for p in pairs:
            by_status[p.outcome_status] = by_status.get(p.outcome_status, 0) + 1
            by_kind[p.scaffold_kind] = by_kind.get(p.scaffold_kind, 0) + 1
            tokens_list.append(p.tokens_consumed)
            if p.outcome_status in TERMINAL_STATUSES:
                resolved += 1
            reward = compute_reward(p.outcome_status)
            if reward is not None and reward > 0:
                positive += 1

        return {
            "total_pairs": total,
            "resolved": resolved,
            "pending": total - resolved,
            "resolution_rate": (resolved / total) if total else 0.0,
            "positive_examples": positive,
            "negative_examples": resolved - positive,
            "counts_by_status": by_status,
            "counts_by_scaffold_kind": by_kind,
            "token_stats": {
                "min": min(tokens_list) if tokens_list else 0,
                "max": max(tokens_list) if tokens_list else 0,
                "avg": (sum(tokens_list) / len(tokens_list)) if tokens_list else 0.0,
            },
        }

    async def deduplicate(
        self,
        statuses: tuple[str, ...] | None = None,
    ) -> int:
        """Remove duplicate pairs (same scaffold_hash) keeping the most recent.

        Only acts on pairs in the given ``statuses`` (default: pending).
        Returns the number of rows removed.
        """
        if statuses is None:
            statuses = ("pending",)

        seen: dict[str, OrnithTrainingPairModel] = {}
        duplicates: list[str] = []

        pairs = await self._repo.list_pairs(limit=10000)
        for p in pairs:
            if p.outcome_status not in statuses:
                continue
            if p.scaffold_hash in seen:
                existing = seen[p.scaffold_hash]
                if p.invoked_at and existing.invoked_at and p.invoked_at > existing.invoked_at:
                    duplicates.append(existing.id)
                    seen[p.scaffold_hash] = p
                else:
                    duplicates.append(p.id)
            else:
                seen[p.scaffold_hash] = p

        for dup_id in duplicates:
            row = await self._repo.get(dup_id)
            if row is not None:
                await self._session.delete(row)

        if duplicates:
            await self._session.flush()

        return len(duplicates)


def _count_lines(path: Path) -> int:
    try:
        text = path.read_text(encoding="utf-8")
        return len([line for line in text.strip().splitlines() if line.strip()])
    except Exception:
        return 0
