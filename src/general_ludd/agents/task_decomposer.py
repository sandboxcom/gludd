"""AG.3 — Hierarchical task decomposition: CrewAI-style role-goal-backstory + manager-agent patterns.

Provides:
- ``RoleGoalBackstory``: formal role metadata (role, goal, backstory, tools).
- ``SubTask``: a decomposed unit of work with dependencies and role assignment.
- ``TaskDecomposer``: breaks complex tasks into ordered sub-tasks.
- ``ManagerAgent``: coordinates a team of roles, assigning sub-tasks by role match.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoleGoalBackstory:
    """Formal metadata for an agent role — the CrewAI role-goal-backstory triad."""

    role: str
    goal: str
    backstory: str
    tools: list[str] = field(default_factory=list)

    def __hash__(self) -> int:
        return hash(self.role)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, RoleGoalBackstory):
            return NotImplemented
        return self.role == other.role

    def __repr__(self) -> str:
        return f"RoleGoalBackstory(role={self.role!r})"


@dataclass
class SubTask:
    """A single decomposed unit of work within a larger task."""

    id: str
    description: str
    dependencies: list[str] = field(default_factory=list)
    assigned_role: str | None = None
    status: str = "pending"

    def __hash__(self) -> int:
        return hash(self.id)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SubTask):
            return NotImplemented
        return self.id == other.id

    def __repr__(self) -> str:
        return f"SubTask(id={self.id!r}, status={self.status!r})"


# ---------------------------------------------------------------------------
# Decomposition patterns — keyword-driven matching
# ---------------------------------------------------------------------------

_DECOMPOSITION_PATTERNS: dict[str, list[str]] = {
    "api": [
        "Define API contract and endpoint signatures",
        "Implement request validation and serialization",
        "Wire up business logic layer",
        "Add authentication and authorization middleware",
        "Write API integration tests",
        "Document the API with OpenAPI spec",
    ],
    "database": [
        "Design the database schema",
        "Write migration scripts",
        "Implement data access layer / repository",
        "Add data validation and constraints",
        "Set up database backup and recovery",
        "Write data layer tests",
    ],
    "authentication": [
        "Design authentication flow",
        "Implement user registration endpoint",
        "Implement login / token issuance",
        "Add password reset flow",
        "Implement role-based access control",
        "Write auth integration tests",
    ],
    "deploy": [
        "Create deployment configuration",
        "Set up CI/CD pipeline",
        "Configure monitoring and alerting",
        "Write infrastructure-as-code",
        "Perform staging deployment and smoke tests",
        "Execute production rollout",
    ],
    "test": [
        "Write unit tests for core logic",
        "Write integration tests for APIs",
        "Set up test fixtures and factories",
        "Add end-to-end test scenarios",
        "Configure test coverage reporting",
        "Run test suite and fix failures",
    ],
    "frontend": [
        "Design UI component hierarchy",
        "Build reusable component library",
        "Implement state management",
        "Wire up API client layer",
        "Add client-side routing",
        "Write frontend component tests",
    ],
    "pipeline": [
        "Design pipeline architecture",
        "Implement data ingestion stage",
        "Build transformation stage",
        "Add model training / inference stage",
        "Implement output / serving stage",
        "Write pipeline integration tests",
    ],
    "review": [
        "Read the target module thoroughly",
        "Identify code smells and anti-patterns",
        "Check for security vulnerabilities",
        "Verify test coverage and quality",
        "Assess documentation completeness",
        "Compile review report with recommendations",
    ],
}

_DEFAULT_DECOMPOSITION: list[str] = [
    "Analyze requirements and constraints",
    "Design the solution architecture",
    "Implement the core logic",
    "Write tests for the implementation",
    "Document the solution",
]

_ROLE_KEYWORD_MAP: dict[str, str] = {
    "backend": "backend_dev",
    "api": "backend_dev",
    "server": "backend_dev",
    "database": "backend_dev",
    "db": "backend_dev",
    "data": "backend_dev",
    "frontend": "frontend_dev",
    "ui": "frontend_dev",
    "ux": "frontend_dev",
    "component": "frontend_dev",
    "react": "frontend_dev",
    "css": "frontend_dev",
    "devops": "devops_engineer",
    "deploy": "devops_engineer",
    "infrastructure": "devops_engineer",
    "ci/cd": "devops_engineer",
    "ci": "devops_engineer",
    "pipeline": "devops_engineer",
    "kubernetes": "devops_engineer",
    "docker": "devops_engineer",
    "test": "qa_engineer",
    "qa": "qa_engineer",
    "quality": "qa_engineer",
    "security": "security_auditor",
    "auth": "security_auditor",
    "vulnerability": "security_auditor",
    "review": "code_reviewer",
    "ml": "ml_engineer",
    "machine learning": "ml_engineer",
    "model": "ml_engineer",
    "training": "ml_engineer",
    "design": "designer",
    "research": "researcher",
}


# ---------------------------------------------------------------------------
# TaskDecomposer
# ---------------------------------------------------------------------------


class TaskDecomposer:
    """Decomposes a complex task description into an ordered list of sub-tasks.

    Uses keyword matching against registered role backstories to produce
    role-specific decompositions.  Register roles via ``register_role()``
    so the decomposer can route sub-tasks to matching team members.
    """

    def __init__(self) -> None:
        self._roles: dict[str, RoleGoalBackstory] = {}

    def register_role(self, role: RoleGoalBackstory) -> None:
        self._roles[role.role] = role

    def list_roles(self) -> list[str]:
        return sorted(self._roles.keys())

    def decompose(self, task_description: str, agent_role: str) -> list[SubTask]:
        if not task_description.strip():
            return []

        lower = task_description.lower()
        matched_patterns: list[str] = []

        for keyword, steps in _DECOMPOSITION_PATTERNS.items():
            if keyword in lower:
                matched_patterns.extend(steps)

        if not matched_patterns:
            matched_patterns = list(_DEFAULT_DECOMPOSITION)

        subtasks: list[SubTask] = []
        for idx, step in enumerate(matched_patterns):
            task_id = str(idx + 1)
            dependencies: list[str] = []
            if idx > 0:
                dependencies = [str(idx)]

            assigned_role = self._match_role(step)

            subtasks.append(
                SubTask(
                    id=task_id,
                    description=step,
                    dependencies=dependencies,
                    assigned_role=assigned_role,
                )
            )

        return subtasks

    def _match_role(self, description: str) -> str | None:
        lower = description.lower()
        for keyword, role_name in _ROLE_KEYWORD_MAP.items():
            if keyword in lower and role_name in self._roles:
                return role_name
        if self._roles:
            return next(iter(self._roles.keys()))
        return None


# ---------------------------------------------------------------------------
# ManagerAgent
# ---------------------------------------------------------------------------


class ManagerAgent:
    """Coordinates a team of role-defined agents, assigning sub-tasks to members.

    A ManagerAgent holds a ``manager_role`` (its own role-goal-backstory) and a
    list of team members (each a ``RoleGoalBackstory``).  When ``assign_tasks()``
    is called, sub-tasks that already carry an ``assigned_role`` are matched
    directly; unassigned sub-tasks are routed by keyword match against team
    member roles.
    """

    def __init__(self, manager_role: RoleGoalBackstory) -> None:
        self.manager_role = manager_role
        self.team: list[RoleGoalBackstory] = []

    def add_team_member(self, member: RoleGoalBackstory) -> None:
        self.team.append(member)

    def assign_tasks(self, tasks: list[SubTask]) -> dict[str, RoleGoalBackstory | None]:
        """Assign each sub-task to the best-matching team member.

        Returns a mapping of sub-task id → RoleGoalBackstory (or None if no
        suitable member could be found).
        """
        member_lookup: dict[str, RoleGoalBackstory] = {
            m.role: m for m in self.team
        }
        assignments: dict[str, RoleGoalBackstory | None] = {}

        for task in tasks:
            if task.assigned_role is not None and task.assigned_role in member_lookup:
                assignments[task.id] = member_lookup[task.assigned_role]
            else:
                assignments[task.id] = self._best_match(task.description)

        return assignments

    def _best_match(self, description: str) -> RoleGoalBackstory | None:
        lower = description.lower()
        for member in self.team:
            keywords = set(member.goal.lower().split())
            keywords.update(member.role.lower().split("_"))
            if any(kw in lower for kw in keywords if len(kw) > 2):
                return member
        return self.team[0] if self.team else None
