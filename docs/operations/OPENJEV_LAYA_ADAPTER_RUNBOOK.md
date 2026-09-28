# OpenJev/Laya Typed-Decision Adapter Operator Runbook

**Applies to:** planned Gludd v0.1.2 typed-decision adapter evidence
**Last reviewed:** 2026-09-27
**Runtime admission status: NOT IMPLEMENTED**
**Recovery execution status: NOT IMPLEMENTED**

This runbook turns the
[OpenJev/Laya adapter contract](../releases/v0.1.2-openjev-laya-adapter-contract.md)
into an operator sequence. The implemented surface is hermetic evidence:
contract checking, normalization, compatibility planning, dry-run reporting,
create-only pre-projection persistence, offline recovery replay, batching and
residency planning, and acceptance replay. It cannot send traffic, admit a
profile, apply a projection, preload or evict a model, promote a canary, or
execute a runtime rollback.

Dry-run recovery modes are evidence, not callable mechanisms.

## Authority and Preconditions

Use this runbook only for `failure_classification` and only with an already-local
schema, exact baseline and candidate profiles, local fixtures, and independently
acquired source/artifact evidence. The current scripts do not discover a remote
endpoint, resolve a tag, fetch an archive, inspect a live model registry, or
download a model. Missing local evidence is a stop condition, not permission to
make a network request.

Assign and record four independent owners before starting:

- compatibility owner for schema, profile, pin, and update classification;
- security owner for source, dependency, license, sanitizer, and redaction review;
- evaluation owner for frozen corpus, calibration, slice, and quality evidence;
- lifecycle owner for the future green generation, leases, drain, and absence
  proof.

The author cannot be the sole approver. OpenJev external endpoints remain
operator-owned; Gludd owns only its client and leases. Laya may be used only
behind a future Gludd-owned universal model-worker generation. Neither project
becomes a provider preset or gains task-policy authority.

The upstream observations and practitioner reports were reviewed on 2026-09-27
and were still too young to satisfy the ninety-day/two-stable-release maturity
gate. Repeat that review for every update. Treat the contract's dated source and
issue links as observations, never as a current availability or safety promise.

## Phase 1: Offline Discovery

Discovery means proving what is already on disk; it does not mean contacting an
upstream service.

1. Identify the baseline profile which currently represents the safe path. Keep
   it immutable and `disabled` for this offline workflow.
2. Resolve the proposed release outside this workflow under the approved source-
   acquisition process. Bring in the full commit, immutable archive, license,
   release notes, security policy, submodule and patch identities, and SBOM.
3. Hash the source archive, server/image, model, tokenizer, renderer, calibration,
   and runtime independently. Record exact HTTPS repository provenance, a full
   commit, and a non-moving release. Reject `main`, `master`, `latest`, preview
   aliases, floating Hub revisions, mutable image tags, repeated/sentinel hashes,
   and displayed model names as identity.
4. Create a new candidate profile with `example_only: false` and
   `profile_state: disabled`. A source, model, tokenizer, renderer, calibration,
   runtime, trust, redaction, or normalization change requires a new fingerprint.
5. Keep the example bundle out of this workflow. Its sentinel digests and
   `example_only: true` state are deliberately non-admissible.
6. For an external OpenJev profile, record endpoint ownership and trust class but
   do not claim remote process or artifact ownership. For a local Laya profile,
   bind every artifact to the future universal-worker identity; do not create a
   parallel supervisor or direct cache-control path.
7. Compare the observed request/response schema with the pinned Choice, Noul, and
   Score fixtures. Unknown fields, changed truncation, routing, probability,
   confidence, or readiness semantics stop the update pending a reviewed contract
   and new fixtures.

Discovery is complete only when another reviewer can reproduce every digest from
the local evidence and can explain every identity-bound difference. Do not reuse
the baseline fingerprint for a changed source or runtime, and do not assign a new
fingerprint when the identity-bound inputs did not change.

## Phase 2: Compatibility Check

Run the implemented checks in this order through their Python APIs or the focused
test harness; there is no supported live-admission command in v0.1.2:

