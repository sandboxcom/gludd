# SPEC — Run Replay and Forensic Bundles

Status: READY-TO-IMPLEMENT (2026-08-12)

**Feature ID:** G10-RR1  
**Target compatibility:** Gludd `0.1.x`, bundle schema
`gludd.run-bundle/v1`  
**Priority / effort:** HIGH / M  
**Owners:** replay, dispatch, API/CLI, security, observability

This specification closes the remaining half of G10. Gludd already records a
small, unordered-by-contract event trail through `RunRecorder`, but operators
cannot inspect a complete run, prove that the record is intact, export it, or
replay it safely. The word `replay()` currently means only “read JSON files.” It
does not reproduce a run.

The implementer should assume no prior audit context. All repository claims in
this document were checked against the development worktree on 2026-08-12.

---

## 1. Decision and user outcome

Build a versioned, integrity-verifiable run bundle and expose three deliberately
different operations:

1. **Inspect / export / verify** — read-only forensic access to the recorded
   inputs, outputs, tool activity, decisions, timing, and source identity.
2. **Simulate** — feed recorded events to Gludd observers without invoking a
   model, a tool, a network client, or a workspace mutation.
3. **Re-execute** — opt-in execution of the recorded task as a *new* run in a
   disposable worktree. It is never called deterministic replay because remote
   models, dependencies, and services can change.

Read-only inspection and simulation are the defaults. Re-execution is disabled
by default, requires a distinct capability, and can never target the original
workspace.

The feature is successful when an operator can answer all of these questions
from one self-contained bundle:

- What exact user/task input, rendered prompt, model/profile parameters, and
  source revision began this run?
- Which tool calls were requested, what bounded/redacted results came back, and
  which file or git changes resulted?
- Which review, policy, budget, and reconciliation decisions were made?
- Is any event missing, reordered, altered, truncated, or from an unsupported
  schema?
- Can the recorded sequence be simulated without side effects?
- Can the task be tried again safely and compared with the original result?

## 2. Verified current gap

### 2.1 What exists

- `src/general_ludd/replay/recorder.py` defines `RunRecorder.record()`,
  `replay()`, and `list_runs()`.
- A run is currently `runs/<run_id>/events/<integer>.json`. `_next_seq()` scans
  the directory, chooses the largest integer plus one, and then writes a JSON
  object.
- `src/general_ludd/agents/dispatcher.py` records `task_started`,
  `task_completed`, and `task_failed` events. The completion event includes the
  full executor output.
- `src/general_ludd/event_loop/loop.py` records lifecycle summaries such as
  `dispatch_started`, `model_generation`, and `dispatch_completed`.
- `src/general_ludd/daemon.py` constructs the recorder under the configured
  replay directory and passes it to the event loop and dispatcher.
- `src/general_ludd/routers/replays.py` exposes only
  `GET /api/replays -> list[str]`.
- Existing direct and integration coverage lives in
  `tests/unit/test_run_recorder.py`, `tests/unit/test_replay_recorder.py`,
  `tests/unit/test_run_recorder_dispatch.py`,
  `tests/unit/test_run_recorder_daemon_wiring.py`, and
  `tests/integration/test_replay_e2e.py`.

### 2.2 What is missing or unsafe to promise

| Gap | Current evidence | Required outcome |
|---|---|---|
| No schema or compatibility marker | Event dictionaries have only an ad hoc `type` field; no manifest or schema version exists. | Version every bundle and event envelope; fail closed on an unsupported major version. |
| Incomplete evidence | Dispatch summaries omit the rendered prompt, provider-visible model response, tool result stream, source revision, and final diff identity. | Record a bounded, redacted, typed lifecycle sufficient for audit and comparison. |
| Sequence race | `_next_seq()` is a list-then-write operation with no per-run or cross-process serialization. | Allocate a unique monotonic sequence under a cross-platform file lock and publish atomically. |
| Silent recorder failure | Dispatcher and event-loop call sites suppress every recording exception. | Preserve non-blocking dispatch by default, but emit metrics/logs and mark the bundle incomplete; support fail-closed `replay.required=true`. |
| No integrity proof | Individual JSON files have no digest chain or signed manifest. | Hash each stored object, hash the ordered event index, and HMAC-sign the finalized manifest. |
| No retention/size contract | Full output can grow without a per-event, per-run, or global quota. | Bound capture, expose truncation metadata, and enforce retention without deleting pinned evidence. |
| No safe output policy | Successful executor output is persisted as supplied. | Apply the canonical secret redactor before storage and never record hidden model reasoning. |
| List-only API; no CLI | The router lists IDs; `src/general_ludd/cli.py` has no replay command group. | Add inspect, verify, export, simulate, and guarded re-execute surfaces. |
| `replay()` is only a reader | It loads JSON files and returns them. | Rename the semantic layers: legacy read, forensic read, simulation, and re-execution. |

