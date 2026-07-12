"""Agent memory subsystem — consolidation, retrieval, episodic recording, cross-task learning.

Mirrors Stanford AutoMemory concepts:
  - Episodic memory: structured records of past task executions
  - Semantic memory: consolidated summaries / learned patterns
  - Memory retrieval: relevance-scored queries over episodic history
  - Memory consolidation: periodic summarization of old entries
  - Cross-task learning: applying lessons from one task to another
  - Local memory: diskcache-backed local key-value store (no SQL)
"""

from general_ludd.memory.consolidation import MemoryConsolidator
from general_ludd.memory.cross_task import CrossTaskLearner
from general_ludd.memory.episodic import EpisodicMemoryRecorder
from general_ludd.memory.local import LocalAgentMemory, MemoryRecord
from general_ludd.memory.retrieval import MemoryRetriever

__all__ = [
    "CrossTaskLearner",
    "EpisodicMemoryRecorder",
    "LocalAgentMemory",
    "MemoryConsolidator",
    "MemoryRecord",
    "MemoryRetriever",
]
