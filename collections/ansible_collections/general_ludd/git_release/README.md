# general_ludd.git_release

Git Mastery and Release Captain Expert collection (spec `GRC-001` —
[`docs/specs/FEATURE_GIT_RELEASE_CAPTAIN_EXPERT.md`](../../../docs/specs/FEATURE_GIT_RELEASE_CAPTAIN_EXPERT.md)).

Evidence-driven planner and operator for repository assessment, history
investigation, branch planning, release preparation, artifact verification,
and zero-downtime deployment. Every observation is recorded as
machine-verifiable evidence so downstream planners can distinguish
observations, inferences, proposed mutations, completed mutations, and
verified outcomes. Read-only repository evidence crosses the runtime boundary
through the authenticated Gludd HTTP API; collection roles do not import the
core package.

## Implemented roles (`roles/`)

All 14 spec §3 roles are implemented. Read-only assessment roles are backed by
the Python evidence module; mutation roles never execute without an explicit
scoped authorization.

| Role | Purpose |
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

The private `collect_repo_evidence` composition role is the single transport
owner reused by `repo_assess`, `history_investigate`, and `branch_plan`.

## Python service API (`src/general_ludd/git_release/`)

Typed entry points consumed by the roles. Public surface is kept narrow on
purpose — downstream code consumes the `RepoEvidence` shape rather than raw
subprocess output.

| Module | Key exports |
|---|---|
| `evidence.py` | `RepoEvidence`, `collect_repo_evidence` |
| `topology.py` | `assess_repo` |
| `contracts.py` | `ReleasePlan`, `ReleaseVerdict`, `ReleaseVerdictState`, `HelperAuthority` |
| `release_state.py` | `ReleaseState`, `ReleaseStateMachine`, `AdvanceResult`, `TransitionError` |
| `deployment.py` | `DeploymentOrchestrator`, `DeploymentConfig`, `DeploymentStrategy`, `HealthGate`, `TrafficShift`, `Decision`, `PromoteDecision`, `RollbackDecision`, `AbortDecision`, `HoldDecision`, `BlueGreenCutComplete` |
| `provenance.py` | `ProvenanceRecord`, `Attestation`, `SignatureState`, `VerificationResult`, `build_provenance`, `verify_provenance` |
| `helper_catalog.py` | `discover_helpers`, `HelperCandidate`, `HelperInput`, `HelperOutput`, `ScoreEvidence` |
| `helper_ranker.py` | `rank_helpers`, `TaskRequirements`, `GeneratedHelperPlan`, `DEFAULT_THRESHOLD`, `SCORE_CRITERIA`, `helper_build_file_changes` |
| `source_registry.py` | `SourceRegistry`, `SourceEntry`, `SourceAuthority`, `FreshnessFlag`, `default_registry` |

## Tests

5 unit-test modules under `tests/unit/test_git_release_*.py` (evidence,
contracts, state, helpers, provenance).

```bash
make test TESTFILE='tests/unit/test_git_release_evidence.py'
make test TESTFILE='tests/unit/test_git_release_*.py'
```

## Safety boundary

The collection is an evidence-driven planner and operator, not a command
generator. It will not circumvent branch protection, review, signing, or
deployment approvals; rewrite a shared branch without an explicit, scoped,
fresh authorization; claim that a release or deployment succeeded from
process exit alone; or invent credentials or upload secret-bearing URLs.