The existing simple reader must remain usable while the new bundle writer rolls
out. Do not delete or silently reinterpret legacy recordings.

## 3. Long-lived user evidence from adjacent agent tools

These are user reports, not vendor marketing. They show the same failure class
persisting across mature coding agents.

1. **Cursor transcript export omits agent commands and their input/output.** A
   March 2026 user report demonstrates that the UI can render the ordered tool
   activity but the exported transcript loses it. Cursor support acknowledged
   the issue and said there was no ETA. Gludd must define one canonical bundle,
   not a lossy export assembled from a second data path.  
   <https://forum.cursor.com/t/exporting-transcript-doesnt-export-agent-commands/155837>
2. **Cursor’s on-disk transcript omits tool outputs.** An April 2026 user wanted
   full traces to learn a repeatable pipeline from prior agent work. Cursor
   confirmed that JSONL includes tool inputs but intentionally excludes outputs
   and suggested custom hooks. Gludd needs bounded tool output with explicit
   truncation markers so audit completeness and disk safety are both visible.  
   <https://forum.cursor.com/t/accessing-the-full-agent-transcript-in-cursor/157311>
3. **Cline checkpoint restore deleted most of a user’s workspace.** The issue
   was opened in 2024 and reopened after another user reported widespread file
   deletion in February 2025. That history is why Gludd simulation is read-only
   and why any actual rerun must use a disposable worktree, never a destructive
   “restore the old workspace” operation.  
   <https://github.com/cline/cline/issues/1213>
4. **Cline checkpoints blocked task activity and degraded on large repos.** The
   June–July 2025 investigation connected checkpoint storage and disk speed to
   severe performance problems, with maintainers planning a redesign for large
   repositories. Gludd therefore needs explicit byte limits, retention,
   asynchronous cleanup, and capture-latency telemetry.  
   <https://github.com/cline/cline/issues/4578>

The product lesson is narrow: a transcript that cannot prove tool activity is
not a forensic record, and a replay feature that mutates the original workspace
is not a safe debugging tool.

## 4. Versioned bundle contract

### 4.1 On-disk layout

Writers create only v1 bundles after rollout phase R2:

```text
runs-v1/<safe-run-id>/
  manifest.json
  manifest.hmac
  events/
    000000000000.json
    000000000001.json
  attachments/
    sha256-<digest>
```

`run_id` is untrusted input. Accept only
`[A-Za-z0-9][A-Za-z0-9._-]{0,127}` before constructing a path. Resolve every
path under the configured replay root and reject symlinks or escapes. Never use
a project name, prompt text, or model-supplied value as a filename.

Large values move to content-addressed attachments. An event points to an
attachment by SHA-256 and states its original byte count, stored byte count,
media type, encoding, redaction count, and whether it was truncated. Duplicate
attachments within a run are stored once.

### 4.2 `manifest.json`

`BundleManifestV1` is a strict Pydantic model in
`src/general_ludd/replay/schema.py`. Required fields:

| Field | Contract |
|---|---|
| `schema` | Literal `gludd.run-bundle/v1`. |
| `run_id` | Validated safe identifier. |
| `parent_run_id` | Original run for a simulation/re-execution, otherwise null. |
| `operation` | `record`, `simulate`, or `reexecute`. |
| `created_at`, `finalized_at` | UTC RFC 3339 timestamps; finalized may be null only for an in-progress/crash bundle. |
| `status` | `running`, `completed`, `failed`, `cancelled`, or `incomplete`. |
| `project_id` | Project scope used for authorization; null only for a documented system run. |
| `source` | Repository URL hash, commit SHA, tree hash, branch, and dirty flag. Do not store credentials embedded in URLs. |
| `runtime` | Gludd version, Python version, OS/architecture, config fingerprint, and feature flags relevant to execution. |
| `model` | Provider/profile/model identifiers, request parameters, and provider revision when exposed; never an API key. |
| `event_count` | Number of ordered event envelopes. |
| `events_sha256` | SHA-256 of the canonical ordered `(sequence, event_digest)` index. |
| `attachments` | Digest, safe metadata, size, and truncation state for each attachment. |
| `completeness` | Expected/observed lifecycle stages, recorder errors, and missing ranges. |
| `retention` | Expiry, pinned state, and legal/audit hold reason without user content. |