1. `check_canonical_contract()` validates the local Draft 2020-12 schema and
   non-runnable examples.
2. `check_profile_update()` compares the exact baseline and candidate. Only
   `COMPATIBLE_PINNED_UPDATE` may continue. `COMPATIBLE_NOOP` requires no update;
   `INCOMPATIBLE` and `INVALID` stop.
3. `build_prepared_update()` creates an in-memory update plan and receipt from an
   event ID, exact UTC-second timestamp, and actor ID. The actor ID is hashed and
   must not enter evidence in clear text.
4. `verify_prepared_update()` rebuilds the document using the same schema and
   profiles. Require canonical byte equality and false admission, network, and
   runtime-change flags.
5. Review `changed_pin_paths`; every other protected section must appear in
   `protected_sections_verified`. Any unexplained path or finding stops the run.

Building and verifying this object do not persist or apply it. Persistence is a
separate Phase 4 decision.

## Phase 3: Dry-Run Report and Verification

Call `build_dry_run_report()` with the local schema and bounded lanes, render it
with `render_dry_run_report()`, then replay the parsed document through
`verify_dry_run_report()` with the same complete inputs. Retain the canonical
newline-terminated JSON and its digest together. A report is acceptable only
when all of the following hold:

- report version is `typed-decision-adapter-dry-run-report/v1`;
- the six stages are exactly `compatibility_update`, `atomic_persistence`,
  `rollback_ready`, `shadow_projection`, `batch_residency`, then
  `normalization_replay`, with ordinals and predecessor digests intact;
- statuses are `verified_offline`, `planned_not_persisted`,
  `verified_not_durable`, `validated_not_applied`, `planned_not_applied`, and
  `verified_offline`, in that order;
- every top-level and per-stage side-effect flag is exactly false;
- rollback is required to `disabled`, is not durable, and says the candidate was
  not applied;
- `prepared_update_replay`, `projection_resume`, and `runtime_rollback` recovery
  modes are present, disabled, and carry their fixed reason codes;
- canonical replay produces the same report and nested digests; and
- no raw source coordinate, actor, lane, profile, fixture, request, state, answer,
  credential, or endpoint appears in the rendered bytes.

The reviewed OpenJev/Laya fixture report is pinned to
`sha256:569516688988b2b4fd5838ad79c577057302270e8713c918c020d2aa939884f9`.
That pin detects fixture drift; it is not an allow-list entry or runtime approval.

## Phase 4: Durable Pre-Projection Handoff

This phase is optional evidence persistence and still performs no projection or
admission. Start it only after the dry-run report replays exactly and owners have
approved the proposed path.

1. Select a dedicated real directory and a new evidence filename. The parent may
   not be a symlink. The destination may not be a symlink or non-regular file.
2. Call `prepare_preprojection_handoff()`. It rebuilds the update, uses create-
   only atomic persistence, flushes and syncs the file and directory, replays the
   file, and emits the rollback-bound recovery receipt.
3. Require mode `0600`. A second identical persistence must return `UNCHANGED`
   without changing modification time. A different existing document is a hard
   conflict and is never overwritten.
4. Replay with `recover_preprojection_handoff()` before trusting the handoff.
   Require `phase: prepared_before_projection`, the same schema, candidate,
   prepared-update, update-receipt, and rollback digests, `required: true`, and
   `target_state: disabled`.
5. Retain the prepared document, recovery receipt, approvals, and dry-run report
   as one append-only evidence set. Never rewrite an old receipt into a new
   schema version.

If the process stops before the durable file and recovery receipt verify, do not
claim a handoff; restart from the local inputs. If it stops after the durable file
but before any future projection, replay `recover_preprojection_handoff()` and
keep the candidate disabled. The current code has no post-projection recovery.

## Phase 5: Staged Admission (Future Gate)

This phase is a required design for a future implementation, not an available
operation. None of the current checker, planner, normalizer, acceptance, dry-run,
or recovery APIs may be used to change profile state or traffic.

Do not begin staged admission until all of these separately implemented gates are
green for the exact identity:

- the provider-admission maturity gate and repeated upstream/security review;
- production contracts, profiles, sanitizer, bounded transport, scheduler, and
  universal lifecycle integration;
