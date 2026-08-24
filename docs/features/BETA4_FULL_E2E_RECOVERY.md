# Beta4 Full-E2E Recovery

## Incident

The beta4 full-E2E run exposed three independent contract failures:

- the authentication test treated every public `GET /readyz` response as a
  readiness success, although the full harness intentionally keeps an
  automatically-created event loop unavailable until an explicit probe task is
  installed; and
- the Azure Container App materializer copied the vLLM module into its isolated
  Terraform root but left the cost-watchdog module outside that root, with an
  invalid `../../modules` reference. `terraform init` therefore failed before
  validation with an unreadable-module error; and
- two enforcement simulators were grouped correctly inside one xdist run but
  still raced on global `/tmp/gludd-*` files when the outer E2E scheduler ran
  the test files in independent pytest processes.

## Runtime contracts

Authentication and readiness are separate signals. `/healthz`, `/readyz`,
`/docs`, and `/openapi.json` remain public even when protected routes fail
closed because no PSK is configured. `/readyz` returns 503 while the daemon
cannot accept work; that status is not an authentication denial. The regression
checks both sides at once: public paths never return the authentication error,
while a protected admin path does.

An Azure Container App validation directory is now self-contained. One ordered
module inventory drives both source localization and asset copying, so the vLLM
and cost-watchdog references cannot drift independently. Missing repository
assets fail before Terraform starts instead of silently producing a partial
root.

Mutable test state is scoped at the process boundary that owns it. Direct
enforcement-test invocations retain the production-compatible `/tmp` default,
while the full runner assigns each test file a private state directory below
its namespaced resource root. One validated path helper is shared by both
simulators. Xdist grouping continues to serialize their tests inside a pytest
process, but correctness no longer depends on grouping crossing process
boundaries where it cannot operate.

## Practitioner evidence

