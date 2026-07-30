"""Git Release Captain Expert collection (spec GRC-001).

Evidence-driven planner and operator for repository assessment, history
investigation, branch planning, and release execution. Public surface is kept
narrow on purpose; downstream code consumes the :class:`RepoEvidence` shape
rather than raw subprocess output.
"""

from __future__ import annotations

from .evidence import RepoEvidence, collect_repo_evidence

__all__ = ["RepoEvidence", "collect_repo_evidence"]
__version__ = "1.0.0"
