---
name: git-release-captain
description: "Use for repository assessment, release planning, zero-downtime deployment (ZDD), helper ranking & generation, build-once/promote-by-digest, canary gating, provenance/SBOM attestation, and release-page verification. Covers the full DISCOVER → PLAN → BUILD_ONCE → VERIFY_OFFLINE → STAGE → CANARY → PROMOTE → VERIFY_RELEASE_PAGE → RELEASED lifecycle with rollback. Trigger keywords: release, ship, cut a release, deploy, zero-downtime, ZDD, canary, blue-green, rollback, promote, digest, provenance, SBOM, CycloneDX, in-toto, attestation, signature, release page, helper ranking, release verdict, repo evidence, branch planning, topology, source authority."
---

# Git Release Captain

An evidence-driven planner and operator for repository assessment, release
planning, and zero-downtime deployment. Implements spec GRC-001. Every transition
in the release lifecycle is gated by spec-mandated preconditions and recorded as
typed evidence — the captain never advances on assertion alone. Backed by
`src/general_ludd/git_release/`.

## When to Use

- "Assess this repo's readiness for a release" (dirty tree, worktrees, policies).
- "Plan a release for commit `<sha>`" (helpers, gates, artifacts, deployment).
- "Drive this release through canary → promote" (health-gated, rollback-aware).
- "Build provenance / SBOM and verify signatures for the shipped artifact."
- "Rank candidate release-helper scripts and emit the wiring changes."

If the query is about ML model promotion/canaries, use `ai-ml-expert` instead.

## Available Roles

| Capability | Entry point |
|---|---|
| Assess repo state | `assess_repo(path)`, `collect_repo_evidence(path)` → `RepoEvidence` |
| Rank helpers | `rank_helpers(candidates, TaskRequirements)`, `helper_build_file_changes(plan)` |
| Discover helpers | `discover_helpers(repo_root)` |
| Plan a release | `ReleasePlan` (gates, artifacts, deployment, rollback, approvals) |
| Drive lifecycle | `ReleaseStateMachine(source_sha, artifact_digest, release_id, deployment_target)` |
| Decide a verdict | `ReleaseVerdict`, `ReleaseVerdictState` |
| Build/verify provenance | `build_provenance(...)`, `verify_provenance(...)` → release-scoped record and deploy/rollback receipt |
| Orchestrate deployment | `DeploymentOrchestrator` (blue-green, canary, traffic shifts) |
| Source authority | `SourceRegistry`, `default_registry()`, `FreshnessFlag` |

## Service API Entry Points

| Entry point | Purpose |
|---|---|
| `ReleaseStateMachine(..., release_id=..., deployment_target=...).advance(...)` | Requires exact deploy/rollback receipts and returns `AdvanceResult` |
| `assess_repo(path)` | `RepoEvidence` (worktrees, dirty paths, operations, policies, upstreams) |
| `rank_helpers(candidates, req)` | Sorted `HelperCandidate`s + `ScoreEvidence` per criterion |
| `build_provenance(...)` / `verify_provenance(...)` | CycloneDX SBOM + in-toto statement; `SignatureState` |
| `DeploymentOrchestrator(config).decide(samples)` | `Promote`/`Hold`/`Abort`/`Rollback`/`BlueGreenCutComplete` |

## ZDD Lifecycle & Safety Boundaries

```
DISCOVER → PLAN → BUILD_ONCE → VERIFY_OFFLINE → STAGE
        → CANARY → PROMOTE → VERIFY_RELEASE_PAGE → RELEASED
                      |              |
                      +--> ROLLBACK <-+
```

- **BUILD_ONCE pins the source SHA.** A moving source ref blocks every later
  stage (GRC-SEC-004 "fail closed").
- **VERIFY_OFFLINE requires non-empty gate evidence** — missing gate evidence
  blocks (GRC-SEC-004).
- **STAGE requires the consumed artifact digest and a deploy-purpose verification
  receipt to match the pinned release, source, target, and build digest**
  (GRC-ZDD-001 "build once, promote by digest").
- **CANARY/PROMOTE require a passed health gate** (GRC-ZDD-003).
- **VERIFY_RELEASE_PAGE requires the remote release page proven complete**
  (GRC-ZDD-005).
- **RELEASED is terminal** — a shipped release cannot transition out; recovery
  is a fresh plan.
- **Rollback** is allowed from `CANARY` and `PROMOTE` only with a rollback-purpose
  receipt for the prior known-good digest; rollback from `RELEASED` is forbidden.
- A blocked `AdvanceResult` leaves `state` unchanged — the machine never silently
  advances on a failed precondition.

## Usage Examples

```python
from general_ludd.git_release import (
    ReleaseStateMachine, ReleaseState, assess_repo, rank_helpers,
)

evidence = assess_repo("/path/to/repo")  # RepoEvidence

sm = ReleaseStateMachine(
    source_sha="a" * 40,
    artifact_digest="0" * 64,
    release_id="release-1",
    deployment_target="production",
)
result = sm.advance(target=ReleaseState.VERIFY_OFFLINE, gate_evidence=[...])
# result.blocked, result.reasons (e.g. ["GRC-SEC-004"]), result.state
```

```python
from general_ludd.git_release import ReceiptPurpose, build_provenance, verify_provenance

prov = build_provenance(
    artifact_name="app.tar.gz",
    artifact_bytes=artifact_bytes,
    dependency_lock_bytes=lock_bytes,
    builder_identity="github-actions:release",
    release_id="release-1",
    source_sha="a" * 40,
)
verdict = verify_provenance(
    prov,
    expected_artifact_bytes=artifact_bytes,
    expected_lock_bytes=lock_bytes,
    expected_subject="app.tar.gz",
    expected_builder_identity="github-actions:release",
    expected_release_id="release-1",
    expected_source_sha="a" * 40,
    verified_attestation_digest=external_signer_payload_digest,
    authorization_id="release-1",
    authorization_source_sha="a" * 40,
    receipt_purpose=ReceiptPurpose.DEPLOY,
    deployment_target="production",
)
# verdict.ok and verdict.receipt are required before staging.
```

## See Also

- `ai-ml-expert` — model promotion / canary gates for ML artifacts
- spec GRC-001 (release state machine) and GRC-ZDD-* (zero-downtime) in `docs/specs/`
- `src/general_ludd/git_release/contracts.py` — `ReleasePlan`, `ReleaseVerdict` shapes
