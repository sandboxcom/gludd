# Feature: Git Mastery and Release Captain Expert

**Spec ID:** GRC-001
**Status:** DRAFT — implementation-ready; cited research pass pending
**Target:** development after `v0.1.0-beta.3`
**Collection:** `general_ludd.git_release`

## 1. Purpose

Gludd SHALL provide a Git and release-captain expert that can investigate a
repository, preserve work, select the project's established build and helper
tools, prepare a reproducible release, operate a zero-downtime deployment, and
prove the result with machine-verifiable evidence.

The expert is an evidence-driven planner and operator, not a command generator.
It SHALL distinguish observations, inferences, proposed mutations, completed
mutations, and verified outcomes. It SHALL prefer an existing project command,
script, package, or mature ecosystem tool over newly generated automation.

## 2. Scope

### 2.1 Included

- Git object, reference, index, worktree, reflog, remote, submodule, LFS,
  partial-clone, shallow-clone, hook, signature, and attribute behavior.
- Repository forensics, branch topology, bisect, blame, range-diff, conflict
  diagnosis, work recovery, patch transport, history hygiene, and safe
  collaboration.
- Release planning, version and changelog validation, gates, artifacts,
  provenance, signing, publishing, staged deployment, rollback, and
  post-release verification.
- Discovery and selection of repository-native build, deploy, test, debug, and
  maintenance helpers.
- Generation of a helper only when no adequate existing helper exists, with
  tests, documentation, idempotence, and least privilege.
- GitHub, GitLab, Forgejo/Gitea, Bitbucket, Buildkite, Jenkins, and local-only
  workflows through capability adapters rather than provider-specific logic in
  the domain model.

### 2.2 Excluded

- Circumventing branch protection, review, signing, or deployment approvals.
- Rewriting a shared branch without an explicit, scoped, fresh authorization.
- Claiming that a release or deployment succeeded from process exit alone.
- Inventing credentials, uploading secrets, or logging secret-bearing URLs.
- Replacing an adequate project-native helper for stylistic preference.

## 3. User-visible roles

| Role | Requirement |
|---|---|
| `repo_assess` | Inventory repository state, topology, policies, toolchain, and hazards without mutation. |
| `history_investigate` | Trace changes with log, blame, bisect, patch-id, range-diff, and object evidence. |
| `work_recover` | Produce a reversible recovery plan from reflog, dangling objects, stashes, worktrees, or backups. |
| `branch_plan` | Plan branch creation, synchronization, backport, merge, rebase, or cherry-pick with collision evidence. |
| `conflict_resolve` | Explain base/ours/theirs, preserve both intentions, validate resolution, and retain recovery points. |
| `helper_discover` | Find build, test, deploy, package, debug, migration, and maintenance entry points. |
| `helper_select` | Rank discovered helpers by project authority, fitness, safety, reproducibility, and support. |
| `helper_build` | Add the narrowest missing helper, its tests, docs, and machine-readable contract. |
| `release_plan` | Derive version, change set, compatibility, gate, artifact, provenance, rollout, and rollback plans. |
| `pipeline_triage` | Correlate failing jobs to a root cause and propose or implement the smallest safe correction. |
| `artifact_build` | Build once in a clean environment and record immutable inputs, digests, SBOM, and provenance. |
| `artifact_verify` | Verify signatures, checksums, installability, smoke behavior, and release-page completeness. |
| `deploy_orchestrate` | Execute canary, rolling, or blue-green deployment with health and rollback gates. |
| `release_recover` | Halt promotion, preserve evidence, roll back safely, and reconcile tags/releases after failure. |

## 4. Required knowledge model

### 4.1 Git mastery

The collection SHALL encode and test:

- Content-addressed objects, refs and symbolic refs, reachability, packfiles,
  alternates, commit graphs, replace refs, and garbage-collection implications.
- Three trees (HEAD, index, working tree), pathspecs, revision syntax, merge
  bases, first-parent history, rename detection, and line-ending attributes.
- Merge, rebase, cherry-pick, revert, reset, restore, checkout, switch, and
  worktree semantics, including abort and recovery paths.
- Hooks, filters, `.gitattributes`, `.gitignore`, sparse checkout, partial and
  shallow clones, submodules, subtrees, Git LFS, signed commits/tags, and SSH or
  OpenPGP verification.
