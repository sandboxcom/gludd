# Model-performance recording lifecycle

S30 makes model-call telemetry durable and concurrency-safe without changing
the telemetry schema or the worker response contract.

## Problem and acceptance contract

The repository previously created one `AsyncSession` from its factory, cached
that session indefinitely, and only flushed new call rows. A flush does not
commit the surrounding transaction. Concurrent worker calls could therefore
share a stateful session, emit SQLAlchemy concurrency warnings, and lose rows
when the process ended. The async worker compounded the problem by calling a
synchronous adapter that invokes `asyncio.run()` from inside the running event
loop; its fail-soft exception handler hid the failed write from job callers.

The corrected contract is:

- an explicitly supplied session remains caller-owned and is flushed, but is
  neither committed nor closed by the repository;
- a factory-owned write receives one new `AsyncSession` and transaction via
  `async_sessionmaker.begin()`, commits on success, rolls back on failure, and
  closes before `record_call()` returns;
- the worker awaits `record_call()` in its existing async handler; and
- telemetry failure remains fail-soft: the job response succeeds while the
  worker logs the recording failure for operators.

## Mature dependency decision and practitioner evidence

This uses the SQLAlchemy 2.x lifecycle primitive already installed by Gludd;
there is no custom session pool, write queue, background task, or new package.
SQLAlchemy documents that
[`async_sessionmaker.begin()`](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#sqlalchemy.ext.asyncio.async_sessionmaker.begin)
provides a session and transaction that commits and closes on context exit. Its
[concurrent-task guidance](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#using-asyncsession-with-concurrent-tasks)
also requires a separate `AsyncSession` per concurrent task because the session
is mutable transaction state.

The production symptom is not novel. In the long-running upstream
[SQLAlchemy discussion #10621](https://github.com/sqlalchemy/sqlalchemy/discussions/10621),
users report assuming an async session context would commit and are directed to
an explicit transaction context. The older
[discussion #5980](https://github.com/sqlalchemy/sqlalchemy/discussions/5980)
documents why one session must not be shared by concurrent asyncio tasks. Those
reports support using the maintained transaction context instead of creating a
Gludd-specific locking or commit layer.

## Resources, observability, and failure behavior

Each factory-owned call uses exactly one session, one transaction, and one
append-only row. The engine pool remains the connection/concurrency bound. The
repository does not retain that session, and the worker creates no process,
thread, file, timer, queue, or detached asyncio task for telemetry. The focused
lifecycle test starts four concurrent writes, observes no warnings under
`-W error`, reads all four committed rows from a fresh session, and proves the
repository retains no session afterward.

Recording remains secondary to job execution. A database or commit failure is
caught at the worker boundary and logged with the job identifier; it never
turns an otherwise successful model job into an HTTP failure. Existing fields,
including the pre-existing error text field, are unchanged, and no prompt or
model-response content is newly persisted.

## Zero-downtime rollout and rollback

No migration, table change, configuration change, or API change is required.
Old and new workers can run together against the same append-only table during
a rolling deployment. New workers commit their own records; old workers remain
read-compatible with those rows. In-flight caller-owned transactions keep
their prior ownership semantics.

Deploy the repository and worker code as one normal application image and
observe recording-error logs plus row growth. Rollback is a source/image
rollback only: route new work to the preceding image and let in-flight requests
finish. Do not delete or rewrite telemetry rows, downgrade the database, or
drain a new background resource. The synchronous compatibility adapter remains
available to older out-of-loop callers, so mixed-version rollback does not
require a flag or state conversion.

## Verification

- `make test-specific TESTFILE=tests/integration/test_model_performance_recording_lifecycle.py::test_factory_owned_concurrent_records_commit_and_release_sessions PYTEST_ARGS='-W error'`
- `make test-specific TESTFILE=tests/unit/test_worker.py::TestModelPerformanceRecording PYTEST_ARGS='-W error'`
- `make test-files TESTFILES='tests/unit/test_model_performance_repo.py' PYTEST_ARGS='-W error'`
- `make coverage-files` with
  `config/coverage_model_performance_recording.ini`, aggregate minimum `85`,
  and per-file minimum `75`
- `make lint-files`, `make typecheck-scope`, `make lint-docstrings`, and
  `make lint-markdown` scoped to the S30 files
