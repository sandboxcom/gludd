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

from general_ludd.memory.consolidation import MemoryConsolidator
from general_ludd.memory.cross_conversation import CrossConversationStore
from general_ludd.memory.cross_convo_memory import (
    ConversationContext,
    ConversationMeta,
    CrossConversationMemory,
    WorkingMemoryItem,
)
from general_ludd.memory.cross_task import CrossTaskLearner
from general_ludd.memory.episodic import EpisodicMemoryRecorder
from general_ludd.memory.hindsight_adapter import HindsightMemoryAdapter
from general_ludd.memory.local import LocalAgentMemory, MemoryRecord
from general_ludd.memory.memory_bank import (
    Disposition,
    MemoryBank,
    MemoryBankConfig,
    MemoryBankRegistry,
    MemoryBankResult,
    MemoryEntry,
    MentalModel,
    load_bank_templates,
)
from general_ludd.memory.observation_consolidator import (
    EvidenceRef,
    MemoryFact,
    Observation,
    ObservationConsolidator,
    ObservationStore,
)
from general_ludd.memory.retrieval import MemoryRetriever
from general_ludd.memory.tempr_retriever import TEMPRResult, TEMPRRetriever

__all__ = [
    "ConversationContext",
    "ConversationMeta",
    "CrossConversationMemory",
    "CrossConversationStore",
    "CrossTaskLearner",
    "Disposition",
    "EpisodicMemoryRecorder",
    "EvidenceRef",
    "HindsightMemoryAdapter",
    "LocalAgentMemory",
    "MemoryBank",
    "MemoryBankConfig",
    "MemoryBankRegistry",
    "MemoryBankResult",
    "MemoryConsolidator",
    "MemoryEntry",
    "MemoryFact",
    "MemoryRecord",
    "MemoryRetriever",
    "MentalModel",
    "Observation",
    "ObservationConsolidator",
    "ObservationStore",
    "TEMPRResult",
    "TEMPRRetriever",
    "WorkingMemoryItem",
    "load_bank_templates",
]