- frozen corpus, held-out calibration, abstention/fallback, and slice metrics;
- secret-safe observability, cancellation, queue, deadline, and lease handling;
- hermetic Molecule and fake-server E2E, optional pinned live evidence, rollback
  drill, coverage/lint/type/security checks, and the full release gate; and
- compatibility, security, evaluation, and lifecycle approvals from distinct
  owners.

Only a future admitted implementation may create a green generation and move an
exact identity through `disabled -> shadow -> 1% -> 5% -> 25%`. Every transition
must be manual, digest-addressed, observation-window bounded, and reversible.
Runtime discovery may remove a declared capability but may never add one. Model
listing alone is not readiness. There is no automatic promotion, and a dry-run
status such as `validated_not_applied` is never evidence that a stage ran live.

## Phase 6: Rollback and ZDD Handoff

The implemented rollback boundary ends before persistence/projection in Phase 3,
or at a durable but still-disabled candidate in Phase 4. In both cases, blue and
the production answer remain unchanged:

- before persistence, reject or retain the report for audit and rebuild from
  corrected local inputs;
- after a verified pre-projection handoff, replay the recovery receipt, keep the
  candidate disabled, and retain the conflicting/failed evidence without apply;
- never invoke a disabled recovery mode or interpret `rollback_required` as an
  executable rollback function.

Future runtime rollback must atomically restore current-path/shadow authority to
blue, stop new candidate selections within the approved bound, let each in-flight
request finish on the identity/sanitizer/calibration it started with, drain
bounded calls, release leases, retire and destroy only Gludd-owned green
resources, and record absence proof. External OpenJev infrastructure remains
operator-owned and must not be destroyed by Gludd. This post-projection rollback
and its 10-second selection-stop objective are NOT IMPLEMENTED.

Blue remains available until green completes its future canary and rollback
drill. No database migration is required for the current evidence workflow. Any
future storage migration must use expand/contract and retain original receipts.

## Operator Decision Table

| Signal | Immediate disposition | Required operator action | Safe state and evidence |
|---|---|---|---|
| Digest mismatch | Stop; reject the candidate/report/receipt | Quarantine the evidence, reacquire the immutable local input through the approved process, recompute independently, and repeat from Phase 1; never edit or re-sign a mismatched receipt | Blue unchanged; candidate disabled; preserve both digests and bounded stage code |
| Schema/version mismatch | Stop; do not coerce, downgrade, or ignore fields | Use the matching supported reader, or land a reviewed schema/normalization change with new fixtures and identity before retry | No normalization or admission; retain original versioned receipt unchanged |
| Identity reuse | Treat as compatibility/security failure | Issue a new fingerprint for changed identity-bound inputs; reject cross-profile, cross-tenant, and cross-version receipt reuse; repeat evaluation and approvals | Existing identity remains authoritative; record only safe digest references |
| Redaction failure | Fail before send or render; never retry with raw data | Disable the candidate path, preserve a bounded `request_redaction_failed` reason/digest, repair and adversarially retest the sanitizer | Current Gludd path answers; no network byte, raw payload, secret, or endpoint is logged |
| Interrupted handoff | Determine whether a verified mode-`0600` prepared document exists | No verified file: restart Phase 2. Verified pre-projection file: replay `recover_preprojection_handoff()` and require the exact disabled rollback proof. Any future post-projection interruption: deny promotion and use only its separately implemented rollback | Blue authoritative; candidate disabled; recovery receipt identifies the last durable pre-projection boundary |
| Disabled recovery | Do not invoke or simulate the named mode | Correct the local inputs and rebuild a dry run, or use Phase 4 replay only when its durable document exists; escalate if a post-projection action would be required | `enabled: false` remains false; no apply, projection resume, runtime rollback, or durability claim |

## Exact Evidence and Receipt Fields

Field names are closed. Missing and unknown fields fail replay. The table lists
the exact implemented operator-facing envelopes; nested objects are listed in the
following paragraphs.

