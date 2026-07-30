"""Git release captain evidence layer.

Read-only wrappers around general_ludd.git_automation that produce the
evidence records defined in docs/specs/FEATURE_GIT_RELEASE_CAPTAIN_EXPERT.md
§5. No function in this package mutates the repository: no fetches, no index
writes, no ref moves, no config changes.
"""

from general_ludd.git_release.evidence import (
    DirtyPath,
    NotARepoError,
    Operation,
    Policy,
    RepoEvidence,
    Upstream,
    WorktreeEvidence,
    collect_repo_evidence,
)
from general_ludd.git_release.topology import describe_topology

__all__ = [
    "DirtyPath",
    "NotARepoError",
    "Operation",
    "Policy",
    "RepoEvidence",
    "Upstream",
    "WorktreeEvidence",
    "collect_repo_evidence",
    "describe_topology",
]
