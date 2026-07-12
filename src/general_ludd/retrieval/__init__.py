"""G3 semantic codebase retrieval — indexing, semantic search, SearXNG-powered research, and agentic context."""

__all__ = (
    "AgenticContextInjector",
    "AgenticResearchContext",
    "CodebaseIndexer",
    "ResearchContextItem",
    "ResearchIndex",
    "ResearchTopic",
    "SearxNGClient",
    "SemanticSearcher",
    "SourceAnnotation",
    "SourceEntry",
)

from general_ludd.retrieval.agentic_context import (
    AgenticContextInjector,
    AgenticResearchContext,
    ResearchContextItem,
    SourceAnnotation,
)
from general_ludd.retrieval.indexer import CodebaseIndexer
from general_ludd.retrieval.research_index import (
    ResearchIndex,
    ResearchTopic,
    SourceEntry,
)
from general_ludd.retrieval.searcher import SemanticSearcher
from general_ludd.retrieval.searx_client import SearxNGClient