| Artifact | Exact fields |
|---|---|
| Prepared document | `version`, `plan`, `receipt` |
| Compatibility evidence | `schema_digest`, `baseline_profile_digest`, `candidate_profile_digest`, `checker_status`, `changed_pin_paths`, `protected_sections_verified`, `findings`, `evidence_digest` |
| Update plan | `version`, `event_id`, `recorded_at`, `actor_digest`, `baseline`, `candidate`, `compatibility_evidence`, `rollback`, `actions`, `admission_enabled`, `network_accessed`, `runtime_changed`, `plan_digest` |
| Update receipt | `version`, `event_id`, `recorded_at`, `actor_digest`, `status`, `plan_digest`, `evidence_digest`, `rollback_digest`, `redacted_fields`, `admission_enabled`, `network_accessed`, `runtime_changed`, `receipt_digest` |
| Dry-run report | `version`, `schema_digest`, `lanes`, `redacted_fields`, `admission_enabled`, `admission_performed`, `apply_performed`, `external_mutation_performed`, `model_invoked`, `models_downloaded`, `network_accessed`, `persistence_performed`, `recovery_performed`, `runtime_changed`, `runtime_invoked`, `report_digest` |
| Dry-run lane | `version`, `lane_reference`, `profile_reference`, `compatibility`, `provenance`, `rollback`, `recovery_modes`, `stages`, `batch_plan_digest`, `fixture_manifest_digest`, `normalization_record_digests`, `normalization_kinds`, `lane_digest` |
| Dry-run stage | `version`, `ordinal`, `stage`, `status`, `predecessor_digest`, `evidence_digest`, `admission_performed`, `apply_performed`, `external_mutation_performed`, `model_invoked`, `models_downloaded`, `network_accessed`, `persistence_performed`, `runtime_changed`, `runtime_invoked`, `stage_digest` |
| Pre-projection recovery receipt | `version`, `phase`, `schema_digest`, `lane_reference`, `candidate_profile_digest`, `prepared_update_digest`, `update_receipt_digest`, `rollback_digest`, `rollback_target_state`, `rollback_required`, `admission_enabled`, `admission_performed`, `model_invoked`, `models_downloaded`, `network_accessed`, `runtime_changed`, `runtime_invoked`, `recovery_digest` |
| Offline acceptance receipt | `version`, `schema_digest`, `lanes`, `redacted_fields`, `admission_enabled`, `admission_performed`, `model_invoked`, `models_downloaded`, `network_accessed`, `runtime_changed`, `runtime_invoked`, `receipt_digest` |

The plan's `baseline` and `candidate` each contain `profile_id`, `fingerprint`,
`profile_digest`, `source_release`, `source_commit`, and
`source_archive_digest`. Its `rollback` contains `version`, `profile_id`,
`identity_fingerprint`, `baseline_profile_digest`, `source_commit`,
`source_archive_digest`, `target_state`, `required`, and `rollback_digest`.

Dry-run `compatibility` contains `version`, `status`, old/new profile digests,
compatibility/plan/receipt/prepared-update digests, changed pin paths, and
protected sections. `provenance` contains the schema digest; repository, source,
commit, and release references; old/new identity and archive digests; and the
candidate fingerprint. `rollback` contains the pre-persistence/projection
boundary, baseline and rollback digests, disabled target, required/durable/apply
booleans. Each recovery mode contains `version`, `mode`, `enabled`, and
`reason_code`.

Each acceptance lane contains `version`, `lane_reference`,
`identity_fingerprint`, candidate/shadow/projection/compatibility/update/receipt/
rollback/batch/fixture digests, normalization record digests and kinds, stage
root and chain, three replay/rollback/persistence booleans. Each stage checkpoint
contains `version`, `ordinal`, `stage`, `predecessor_digest`, `evidence_digest`,
and `checkpoint_digest`.

## Observability and Evidence Retention

The current implementation observes its work through returned typed objects and
canonical JSON, not through a runtime dashboard. For every offline run, retain:

- schema, baseline, candidate, source/archive, identity, report, prepared-update,
  receipt, rollback, batch-plan, fixture-manifest, and normalization digests;
- event ID, exact UTC timestamp, hashed actor, lane/profile references, stage
  ordinal/status/predecessor/evidence digest, and bounded result code;
