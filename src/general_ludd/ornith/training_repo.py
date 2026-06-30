"""Repository for Ornith training ``(scaffold, outcome)`` pairs.

Captures each Ornith ``solve()`` invocation as a row, lets the outcome
observer / review / gate update the outcome later, and exports a JSONL
dataset suitable for Ornith's offline RL trainer (one
``{"scaffold": ..., "outcome": ..., "reward": ...}`` per line, with
``reward`` computed from the outcome status).
"""

from __future__ import annotations

import hashlib
import json as _json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from general_ludd.db.models import OrnithTrainingPairModel

#: Mapping from outcome_status to RL reward. ``pending`` pairs are skipped
#: on export (no signal yet). Values follow the spec: clearly correct
#: scaffolds score 1.0; actively bad ones (reverted) go negative.
REWARD_MAP: dict[str, float | None] = {
    "succeeded": 1.0,
    "applied": 0.7,
    "rejected_by_review": 0.1,
    "rejected_by_gate": 0.0,
    "reverted": -0.2,
    "pending": None,
}

VALID_OUTCOME_STATUSES = frozenset(REWARD_MAP.keys())
VALID_SCAFFOLD_KINDS = frozenset({"playbook", "module", "plugin", "rego", "patch"})


@dataclass
class OrnithInvocation:
    """The scaffold half of a training pair -- what Ornith produced."""

    task_description: str
    target_files: list[str]
    scaffold_kind: str
    scaffold_content: str
    agent_id: str
    iterations_used: int = 0
    tokens_consumed: int = 0
    model_sha: str = ""
    project_id: str | None = None
    invoked_at: datetime = field(default_factory=lambda: datetime.now(UTC))


def compute_scaffold_hash(scaffold_content: str) -> str:
    """sha256 of the scaffold content -- used for dedup."""
    return hashlib.sha256(scaffold_content.encode("utf-8")).hexdigest()


def compute_reward(outcome_status: str) -> float | None:
    """Return the RL reward for an outcome status, or None to skip."""
    return REWARD_MAP.get(outcome_status)


