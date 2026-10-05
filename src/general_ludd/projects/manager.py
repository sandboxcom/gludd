"""Manage weighted project dispatch, persistence, and workspace materialization."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import subprocess
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from general_ludd.db.repository import (
        ProjectRelationshipRepository,
        ProjectRepository,
    )

logger = logging.getLogger(__name__)

_VALID_RELATION_TYPES = frozenset({"parent", "child", "sibling", "external"})
_VALID_LOCATION_KINDS = frozenset({"gludd_project_name", "directory", "url"})


def _stable_config_project_id(project: dict[str, Any]) -> str:
    """Derive a restart-stable identity for a project lacking an explicit id."""
    explicit = project.get("project_id")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    name = str(project.get("name", "unnamed")).strip() or "unnamed"
    identity = f"configured-project:{name}"
    return f"proj-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:8]}"


def _normalize_repo_url(url: str) -> str:
    """Normalize a git remote URL for equality comparison.

    Collapses the common spellings of the same remote so a project registered
    with one form (e.g. ``git@github.com:sandboxcom/gludd.git``) matches the
    self-repo URL detected from another form (e.g.
    ``https://github.com/sandboxcom/gludd``):

      * strip a trailing ``.git``
      * lowercase
      * drop scheme (``https://`` / ``http://`` / ``ssh://``)
      * drop userinfo (``git@host:`` scp form and ``user@host/`` URI form)
      * convert the scp ``:`` separator to ``/``

    The result is a canonical ``host/owner/repo`` string with no trailing slash.
    """
    u = (url or "").strip().lower()
    if u.endswith(".git"):
        u = u[:-4]
    if u.startswith("ssh://"):
        u = u[6:]
    elif u.startswith("https://"):
        u = u[8:]
    elif u.startswith("http://"):
        u = u[7:]
    # scp-like form: git@github.com:owner/repo  ->  github.com/owner/repo
    if u.startswith("git@"):
        u = u[4:]
        u = u.replace(":", "/", 1)
    # drop any remaining userinfo before the first '/' (user@host/...)
    if "@" in u.split("/", 1)[0]:
        u = u.split("@", 1)[1]
    return u.strip("/")


def _resolve_self_repo_url() -> str:
    """Resolve the running daemon's own repo URL.

    Priority:
      1. ``GLUDD_SELF_REPO_URL`` env var (explicit operator override)
      2. ``git remote get-url origin`` in the daemon's CWD (the live checkout)
      3. empty string (detection disabled — nothing matches)

    Failures (not a git repo, git missing, timeout) return ``""`` rather than
    raising — detection must be best-effort, never block daemon startup.
    """
    override = os.environ.get("GLUDD_SELF_REPO_URL", "")
    if override.strip():
        return override.strip()
    try:
        proc = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return ""


def _detect_self_project(
    project: ProjectWeight, self_repo_url: str | None = None
) -> bool:
    """True when ``project.repo_url`` is the gludd repo itself.

    Compares the project's ``repo_url`` against the daemon's own repo URL. Both
    sides are normalized via :func:`_normalize_repo_url` so SSH/HTTPS/SCP
    spellings of the same remote match. An explicit ``self_repo_url`` override
    (used by tests and config) takes precedence over auto-detection. An empty
    project URL never matches (an unconfigured project cannot be "self").
    """
    project_url = getattr(project, "repo_url", "") or ""
    if not project_url:
        return False
    target = self_repo_url if self_repo_url is not None else _resolve_self_repo_url()
    if not target:
        return False
    return _normalize_repo_url(project_url) == _normalize_repo_url(target)


def _infer_location_kind(location: str) -> str:
    """Infer the ``location_kind`` DISCRIMINATOR of an explicitly-declared edge.

    Inference is ONLY for the ``kind`` discriminator — never for the existence or
    target of a relationship (those are always operator-declared). Rules:
      - contains a URL scheme (``://``) -> ``url``
      - path-like (contains ``/`` or starts with ``.``) -> ``directory``
      - otherwise -> ``gludd_project_name``
    """
    loc = (location or "").strip()
    if "://" in loc:
        return "url"
    if "/" in loc or loc.startswith("."):
        return "directory"
    return "gludd_project_name"


def normalize_relationship_config(rel: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize one declared ``relationships:`` entry into an edge dict.

    Accepts the config shape (``relation``/``location``/``kind``/
    ``controlled_by_gludd``/``interface_hint``/``interface_contract``) and returns
    a normalized dict keyed by the ORM column names, or ``None`` when the entry is
    malformed (missing/invalid ``relation`` or empty ``location``). ``kind`` is
    inferred from ``location`` only when omitted. The returned dict carries
    ``related_project_id=None`` (resolution happens at persist time against the
    project set) so it is directly consumable by ``ProjectRelationshipModel``.
    """
    if not isinstance(rel, dict):
        return None
    relation = str(rel.get("relation", "")).strip().lower()
    location = str(rel.get("location", "")).strip()
    if relation not in _VALID_RELATION_TYPES or not location:
        logger.warning(
            "Skipping malformed relationship (relation=%r location=%r)",
            relation,
            location,
        )
        return None
    kind = str(rel.get("kind", "")).strip().lower()
    if kind not in _VALID_LOCATION_KINDS:
        kind = _infer_location_kind(location)
    contract = rel.get("interface_contract", {})
    if isinstance(contract, dict):
        contract_text = json.dumps(contract)
    elif isinstance(contract, str) and contract.strip():
        contract_text = contract
    else:
        contract_text = "{}"
    interface_hint = rel.get("interface_hint")
    edge: dict[str, Any] = {
        "relation_type": relation,
        "location_kind": kind,
        "location_value": location,
        "controlled_by_gludd": bool(rel.get("controlled_by_gludd", False)),
        "interface_hint": str(interface_hint) if interface_hint else None,
        "interface_contract": contract_text,
        "related_project_id": None,
    }
    # Record whether the operator EXPLICITLY declared control, so persist-time
    # resolution can default a resolved gludd neighbor to controlled without
    # overriding an explicit operator choice.
    if "controlled_by_gludd" in rel:
        edge["_controlled_explicit"] = True
    return edge


def parse_relationships(pcfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse a project config block's ``relationships:`` list into edge dicts.

    Malformed entries are skipped (with a warning); a missing/non-list
    ``relationships`` yields an empty list.
    """
    raw = pcfg.get("relationships", [])
    if not isinstance(raw, list):
        return []
    edges: list[dict[str, Any]] = []
    for rel in raw:
        normalized = normalize_relationship_config(rel)
        if normalized is not None:
            edges.append(normalized)
    return edges


@dataclass
class ProjectWeight:
    """Describe one project's dispatch allocation and runtime metadata."""

    project_id: str
    name: str
    weight: float
    description: str = ""
    config: dict[str, Any] = field(default_factory=dict)
    workspace_path: str = ""
    repo_url: str = ""
    dispatch_mode: str = "active"
    created_at: float = 0.0
    active: bool = True


class ProjectAllocationError(ValueError):
    """Raised when a requested project allocation violates weight invariants."""


class ProjectManager:
    """Maintain active projects under a finite 100-percent allocation budget."""

    def __init__(self) -> None:
        """Initialize an empty project allocation registry."""
        self._projects: dict[str, ProjectWeight] = {}

    def add_project(
        self,
        name: str,
        weight: float,
        description: str = "",
        workspace_path: str = "",
        repo_url: str = "",
        dispatch_mode: str = "active",
        project_id: str | None = None,
        **config: Any,
    ) -> ProjectWeight:
        """Register a project when its weight fits the remaining budget.

        Raises:
            ProjectAllocationError: If the requested weight would make active
                allocations exceed 100 percent.
        """
        total = sum(p.weight for p in self._projects.values() if p.active)
        if total + weight > 100.0:
            available = 100.0 - total
            raise ProjectAllocationError(
                f"Cannot allocate {weight}% to '{name}': only {available}% available (current total: {total}%)"
            )
        selected_project_id = project_id or f"proj-{uuid.uuid4().hex[:8]}"
        if selected_project_id in self._projects:
            raise ProjectAllocationError(
                f"Project '{selected_project_id}' is already registered"
            )
        import time

        project = ProjectWeight(
            project_id=selected_project_id,
            name=name,
            weight=weight,
            description=description,
            config=config,
            workspace_path=workspace_path,
            repo_url=repo_url,
            dispatch_mode=dispatch_mode,
            created_at=time.time(),
        )
        self._projects[selected_project_id] = project
        logger.info("Added project %s (%s) at %.1f%% mode=%s", name, selected_project_id, weight, dispatch_mode)
        return project

    def remove_project(self, project_id: str) -> None:
        """Mark a registered project inactive, ignoring unknown identifiers."""
        project = self._projects.get(project_id)
        if project is None:
            return
        project.active = False
        logger.info("Removed project %s (%s)", project.name, project_id)

    def set_weight(self, project_id: str, new_weight: float) -> None:
        """Set one active project's finite weight within the shared budget.

        Raises:
            ProjectAllocationError: If the project is missing or inactive, the
                weight is non-finite or out of range, or the total exceeds 100.
        """
        project = self._projects.get(project_id)
        if project is None:
            raise ProjectAllocationError(f"Project '{project_id}' not found")
        if not project.active:
            raise ProjectAllocationError(f"Project '{project_id}' is not active")
        # Reject NaN/inf up front: `NaN < 0` and `NaN > 100` are both False, so a
        # non-finite weight would slip past the range check, then poison
        # select_project's weighted-random math (total becomes NaN, every
        # `r <= cumulative` is False) and silently hand ALL work to the last
        # project — starving every other one.
        if not math.isfinite(new_weight):
            raise ProjectAllocationError(f"Weight must be a finite number, got {new_weight!r}")
        if new_weight < 0 or new_weight > 100:
            raise ProjectAllocationError(f"Weight must be between 0 and 100, got {new_weight}")
        others_total = sum(p.weight for p in self._projects.values() if p.active and p.project_id != project_id)
        if others_total + new_weight > 100.0:
            available = 100.0 - others_total
            raise ProjectAllocationError(
                f"Cannot set weight to {new_weight}%: only {available}% available for this project"
            )
        old_weight = project.weight
        project.weight = new_weight
        logger.info("Changed project %s weight from %.1f%% to %.1f%%", project.name, old_weight, new_weight)

    def rebalance(self, weights: dict[str, float]) -> None:
        """Atomically replace active weights with a finite 100-percent mapping.

        Raises:
            ProjectAllocationError: If a weight is non-finite, the total is not
                100 percent, or a referenced project is missing or inactive.
        """
        # Reject NaN/inf before summing: a non-finite weight makes `total` NaN,
        # `abs(NaN - 100) > 0.01` is False, so it would pass the sum check and
        # then poison select_project's weighted-random math (see set_weight).
        for pid, w in weights.items():
            if not math.isfinite(w):
                raise ProjectAllocationError(
                    f"Weight for '{pid}' must be a finite number, got {w!r}"
                )
        total = sum(weights.values())
        if abs(total - 100.0) > 0.01:
            raise ProjectAllocationError(f"Weights must sum to 100%, got {total:.1f}%")
        for pid, _w in weights.items():
            project = self._projects.get(pid)
            if project is None:
                raise ProjectAllocationError(f"Project '{pid}' not found")
            if not project.active:
                raise ProjectAllocationError(f"Project '{pid}' is not active")
        for pid, w in weights.items():
            self._projects[pid].weight = w
        logger.info("Rebalanced %d projects", len(weights))

    def get_project(self, project_id: str) -> ProjectWeight | None:
        """Return a project by identifier, including inactive projects."""
        return self._projects.get(project_id)

    def list_projects(self, active_only: bool = True) -> list[ProjectWeight]:
        """List registered projects, optionally filtering inactive entries."""
        projects = list(self._projects.values())
        if active_only:
            projects = [p for p in projects if p.active]
        return projects

    def list_active(self) -> list[ProjectWeight]:
        """List projects currently eligible for allocation."""
        return self.list_projects(active_only=True)

    def total_weight(self) -> float:
        """Return the sum of all active project weights."""
        return sum(p.weight for p in self._projects.values() if p.active)

    def get_allocation(self) -> dict[str, float]:
        """Map each active project identifier to its current weight."""
        return {p.project_id: p.weight for p in self._projects.values() if p.active}

    def select_project(self) -> ProjectWeight | None:
        """Select an active-dispatch project using weighted randomness."""
        import random

        active = [p for p in self.list_projects(active_only=True)
                  if p.dispatch_mode == "active"]
        if not active:
            return None
        weights = [p.weight for p in active]
        total = sum(weights)
        if total <= 0:
            return active[0]
        r = random.random() * total
        cumulative = 0.0
        for project in active:
            cumulative += project.weight
            if r <= cumulative:
                return project
        return active[-1]

    def get_summary(self) -> dict[str, Any]:
        """Summarize capacity and metadata for active and inactive projects."""
        projects = self.list_projects(active_only=False)
        active = [p for p in projects if p.active]
        return {
            "total_projects": len(projects),
            "active_projects": len(active),
            "total_weight": sum(p.weight for p in active),
            "unallocated": 100.0 - sum(p.weight for p in active),
            "projects": [
                {
                    "project_id": p.project_id,
                    "name": p.name,
                    "weight": p.weight,
                    "description": p.description,
                    "dispatch_mode": p.dispatch_mode,
                    "repo_url": p.repo_url,
                    "workspace_path": p.workspace_path,
                    "active": p.active,
                }
                for p in projects
            ],
        }


def materialize_project_workspace(
    repo_url: str,
    workspace_path: str,
    base_dir: str | None = None,
) -> str | None:
    """Clone ``repo_url`` into the project's workspace ``repo`` directory.

    W3.11 (H13): a dispatched job has no code to edit unless the project's
    repo_url is actually checked out. This uses the real ``GitAutomation.clone``
    (idempotent) — no ad-hoc shelling out.

    Returns the path to the materialized repo checkout, or ``None`` when there is
    no repo_url to clone. On clone failure returns ``None`` and logs a warning
    (the project still exists; it simply has no checkout yet).
    """
    if not repo_url:
        return None

    from general_ludd.git_automation.repo import GitAutomation, reject_unsafe_repo_url
    from general_ludd.projects.workspace import (
        ProjectWorkspace,
        confine_workspace_path,
        default_workspace_base,
    )

    selected_base = base_dir or default_workspace_base()

    # SECURITY: this is reachable unauthenticated (POST /admin/projects and DB
    # restore). Refuse a dangerous repo_url (ext:: RCE, file://, SSRF host) BEFORE
    # any git.clone — fail closed by returning None so nothing is ever cloned.
    try:
        safe_url = reject_unsafe_repo_url(repo_url)
    except ValueError as exc:
        logger.warning("Refusing unsafe repo_url %r: %s", repo_url, exc)
        return None

    # SECURITY: confine an untrusted workspace_path to base_dir (reject absolute /
    # '..' escape) BEFORE ensure_dirs(), so no directory is ever created outside
    # base_dir. An empty workspace_path falls back to a fixed in-base name.
    ws_pid = workspace_path or "default"
    try:
        confined_root = confine_workspace_path(
            selected_base,
            workspace_path or "default",
        )
    except ValueError as exc:
        logger.warning("Refusing unsafe workspace_path %r: %s", workspace_path, exc)
        return None

    ws = ProjectWorkspace(
        project_id=ws_pid,
        base_dir=selected_base,
        workspace_path=confined_root,
    )
    ws.ensure_dirs()
    repo_dir = str(ws.repo_dir)

    git = GitAutomation()
    result = git.clone(safe_url, repo_dir)
    if not result.success:
        logger.warning(
            "Failed to materialize workspace for %s: %s", repo_url, result.message
        )
        return None
    if result.already_present:
        logger.info("Workspace already materialized at %s", repo_dir)
    else:
        logger.info("Cloned %s into %s", repo_url, repo_dir)
    return repo_dir


async def persist_project(
    repo: ProjectRepository,
    *,
    project_id: str,
    name: str,
    weight: float,
    description: str = "",
    repo_url: str = "",
    workspace_path: str = "",
    dispatch_mode: str = "active",
) -> None:
    """Persist a project through ProjectRepository so restarts keep it.

    ProjectModel has no dedicated repo_url/weight/dispatch_mode columns, so those
    spine-relevant fields live in the JSON ``config`` column. ``rebuild_manager_from_db``
    reads them back. Idempotent: an existing project_id is updated, not duplicated.
    """
    cfg = {
        "repo_url": repo_url,
        "weight": weight,
        "dispatch_mode": dispatch_mode,
    }
    existing = await repo.get_by_id(project_id)
    if existing is not None:
        existing.name = name
        existing.description = description
        existing.workspace_path = workspace_path
        existing.config = json.dumps(cfg)
        existing.active = True
        return
    await repo.create(
        data={
            "project_id": project_id,
            "name": name,
            "description": description,
            "workspace_path": workspace_path,
            "config": json.dumps(cfg),
            "active": True,
        }
    )


async def rebuild_manager_from_db(repo: ProjectRepository) -> ProjectManager:
    """Build a fresh ProjectManager from the persisted (active) projects.

    Used on daemon startup so DB-persisted projects survive a restart instead of
    only living in config. repo_url/weight/dispatch_mode are read back from the
    JSON ``config`` column.
    """
    mgr = ProjectManager()
    import time

    for row in await repo.list_active():
        try:
            cfg = json.loads(row.config) if row.config else {}
        except (ValueError, TypeError):
            cfg = {}
        weight = float(cfg.get("weight", 0.0) or 0.0)
        project = ProjectWeight(
            project_id=row.project_id,
            name=row.name,
            weight=weight,
            description=row.description or "",
            workspace_path=row.workspace_path or "",
            repo_url=str(cfg.get("repo_url", "") or ""),
            dispatch_mode=str(cfg.get("dispatch_mode", "active") or "active"),
            created_at=time.time(),
            active=True,
        )
        mgr._projects[project.project_id] = project
    logger.info("Rebuilt ProjectManager from DB: %d active project(s)", len(mgr._projects))
    return mgr


def seed_from_config(config: dict[str, Any]) -> ProjectManager:
    """Build a manager from valid project entries in application configuration.

    Invalid entry shapes and allocations that exceed the shared budget are
    skipped, while declared relationship metadata is retained for persistence.
    """
    mgr = ProjectManager()
    projects_list = config.get("projects", [])
    if not isinstance(projects_list, list):
        return mgr
    for pcfg in projects_list:
        if not isinstance(pcfg, dict):
            continue
        try:
            project = mgr.add_project(
                name=pcfg.get("name", "unnamed"),
                weight=float(pcfg.get("weight", 10)),
                description=pcfg.get("description", ""),
                workspace_path=pcfg.get("workspace_path", ""),
                repo_url=pcfg.get("repo_url", ""),
                dispatch_mode=pcfg.get("dispatch_mode", "active"),
                project_id=_stable_config_project_id(pcfg),
            )
        except ProjectAllocationError:
            logger.warning("Skipping project '%s': allocation would exceed 100%%", pcfg.get("name", "?"))
            continue
        # Carry declared topology edges on the in-memory project so they can be
        # mapped to ProjectRelationshipModel rows at persist time. Declared >
        # inferred: only the location_kind discriminator may be inferred.
        edges = parse_relationships(pcfg)
        if edges:
            project.config["relationships"] = edges
    return mgr


async def persist_relationships_from_config(
    rel_repo: ProjectRelationshipRepository,
    project_id: str,
    edges: list[dict[str, Any]],
    *,
    name_to_id: dict[str, str] | None = None,
    workspace_to_id: dict[str, str] | None = None,
    repo_url_to_id: dict[str, str] | None = None,
) -> list[Any]:
    """Map parsed config relationship edges to ProjectRelationshipModel rows.

    For each normalized edge (from :func:`parse_relationships`), resolves
    ``related_project_id`` best-effort from the supplied lookups:
      - ``gludd_project_name`` -> ``name_to_id``
      - ``directory``          -> ``workspace_to_id``
      - ``url``                -> ``repo_url_to_id``
    An unresolved neighbor is kept with ``related_project_id=None`` (declared
    intent is preserved and re-resolves on the next rebuild). Upsert is idempotent
    on the unique edge tuple, so re-seeding does not create duplicate rows. A
    resolved gludd project defaults ``controlled_by_gludd=True`` unless the
    operator declared it False; an unresolved URL/dir keeps its declared default.
    Returns the persisted rows.
    """
    name_to_id = name_to_id or {}
    workspace_to_id = workspace_to_id or {}
    repo_url_to_id = repo_url_to_id or {}
    rows: list[Any] = []
    for edge in edges:
        data = dict(edge)
        controlled_explicit = bool(data.pop("_controlled_explicit", False))
        kind = data.get("location_kind")
        value = data.get("location_value", "")
        related: str | None = None
        if kind == "gludd_project_name":
            related = name_to_id.get(value)
        elif kind == "directory":
            related = workspace_to_id.get(value)
        elif kind == "url":
            related = repo_url_to_id.get(value)
        # Never resolve to self.
        if related == project_id:
            related = None
        data["related_project_id"] = related
        # A resolved gludd project is controllable unless the operator said otherwise.
        if related is not None and not controlled_explicit:
            data["controlled_by_gludd"] = True
        data["project_id"] = project_id
        rows.append(await rel_repo.add_relationship(data))
    return rows