- Distributed collaboration, protected branches, fork workflows, patch series,
  backports, release branches, hotfixes, and multi-remote reconciliation.
- Failure cases: detached HEAD, ambiguous revisions, unrelated histories,
  missing merge bases, dirty worktrees, interrupted operations, locked refs,
  case-only renames, symlink and mode changes, shallow history, and force-push
  races.

### 4.2 Release-captain mastery

The collection SHALL encode and test:

- Semantic and calendar versioning, prerelease ordering, compatibility policy,
  deprecation, migration, changelog, and release-note generation.
- Reproducible builds, hermetic inputs, dependency locks, SBOMs, provenance,
  signatures, checksums, artifact retention, and install/upgrade/uninstall
  verification.
- CI gate ownership, flaky-test handling without suppression, platform
  matrices, package registries, container registries, release pages, and
  immutable tag-to-artifact correspondence.
- Canary, rolling, blue-green, shadow, feature-flag, and traffic-shift
  deployments, with schema compatibility and mixed-version operation.
- Incident command, stop-the-line criteria, rollback/roll-forward decisions,
  communication, evidence retention, and post-release review.

### 4.3 Helper discovery and selection

`helper_discover` SHALL examine, in priority order:

1. Repository instructions and contracts: `AGENTS.md`, `CONTRIBUTING*`,
   `README*`, `DEVELOPMENT*`, `SECURITY*`, `RELEASING*`, and runbooks.
2. Native entry points: `Makefile`, `Taskfile*`, `justfile`, `tox.ini`,
   `noxfile.py`, `pyproject.toml`, package-manager scripts, language build
   files, and executable scripts.
3. CI and deployment declarations: workflow files, pipeline definitions,
   container files, Compose files, Helm charts, Ansible, Terraform, Packer,
   Nix, and platform manifests.
4. Debug and operations helpers: health checks, smoke tests, log collectors,
   profiling, tracing, crash dumps, migrations, backup/restore, and rollback.
5. Ecosystem-native mature tools only after project-native options are
   exhausted.

Candidates SHALL be ranked with a recorded explanation:

`project authority > existing CI usage > maintained ecosystem standard >`
`locally generated helper`.

The selector SHALL score capability fit, documentation, maintenance, license,
platform support, determinism, security posture, observability, reversibility,
and adoption cost. Popularity alone SHALL NOT authorize a tool.

## 5. Interfaces and data contracts

All contracts SHALL be versioned JSON-serializable records. Unknown required
fields SHALL fail validation; additive optional fields SHALL preserve backward
compatibility.

### 5.1 `RepoEvidence`

```text
schema_version: string
repo_root: absolute path
head_sha: full commit SHA
branch: string | null
upstreams: [{local_ref, remote_ref, ahead, behind}]
worktrees: [{path, branch, head_sha, dirty}]
operations: [{kind, state, recovery_command_id}]
dirty_paths: [{path, index_state, worktree_state, untracked}]
policies: [{source, rule_id, text_digest}]
evidence_time: RFC3339 timestamp
```

### 5.2 `HelperCandidate`

```text
id: stable string
kind: build | test | deploy | debug | package | migrate | rollback | other
source_path: repository-relative path or package URL
authority: repository | ci-used | ecosystem | generated
invocation_id: policy-registry command ID
inputs: [{name, required, secret, default}]
outputs: [{name, path_or_channel, digestible}]
side_effects: [enum]
supports_dry_run: bool
supports_rollback: bool
observability: [event name]
score: integer 0..100
score_evidence: [{criterion, value, source}]
```

### 5.3 `ReleasePlan`

```text
release_id: UUID
source_sha: full commit SHA
version: normalized version
change_set: [commit SHA]
required_gates: [{id, command_id, timeout_s, success_contract}]
artifacts: [{id, platform, format, expected_name, verification}]
provenance: {sbom, signature, attestation, builder_identity}
deployment: {strategy, stages, health_gates, pause_points}
rollback: {trigger, target, data_compatibility, command_id}
approvals: [{scope, approver_class, state, expires_at}]
```

### 5.4 `ReleaseVerdict`

```text
release_id: UUID
source_sha: full commit SHA
tag_target_sha: full commit SHA
gate_results: [{id, state, evidence_uri, digest}]
artifact_results: [{id, digest, signature_state, install_state}]
deployment_results: [{stage, health, traffic_percent, evidence_uri}]
release_page: {url, asset_names, asset_digests}
state: blocked | ready | deploying | rolled_back | released
reasons: [stable reason code]
```