Canonical JSON uses UTF-8, sorted keys, compact separators, and no NaN or
Infinity. `manifest.hmac` is HMAC-SHA-256 over the canonical finalized manifest
using a versioned replay-integrity key resolved through the existing secrets
backend. The key ID is safe metadata in the manifest; the key is never stored
beside the bundle.

If no durable signing key is available, recording can continue only when
`replay.required=false`; the bundle is visibly `integrity="unsigned"` and a
warning metric increments. Required mode makes readiness fail and blocks new
dispatch until signing is healthy.

### 4.3 Event envelope

Every file in `events/` validates as `EventEnvelopeV1`:

```json
{
  "schema": "gludd.run-event/v1",
  "sequence": 7,
  "event_id": "uuid-v7-or-equivalent",
  "occurred_at": "2026-08-12T12:00:00Z",
  "recorded_at": "2026-08-12T12:00:00.010Z",
  "type": "tool.result",
  "project_id": "project-123",
  "correlation": {"todo_id": "...", "task_id": "...", "trace_id": "..."},
  "payload": {},
  "redaction": {"count": 0, "kinds": []},
  "digest": "sha256:..."
}
```

The v1 event taxonomy is closed and documented:

- `run.started`, `run.completed`, `run.failed`, `run.cancelled`
- `prompt.rendered`
- `model.requested`, `model.responded`, `model.failed`
- `tool.requested`, `tool.responded`, `tool.failed`
- `workspace.snapshot`, `workspace.diff`
- `gate.started`, `gate.completed`
- `review.decided`, `policy.decided`, `budget.decided`
- `reconcile.decided`
- `recording.degraded`

Unknown v1 event types are rejected by writers. Readers may preserve and display
an unknown *minor/additive payload field*, but they must reject an unknown
schema major. Payload models live beside the envelope instead of remaining raw
`dict[str, Any]` contracts.

`prompt.rendered` stores the provider-visible prompt after canonical redaction,
not hidden chain-of-thought. `model.responded` stores only provider-visible
output. `tool.responded` stores bounded stdout/stderr/result attachments.
`workspace.diff` stores a redacted patch only within its cap plus the complete
diff digest and file statistics. A truncation flag is never optional when
captured bytes differ from original bytes.

### 4.4 Atomicity and concurrency

Add `RunBundleStore` in `src/general_ludd/replay/store.py` and keep
`RunRecorder` as the small call-site facade.

- Use the maintained cross-platform `filelock` package for one lock per safe
  run ID. Add it as a direct, locked dependency rather than relying on a
  transitive install.
- Under the lock, allocate the next sequence, write a same-directory temporary
  file, `fsync`, and atomically replace the final event path. Reuse the
  repository’s established temp-plus-`os.replace` pattern; do not invent a
  second checkpoint database.
- Finalization computes the digest index and atomically publishes the manifest
  and HMAC. A reader seeing no finalized manifest reports `incomplete`; it never
  guesses that a crash bundle completed.
- A process crash can lose the current unpublished event but cannot yield a
  valid manifest whose `event_count` or digest chain claims that event exists.
- The recorder preserves its current synchronous call shape. Disk work beyond
  the bounded atomic append, export compression, retention, and simulations run
  off the daemon event loop.

### 4.5 API and CLI

Preserve `GET /api/replays -> list[str]` unchanged through Gludd `0.1.x`.
New clients use versioned routes:

