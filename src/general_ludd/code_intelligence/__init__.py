"""Code intelligence — AST extraction, call graphs, search, git analysis, complexity scoring."""

from __future__ import annotations

from typing import TYPE_CHECKING

__all__ = (
    "ASTBlockExtractor",
    "CallGraph",
    "CodeComplexityScorer",
    "CodeSearch",
    "CodebaseIntrospector",
    "ComplexityScore",
    "DirectoryComplexityReport",
    "GitIntelligence",
)

from general_ludd.code_intelligence.callgraph import CallGraph
from general_ludd.code_intelligence.complexity_scorer import (
    CodeComplexityScorer,
    ComplexityScore,
    DirectoryComplexityReport,
)
from general_ludd.code_intelligence.extractor import ASTBlockExtractor
from general_ludd.code_intelligence.git_intel import GitIntelligence
from general_ludd.code_intelligence.search import CodeSearch

# Reload keeps the module dictionary; refresh any previously resolved export.
globals().pop("CodebaseIntrospector", None)

if TYPE_CHECKING:
    from general_ludd.code_intelligence.introspect import CodebaseIntrospector as CodebaseIntrospector


def __getattr__(name: str) -> object:
    """Resolve the introspector re-export without loading it for unrelated imports."""
    if name == "CodebaseIntrospector":
        from general_ludd.code_intelligence.introspect import CodebaseIntrospector

        globals()[name] = CodebaseIntrospector
        return CodebaseIntrospector
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """Keep all declared public exports discoverable before their first use."""
    return sorted(set(globals()) | set(__all__))