- owner approvals, compatibility findings, changed pin paths, protected sections,
  redaction inventory, and resource bounds; and
- whether persistence occurred and, if so, path ownership, mode `0600`, create or
  unchanged result, replay result, and last durable phase.

Never retain raw actor, credential, endpoint, request ID, state, answer, fixture
ID, lane ID, or arbitrary source coordinate in the rendered dry-run report or
untrusted error text. Use stable reason codes and domain-separated digests. A
replay rejection must expose only its bounded stage, never the rejected value.

A future admitted runtime must add namespaced metrics for exact identity and
generation, selection state, readiness, queue/concurrency/deadline, request
result/reason, sanitizer failure, latency, measured resident bytes, lease and
in-flight counts, drain state, and absence proof. Those metrics and alerts are
NOT IMPLEMENTED by the offline scripts and are a prerequisite for Phase 5.

## Resource and Safety Limits

All limits fail closed; booleans, floats, strings, and coercible values do not
stand in for integers.

| Boundary | Hard limit or rule |
|---|---|
| Contract loader | 2 MiB local JSON input; regular UTF-8 file; no symlink |
| Prepared update | 256 KiB prepared update; create-only mode `0600`; real parent; no overwrite |
| Metadata | Event ID 1-128 conservative characters; actor ID 1-256 trimmed characters; exact UTC second |
| Acceptance | 16 lanes and 64 normalization fixtures per lane maximum; unique conservative lane/request/fixture identities |
| Planner | 1,024 planner requests, 64 planner profiles, 64 resident identities, 1 GiB aggregate request bytes |
| Residency counters | Measured/configured/profile minimum governs; absolute byte ceiling 1 PiB, counter ceiling 1,000,000, usage sequence at most `2^63 - 1` |
| Request common subset | At most 32 questions, 20 Choice options, 2-10 Score levels; exact profile byte/token/deadline bounds |
| Normalization input | State traversal at most 32 levels / 65,536 nodes, then profile character/byte ceilings |
| Normalization record | Parse at most 8 levels / 4,096 nodes / 256 KiB characters |
| Execution | One bounded local test worker for this runbook; no downloader, endpoint client, server, model load, or runtime process |

Configured and profile limits must be lower than or equal to hard parser ceilings.
Measured resident bytes and actual device headroom govern future admission; a
published estimate never does. Requested, leased, or in-flight identities are
protected from future eviction. The current planner only records intent and
never preloads or evicts.

## Safe Upgrade and Revert Sequence

Use this sequence for every candidate; a later phase never repairs missing
evidence from an earlier phase.

1. Keep blue authoritative and immutable. Acquire and review exact local source,
   artifact, license, security, and SBOM evidence.
2. Create a new disabled identity and profile; never alter the admitted identity
   in place.
3. Validate the canonical contract and exact pinned compatibility. Build and
   replay the in-memory prepared update.
4. Build, render, and replay the dry-run report. Confirm the six-stage order,
   rollback boundary, disabled recovery modes, false flags, redaction, and exact
   digest set.
5. Obtain independent compatibility, security, evaluation, and lifecycle review.
6. If durable evidence is required, perform the create-only pre-projection
   handoff and replay its recovery receipt. Candidate remains disabled.
7. **Stop here in v0.1.2.** Current code cannot safely admit, shadow, canary,
   promote, demote, or execute rollback.
8. Only after Phase 5 is implemented and all gates pass, create and attest green
   without traffic, prove readiness, then advance manually through shadow and
   bounded canaries by exact digest.
9. Preserve blue until green completes its observation windows and a rollback/
   absence drill. Never make promotion automatic.

Revert by the last verified boundary:

- in-memory or dry-run only: reject the candidate and restart with corrected
  immutable inputs; there is no production change to undo;
- durable pre-projection evidence: replay the recovery receipt, retain the
  evidence set, and leave the candidate disabled; and
- future shadow/canary: use only the separately implemented atomic blue pointer,
  drain, owned-resource retirement, lease release, and absence-proof workflow.
  Until that workflow exists and is tested, Phase 5 remains closed.