| Method | Route | Capability | Result |
|---|---|---|---|
| `GET` | `/api/v1/replays` | `replay:read` | Cursor-paginated safe summaries, project-scoped. |
| `GET` | `/api/v1/replays/{run_id}` | `replay:read` | Manifest and redacted event page. |
| `POST` | `/api/v1/replays/{run_id}/verify` | `replay:read` | Digest/HMAC/schema/completeness verdict. |
| `GET` | `/api/v1/replays/{run_id}/export` | `replay:export` | Streamed ZIP bundle; no in-memory archive build. |
| `POST` | `/api/v1/replays/{run_id}/simulate` | `replay:simulate` | New read-only simulation record and comparison. |
| `POST` | `/api/v1/replays/{run_id}/reexecute` | `replay:execute` | Accepted job with new run ID and disposable-worktree identity. |

All collection routes require bounded `limit` (default 50, maximum 200) and an
opaque cursor. Event pages default to metadata; attachment content requires
explicit `include=content` and the export capability. Unknown runs return 404;
unauthorized cross-project runs return 404 to avoid identifier disclosure.

Add this command group to `src/general_ludd/cli.py`:

```text
gludd replay list [--project ID] [--limit N]
gludd replay show RUN_ID [--format human|json] [--include-content]
gludd replay verify RUN_ID
gludd replay export RUN_ID --output FILE.zip
gludd replay simulate RUN_ID
gludd replay reexecute RUN_ID --confirm --model-mode pinned|current
```

`show` and `verify` are read-only. `export` refuses to overwrite an existing
file unless the user supplies the existing CLI overwrite flag. `reexecute`
requires a non-interactive confirmation flag and an idempotency key at the API.

### 4.6 Simulation and re-execution semantics

`ReplayService.simulate()` validates the whole bundle before emitting any
event. It replays the ordered envelopes into an isolated in-memory observer bus.
The simulator receives implementations that raise on network, model, tool,
subprocess, secret, git, and filesystem mutation access. The output is a
timeline/comparison report; it cannot produce a code change.

`ReplayService.reexecute()` is a new live run, never a restore:

1. Verify schema, HMAC, project authorization, source revision availability,
   idempotency key, budget, and `replay.allow_reexecute=true`.
2. Create a namespaced disposable worktree from the recorded source commit.
   Refuse a missing commit, a dirty original source, or an existing target path.
3. Start with network denied and no inherited credentials. An operator may
   grant only policy-allowlisted network destinations and secret aliases.
4. Route the recorded task through the current pipeline. `pinned` requests the
   recorded model/profile/parameters and fails if unavailable; `current` uses
   current routing and labels the comparison accordingly.
5. Write a new v1 bundle with `parent_run_id`; compare output/diff/gate/review
   results by digest and structured fields.
6. Retain the worktree only on configured failure quarantine. Otherwise clean
   it using the existing safe worktree lifecycle after the final bundle is
   durable.

No mode promises bit-for-bit model determinism. The UI and API use “simulate”
and “re-execute,” not an ambiguous “restore” action.

## 5. Configuration and limits

Add a typed `replay` block to the existing config schema. Defaults favor safe
forensics without unbounded disk use:

```yaml
replay:
  enabled: true
  required: false
  writer_schema: v1
  legacy_dual_write: true
  allow_reexecute: false
  retention_days: 30
  max_total_bytes: 5368709120
  max_bundle_bytes: 104857600
  max_event_bytes: 1048576
  max_attachment_bytes: 16777216
  max_events_per_run: 10000
  canary_percent: 0
  signing_key_alias: replay_integrity_v1
```

Reject negative values, percentages outside 0–100, unknown writer schemas, and
limits above documented operator maxima. Configuration reload may tighten
future capture and retention, but it never rewrites a finalized bundle.

The retention worker runs at a bounded interval and deletes oldest expired,
unpinned, finalized bundles until both age and byte quotas pass. It uses a
single namespaced worker, reports progress, skips locked/in-progress bundles,
and never follows symlinks. Audit/legal holds override age and byte cleanup.

## 6. Security and privacy

### 6.1 Authorization

- Reuse the existing daemon authentication path; replay routes are not public.
- Enforce separate `replay:read`, `replay:export`, `replay:simulate`, and
  `replay:execute` capabilities. Write/execute does not follow from read.
- Scope every bundle to `project_id`. A principal must hold the capability for
  that project; system-run access is admin-only.
- Record every export, verification failure, simulation, re-execution, pin,
  unpin, and retention deletion in the existing audit channel without payload
  content.

### 6.2 Content handling

