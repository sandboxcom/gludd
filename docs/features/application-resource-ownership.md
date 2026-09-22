# Application resource ownership

Gludd records every application-owned process, asynchronous task, client,
temporary artifact, and service together with its acquisition site and teardown
evidence. The gate is semantic and fail-closed: a new, stale, duplicate-count,
or unowned record fails `make check-resource-ownership` before tests start.

## Contract

`config/resource_ownership_inventory.json` is generated only through the checker
write mode. Normal validation uses read-only mode and compares the current AST
evidence with the tracked inventory. Identity is the counted tuple of path,
resource kind, owner, and the acquisition/teardown source hash. Line and column
remain review coordinates, not identity, so inserting unrelated code cannot turn
one unchanged resource into simultaneous new and stale findings. Duplicate
semantic acquisitions remain count-preserving: two acquisitions require two
inventory records. Cleanup must be in the Gludd owner and cover success, failure,
cancellation, and shutdown. A test may assert cleanup, but it must not reap a
resource on Gludd's behalf.

Ownership may transfer only to an explicit application lifecycle boundary: a
class `close`/`aclose`, a FastAPI shutdown owner, a structured task group, or a
tracked registry that is cancelled and awaited. Injected clients and external
model endpoints remain caller-owned and are never stopped by Gludd.

Cloud compute uses a project-scoped composite identity: project, provider, and
instance identifier. The lifecycle manager permits the same provider-local
identifier in different projects without overwriting either owner. An unscoped
mutation is rejected when that identifier is ambiguous. Cleanup also fails
closed when no destroy callback is installed; the resource remains tracked
instead of being falsely reported as destroyed.

The same identity is persisted in the local restart registry and the shared
deployment database. Migration 047 backfills legacy rows to the `default`
project and changes the database primary key to project, provider, and instance.
Repository reads, destroy claims, successful deletion, and failed-claim release
all resolve that exact tuple. This prevents a worker in one project from reading,
overwriting, or destroying another project's coincidentally identical cloud ID.

The identity value object and project-ID validation live in the core schema
layer at `general_ludd.schemas.project_identity`. Infrastructure, cloud, and
deployment schemas depend on that neutral primitive; the higher-level
`general_ludd.projects.identity` module is only a compatibility export. This
keeps the ownership contract reusable by every workload without making core
schemas depend on project orchestration or self-improvement implementation.

Deleting a project is cleanup-first. Gludd destroys only resources attributed to
that project, verifies that none remain, and then deactivates the persisted
project and removes it from the scheduler. A failed or unavailable destroy path
returns HTTP 409 and preserves both the project and its ownership evidence. This
ordering keeps project deletion safe under retries and prevents one project from
destroying another project's compute.

The local game-model target exposes three explicit modes:

- `hermetic` uses the test-owned fake endpoint and is the safe default.
- `managed` forwards `LOCAL_MODEL_PATH`, runs the real managed inference
  acceptance, exercises game generation, and verifies Gludd shutdown.
- `external` requires `LOCAL_MODEL_BASE_URL`; Gludd uses but does not own or stop
  that service.

## Mature analysis and practitioner evidence

