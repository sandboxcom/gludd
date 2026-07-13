"""AG.15 — Reflexion loops: self-critique and iterative improvement cycles.

Implements the try → evaluate → reflect → retry pattern for AI agents.
Agents critique their own outputs across multiple attempts, storing lessons
in a persistent memory to improve on subsequent tries.
"""

from general_ludd.ag14_reflexion.loop import (
    EpisodeRecord,
    ReflexionLoop,
    ReflexionMemory,
    ReflexionResult,
    create_reflexion_loop,
)

__all__ = [
    "EpisodeRecord",
    "ReflexionLoop",
    "ReflexionMemory",
    "ReflexionResult",
    "create_reflexion_loop",
]
