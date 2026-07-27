"""Agent memory subsystem — consolidation, retrieval, episodic recording, cross-task learning.

Mirrors Stanford AutoMemory concepts:
  - Episodic memory: structured records of past task executions
  - Semantic memory: consolidated summaries / learned patterns
  - Memory retrieval: relevance-scored queries over episodic history
  - Memory consolidation: periodic summarization of old entries
  - Cross-task learning: applying lessons from one task to another
  - Local memory: diskcache-backed local key-value store (no SQL)
  - Cross-conversation: LangGraph Store API wrapper for persistent cross-session state
"""

from general_ludd.memory.consolidation import MemoryConsolidator, consolidate_cascade
from general_ludd.memory.cross_conversation import CrossConversationStore
from general_ludd.memory.cross_convo_memory import (
    ConversationContext,
    ConversationMeta,
    CrossConversationMemory,
    WorkingMemoryItem,
)
from general_ludd.memory.cross_task import CrossTaskLearner
from general_ludd.memory.embedding_store import MemoryEmbeddingStore
from general_ludd.memory.episodic import EpisodicMemoryRecorder, reconstruct_timeline
from general_ludd.memory.local import LocalAgentMemory, MemoryRecord
from general_ludd.memory.procedural import ProceduralMemoryStore, Procedure
from general_ludd.memory.retrieval import MemoryRetriever, hybrid_search, score_memory
from general_ludd.memory.semantic import Fact, SemanticMemoryStore

__all__ = [
    "ConversationContext",
    "ConversationMeta",
    "CrossConversationMemory",
    "CrossConversationStore",
    "CrossTaskLearner",
    "EpisodicMemoryRecorder",
    "Fact",
    "LocalAgentMemory",
    "MemoryConsolidator",
    "MemoryEmbeddingStore",
    "MemoryRecord",
    "MemoryRetriever",
    "ProceduralMemoryStore",
    "Procedure",
    "SemanticMemoryStore",
    "WorkingMemoryItem",
    "consolidate_cascade",
    "hybrid_search",
    "reconstruct_timeline",
    "score_memory",
]