HashiCorp Terraform issue
[#23333](https://github.com/hashicorp/terraform/issues/23333), opened
2019-11-09, records years of practitioner reports around upward-traversing local
module paths and `Unreadable module directory`; maintainers confirmed that
root-local `./...` paths behaved correctly. Issue
[#22785](https://github.com/hashicorp/terraform/issues/22785), opened
2019-09-12 and still open when reviewed on 2026-08-20, includes a 2022 CI report
where a packaged module tree accidentally omitted nested modules. Both reports
support shipping a complete, root-local module graph rather than depending on
the source checkout's parent layout.

Kubernetes issue
[#122591](https://github.com/kubernetes/kubernetes/issues/122591), opened
2024-01-04 and still open when reviewed on 2026-08-20, documents the operational
difference between readiness and liveness: a failed probe correctly removes a
workload from service without necessarily implying process death. Long-lived
issue [#89898](https://github.com/kubernetes/kubernetes/issues/89898), opened
2020-04-06, records intermittent readiness failures under real resource and
connection pressure. Those reports reinforce that a 503 readiness signal must
not be reinterpreted as a failed public-authentication contract.

Pytest issue
[#11790](https://github.com/pytest-dev/pytest/issues/11790), opened 2024-01-08,
documents user reports that separate concurrent pytest invocations can collide
even when each invocation expects a unique `tmp_path`; the accepted remedy is
to uniqueify the temporary-root prefix. Pytest-xdist issue
[#18](https://github.com/pytest-dev/pytest-xdist/issues/18), open since
2015-12-02, explains that grouping and fixture locality are scheduler concepts
within one xdist controller. Together they support per-invocation state roots
instead of assuming an xdist group coordinates independently launched pytest
processes.

## Committed-head gate follow-up

A later committed-head gate exposed four portability assumptions: token-bucket
tests measured scheduler time, a tiny Diffie-Hellman test group assumed random
samples were unique, the base dependency set tried to run an OpenCV media test,
and a nested E2E command did not quote its private basetemp. The same run found
a stale hard-coded deploy-key name and an unused async database double that
leaked an unawaited coroutine warning.

Uv issue
[#14645](https://github.com/astral-sh/uv/issues/14645), opened 2025-07-16,
records a CI user finding that an optional dependency is absent unless the
corresponding `--extra` is explicitly supplied to `uv sync`. The opencv-python
project tells server and CI users to select exactly one headless wheel, while
issue [#677](https://github.com/opencv/opencv-python/issues/677), open since
2022-06-10, records conflicts caused by installing overlapping OpenCV variants.
Gludd therefore keeps OpenCV out of the core runtime and installs the locked
`game-e2e` extra only in the dedicated GHE job.

Rate-limit arithmetic now uses an injected monotonic clock in tests, so host
scheduler timing is outside the acceptance boundary. The game job performs a
frozen optional-extra sync before running its media suite; base-runtime gates
skip only the media node when OpenCV is absent. Every E2E file retains an
independently quoted basetemp below its owned run root.

This changes only tests, their dedicated CI environment, and ephemeral runner
state. A running Gludd deployment is neither restarted nor migrated. Rollback
is a code/workflow revert, with no database, model, tag, or release-asset
mutation. The CI runner owns and removes its basetemp through the existing
bounded teardown path.

## Dual-track committed-head release contract

Beta4 validation is not a local-first, hosted-later sequence. Each complete
change is committed and pushed to its feature branch as soon as its focused
checks and collection gate pass. GHA/GHE must then report a run for that exact
commit SHA while the full local committed-head gate runs concurrently. A local
result from an uncommitted tree cannot substitute for hosted evidence, and a
hosted result for an earlier SHA cannot validate later local edits.

Every repair discovered by either lane is a separate focused, tested commit and
is pushed immediately so both lanes reconverge on the new SHA. Promotion from
the feature branch to `development`, from `development` to `master`, and the
`v0.1.0-beta.4` tag each require local and hosted green results for the same
immutable commit. A failed or absent hosted run blocks promotion even when all
local checks pass. This ordering keeps rollback code-only, exposes platform and
runner differences early, and prevents a long uncommitted local sweep from
delaying the CI feedback that the release depends on.

## Hosted runner boundary evidence

The first exact-SHA hosted run under this contract exposed three boundaries that
the macOS development host could not exercise. Docker's bridge translated the
loopback-published health request to the bridge gateway address, the Ansible EE
smoke invoked ambient `python3` instead of the definition's managed interpreter,
and one oversized unit shard plus a healthy Molecule shard reached their step or
job ceilings while still making progress.

Docker's current
[port-publishing documentation](https://docs.docker.com/engine/network/port-publishing/)
describes the default bridge's NAT and masquerading behavior. The smoke now
discovers that runner's bridge gateway and admits only its exact IPv4 `/32`, in
addition to loopback. It never admits `0.0.0.0/0`, and the application default
remains loopback-only. The
[Ansible Builder definition reference](https://ansible.readthedocs.io/_/downloads/builder/en/latest/pdf/)
defines `python_interpreter.python_path` as the interpreter selected for an
execution environment; the smoke therefore invokes the configured
`/usr/bin/python3.11` directly rather than relying on container `PATH`.

GitHub Community discussion
[#26679](https://github.com/orgs/community/discussions/26679), opened
2019-12-04 with follow-up reports through 2024, records practitioners finding
that explicit job and step timeouts terminate otherwise live work. Discussion
[#66522](https://github.com/orgs/community/discussions/66522), opened
2023-09-08, similarly documents hosted jobs receiving shutdown signals after
runner-image or scheduling changes. Gludd keeps finite ceilings, partitions the
oversized `unit-3` range into disjoint lanes, and budgets Molecule for two
bounded attempts plus teardown and artifact publication.

The hosted split is also the canonical local split. The named-shard expander
uses the same `unit-3a` (`n`--`r`) and `unit-3b` (`s`--`z` plus secrets)
patterns as the workflow, and an exhaustive contract proves that every unit
test file has exactly one execution lane. The retired monolithic `unit-3` name
fails closed instead of silently selecting a different local test surface.

The same run also exposed an environment-ownership race inside `unit-2`.
`test_guardrails` launched the `healthcheck` and `ansible-syntax` Make targets
from an already-running pytest shard. Those targets used ordinary `uv run`,
which is documented to lock and synchronize the project environment before
launch. A nested sync could therefore replace `.venv` while sibling xdist
workers were still importing packages or spawning `sys.executable`. The
[uv synchronization guide](https://docs.astral.sh/uv/concepts/projects/sync/)
documents `uv run --no-sync` as the supported way to consume an already
prepared environment without checking or updating it. Both nested targets now
use that boundary, and the mock-daemon tests no longer fall back to an ambient
interpreter or skip when their owned interpreter disappears.

Named shards now also apply `-W error` in both the hosted adaptive runner and
the local `test-ci-shard`/`test-ci-shard-slice` replicas. A warning therefore
has the same fail-closed meaning in both lanes. This immediately found two
health-check tests that constructed coroutine objects during registration
instead of providing the documented callable; those tests now retain callable
ownership until execution and leave no unawaited coroutine for garbage
collection.

The first warning-strict local replay then exposed a second ownership boundary:
`urllib.error.HTTPError` is both an exception and a file-like response. A
successful `urlopen` call can enter a context manager, but a raised HTTP error
must be closed from the exception path itself. CPython's
[HTTPError implementation](https://github.com/python/cpython/blob/main/Lib/urllib/error.py)
documents that dual role, and its own
[urllib regression tests](https://github.com/python/cpython/blob/main/Lib/test/test_urllib.py)
explicitly close caught HTTP errors. A 2025 practitioner report in CPython issue
[#132210](https://github.com/python/cpython/issues/132210) shows the same easy
failure mode in real exporter code: the error body is consumed through `e.fp`
after `urlopen` raises. Gludd now closes both successful and error responses,
streams downloads into an owned same-directory temporary file, fsyncs it, and
atomically replaces the destination. Failure removes the temporary file and
leaves the prior model untouched.

The same replay found a timing-test false positive at sub-millisecond scale:
one 0.4 ms config reload exceeded 2.5 times a 0.2 ms sample average while still
sitting orders of magnitude below the package's real import budget. The
pytest-benchmark maintainers explain in issue
[#186](https://github.com/ionelmc/pytest-benchmark/issues/186) that calibrated
microbenchmarks require rounds and iterations rather than a handful of raw
timer samples. Gludd's lightweight smoke keeps its ratio guard but applies a
1 ms absolute jitter floor; the independent 20--100 ms package ceilings remain
unchanged. This removes scheduler-noise failures without weakening the actual
startup-performance contract.

The Python 3.11 hosted lane also reports the origin of ``str | None`` as
``types.UnionType``, while Python 3.14 aliases that runtime form with
``typing.Union``. CPython's current
[typing reference](https://github.com/python/cpython/blob/main/Doc/library/typing.rst)
explicitly requires compatibility checks to admit either origin. The immutable
NamedTuple audit now follows that cross-version contract instead of treating the
3.11 representation as a mutable container.

A clean hosted checkout correctly lacks the untracked ``.gate-status`` runtime
snapshot that may remain after a local gate. The observability tests therefore
parse an isolated, test-owned snapshot and separately verify the Makefile's
atomic publisher; they never require prior local activity. This is the inverse
of the persistent-worktree failure reported by practitioners in
[actions/checkout issue #1475](https://github.com/actions/checkout/issues/1475):
test correctness cannot depend on either retained or pre-generated checkout
state.

Finally, a 500-character permission subject reached a filesystem lookup on
macOS but raised ``ENAMETOOLONG`` on the Linux runner. A related cross-platform
practitioner report in CPython issue
[#122353](https://github.com/python/cpython/issues/122353) records different
errors when oversized path inputs reach platform filesystem APIs. Gludd now
validates the UTF-8 filename component against the portable 255-byte boundary
before I/O. Read-only lookup still returns the in-memory default spec, while a
write fails closed with a stable 400 response and creates no partial file. The
same hosted replay corrected a contradictory cleanup assertion: dead SQLAlchemy
engine identities are weakly owned, collected, and removed rather than retained
in the process-global idempotence set.

Unit 3b then exposed an ordering defect in the sliding-window median. The
outgoing value was marked and pruned before its effective heap membership was
charged, so the balance could be updated against a different heap top. CPython's
[heapq reference](https://github.com/python/cpython/blob/main/Doc/library/heapq.rst)
defines the running-median invariant as two balanced heaps and separately
describes lazy removal of marked entries. A practitioner report in
[sortedcontainers issue #83](https://github.com/grantjenks/python-sortedcontainers/issues/83)
shows the broader failure mode: removal becomes incorrect when lookup and
ordering assumptions diverge. Gludd now charges the outgoing value to its
current partition before either heap is pruned, removes exhausted tombstones,
and checks every randomized emitted window against ``statistics.median``.

This failure mode has a longer practitioner history. astral-sh/uv issue
[#12751](https://github.com/astral-sh/uv/issues/12751), opened 2025-04-07,
reports failures when multiple parallel tasks invoke syncing `uv run`
operations, while issue
[#11454](https://github.com/astral-sh/uv/issues/11454), opened 2025-02-12,
records repeated `pytest` spawn failures when a virtual environment is moved or
recreated. Gludd prepares dependencies once before a shard and treats the venv
as immutable for the shard lifetime. Synchronization remains an explicit setup
phase; nested checks are read-only consumers.

Hosted Unit 1b then surfaced an owned-pipe defect that local collection order
had not made visible: the streaming E2E logger killed and waited for its child
but retained the `stdout=PIPE` file object until garbage collection. CPython's
[subprocess reference](https://github.com/python/cpython/blob/main/Doc/library/subprocess.rst)
states that a `Popen` context manager closes standard file descriptors and waits
for the process on exit. Practitioner investigation in CPython issue
[#114177](https://github.com/python/cpython/issues/114177) documents that
subprocess pipe descriptors can otherwise remain orphaned until interpreter
shutdown and surface only as finalizer `ResourceWarning`s. Gludd now keeps its
existing bounded kill/wait and live-output thread, while the `Popen` context
owns final descriptor closure on success and timeout. The regression asserts
the real child pipe is closed, so no test-only cleanup compensates for the
application helper.

The hosted `other` shard exposed the same ownership error at the async database
boundary: all HITL assertions passed, but fifteen short-lived SQLite engines and
their HTTP clients survived until interpreter finalization. SQLAlchemy's
[engine disposal reference](https://github.com/sqlalchemy/sqlalchemy/blob/main/doc/build/core/connections.rst)
explicitly lists test suites with ad-hoc engines as a case for deterministic
`dispose()` and warns against relying on garbage collection. Aiosqlite's
[connection finalizer](https://github.com/omnilib/aiosqlite/blob/main/aiosqlite/core.py)
likewise emits `ResourceWarning` when a live connection is deleted before
`close()` and directs callers to async context ownership. In the practitioner
discussion
[#10457](https://github.com/sqlalchemy/sqlalchemy/discussions/10457), users
reported hundreds of aborted async connections; the maintainer clarified on
2024-02-15 that connections must be explicitly closed inside the active event
loop before garbage collection. Discussion
[#10857](https://github.com/sqlalchemy/sqlalchemy/discussions/10857) records the
same fixture-level `await engine.dispose()` pattern for pytest. Reviewed on
2026-08-24, this evidence supports one async fixture that creates the engine in
the test's loop, owns the client with `async with`, and disposes the engine in a
`finally` block. There is no post-test collector, retry, sleep, or external
cleanup task to hide a missing owner.

## Verification and resources

The focused matrix runs one deterministic authentication/readiness test in both
normal and `GLUDD_E2E_ACTIVE=1` modes, the Azure materialization unit family,
the real state-free Terraform init/validate E2E, and both enforcement files in
two simultaneous outer-scheduler processes. The tests use pytest-owned
temporary directories, two bounded workers, no cloud credentials, and no cloud
resource creation. Every copied module is repository-owned and contains no
state or secrets.

## ZDD and rollback

The change affects newly-created validation and deployment directories only.
Existing Terraform state and running deployments are untouched, so rollout is
zero-downtime. Rollback is a code-only revert: no state migration is required.
During a mixed-version rollout, old daemon processes continue using their
already-materialized directories while new processes produce the complete
root-local module graph. Enforcement simulator state is ephemeral beneath each
file's E2E base directory and is removed by the existing bounded runner cleanup;
it never mutates a live plugin session's shared state during a full-suite run.