- Extract the existing key-aware payload redaction behavior into a canonical
  `general_ludd.security.redaction` helper and reuse it at every recorder entry
  point. Do not maintain replay-only secret regexes.
- Redact before writing, hashing attachments, logging, or metrics. Raw secret
  values must never reach replay storage.
- Never persist hidden provider reasoning, chain-of-thought, API keys, bearer
  headers, STS tokens, cookies, credential-bearing URLs, raw environment dumps,
  or OpenBao responses.
- Preserve the fact and category of a redaction so operators can distinguish
  “empty” from “removed for safety.”
- Apply recursion, collection-length, string-length, and total-event limits
  before serialization. Cyclic or unsupported objects become a typed capture
  error, not an unbounded `repr()`.

### 6.3 Integrity and archive safety

- Verify every event/attachment digest and the finalized manifest HMAC before
  export, simulation, or re-execution. Read-only `show` may display a corrupt
  bundle only with a loud `UNVERIFIED` state and no attachment content.
- Rotate signing keys by key ID. Readers retain old verification keys for the
  configured retention window. Missing or revoked keys fail verification.
- Export ZIP entries are generated from safe fixed paths. Imports, if added in
  a later feature, must reject absolute paths, `..`, links, duplicate names,
  compression bombs, and unsupported schemas. v1 does not add import.
- Re-execution uses the existing sandbox/resource policy with CPU, memory,
  process, wall-time, output, and network limits. It never runs in the daemon’s
  source checkout.

## 7. Zero-downtime rollout and rollback

The reader ships before the writer. No database migration or daemon outage is
required.

### R0 — reader and telemetry only

- Add the strict v1 models, legacy v0 adapter, verifier, summaries, and metrics.
- Keep the current writer unchanged.
- `GET /api/replays` remains byte-shape compatible. New v1 routes can inspect
  legacy runs as `schema="legacy-v0", integrity="unverified"`.
- Rollback: revert the new routes/reader; current recordings are untouched.

### R1 — shadow dual-write canary

- Enable v1 writing for a deterministic `hash(run_id) % 100 < canary_percent`
  cohort while preserving the legacy event files.
- Start at 1%, then 10%, 50%, and 100% only after a full retention interval or
  the release owner’s documented shorter beta soak.
- Compare event type/count, terminal status, write latency, bytes, redaction,
  and verification success. A mismatch or verification failure automatically
  sets canary to 0; dispatch continues unless `required=true`.
- Rollback: set `canary_percent: 0`. Legacy writes never stopped, so the older
  binary and `/api/replays` remain functional.

### R2 — v1 primary, legacy compatibility window

- Set `writer_schema: v1`, retain `legacy_dual_write: true`, and enable read-only
  CLI/API for all projects.
- Keep dual-write and the legacy reader through at least two `0.1.x` beta/minor
  releases. Publish byte/latency overhead before removing dual-write.
- Re-execution remains disabled during this phase.
- Rollback: set `writer_schema: v0`; v1 readers still expose already-written
  bundles and legacy data remains current.

### R3 — guarded re-execution

- Enable only for an internal canary project with sandbox/network deny verified.
- Promote per project, never globally as part of a code deploy.
- Rollback immediately by setting `allow_reexecute: false`; inspect, export,
  verify, and simulation remain available.

Mixed-version rule: v1 readers read legacy-v0 and v1. v1 writers never rewrite
legacy data in place. An unknown `gludd.run-bundle/vN` major is listed as
unsupported and cannot be exported with a valid verdict, simulated, or run.

## 8. Observability and operations

### 8.1 Metrics

Add bounded-label metrics; never label by run, todo, user, or project ID:

- `gludd_replay_events_recorded_total{schema,event_type}`
- `gludd_replay_record_failures_total{reason}`
- `gludd_replay_bundles_finalized_total{schema,status}`
- `gludd_replay_bundle_bytes{schema}` histogram
- `gludd_replay_record_seconds{event_type}` histogram
- `gludd_replay_redactions_total{kind}`
- `gludd_replay_truncations_total{content_type}`
- `gludd_replay_verify_total{schema,outcome,reason}`
- `gludd_replay_operations_total{operation,outcome}`
- `gludd_replay_legacy_reads_total`
- `gludd_replay_retention_deleted_total{reason}`
- `gludd_replay_store_bytes` gauge
- `gludd_replay_incomplete_bundles` gauge

