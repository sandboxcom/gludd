"""Cross-conversation memory — high-level session/context manager for AG.5.

Wraps CrossConversationStore to provide:
  - Conversation lifecycle: start, end, list, delete
  - Working memory: scoped key-value persistence across conversations
  - Context injection: retrieve relevant context from past conversations
  - Summary generation: capture decisions and outcomes per conversation
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, cast

from general_ludd.memory.cross_conversation import CrossConversationStore

logger = logging.getLogger(__name__)

NAMESPACE_CONVERSATIONS = "conversations"
NAMESPACE_WORKING = "working_memory"
NAMESPACE_SUMMARIES = "summaries"


@dataclass
class ConversationMeta:
    """Metadata for a single conversation session."""

    conversation_id: str
    agent_id: str = ""
    project_id: str | None = None
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    status: str = "active"  # active, completed, abandoned
    tags: list[str] = field(default_factory=list)
    summary: str = ""
    decision_count: int = 0
    outcome: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "agent_id": self.agent_id,
            "project_id": self.project_id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "status": self.status,
            "tags": self.tags,
            "summary": self.summary,
            "decision_count": self.decision_count,
            "outcome": self.outcome,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> ConversationMeta:
        ended_at_raw = data.get("ended_at")
        pid = data.get("project_id")
        return ConversationMeta(
            conversation_id=str(data.get("conversation_id", "")),
            agent_id=str(data.get("agent_id", "")),
            project_id=str(pid) if pid is not None else None,
            started_at=float(data.get("started_at", 0)),
            ended_at=float(ended_at_raw) if ended_at_raw is not None else None,
            status=str(data.get("status", "active")),
            tags=list(data.get("tags", [])),
            summary=str(data.get("summary", "")),
            decision_count=int(data.get("decision_count", 0)),
            outcome=str(data.get("outcome", "unknown")),
        )


@dataclass
class WorkingMemoryItem:
    """A single working-memory entry scoped to a conversation."""

    conversation_id: str
    key: str
    value: Any
    project_id: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "key": self.key,
            "value": self.value,
            "project_id": self.project_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> WorkingMemoryItem:
        pid = data.get("project_id")
        return WorkingMemoryItem(
            conversation_id=str(data.get("conversation_id", "")),
            key=str(data.get("key", "")),
            value=data.get("value"),
            project_id=str(pid) if pid is not None else None,
            created_at=float(data.get("created_at", 0)),
            updated_at=float(data.get("updated_at", 0)),
        )


@dataclass
class ConversationContext:
    """The full context bundle for a conversation — meta + working memory."""

    meta: ConversationMeta
    working_memory: dict[str, Any] = field(default_factory=dict)
    summary: str = ""
    project_id: str | None = None


class CrossConversationMemory:
    """High-level cross-conversation memory manager.

    Manages conversation lifecycles, working memory, context injection,
    and summary generation.  Uses CrossConversationStore for persistence.
    """

    def __init__(self, store: CrossConversationStore | None = None) -> None:
        self._store = store or CrossConversationStore()

    # ================================================================== lifecycle

    def start_conversation(
        self,
        conversation_id: str,
        agent_id: str = "default",
        tags: list[str] | None = None,
        project_id: str | None = None,
    ) -> ConversationMeta:
        meta = ConversationMeta(
            conversation_id=conversation_id,
            agent_id=agent_id,
            project_id=project_id,
            tags=tags or [],
        )
        self._store.put(
            key=conversation_id,
            value=meta.to_dict(),
            namespace=NAMESPACE_CONVERSATIONS,
            project_id=project_id,
        )
        return meta

    def end_conversation(
        self,
        conversation_id: str,
        summary: str = "",
        outcome: str = "completed",
    ) -> ConversationMeta | None:
        existing = self._store.get(conversation_id, namespace=NAMESPACE_CONVERSATIONS)
        if existing is None:
            return None
        meta = ConversationMeta.from_dict(existing["value"])
        meta.ended_at = time.time()
        meta.status = "completed"
        meta.outcome = outcome
        if summary:
            meta.summary = summary
        pid = meta.project_id
        self._store.put(
            key=conversation_id,
            value=meta.to_dict(),
            namespace=NAMESPACE_CONVERSATIONS,
            project_id=pid,
        )
        if summary:
            self._save_summary(conversation_id, summary, meta.agent_id, meta.tags, project_id=pid)
        return meta

    def get_conversation(self, conversation_id: str) -> ConversationMeta | None:
        raw = self._store.get(conversation_id, namespace=NAMESPACE_CONVERSATIONS)
        if raw is None:
            return None
        return ConversationMeta.from_dict(raw["value"])

    def list_conversations(
        self,
        agent_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
        project_id: str | None = None,
    ) -> list[ConversationMeta]:
        results = self._store.search(
            namespace_prefix=NAMESPACE_CONVERSATIONS,
            limit=limit,
            project_id=project_id,
        )
        metas = []
        for r in results:
            meta = ConversationMeta.from_dict(r["value"])
            if agent_id and meta.agent_id != agent_id:
                continue
            if status and meta.status != status:
                continue
            metas.append(meta)
        return metas

    def delete_conversation(self, conversation_id: str) -> bool:
        deleted = self._store.delete(conversation_id, namespace=NAMESPACE_CONVERSATIONS)
        self.clear_working_memory(conversation_id)
        self._store.delete(conversation_id, namespace=NAMESPACE_SUMMARIES)
        return deleted

    # ==================================================================== context

    def get_context(self, conversation_id: str) -> ConversationContext | None:
        meta_raw = self._store.get(conversation_id, namespace=NAMESPACE_CONVERSATIONS)
        if meta_raw is None:
            return None
        meta = ConversationMeta.from_dict(meta_raw["value"])
        working = self.get_all_working_memory(conversation_id)
        pid = meta.project_id
        summary_raw = self._store.get(conversation_id, namespace=NAMESPACE_SUMMARIES, project_id=pid)
        summary = summary_raw["value"].get("text", "") if summary_raw else ""
        return ConversationContext(meta=meta, working_memory=working, summary=summary, project_id=pid)

    def import_context(
        self,
        conversation_id: str,
        similar_terms: str = "",
        limit: int = 5,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        all_results = self._store.search(
            namespace_prefix=NAMESPACE_CONVERSATIONS,
            limit=100,
            project_id=project_id,
        )
        contexts = []
        for r in all_results:
            meta = ConversationMeta.from_dict(r["value"])
            if meta.conversation_id == conversation_id:
                continue
            working = self.get_all_working_memory(meta.conversation_id)
            summary_raw = self._store.get(
                meta.conversation_id, namespace=NAMESPACE_SUMMARIES, project_id=project_id,
            )
            summary_text = summary_raw["value"].get("text", "") if summary_raw else meta.summary
            score = self._relevance_score(meta, working, summary_text, similar_terms)
            if score > 0:
                contexts.append({
                    "conversation_id": meta.conversation_id,
                    "agent_id": meta.agent_id,
                    "status": meta.status,
                    "outcome": meta.outcome,
                    "summary": summary_text,
                    "tags": meta.tags,
                    "relevance_score": round(score, 3),
                    "started_at": meta.started_at,
                })
        contexts.sort(key=lambda c: c["relevance_score"], reverse=True)
        return contexts[:limit]

    # =========================================================== working memory

    def set_working_memory(
        self,
        conversation_id: str,
        key: str,
        value: Any,
        ttl: float | None = None,
        project_id: str | None = None,
    ) -> WorkingMemoryItem:
        item_key = f"{conversation_id}:{key}"
        existing = self._store.get(item_key, namespace=NAMESPACE_WORKING)
        now = time.time()
        item = WorkingMemoryItem(
            conversation_id=conversation_id,
            key=key,
            value=value,
            project_id=project_id,
            created_at=now,
            updated_at=now,
        )
        if existing:
            item.created_at = existing["value"].get("created_at", now)
        self._store.put(
            key=item_key,
            value=item.to_dict(),
            namespace=NAMESPACE_WORKING,
            ttl=ttl,
            project_id=project_id,
        )
        return item

    def get_working_memory(self, conversation_id: str, key: str) -> Any | None:
        item_key = f"{conversation_id}:{key}"
        raw = self._store.get(item_key, namespace=NAMESPACE_WORKING)
        if raw is None:
            return None
        return raw["value"].get("value")

    def get_all_working_memory(self, conversation_id: str) -> dict[str, Any]:
        results = self._store.search(
            namespace_prefix=NAMESPACE_WORKING,
            limit=200,
        )
        prefix = f"{conversation_id}:"
        out: dict[str, Any] = {}
        for r in results:
            k = str(r["key"])
            if k.startswith(prefix):
                short_key = k[len(prefix):]
                out[short_key] = r["value"].get("value")
        return out

    def delete_working_memory(self, conversation_id: str, key: str) -> bool:
        item_key = f"{conversation_id}:{key}"
        return self._store.delete(item_key, namespace=NAMESPACE_WORKING)

    def clear_working_memory(self, conversation_id: str) -> int:
        all_keys = self.get_all_working_memory(conversation_id)
        count = 0
        for key in list(all_keys.keys()):
            if self.delete_working_memory(conversation_id, key):
                count += 1
        return count

    # ================================================================ summaries

    def _save_summary(
        self,
        conversation_id: str,
        text: str,
        agent_id: str,
        tags: list[str],
        project_id: str | None = None,
    ) -> None:
        self._store.put(
            key=conversation_id,
            value={
                "text": text,
                "agent_id": agent_id,
                "tags": tags,
                "saved_at": time.time(),
            },
            namespace=NAMESPACE_SUMMARIES,
            project_id=project_id,
        )

    def get_summary(self, conversation_id: str) -> str | None:
        raw = self._store.get(conversation_id, namespace=NAMESPACE_SUMMARIES)
        if raw is None:
            return None
        return cast(str, raw["value"].get("text"))

    def search_summaries(
        self, query: str, limit: int = 10, project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        results = self._store.search(
            namespace_prefix=NAMESPACE_SUMMARIES,
            limit=limit * 2,
            project_id=project_id,
        )
        filtered = []
        query_lower = query.lower()
        for r in results:
            text = str(r["value"].get("text", ""))
            tags = r["value"].get("tags", [])
            tag_match = any(query_lower in str(t).lower() for t in tags)
            text_match = query_lower in text.lower()
            if text_match or tag_match:
                filtered.append({
                    "conversation_id": r["key"],
                    "text": text,
                    "tags": tags,
                    "saved_at": r["value"].get("saved_at"),
                    "match_type": "text" if text_match else "tag",
                })
        return filtered[:limit]

    # =========================================================== decision log

    def record_decision(
        self,
        conversation_id: str,
        decision: str,
        reasoning: str = "",
    ) -> WorkingMemoryItem:
        entry = {"decision": decision, "reasoning": reasoning, "timestamp": time.time()}
        item = self.set_working_memory(
            conversation_id,
            f"_decision_{int(time.time() * 1_000_000)}",
            entry,
        )
        meta = self.get_conversation(conversation_id)
        if meta is not None:
            meta.decision_count += 1
            self._store.put(
                key=conversation_id,
                value=meta.to_dict(),
                namespace=NAMESPACE_CONVERSATIONS,
            )
        return item

    def get_decisions(
        self, conversation_id: str, limit: int = 20,
    ) -> list[dict[str, Any]]:
        wm = self.get_all_working_memory(conversation_id)
        decisions = []
        for key, value in wm.items():
            if key.startswith("_decision_") and isinstance(value, dict):
                decisions.append(value)
        decisions.sort(key=lambda d: d.get("timestamp", 0))
        return decisions[-limit:]

    # =========================================================== persistence

    def purge_expired(self) -> int:
        return self._store.purge_expired()

    @property
    def available(self) -> bool:
        return self._store.available

    # ============================================================ private

    @staticmethod
    def _relevance_score(
        meta: ConversationMeta,
        working_memory: dict[str, Any],
        summary: str,
        similar_terms: str,
    ) -> float:
        if not similar_terms.strip():
            return 0.0
        score = 0.0
        terms = similar_terms.lower().split()
        text_blob = (
            f"{' '.join(meta.tags)} {meta.outcome} {summary} "
            f"{json.dumps(working_memory, default=str)}"
        ).lower()

        for term in terms:
            if term in text_blob:
                score += 0.3
        if meta.outcome in text_blob and meta.outcome in similar_terms.lower():
            score += 0.2
        return min(score, 1.0)