Research was refreshed on 2026-08-20. Ruff's
[SIM115](https://docs.astral.sh/ruff/rules/open-file-with-context-handler/) and
Pylint's
[R1732](https://pylint.readthedocs.io/en/stable/user_guide/messages/refactor/consider-using-with.html)
cover common file-like context managers. Python's
[asyncio task documentation](https://docs.python.org/3/library/asyncio-task.html)
recommends retaining task references and structured concurrency. None of those
tools expresses Gludd's cross-resource owner and shutdown-transfer contract, so
the repository checker layers that application-specific invariant on top of Ruff
and mypy instead of replacing them.

Long-lived practitioner reports show why acquisition alone is insufficient:

- The detect-secrets design notes that
  [line number is deliberately excluded from finding identity](https://github.com/Yelp/detect-secrets/blob/master/docs/design.md#potentialsecret)
  because code moves during ordinary iteration. Its 2019 practitioner report
  [#212](https://github.com/Yelp/detect-secrets/issues/212) records the concrete
  cost of line-number-only baseline churn: a hook rewrote the baseline and
  interrupted the commit even though no new secret existed. Gludd therefore
  treats coordinates as diagnostics while preserving semantic identity and
  occurrence counts.
- CPython issue [#79325](https://github.com/python/cpython/issues/79325), opened
  2018-11-02, documents `TemporaryDirectory` cleanup failures.
- CPython issue [#125502](https://github.com/python/cpython/issues/125502), opened
  in 2024, reports cancelled subprocess/task teardown that can leave a program
  hanging.
- HTTPX discussion
  [#2437](https://github.com/encode/httpx/discussions/2437), dated 2022-11-11,
  describes async-client cancellation during server shutdown.
- llama-cpp-python issue
  [#302](https://github.com/abetlen/llama-cpp-python/issues/302), opened
  2023-05-30, records model resources not being unloaded before another load.
- Terraform issue
  [#22301](https://github.com/hashicorp/terraform/issues/22301), opened in 2019,
  records the operational difficulty of moving a live resource to a new address
  without Terraform proposing replacement. A HashiCorp practitioner discussion,
  [How to move created resources to modules](https://discuss.hashicorp.com/t/how-to-move-created-resources-to-modules/32440),
  likewise centers explicit state movement to preserve ownership while refactoring.
  Gludd therefore keeps project identity stable and separate from incidental
  runtime coordinates.

These reports span several years and support an exact owner-side contract rather
than harness cleanup or garbage-collector finalizers.

## Zero-downtime deployment and rollback

The gate is static and introduces no runtime process. Land owner cleanup first,
write the inventory from that same commit, and then enable the gate dependency.
Existing services continue serving while new instances adopt deterministic
shutdown; no endpoint ownership changes in external mode.

Rollback restores the previous application commit and its matching inventory as
one unit. If checker execution itself must be rolled back, remove only the gate
dependency while retaining owner-side cleanup. Never compensate by killing broad
process groups or deleting caller-owned model artifacts.

Runtime rollout is additive and zero-downtime: new deployment managers register
the composite identity while existing records continue to use the validated
`default` project. Migration 047 keeps a server default while old application
instances drain, so their inserts remain valid during a rolling upgrade. Cleanup
code understands both paths. A downgrade first checks for repeated instance IDs;
if project/provider scoping is carrying otherwise-colliding resources, it refuses
the lossy downgrade. Operators must destroy or explicitly transfer those records
before retrying. Project deletion likewise leaves the project active and returns
HTTP 409 whenever durable or in-memory cleanup cannot be verified.

Exact-head verification has one logical owner. A live shard supervisor and its
verified observed child PIDs are reported as running work, never as unknown or
idle. A failed gate contributes bounded failure evidence; it does not create a
duplicate backlog item or authorize a second concurrent gate. After the
ownership changes, the first replay advanced monotonically through integration
and into unit shard 3, where it exposed ordinary complexity failures. Those were
fixed by splitting project route registration and separating FreeLLMAPI's
immutable contracts, untrusted schema parser, and signature admission facade;
the thresholds were not increased.

## Release-progress ownership

`TASKS.md` is both the durable evidence ledger and the repository backlog. Those
are different reporting scopes. `make active-work-status` treats the first
explicit `<version> milestone is the exact task set <range>` declaration as the
active delivery boundary. Its `open_task_ids` contains only unchecked tasks in
that range. `task_scope` reports the ledger path, range, milestone count,
backlog count, and repository-wide count without copying the complete backlog
into every status heartbeat. The IDs remain directly auditable in the named
ledger. If no exact milestone declaration exists, the command fails safely to
repository scope and preserves its former all-open behavior.

Research refreshed on 2026-09-22 supports that separation:

- GitHub's
  [milestone progress documentation](https://docs.github.com/en/issues/using-labels-and-milestones-to-track-work/viewing-your-milestones-progress)
  defines progress in terms of open work remaining in the selected milestone,
  rather than every open repository issue.
- Linear documents the same distinction between
  [current-cycle scope](https://linear.app/docs/cycle-graph) and separate
  [active and backlog views](https://linear.app/docs/default-team-pages).
- A long-lived GitHub Projects practitioner discussion
  [#9575](https://github.com/orgs/community/discussions/9575) describes release
  milestones alongside a catch-all backlog. Conflating those groups removes the
  view operators rely on for delivery focus.
- GitHub community report
  [#193565](https://github.com/orgs/community/discussions/193565) records a
  progress-summary regression where the aggregate counter diverged from the
  enumerated child connection. Gludd therefore derives each status snapshot
  directly from the current ledger and reports the selected scope alongside the
  complete enumerated IDs; it does not persist a second mutable counter.

## Resource bounds and operations

Validation is a single Python AST pass over explicit paths, starts no daemon, and
writes only the requested inventory in write mode. Use the documented Make target
variables so parallel worktrees have distinct reports and temp roots. A stale
semantic inventory entry is intentional failure evidence: regenerate it only
after reviewing the changed acquisition and teardown pair. Coordinate-only moves
require no inventory rewrite and therefore do not invalidate an otherwise tested
release head.
