# Database repository split

The stable `general_ludd.db.repository` module remains the public facade while
repositories live in cohesive, cycle-free implementation modules. This is a
source-layout refactor only: constructors, method signatures, SQL ordering,
exceptions, and transaction ownership are unchanged.

## Boundaries

- `repositories/shared.py` owns tenant scoping, locked-error classification,
  the concurrency exception hierarchy, and the facade-aware list-limit port.
- `repositories/todos.py` owns todo and task-return aggregates.
- `repositories/projects.py` owns variables, projects, relationships, and
  features.
- `repositories/messaging.py` owns audit events and agent messages.
- `repositories/operations.py` owns queues, human todos, remediation, and
  Slurm jobs.
- `repositories/metrics.py` owns benchmarks, prompt profiles, spend, role runs,
  and model performance.
- `repositories/memory.py` owns agent-memory records.
- `repository.py` explicitly re-exports every moved repository, error, helper,
  and compatibility constant. Existing `general_ludd.db` imports retain exact
  object identity with both facade and implementation paths.

The implementation modules never import the facade. The list-limit port reads
an already-loaded facade module from `sys.modules`, preserving the established
`general_ludd.db.repository._DEFAULT_LIST_LIMIT` monkeypatch seam without a
reverse import or repository/session singleton.

## Transaction contract

Repository methods continue to add and flush against the injected
`AsyncSession`; they do not commit a caller-owned transaction. A compatibility
test writes a real model-performance row, rolls the caller's session back, and
proves a fresh session cannot observe the row.

This boundary matches long-running SQLAlchemy practitioner guidance:

- [Discussion #8554](https://github.com/sqlalchemy/sqlalchemy/discussions/8554)
  documents why a transaction and its session belong to the outer task and why
  concurrently sharing one `AsyncSession` is unsafe.
- [Discussion #10621](https://github.com/sqlalchemy/sqlalchemy/discussions/10621)
  explains that closing a session context does not imply a commit and that
  transaction ownership must be explicit.
- [Discussion #12140](https://github.com/sqlalchemy/sqlalchemy/discussions/12140)
  records the recurring failure caused by nesting transaction managers or
  committing inside an already active caller transaction.

Accordingly, extraction did not introduce commits, shared sessions, implicit
task-local state, or parallel use of one session.

## Verification

- Compatibility tests cover exact facade and package identities, constructor
  signatures, both cold import orders, the list-limit patch port, tenant
  filters, and caller-owned rollback.
- The warning-strict coverage suite passes 679 tests.
- Branch-aware component coverage is 92% overall. The aggregate line and branch
  metrics exceed 85%, and every measured file's line and branch metrics exceed
  75%.
- Physical line counts are: facade 78, `todos.py` 1,075, `metrics.py` 912,
  `operations.py` 577, `projects.py` 503, `messaging.py` 294, `memory.py` 194,
  and `shared.py` 48. The facade stays below 2,000 lines, and every component
  stays below the plan's 1,500-line target and the 2,500-line gate.