## 6. Execution and security requirements

### GRC-SEC-001: Plan before mutation

Every mutating action SHALL cite a fresh `RepoEvidence`, declare affected refs
and paths, declare a recovery point, and pass policy authorization. Evidence
older than the configured maximum age SHALL be refreshed.

### GRC-SEC-002: Command allowlist

Roles SHALL invoke capability-registry command IDs, never concatenate a shell
string. Arguments SHALL remain an array, paths SHALL be canonicalized, and
environment variables SHALL be allowlisted and secret-redacted.

### GRC-SEC-003: Destructive operations

Force push, reset of a shared ref, tag deletion or movement, release deletion,
artifact replacement, and production rollback require:

- an explicit target resolved to immutable identifiers;
- an approval scoped to that target and operation;
- a dry-run or precondition check;
- a verified recovery point;
- an audit event before and after the mutation.

`--force` SHALL be rejected when a lease-aware operation can satisfy the plan.

### GRC-SEC-004: Fail closed

Missing policies, unknown branch state, dirty paths outside the plan, a moving
source SHA, mismatched tag target, unavailable gate evidence, unverifiable
artifact digest, or ambiguous deployment health SHALL produce `blocked`.

### GRC-SEC-005: Supply chain

Build dependencies SHALL be locked. Network-fetched inputs SHALL be digest
verified. Build outputs SHALL receive checksums, an SBOM, and provenance.
Signing keys SHALL remain in an external signer or secret provider and SHALL
never appear in prompts, logs, generated scripts, or artifacts.

### GRC-SEC-006: Generated helper constraints

A generated helper SHALL:

- be the smallest missing adapter around existing tools;
- support `--help`, deterministic non-interactive execution, and dry-run when
  mutation is possible;
- be idempotent or expose an idempotency key;
- use bounded timeouts and propagate nonzero exits;
- stream phase progress and structured results;
- include unit, failure-path, and behavioral tests;
- be registered in the repository's helper contract and documentation.

## 7. Zero-downtime release protocol

The expert SHALL implement the following state machine:

```text
DISCOVER -> PLAN -> BUILD_ONCE -> VERIFY_OFFLINE -> STAGE
         -> CANARY -> PROMOTE -> VERIFY_RELEASE_PAGE -> RELEASED
                       |              |
                       +--> ROLLBACK <-+
```

### GRC-ZDD-001: Build once, promote by digest

Every stage SHALL consume the same immutable artifact digest. Rebuilding for a
later environment is prohibited.

### GRC-ZDD-002: Compatibility

Before traffic shift, the plan SHALL prove backward/forward API compatibility,
expand-contract database ordering, rollback-safe data changes, and mixed-version
operation for at least the maximum rollout duration.

### GRC-ZDD-003: Health gates

Health gates SHALL combine availability, error rate, latency, saturation,
correctness probes, and domain-specific smoke tests. A process being alive is
insufficient. Missing or stale telemetry SHALL block promotion.

### GRC-ZDD-004: Controlled promotion

Traffic changes SHALL be bounded and observable. Each step SHALL have a minimum
observation window, an abort threshold, and an automatic rollback target. A
manual pause point SHALL not discard the idempotency key or release evidence.

### GRC-ZDD-005: Release-page closure

The release is not `released` until the remote release page names the exact
source SHA and exposes every expected artifact, checksum, signature,
attestation, SBOM, and release note. Each remote asset digest SHALL match the
locally verified digest.

## 8. Failure behavior and recovery

| Failure | Required behavior |
|---|---|
| Source ref moves | Stop; retain built evidence; require a new plan keyed to the new SHA. |
| Gate times out | Mark `blocked`, terminate descendants, retain logs, do not infer failure or success. |
| Flaky test | Reproduce and repair or formally quarantine through policy; never silently rerun to green. |
| Artifact mismatch | Quarantine artifact, stop promotion, preserve both digests and builder evidence. |
| Partial publish | Reconcile idempotently by digest; never overwrite a different asset under the same name. |
| Canary regression | Stop traffic increase, roll back automatically, verify recovery, open an incident record. |
| Database incompatibility | Do not deploy; if already applied, use the approved roll-forward/restore plan. |
| Lost credentials | Stop; do not downgrade signing or protection; emit a redacted operator action. |
| Merge conflict | Preserve recovery refs, produce base/ours/theirs evidence, and require validated resolution. |