class OrnithTrainingRepo:
    """Persistence + dataset export for Ornith training pairs."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record_pair(
        self, invocation: OrnithInvocation
    ) -> OrnithTrainingPairModel:
        """Record a freshly-produced scaffold. Called right after solve()."""
        if invocation.scaffold_kind not in VALID_SCAFFOLD_KINDS:
            raise ValueError(
                f"invalid scaffold_kind {invocation.scaffold_kind!r}; "
                f"must be one of {sorted(VALID_SCAFFOLD_KINDS)}"
            )
        if not invocation.agent_id:
            raise ValueError("agent_id must not be empty")
        row = OrnithTrainingPairModel(
            invoked_at=invocation.invoked_at,
            task_description=invocation.task_description,
            target_files=_json.dumps(invocation.target_files),
            scaffold_kind=invocation.scaffold_kind,
            scaffold_content=invocation.scaffold_content,
            scaffold_hash=compute_scaffold_hash(invocation.scaffold_content),
            iterations_used=invocation.iterations_used,
            tokens_consumed=invocation.tokens_consumed,
            model_sha=invocation.model_sha,
            outcome_status="pending",
            outcome_details="{}",
            project_id=invocation.project_id,
            agent_id=invocation.agent_id,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def get(self, pair_id: str) -> OrnithTrainingPairModel | None:
        stmt = select(OrnithTrainingPairModel).where(
            OrnithTrainingPairModel.id == pair_id
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def set_outcome(
        self,
        pair_id: str,
        status: str,
        details: dict[str, Any] | None = None,
    ) -> OrnithTrainingPairModel:
        """Set the outcome for a pair. Called by observer / review / gate."""
        if status not in VALID_OUTCOME_STATUSES:
            raise ValueError(
                f"invalid outcome_status {status!r}; "
                f"must be one of {sorted(VALID_OUTCOME_STATUSES)}"
            )
        row = await self.get(pair_id)
        if row is None:
            raise KeyError(pair_id)
        row.outcome_status = status
        row.outcome_details = _json.dumps(details or {})
        row.outcome_set_at = datetime.now(UTC)
        await self._session.flush()
        return row

    async def get_pending_outcomes(
        self, limit: int = 100, older_than_minutes: int = 0
    ) -> list[OrnithTrainingPairModel]:
        """Pairs whose outcome is still pending, optionally older than N minutes."""
        stmt = (
            select(OrnithTrainingPairModel)
            .where(OrnithTrainingPairModel.outcome_status == "pending")
            .order_by(OrnithTrainingPairModel.invoked_at.asc())
            .limit(limit)
        )
        if older_than_minutes > 0:
            cutoff = datetime.now(UTC) - timedelta(minutes=older_than_minutes)
            stmt = stmt.where(OrnithTrainingPairModel.invoked_at <= cutoff)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_pairs(
        self,
        status: str | None = None,
        project_id: str | None = None,
        limit: int = 100,
    ) -> list[OrnithTrainingPairModel]:
        stmt = select(OrnithTrainingPairModel).order_by(
            OrnithTrainingPairModel.invoked_at.desc()
        )
        if status is not None:
            stmt = stmt.where(OrnithTrainingPairModel.outcome_status == status)
        if project_id is not None:
            stmt = stmt.where(OrnithTrainingPairModel.project_id == project_id)
        stmt = stmt.limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_pairs_by_statuses(
        self,
        statuses: list[str],
        project_id: str | None = None,
        limit: int = 100,
        lookback_days: int = 0,
    ) -> list[OrnithTrainingPairModel]:
        """Return pairs whose ``outcome_status`` is in ``statuses``.

        Used by the self-improvement role to pull the artifacts that NEED
        improvement (those rejected by the gate, by review, or reverted).
        ``lookback_days > 0`` restricts to pairs invoked within the last N
        days; ``limit`` caps the row count.
        """
        if not statuses:
            return []
        unknown = [s for s in statuses if s not in VALID_OUTCOME_STATUSES]
        if unknown:
            raise ValueError(
                f"invalid outcome_status(es) {unknown!r}; "
                f"must be one of {sorted(VALID_OUTCOME_STATUSES)}"
            )
        stmt = (
            select(OrnithTrainingPairModel)
            .where(OrnithTrainingPairModel.outcome_status.in_(statuses))
            .order_by(OrnithTrainingPairModel.invoked_at.desc())
        )
        if project_id is not None:
            stmt = stmt.where(OrnithTrainingPairModel.project_id == project_id)
        if lookback_days > 0:
            cutoff = datetime.now(UTC) - timedelta(days=lookback_days)
            stmt = stmt.where(OrnithTrainingPairModel.invoked_at >= cutoff)
        stmt = stmt.limit(max(1, min(limit, 1000)))
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def export_dataset(
        self,
        since: datetime | None = None,
        project_id: str | None = None,
        out_path: str | Path | None = None,
    ) -> Path:
        """Export the labeled dataset as JSONL for Ornith's offline RL trainer.

        Each line: ``{"scaffold": ..., "outcome": ..., "reward": ...}``.
        Pending pairs (no outcome yet) are skipped.
        """
        stmt = select(OrnithTrainingPairModel).order_by(
            OrnithTrainingPairModel.invoked_at.asc()
        )
        if since is not None:
            stmt = stmt.where(OrnithTrainingPairModel.invoked_at >= since)
        if project_id is not None:
            stmt = stmt.where(OrnithTrainingPairModel.project_id == project_id)
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())

        if out_path is None:
            out_path = (
                Path(".") / f"ornith-dataset-{int(datetime.now(UTC).timestamp())}.jsonl"
            )
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as fh:
            for row in rows:
                reward = compute_reward(row.outcome_status)
                if reward is None:
                    continue
                try:
                    target_files = _json.loads(row.target_files or "[]")
                except Exception:
                    target_files = []
                try:
                    details = _json.loads(row.outcome_details or "{}")
                except Exception:
                    details = {}
                record = {
                    "scaffold": {
                        "id": row.id,
                        "kind": row.scaffold_kind,
                        "content": row.scaffold_content,
                        "hash": row.scaffold_hash,
                        "task_description": row.task_description,
                        "target_files": target_files,
                        "iterations_used": row.iterations_used,
                        "tokens_consumed": row.tokens_consumed,
                        "model_sha": row.model_sha,
                        "agent_id": row.agent_id,
                        "project_id": row.project_id,
                        "invoked_at": row.invoked_at.isoformat()
                        if row.invoked_at
                        else None,
                    },
                    "outcome": {
                        "status": row.outcome_status,
                        "details": details,
                        "set_at": row.outcome_set_at.isoformat()
                        if row.outcome_set_at
                        else None,
                    },
                    "reward": reward,
                }
                fh.write(_json.dumps(record, default=str) + "\n")
        return out

    async def stats(self) -> dict[str, Any]:
        """Aggregate stats: counts by outcome_status, success_rate, avg_tokens."""
        stmt = select(
            OrnithTrainingPairModel.outcome_status,
            func.count(OrnithTrainingPairModel.id),
        ).group_by(OrnithTrainingPairModel.outcome_status)
        result = await self._session.execute(stmt)
        counts: dict[str, int] = {}
        total = 0
        for status, cnt in result.all():
            counts[status] = int(cnt)
            total += int(cnt)
        succeeded = counts.get("succeeded", 0)
        resolved = sum(
            cnt
            for status, cnt in counts.items()
            if status
            in {
                "succeeded",
                "rejected_by_gate",
                "rejected_by_review",
                "reverted",
            }
        )
        success_rate = (succeeded / resolved) if resolved else 0.0
        total_tokens_stmt = select(
            func.coalesce(func.sum(OrnithTrainingPairModel.tokens_consumed), 0)
        )
        total_tokens = int(
            (await self._session.execute(total_tokens_stmt)).scalar() or 0
        )
        avg_tokens_per_call = (total_tokens / total) if total else 0.0
        return {
            "counts_by_status": counts,
            "total": total,
            "success_rate": success_rate,
            "avg_tokens_per_call": avg_tokens_per_call,
        }
