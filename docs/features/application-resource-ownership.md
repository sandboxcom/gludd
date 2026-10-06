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

## Durable todo claim ownership

For todo-driven compute, the claim commit and session close precede provisioning.
Commit failure or rollback clears the detached claimed batch, so neither the
provider nor the runner can observe provisional ownership. Under competing
workers and restart, the database fence yields
one durable winner and one provisioning call.
Non-runnable work retains zero allocation. A worker without that durable proof
neither provisions nor tears down foreign compute.

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
that range plus any checked task whose explicit status remains non-complete.
Unchecked tasks remain open even if implementation-phase metadata says
`completed`; only the evidence gate may close their acceptance marker.
`task_scope` reports the ledger path, range, milestone count,
backlog count, and repository-wide count without copying the complete backlog
into every status heartbeat. The IDs remain directly auditable in the named
ledger. If no exact milestone declaration exists, the command fails safely to
repository scope and preserves its former all-open behavior.

The Codex Stop hook and repository `codex-stop-guard` consume the same parser in
`scripts/task_scope.py`; neither recounts markers independently. An unfinished
active-milestone item or a ratchet entry can continue or fail the release loop.
Future and historical backlog items remain visible as excluded inventory but
cannot keep the current release turn alive or make a clean milestone fail. This
matters because the
[official OpenAI hook contract](https://learn.chatgpt.com/docs/hooks) defines a
Stop-hook `decision: "block"` as a request to create another continuation prompt,
not as a passive warning. The predicate therefore has to describe an attainable,
evidence-bound finish line. OpenAI's
[Goals guidance](https://developers.openai.com/cookbook/examples/codex/using_goals_in_codex)
similarly distinguishes durable evidence-based completion from an endless loop.

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

Those practitioner reports also govern continuation: the Stop hook must not turn
the catch-all backlog described in #9575 into current milestone ownership, and
must derive its count from the same enumerated connection used by status so it
cannot reproduce the divergent aggregate in #193565.

## Exact hosted closeout evidence (2026-10-06)

Candidate `26e95a6f5749d96ba752e0a824dc8cbad6e256c6` completed hosted
Build and Release run `37427487620` with 27 passing jobs and zero failures. The
only two non-passing jobs were the intentionally skipped `release` and
`release_source_proof` tag-only stages. Its dedicated
`claim-before-provision-acceptance` job passed 97 tests with all Azure identity
variables empty, live cloud execution disabled, and outbound proxies confined to
an unreachable loopback port. Hosted Molecule run `37427534191` independently
completed all six jobs successfully.

This closes S83.158 because the exact candidate exercised the same durable
discover, rank, claim, provision, perform, verify, and release contract already
proved locally, while making accidental paid acquisition fail before spend. The
content-addressed
[`s83_hosted_candidate_26e95a6f.json`](../evidence/s83_hosted_candidate_26e95a6f.json)
receipt binds both immutable run identities and the task decision. The long-lived
practitioner reports #9575 and #193565 above remain relevant: completion is
derived from enumerated task/run evidence rather than an aggregate green badge,
and no later release task is closed by this narrower proof.

## Resource bounds and operations

Validation is a single Python AST pass over explicit paths, starts no daemon, and
writes only the requested inventory in write mode. Use the documented Make target
variables so parallel worktrees have distinct reports and temp roots. A stale
semantic inventory entry is intentional failure evidence: regenerate it only
after reviewing the changed acquisition and teardown pair. Coordinate-only moves
require no inventory rewrite and therefore do not invalidate an otherwise tested
release head.

The 2026-09-29 release replay demonstrated the intended fail-closed behavior.
Three new atomic writers already had `finally` cleanup, but their Ansible-runtime,
CI-failure-ledger, and PyInstaller-receipt acquisitions had not landed with the
matching inventory entries. The complete preflight ledger retained all three
hashes while continuing later checks. After reviewing the actual acquisition and
teardown pairs, the canonical writer admitted them as one counted inventory
update; read-only validation now reports 212 owned resources and the checker
suite passes 32/32. This is not a baseline bypass: code and inventory remain one
commit, and a future source-hash change fails closed again.