## 9. Observability

Every role SHALL emit OpenTelemetry-compatible events with:

- `trace_id`, `release_id`, `operation_id`, `repo_id`, `source_sha`, role, phase,
  monotonic sequence, start/end timestamps, duration, and outcome;
- stable reason codes and redacted diagnostics;
- command ID and argument digest, never raw secret values;
- input/output artifact digests and evidence URIs;
- resource use for builds and long-running tests;
- heartbeats at least every 30 seconds while an operation is active.

Required events:

```text
git.repo.assessed
git.operation.planned
git.operation.applied
git.operation.recovered
helper.candidate.discovered
helper.candidate.selected
helper.generated
release.plan.created
release.gate.completed
release.artifact.built
release.artifact.verified
release.deployment.stage
release.rollback
release.page.verified
```

Metrics SHALL include gate duration/failure class, build reproducibility rate,
artifact verification rate, canary rollback count, deployment recovery time,
helper reuse ratio, and operations blocked by safety policy.

## 10. Knowledge freshness and practitioner evidence

The knowledge package SHALL maintain a source registry with retrieval time,
license, content digest, authority class, and review expiry. It SHALL include
official Git documentation and hosting-provider documentation, build and
packaging specifications, supply-chain standards, and long-lived practitioner
reports from public issue trackers or forums.

The serialized research follow-up SHALL add cited practitioner evidence for at
least these recurring classes before implementation begins:

- shallow/partial clone history surprises;
- line-ending, file-mode, case-folding, and rename behavior;
- submodule and LFS authentication or reproducibility failures;
- force-push, tag-movement, protected-branch, and concurrent-release races;
- CI cancellation, stale status, cache poisoning, and flaky gate behavior;
- package/signing/provenance differences across release platforms.

Each finding SHALL record URL, first/last observed date when available, affected
versions/platforms, symptom, root cause or uncertainty, mitigation, and the
requirement IDs it informed. The expert SHALL prefer current official behavior
when a forum report conflicts with maintained documentation.

### 10.1 Practitioner findings for serialization and CI cancellation

