"""Cohesive database repository implementations behind the stable facade."""

from general_ludd.db.repositories.memory import MemoryRepository
from general_ludd.db.repositories.messaging import AgentMessageRepository, AuditEventRepository
from general_ludd.db.repositories.metrics import (
    BenchmarkRepository,
    ModelPerformanceRepository,
    PromptProfileRepository,
    RoleRunRepository,
    SpendRepository,
)
from general_ludd.db.repositories.operations import (
    HumanTodoRepository,
    QueueRepository,
    RemediationActionRepository,
    SlurmJobRepository,
)
from general_ludd.db.repositories.projects import (
    FeatureRepository,
    ProjectRelationshipRepository,
    ProjectRepository,
    VariableNamespaceRepository,
)
from general_ludd.db.repositories.shared import ConcurrencyError, InvalidTransitionError, scoped_to
from general_ludd.db.repositories.todos import TaskReturnRepository, TodoRepository

__all__ = (
    "AgentMessageRepository",
    "AuditEventRepository",
    "BenchmarkRepository",
    "ConcurrencyError",
    "FeatureRepository",
    "HumanTodoRepository",
    "InvalidTransitionError",
    "MemoryRepository",
    "ModelPerformanceRepository",
    "ProjectRelationshipRepository",
    "ProjectRepository",
    "PromptProfileRepository",
    "QueueRepository",
    "RemediationActionRepository",
    "RoleRunRepository",
    "SlurmJobRepository",
    "SpendRepository",
    "TaskReturnRepository",
    "TodoRepository",
    "VariableNamespaceRepository",
    "scoped_to",
)
