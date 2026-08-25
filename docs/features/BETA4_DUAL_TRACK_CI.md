# Beta 4 Dual-Track CI Evidence

Status: implemented for the `v0.1.0-beta.4` candidate pipeline on 2026-08-25.

## Incident

The failed candidate at commit
`12a7052c1d09303bad8fab7bb3655b28dd1d0268` exposed a release-process defect.
Local checks and GitHub Actions were treated as sequential confidence signals,
while the hosted workflow still used a different, long-lived test runner. Run
`32805553808` consequently exposed worker retention, delayed resource warnings,
and whole-shard retries that the bounded local runner did not reproduce.

The defect was procedural as well as technical: a local green result was allowed
to influence release confidence before terminal hosted evidence existed for the
same immutable commit.

## The rule

One clean commit is frozen as the candidate. Local and GitHub-hosted tests start
from that exact SHA and run concurrently through
`scripts/run_ci_shards_serial.py`. A candidate is not release-ready unless both
lanes publish terminal, successful, exact-SHA attestations.

The following evidence is invalid:

- an attestation for a different or abbreviated commit;
- an attestation produced from a dirty checkout;
- a queued, cancelled, skipped, timed-out, or incomplete run;
- a successful retry that used changed workflow code from another commit;
- coverage without the corresponding terminal shard attestation; or
- a locally compensated cleanup that hides an application-owned resource leak.

Every bounded shard batch uses one worker, disables worker restarts, has a unique
base temporary directory, emits heartbeats, and terminates its owned process group
with bounded TERM-to-KILL cleanup. Molecule shards execute each assigned scenario
once and preserve their individual logs; a completed scenario is never replayed
as a substitute for fixing a failed one.

`make require-dual-track-green SHA=<full-sha>` is the release precondition. It
first requires a successful GitHub workflow for that exact SHA, downloads only
that run's `coverage-*` artifacts, validates all eight hosted shard attestations,
and compares them with the local all-shard attestation. Both `release-dry-run` and
`release-cut` invoke this target. The verifier rejects missing, duplicate,
malformed, dirty, failed, wrong-lane, or wrong-SHA evidence.

## Zero-downtime development

Candidate validation does not mutate the running Gludd service or the external
local-model endpoint. Mutable test resources live under the project namespace
returned by `scripts/resource_arbiter.py`; each shard gets an additional unique
batch namespace. The runner removes only the workspaces and coverage fragments it
owns. It does not stop externally owned services, including a user-started model
server.

Feature work may continue on another branch while a candidate is tested, but the
candidate SHA, workflow definition, and evidence set remain immutable. A failed
candidate is abandoned and a new commit starts both lanes again. This gives
development continuity without weakening release evidence.

## Rollback and recovery

No tag or release is created until dual-track verification succeeds. On failure,
retain the hosted logs and attestations, remove only the candidate's namespaced
temporary resources, fix the owner, and create a new commit. Do not rerun an old
commit after changing the workflow on a different branch: GitHub reruns retain the
original `GITHUB_SHA` and `GITHUB_REF`.

If a worker dies or stops producing output, the runner fails closed instead of
restarting it. This avoids xdist cases where completed work is requeued or the
controller waits indefinitely on a dead worker. Coverage remains gated at 85%
aggregate and at least 75% per measured file.

## Upstream and practitioner evidence

Reviewed 2026-08-25:

- [GitHub documentation: rerunning workflows and jobs](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/re-run-workflows-and-jobs?tool=cli)
  confirms reruns use the original commit and ref.
- [GitHub Community discussion 27083](https://github.com/orgs/community/discussions/27083)
  documents the practical consequence that a rerun uses the workflow from the
  original SHA rather than a later fix.
- [GitHub Community discussion 17854](https://github.com/orgs/community/discussions/17854)
  reports artifact replacement and visibility surprises across reruns, supporting
  immutable, SHA-bound terminal evidence instead of artifact-name trust alone.
- [pytest-xdist issue 1323](https://github.com/pytest-dev/pytest-xdist/issues/1323)
  documents hangs and completed-work requeue behavior after worker restart.
- [pytest-xdist issue 1313](https://github.com/pytest-dev/pytest-xdist/issues/1313)
  documents controller hangs after a worker dies with retained pipes.
- [pytest-xdist issue 1278](https://github.com/pytest-dev/pytest-xdist/issues/1278)
  documents missed nonzero worker exits, supporting fail-closed controller parsing.
- [Git worktree documentation](https://git-scm.com/docs/git-worktree) describes
  locked worktree ownership and pruning behavior used to preserve active candidate
  state.
