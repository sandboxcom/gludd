"""AG.8: Named Pipeline Passes — registry, execution, and result tracking."""

from general_ludd.ag8_named_passes.registry import (
    BUILTIN_PASSES,
    NamedPass,
    PassRegistry,
    PassResult,
    PassStatus,
)

__all__ = [
    "BUILTIN_PASSES",
    "NamedPass",
    "PassRegistry",
    "PassResult",
    "PassStatus",
]
