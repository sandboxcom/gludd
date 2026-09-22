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
`default` project. Cleanup code understands both paths. Rollback must not remove
project attribution before all resources created by the newer version are
destroyed or transferred; otherwise the fail-closed HTTP 409 behavior is the safe
operational state.

## Resource bounds and operations

Validation is a single Python AST pass over explicit paths, starts no daemon, and
writes only the requested inventory in write mode. Use the documented Make target
variables so parallel worktrees have distinct reports and temp roots. A stale
semantic inventory entry is intentional failure evidence: regenerate it only
after reviewing the changed acquisition and teardown pair. Coordinate-only moves
require no inventory rewrite and therefore do not invalidate an otherwise tested
release head.
