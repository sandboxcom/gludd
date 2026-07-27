"""Semantic memory — facts, concepts, and learned patterns.

Stores structured knowledge that the agent has acquired: codebase facts,
API contracts, configuration patterns, error meanings, and domain concepts.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

SEMANTIC_NAMESPACE = "semantic"


@dataclass
class Fact:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    key: str = ""
    value: str = ""
    confidence: float = 1.0
    source: str = ""
    category: str = ""
    tags: list[str] = field(default_factory=list)
    embedding_metadata: dict[str, object] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = ""
    access_count: int = 0


class SemanticMemoryStore:
    """Persistent store for learned facts and concepts."""

    def __init__(self, memory_repo: Any) -> None:
        self._repo = memory_repo

    async def upsert_fact(
        self,
        fact: Fact,
        project_id: str | None = None,
    ) -> str:
        existing = await self.get_fact_by_key(fact.key, project_id=project_id)
        if existing is not None:
            fact.id = existing.id
            fact.access_count = existing.access_count
        serialized = json.dumps(_fact_to_dict(fact), default=str)
        await self._repo.set(
            agent_id="system",
            key=fact.key,
            value=serialized,
            namespace=SEMANTIC_NAMESPACE,
            project_id=project_id,
        )
        return fact.id

    async def get_fact(
        self,
        fact_id: str,
        project_id: str | None = None,
    ) -> Fact | None:
        row = await self._repo.get(
            "system",
            fact_id,
            namespace=SEMANTIC_NAMESPACE,
            project_id=project_id,
        )
        if row is None:
            return None
        data = json.loads(row.value) if isinstance(row.value, str) else row.value
        fact = _dict_to_fact(data)
        fact.access_count += 1
        return fact

    async def get_fact_by_key(
        self,
        key: str,
        project_id: str | None = None,
    ) -> Fact | None:
        all_facts = await self.list_facts(project_id=project_id, limit=500)
        for fact in all_facts:
            if fact.key == key:
                return fact
        return None

    async def list_facts(
        self,
        project_id: str | None = None,
        category: str | None = None,
        min_confidence: float = 0.0,
        limit: int = 200,
    ) -> list[Fact]:
        rows = await self._repo.list_by_namespace(
            "system",
            namespace=SEMANTIC_NAMESPACE,
            project_id=project_id,
            limit=limit,
        )
        results: list[Fact] = []
        for row in rows:
            try:
                data = json.loads(row.value)
                fact = _dict_to_fact(data)
                if category is not None and fact.category != category:
                    continue
                if fact.confidence < min_confidence:
                    continue
                results.append(fact)
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
        return results

    async def search_facts(
        self,
        query: str,
        project_id: str | None = None,
        top_k: int = 10,
    ) -> list[tuple[Fact, float]]:
        all_facts = await self.list_facts(project_id=project_id, limit=500)
        query_lower = query.lower()
        query_terms = set(query_lower.split())

        scored: list[tuple[Fact, float]] = []
        for fact in all_facts:
            score = 0.0
            fact_text = (f"{fact.key} {fact.value} {fact.category} {' '.join(fact.tags)}").lower()
            fact_terms = set(fact_text.split())

            common = query_terms & fact_terms
            if query_terms:
                score += len(common) / max(len(query_terms), 1) * 0.5
            if query_lower in fact_text:
                score += 0.3
            if score > 0:
                score += fact.confidence * 0.2
                scored.append((fact, score))

        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:top_k]

    async def consolidate_from_consolidated(
        self,
        consolidator: Any,
        agent_id: str,
        project_id: str | None = None,
    ) -> int:
        summaries = await consolidator.get_consolidated(
            agent_id,
            project_id=project_id,
        )
        created = 0
        for summary in summaries:
            task_type = str(summary.get("task_type", ""))
            if not task_type:
                continue
            outcome_stats = summary.get("outcomes", {})
            total = sum(outcome_stats.values()) if isinstance(outcome_stats, dict) else 0
            success_count = outcome_stats.get("success", 0) if isinstance(outcome_stats, dict) else 0
            confidence = success_count / total if total > 0 else 0.5

            fact = Fact(
                key=f"task_pattern:{task_type}",
                value=json.dumps(
                    {
                        "task_type": task_type,
                        "episode_count": summary.get("episode_count", 0),
                        "outcomes": outcome_stats,
                        "error_patterns": summary.get("error_patterns", []),
                        "key_takeaways": summary.get("key_takeaways", []),
                    },
                    default=str,
                ),
                confidence=confidence,
                source="memory_consolidation",
                category="task_pattern",
                tags=[task_type, "consolidated"],
            )
            await self.upsert_fact(fact, project_id=project_id)
            created += 1

        return created

    async def delete_fact(
        self,
        fact_id: str,
        project_id: str | None = None,
    ) -> bool:
        row = await self._repo.get(
            "system",
            fact_id,
            namespace=SEMANTIC_NAMESPACE,
            project_id=project_id,
        )
        if row is None:
            return False
        await self._repo.delete(
            "system",
            fact_id,
            namespace=SEMANTIC_NAMESPACE,
        )
        return True


def _fact_to_dict(fact: Fact) -> dict[str, object]:
    return {
        "id": fact.id,
        "key": fact.key,
        "value": fact.value,
        "confidence": fact.confidence,
        "source": fact.source,
        "category": fact.category,
        "tags": fact.tags,
        "embedding_metadata": fact.embedding_metadata,
        "created_at": fact.created_at,
        "updated_at": fact.updated_at,
        "access_count": fact.access_count,
    }


def _dict_to_fact(data: dict[str, Any]) -> Fact:
    return Fact(
        id=str(data.get("id", "")),
        key=str(data.get("key", "")),
        value=str(data.get("value", "")),
        confidence=float(data.get("confidence", 1.0)),
        source=str(data.get("source", "")),
        category=str(data.get("category", "")),
        tags=list(data.get("tags", [])),
        embedding_metadata=dict(data.get("embedding_metadata", {})),
        created_at=str(data.get("created_at", datetime.now(UTC).isoformat())),
        updated_at=str(data.get("updated_at", "")),
        access_count=int(data.get("access_count", 0)),
    )