### 8.2 Logs, traces, and readiness

- Structured logs include safe run ID, operation, schema, sequence, duration,
  byte count, and error class. Never log event payloads or attachment content.
- Trace spans cover `replay.record`, `replay.finalize`, `replay.verify`,
  `replay.export`, `replay.simulate`, `replay.reexecute`, and
  `replay.retention`. Large cleanup/export operations emit periodic progress.
- `/readyz` remains green for recorder degradation when `required=false`, but
  the status body reports the replay subsystem as degraded. With
  `required=true`, an unwritable store, unavailable signing key, or failing
  self-check makes readiness 503 and prevents new dispatch.
- Alert on any HMAC/digest failure, incomplete-bundle growth, required-mode
  failure, storage above 85%, sustained record error rate, retention failure,
  or re-execution outside its disposable worktree.

The operator runbook must cover corrupt/incomplete bundles, signing-key
rotation, storage pressure, pinning, canary rollback, and quarantined reruns.

## 9. Testing and coverage

Use test-first commits. Do not weaken existing assertions to accommodate the
new behavior; update an old assertion only when this specification explicitly
changes its public contract.

### 9.1 Unit and property tests

- Strict manifest/envelope parsing, canonical serialization, and unknown-major
  rejection.
- Safe run-ID/path validation, symlink refusal, traversal/Unicode edge cases.
- Concurrent thread and process append produces unique contiguous sequences.
- Crash between temporary write, event publish, and manifest finalization never
  verifies as complete.
- Digest/HMAC verification, key rotation, corrupt/missing/extra/reordered events,
  and attachment mismatch.
- Redaction before persistence/hash; recursion and byte caps; explicit
  truncation metadata; provider-hidden reasoning never accepted.
- Retention ordering, pin/hold behavior, locked-run skip, and total-byte quota.
- Legacy reader behavior and v1 round trip.
- Cursor pagination, idempotency, and stable human/JSON CLI rendering.

Use Hypothesis for manifest/event/path and corruption cases. Do not create a
custom fuzzer when the existing property-test dependency covers the input.

### 9.2 Integration and E2E tests

- Real dispatcher/event-loop run produces the expected lifecycle stages and a
  verifiable finalized bundle.
- Read API enforces capability and project isolation; unauthorized IDs do not
  leak existence.
- Streamed export matches the verified manifest and stays within bounded memory.
- Simulation proves zero model, network, subprocess, secret, git, and workspace
  calls while preserving event order.
- Re-execution proves a new run/worktree/branch identity, recorded parent link,
  network deny, selective secret grants, source-workspace immutability, budget
  enforcement, and safe cleanup/quarantine.
- Kill/restart during capture; the partial bundle is visible as incomplete and
  the daemon continues/rejects according to `required`.
- Exercise R0/R1/R2 mixed readers/writers and configuration rollback.
- Linux x86_64/aarch64, macOS arm64, and Windows x86_64 verify path, lock, ZIP,
  and atomic publish behavior on supported Python versions.

### 9.3 Quality gates

- Overall project coverage remains at least **85%**.
- Every new or changed executable file remains at least **75%**; target at least
  85% for `replay/schema.py`, `replay/store.py`, and `replay/service.py`.
- `make lint`, `make typecheck`, `make test-count`, `make lint-specs`, focused
  unit/integration targets, `make gate-audit`, security scans, and the release
  cross-platform jobs must pass with zero warnings before promotion.
- Any new Make target must be added through the target contract, followed by
  `make check-make-target-contract` and its documented behavioral example.

## 10. Acceptance criteria

- [ ] **RR-AC-01 — Versioned completeness.** A completed dispatch creates a
  finalized `gludd.run-bundle/v1` manifest and ordered typed events covering
  prompt, model, tool, workspace, gate, review/policy/budget, reconcile, and
  terminal outcome, with explicit missing/truncated markers.
- [ ] **RR-AC-02 — Integrity.** `verify` detects any modified, missing, extra,
  reordered, or corrupt event/attachment and validates the HMAC key ID without
  exposing key material.
- [ ] **RR-AC-03 — Atomic concurrency.** Concurrent multi-worker recording
  yields unique contiguous event sequences; crash injection cannot create a
  falsely valid finalized bundle.
