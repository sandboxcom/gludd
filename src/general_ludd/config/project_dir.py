"""Project-local .gludd/ directory discovery + config overlay (Phase 1).

Walk UP from ``start`` (default: cwd) until we find an ancestor that contains
a ``.gludd/`` directory — git-root style.  A ``GLUDD_PROJECT_DIR`` env var
overrides the walk entirely, letting operators pin the directory explicitly.

Returns ``None`` when no ``.gludd/`` directory is found (not an error; the
daemon simply runs with the user-level config only).

See docs/design/PROJECT_LOCAL_GLUDD_DIR.md (request #17).
"""

from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path
from typing import Any

# H.7 / H-PROJECT-OVERLAY-DANGEROUS-FIELDS:
#
# An untrusted project config (.gludd/general-ludd.yml) must NEVER override
# security-critical infrastructure settings.  If a project could set e.g.
# ``database.url`` or ``self_improve.enabled``, a malicious or compromised
# repository could redirect the daemon's database, install self-improving
# code, bypass the budget cap, rewire connectors, or reconfigure issues
# polling — all without operator knowledge.
#
# The denylist below is the set of top-level UserConfig fields that a
# project overlay MUST NOT set.  Sub-fields of these are also blocked
# because the merge is deep (a project setting ``database.url`` wins over
# the user's ``database.port`` via recursive dict merge).
#
# Fields explicitly ALLOWED for project override:
#   rules, pipeline, compaction, remediation, orchestration,
#   relationship_routing, notifications, human_in_the_loop,
#   deletion_gate_threshold, use_langgraph_*, use_langchain_*,
#   use_hub, checkpointing, default_spot, slurm_*,
#   compute_idle_*, ornith_*
#
# Everything else — especially connectors, database, budget, issues,
# self_improve, agents, model_routing, model_profiles, network,
# observability, process_isolation, self_update — is DENIED.

PROJECT_OVERLAY_DENYLIST: frozenset[str] = frozenset(
    {
        "agents",
        "budget",
        "connectors",
        "database",
        "issues",
        "model_profiles",
        "model_routing",
        "network",
        "observability",
        "process_isolation",
        "self_improve",
        "self_update",
    }
)

# Allowlist: fields that a project overlay MAY set.  When set the validator
# checks keys against this list — a project may ONLY set fields explicitly
# listed here.  Behavioural / cosmetic fields only; security-posture fields
# (connectors, database, budget, issues, self_improve, etc.) are absent by
# design and can only be set by the operator's user-level config.
PROJECT_OVERLAY_ALLOWLIST: frozenset[str] = frozenset(
    {
        "rules",
        "pipeline",
        "compaction",
        "remediation",
        "orchestration",
        "relationship_routing",
        "notifications",
        "human_in_the_loop",
        "deletion_gate_threshold",
        "use_langgraph_tool_loop",
        "use_langchain_routing",
        "use_langchain_retry",
        "use_hub",
        "checkpointing",
        "default_spot",
        "slurm_max_resubmits",
        "slurm_preemption_backoff_schedule",
        "compute_idle_check_interval_ticks",
        "compute_idle_teardown_threshold_ticks",
        "compute_idle_gpu_sm_pct",
        "compute_idle_preemption_notice_ticks",
        "ornith_enabled",
        "ornith_binary_path",
        "ornith_model_sha",
        "ornith_max_iterations",
        "ornith_timeout_seconds",
    }
)


class ProjectOverlayValidationError(ValueError):
    """Raised when a project config overlay attempts to set dangerous fields."""


def validate_project_overlay(
    project_data: dict[str, Any],
    *,
    denylist: frozenset[str] | None = None,
    allowlist: frozenset[str] | None = None,
) -> None:
    """Reject dangerous field overrides from an untrusted project config.

    By default uses the module-level :data:`PROJECT_OVERLAY_DENYLIST` and
    :data:`PROJECT_OVERLAY_ALLOWLIST`.  Callers may pass explicit lists for
    testing or for operator-supplied overrides.

    Raises :class:`ProjectOverlayValidationError` if any key in
    *project_data* is forbidden.
    """
    denylist = PROJECT_OVERLAY_DENYLIST if denylist is None else denylist
    _allowlist = PROJECT_OVERLAY_ALLOWLIST if allowlist is None else allowlist

    forbidden: list[str] = []
    for key in project_data:
        if _allowlist is not None:
            if key not in _allowlist:
                forbidden.append(key)
        elif key in denylist:
            forbidden.append(key)

    if forbidden:
        raise ProjectOverlayValidationError(
            f"Project config overlay sets forbidden field(s): "
            f"{', '.join(sorted(forbidden))}. "
            f"Untrusted project configs must not override infrastructure settings."
        )


def find_project_gludd_dir(start: Path | None = None) -> Path | None:
    """Return the first ancestor of *start* that contains a ``.gludd/`` dir.

    Resolution order:
    1. ``GLUDD_PROJECT_DIR`` env var — if set, return that path directly
       (must point to the ``.gludd/`` directory itself, not its parent);
       returns ``None`` if it does not exist.
    2. Walk up from *start* (default: ``Path.cwd()``) to the filesystem root,
       returning the first directory that *contains* a ``.gludd/`` child.
    3. ``None`` — no ``.gludd/`` found anywhere in the ancestor chain.
    """
    env_override = os.environ.get("GLUDD_PROJECT_DIR")
    if env_override:
        p = Path(env_override)
        return p if p.is_dir() else None

    if start is None:
        try:
            current = Path.cwd().resolve()
        except FileNotFoundError:
            current = Path(__file__).resolve().parents[3]
    else:
        current = start.resolve()
    while True:
        candidate = current / ".gludd"
        if candidate.is_dir():
            return candidate
        parent = current.parent
        if parent == current:
            # Reached the filesystem root without finding .gludd/
            return None
        current = parent


def project_config_path(project_gludd_dir: Path | None = None) -> Path | None:
    """Return ``<.gludd>/general-ludd.yml`` if it exists, else ``None``.

    Args:
        project_gludd_dir: The ``.gludd/`` directory returned by
            :func:`find_project_gludd_dir`.  If ``None`` the function returns
            ``None`` immediately (no crash).
    """
    if project_gludd_dir is None:
        return None
    p = project_gludd_dir / "general-ludd.yml"
    return p if p.exists() else None


def merge_config(user: dict[str, Any], project: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge *project* on top of *user* (project wins on conflicts).

    Merge semantics:
    - **Scalars / None**: project value replaces user value.
    - **Dicts**: recursively merged — project keys win on collisions.
    - **Lists** (``rules``, ``queues``, ``connectors``, …): project value
      *replaces* the user list entirely.  This is intentional — list fields
      are declarative (a project override is expected to be the full list, not
      an append).  Callers that need append semantics must do so explicitly
      before calling this function.

    Neither *user* nor *project* is mutated; a structurally independent dict
    is returned so later mutations cannot change either input snapshot.
    """
    result = deepcopy(user)
    _merge_project_into(result, project)
    return result


def _merge_project_into(result: dict[str, Any], project: dict[str, Any]) -> None:
    """Overlay *project* onto an already detached result in place."""
    for key, proj_val in project.items():
        result_val = result.get(key)
        if isinstance(proj_val, dict) and isinstance(result_val, dict):
            _merge_project_into(result_val, proj_val)
        else:
            # Scalars and lists: project wins outright.
            result[key] = deepcopy(proj_val)
