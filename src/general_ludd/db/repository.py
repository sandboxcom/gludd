"""Repository implementations for the agentic harness."""

from __future__ import annotations

from general_ludd.db.models import TodoModel as TodoModel
from general_ludd.db.repositories.memory import MemoryRepository as MemoryRepository
from general_ludd.db.repositories.messaging import (
    BROADCAST_RECIPIENT as BROADCAST_RECIPIENT,
)
from general_ludd.db.repositories.messaging import (
    AgentMessageRepository as AgentMessageRepository,
)
from general_ludd.db.repositories.messaging import (
    AuditEventRepository as AuditEventRepository,
)
from general_ludd.db.repositories.metrics import (
    BenchmarkRepository as BenchmarkRepository,
)
from general_ludd.db.repositories.metrics import (
    ModelPerformanceRepository as ModelPerformanceRepository,
)
from general_ludd.db.repositories.metrics import (
    PromptProfileRepository as PromptProfileRepository,
)
from general_ludd.db.repositories.metrics import RoleRunRepository as RoleRunRepository
from general_ludd.db.repositories.metrics import SpendRepository as SpendRepository
from general_ludd.db.repositories.operations import (
    HUMAN_TODO_CATEGORIES as HUMAN_TODO_CATEGORIES,
)
from general_ludd.db.repositories.operations import (
    HUMAN_TODO_PRIORITIES as HUMAN_TODO_PRIORITIES,
)
from general_ludd.db.repositories.operations import (
    HUMAN_TODO_STATUSES as HUMAN_TODO_STATUSES,
)
from general_ludd.db.repositories.operations import (
    HUMAN_TODO_TERMINAL as HUMAN_TODO_TERMINAL,
)
from general_ludd.db.repositories.operations import HumanTodoRepository as HumanTodoRepository
from general_ludd.db.repositories.operations import QueueRepository as QueueRepository
from general_ludd.db.repositories.operations import (
    RemediationActionRepository as RemediationActionRepository,
)
from general_ludd.db.repositories.operations import SlurmJobRepository as SlurmJobRepository
from general_ludd.db.repositories.projects import FeatureRepository as FeatureRepository
from general_ludd.db.repositories.projects import (
    ProjectRelationshipRepository as ProjectRelationshipRepository,
)
from general_ludd.db.repositories.projects import ProjectRepository as ProjectRepository
from general_ludd.db.repositories.projects import (
    VariableNamespaceRepository as VariableNamespaceRepository,
)
from general_ludd.db.repositories.shared import ConcurrencyError as ConcurrencyError
from general_ludd.db.repositories.shared import (
    InvalidTransitionError as InvalidTransitionError,
)
from general_ludd.db.repositories.shared import _is_locked_error as _is_locked_error
from general_ludd.db.repositories.shared import scoped_to as scoped_to
from general_ludd.db.repositories.todos import _MAX_PRIORITY as _MAX_PRIORITY
from general_ludd.db.repositories.todos import _MIN_PRIORITY as _MIN_PRIORITY
from general_ludd.db.repositories.todos import _PRIORITY_LABELS as _PRIORITY_LABELS
from general_ludd.db.repositories.todos import (
    _TODO_STR_FIELD_MAX_BYTES as _TODO_STR_FIELD_MAX_BYTES,
)
from general_ludd.db.repositories.todos import (
    ALLOWED_TODO_CREATE_FIELDS as ALLOWED_TODO_CREATE_FIELDS,
)
from general_ludd.db.repositories.todos import VALID_TRANSITIONS as VALID_TRANSITIONS
from general_ludd.db.repositories.todos import TaskReturnRepository as TaskReturnRepository
from general_ludd.db.repositories.todos import TodoRepository as TodoRepository
from general_ludd.db.repositories.todos import _todo_dependency_ids as _todo_dependency_ids

# Historical monkeypatch seam used by every extracted bounded-list query.
_DEFAULT_LIST_LIMIT = 1000

# Keep the facade's static optimistic-concurrency evidence discoverable to AST
# consumers while the implementation lives in ``repositories.todos``.
_TODO_VERSION_COLUMN = TodoModel.version