- A GitHub Actions community thread opened in April 2021 reported that
  workflow-level `cancel-in-progress` appeared to queue rather than cancel, then
  confirmed the behavior was working in May 2021. The long-lived lesson is that
  concurrency behavior must be verified rather than inferred from configuration:
  [GitHub Community discussion #26566](https://github.com/orgs/community/discussions/26566).
  Current GitHub documentation says a new run in the same group cancels an active
  run when `cancel-in-progress: true`, so Gludd's push guard MUST remain
  fail-closed while a branch run is active instead of treating a force override
  as permission to churn that run. This informs GRC-SEC-004 and GRC-AT-003.
- Users have reported `.git/index.lock` failures across editor and agent tooling
  since at least 2021, including a concurrent-process report in
  [r/logseq](https://www.reddit.com/r/logseq/comments/pmxtg7/) and a 2026 agent
  report in
  [r/ClaudeAI](https://www.reddit.com/r/ClaudeAI/comments/1v2m8mu/claude_cowork_repeatedly_leaves_git_indexlock/).
  Deleting the lock file treats only the symptom and can race a live Git process.
  Gludd therefore serializes the full duration of every Git subprocess with a
  repository-common lock, uses a bounded acquisition timeout, and tests regular
  checkouts plus linked worktrees across threads and spawned processes. This
  informs GRC-SEC-003, GRC-SEC-004, and GRC-AT-003.

## 11. Implementation layout

```text
collections/ansible_collections/general_ludd/git_release/
├── galaxy.yml
├── README.md
└── roles/
    ├── repo_assess/
    ├── history_investigate/
    ├── work_recover/
    ├── branch_plan/
    ├── conflict_resolve/
    ├── helper_discover/
    ├── helper_select/
    ├── helper_build/
    ├── release_plan/
    ├── pipeline_triage/
    ├── artifact_build/
    ├── artifact_verify/
    ├── deploy_orchestrate/
    └── release_recover/
src/general_ludd/git_release/
├── contracts.py
├── evidence.py
├── topology.py
├── helper_catalog.py
├── helper_ranker.py
├── release_state.py
├── provenance.py
├── deployment.py
└── source_registry.py
tests/{unit,integration,e2e}/git_release/
```

Provider adapters SHALL live outside the domain model and implement typed
protocols for repository hosting, CI, artifact registries, signing, deployment,
and telemetry.

## 12. Delivery phases

1. **GRC-P1 — Read-only Git evidence:** contracts, repository assessment,
   topology, history investigation, source registry, and deterministic fixtures.
2. **GRC-P2 — Safe Git operations:** branch planning, conflict handling,
   recovery points, authorization, and failure injection.
3. **GRC-P3 — Helper intelligence:** discovery, ranking, contract extraction,
   and generated-helper TDD.
4. **GRC-P4 — Release planning:** version/change-set logic, gates, artifacts,
   provenance, and remote-provider fakes.
5. **GRC-P5 — ZDD orchestration:** canary/rolling/blue-green state machine,
   mixed-version and rollback tests.
6. **GRC-P6 — Release closure:** real sandbox forge E2E, package installation,
   signature and release-page verification.

Each phase SHALL be independently deployable behind a default-off capability
flag. Read-only roles MAY be enabled before mutation roles.

## 13. Measurable acceptance tests

### GRC-AT-001: Repository evidence

Given fixtures for clean, dirty, detached, shallow, multi-worktree, submodule,
LFS, interrupted-rebase, and diverged-upstream repositories, `repo_assess`
SHALL return the expected normalized `RepoEvidence` with no mutation.

### GRC-AT-002: Recovery

For 20 seeded loss scenarios, `work_recover` SHALL preserve the original object
database, create a recovery ref, and restore the expected tree in 20/20 cases.
An expired or missing object SHALL return `blocked`, not a fabricated recovery.

### GRC-AT-003: Concurrency safety

When the remote ref moves after planning, branch update and release publication
SHALL refuse the stale operation in 100/100 race-injection trials.

### GRC-AT-004: Helper selection

For a corpus containing Make, Task, Just, Python, Node, Rust, Java, Go,
container, Terraform, Ansible, and mixed-language repositories, discovery SHALL
find every CI-invoked entry point. Selection SHALL choose the repository-native
helper in every golden case and SHALL explain every score component.

### GRC-AT-005: No needless helper generation

If an adequate helper scores above the policy threshold, `helper_build` SHALL
make zero file changes. If none does, it SHALL create one narrow helper plus
tests, documentation, and a contract; rerunning SHALL make zero further changes.

### GRC-AT-006: Reproducible artifacts

Two clean builds of the same source, lockfiles, toolchain, and declared
environment SHALL produce byte-identical artifacts or an explicitly normalized
format with identical canonical digest. All expected artifacts SHALL install,
smoke-test, uninstall, and verify checksums/signatures.

### GRC-AT-007: ZDD and rollback

In a disposable two-version service, a canary rollout SHALL sustain the
configured synthetic load without failed requests. Injected latency, error,
schema, and correctness regressions SHALL stop promotion and restore the prior
healthy digest within the configured recovery objective.

### GRC-AT-008: Release-page proof

Against a sandbox forge, the release SHALL remain `deploying` until all expected
assets and metadata are remotely visible and digest-matched. Missing,
duplicated, or mismatched assets SHALL yield `blocked`.

### GRC-AT-009: Security

Tests SHALL prove command-injection resistance, path containment, secret
redaction, signature failure handling, authorization expiry, protected-ref
behavior, and fail-closed handling of missing telemetry and policy.

### GRC-AT-010: Quality gate

- Overall changed-code coverage SHALL be at least 85%.
- Every changed production file SHALL be at least 75%.
- Unit, integration, provider-contract, failure-injection, and sandbox-forge
  E2E suites SHALL pass with warnings treated as errors.
- Collection build, syntax, lint, typecheck, security scan, SBOM generation,
  and documentation link checks SHALL pass.
- A release-role change SHALL prove zero downtime or be blocked from merge.

## 14. Definition of done

The feature is implemented only when all GRC acceptance tests are automated,
the source registry contains the cited research and practitioner evidence, each
role has a documented privilege and side-effect contract, default mutation
capabilities are off, and the full project gate is green on the exact commit
that is proposed for merge.
