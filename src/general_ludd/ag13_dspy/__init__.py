"""AG.13 — DSPy-style prompt optimization registry and optimizer.

Provides :class:`PromptSpec`, :class:`PromptTemplate`, :class:`PromptRegistry`,
and :class:`PromptOptimizer` — a lightweight prompt-compilation pipeline
modeled on the DSPy signature → module → optimizer pattern.
"""

from general_ludd.ag13_dspy.optimizer import PromptOptimizer
from general_ludd.ag13_dspy.registry import (
    PromptRegistry,
    PromptSpec,
    PromptTemplate,
)

__all__ = [
    "PromptOptimizer",
    "PromptRegistry",
    "PromptSpec",
    "PromptTemplate",
]