- [ ] **RR-AC-04 — Forensic surfaces.** Versioned API and CLI list, show, verify,
  and stream-export authorized project-scoped runs; the legacy list response is
  unchanged for `0.1.x`.
- [ ] **RR-AC-05 — Read-only simulation.** Simulation reproduces the event
  timeline and comparison while tests prove zero network, model, tool,
  subprocess, secret, git, and workspace side effects.
- [ ] **RR-AC-06 — Isolated re-execution.** Opt-in re-execution always creates a
  new run in a disposable worktree at the recorded commit, links the parent,
  enforces budget/sandbox/network/secret policy, and never mutates the source
  workspace.
- [ ] **RR-AC-07 — Authorization.** Separate read/export/simulate/execute
  capabilities and project scoping are enforced; unauthorized lookup does not
  reveal whether a run exists.
- [ ] **RR-AC-08 — Privacy.** Secret values and hidden reasoning never reach
  storage, logs, metrics, exports, or diffs; redaction and truncation remain
  observable as metadata.
- [ ] **RR-AC-09 — Compatibility.** Legacy-v0 bundles remain readable, legacy
  list clients remain compatible, and unknown schema majors fail closed.
- [ ] **RR-AC-10 — ZDD and rollback.** Reader-first and dual-write canaries run
  without daemon downtime; configuration can restore v0 writing or disable
  re-execution immediately while preserving recorded evidence.
- [ ] **RR-AC-11 — Operations.** Size/retention limits, pin/hold, readiness
  semantics, bounded metrics, progress, alerts, and the corruption/key/storage
  runbook are verified.
- [ ] **RR-AC-12 — Quality.** Focused, integration, mixed-version,
  crash/concurrency, security, and cross-platform tests pass; project coverage
  is at least 85%, no changed executable file is below 75%, and the complete
  release gate is green.

## 11. Landing plan

Each numbered item is a small complete commit on one development-derived
feature branch. Tests land before or with the behavior they pin; do not create
the same shared-infrastructure change independently on another branch.

1. **Schema and legacy reader:** failing schema/compatibility tests, strict v1
   models, legacy adapter, and safe run ID.
2. **Atomic bundle store:** concurrency/crash/integrity tests, direct `filelock`
   dependency, atomic event/manifest store, verifier, and retention primitives.
3. **Recorder capture contract:** failing lifecycle/redaction/limits tests, typed
   recorder facade, canonical redactor extraction, completeness tracking, and
   dual-write canary.
4. **Read-only service/API/CLI:** authorization and compatibility tests, v1 list,
   show, verify, streamed export, and CLI commands. Preserve the legacy route.
5. **Simulation:** no-side-effect tests first, isolated observer bus, structured
   comparison, and audit/metrics.
6. **Re-execution:** disabled-by-default config and adversarial isolation tests,
   disposable-worktree job path, idempotency, policy grants, cleanup/quarantine,
   and comparison bundle.
7. **Operations and rollout:** readiness, retention, canary auto-disable,
   dashboards/alerts, operator runbook, config reference, and changelog.
8. **Promotion evidence:** focused suites, `make test-count`, `make lint-specs`,
   `make gate-audit`, cross-platform CI, then development-to-master promotion
   through the repository release flow.

Implementation should modify or add these primary files:

- `src/general_ludd/replay/schema.py` (new)
- `src/general_ludd/replay/store.py` (new)
- `src/general_ludd/replay/service.py` (new)
- `src/general_ludd/replay/recorder.py`
- `src/general_ludd/agents/dispatcher.py`
- `src/general_ludd/event_loop/loop.py`
- `src/general_ludd/routers/replays.py`
- `src/general_ludd/cli.py`
- the typed configuration schema and example/reference
- focused unit/integration/E2E tests and the operator runbook

## 12. Explicit non-goals

- Capturing or reconstructing hidden model chain-of-thought.
- Claiming remote-model or external-service calls are deterministic.
- Restoring over an existing workspace, branch, or uncommitted change.
- Importing arbitrary third-party archives in v1.
- Treating replay bundles as a backup for the repository or database.
- Replacing OpenTelemetry; the bundle is per-run evidence, while telemetry is
  aggregate/operational observation.
- Retaining unbounded raw stdout, diffs, prompts, or model output in pursuit of
  an impossible notion of completeness.
