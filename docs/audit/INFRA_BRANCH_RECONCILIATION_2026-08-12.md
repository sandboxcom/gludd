# Gate and orchestration branch reconciliation

Date: 2026-08-12
Base: development `4f5d91a6`

## Source decisions

| Source ref | Unique tip | Reconciliation |
|---|---|---|
| `feature/gate-oom-fix` | `c1de962b` | Ancestry-only. Its fixed 4-GiB-per-worker helper is superseded by `adaptive_test.py`, which measures available memory and load, emits heartbeats, and halves/retries on OOM-shaped exits. Its destructive worktree remover is superseded by the scoped `wt-remove*` family. |
| `feature/mainthread-budget-hook` | `86d037af` | Ancestry-only. The advisory, absolute-path Claude shell hook is superseded by the blocking, project-aware OpenCode `enforce-delegate.ts` implementation with isolated JSON state, pressure release, read-grind detection, runtime verification, and subagent bypass. |
| `feature/unforgeable-gate-status` | `7b082e91` | Preserve the security objective with a current implementation. The old patch signed only the pytest line before smoke and the terminal result, and bound only `HEAD^{tree}`; it could therefore attest an incomplete gate or miss staged/worktree drift. |
| `feature/wave3-ship-v3` | `5ec28240` | Ancestry-only. Current Make targets already provide single/list cherry-pick, continue/skip/abort, guarded soft/hard reset, shared-file preflight, and documented recovery. The other two commits are patch-equivalent. |

Every original ref is merged as ancestry after focused proof, so completed work
does not remain in the unmerged-branch ledger.

## Practitioner evidence

- [pytest-xdist issue #792](https://github.com/pytest-dev/pytest-xdist/issues/792)
  records the multi-year need to override what `-n auto` means for constrained
  builders. Gludd retains its stronger available-memory/load calculation and
  OOM retry rather than importing a second fixed-total-RAM worker policy.
- [pre-commit issue #3245](https://github.com/pre-commit/pre-commit/issues/3245)
  shows a long-lived class of commit-time failures around stashed or mutated
  worktree state. Gate evidence is therefore checked again after pre-commit and
  auto-staging, not only as an early Make prerequisite.
- [pre-commit issue #860](https://github.com/pre-commit/pre-commit/issues/860)
  has tracked merge-aware file-selection ambiguity since 2018. The attestation
  hashes the complete candidate repository state instead of trusting a selected
  changed-file list.
- [GitHub status-check documentation](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/collaborating-on-repositories-with-code-quality-features/about-status-checks)
  describes checks as belonging to each pushed commit and explicitly models
  stale results. The local equivalent binds the result to HEAD plus the exact
  file tree and rejects evidence older than 30 minutes.

## Pagination incident and compatibility evidence

Evidence reviewed on 2026-08-20:

- The [Git 2.51 release notes](https://github.com/git/git/blob/master/Documentation/RelNotes/2.51.0.adoc)
  identify `for-each-ref --start-after` as a new pagination capability. The
  [2.42 manual](https://git-scm.com/docs/git-for-each-ref/2.42.0.html) predates
  that option, so the inventory retains its bounded legacy scan rather than
  assuming every practitioner host has upgraded.
- The [upstream Git mailing-list review](https://www.spinics.net/lists/git/msg505878.html)
  explicitly works through 100-ref page markers. Later review still corrected
  the option's marker terminology, demonstrating that this young interface was
  refined after initial review. Gludd therefore verifies monotonic output at
  its own boundary instead of trusting backend cursor behavior implicitly.
- The current [Git `for-each-ref` manual](https://git-scm.com/docs/git-for-each-ref/2.52.0.html)
  defines the marker as excluded and warns that refs may change between page
  requests. Gludd accepts and removes only an exact repeated boundary marker,
  fails closed on any ref below it, deduplicates across pages, and never treats
  a repeated marker as proof that enumeration is exhausted.

The 2026-08-20 incident had two independent symptoms: an inclusive boundary
from a supported pagination backend made page five appear stationary, and the
eventual exhaustive result contained 641 heads but semantic inspection had an
unrelated 256-head cap. One authoritative 10,000-ref ceiling now bounds both
enumeration and distinct-head inspection. It neither truncates branches nor
silently omits semantic evidence; exceeding that ceiling remains an explicit
failure.

This is a read-only, zero-downtime change. Each page and semantic head retains
observable progress, page size remains at most 100, Git calls retain their
10-second timeout, commit inspection remains bounded to 500 commits, and path
and output caps are unchanged. No process, database, service, or ref is created
or mutated. Rollback is a single code-and-test revert with no data migration;
operators may run the prior version concurrently because both versions only
read repository state.

## Versioned gate-attestation contract

A successful full `make gate` appends five version-1 fields only after smoke and
the terminal `=== GATE: PASSED ===` marker:

```text
attestation-version 1
attestation-state <sha256>
attestation-epoch <unix-seconds>
attestation-status-digest <sha256>
attestation-signature <hmac-sha256>
```

The signature covers the version, repository-state digest, epoch, and digest of
the complete unsigned status body. The repository state covers HEAD and every
tracked or non-ignored untracked path using Git-compatible mode/blob identities.
At commit time the verifier separately computes the index state and requires it
to equal the tested worktree. This closes staged-only, unstaged, new-file, replay,
post-gate mutation, partial-gate, stale-time, and status-edit seams.

The 32-byte key is created with mode `0600` at
`~/.config/gludd/gate-attestation.key`; tests override the location. The key and
signature are never logged. This protects the Make-only/worktree sandbox threat
model, not a hostile process already executing as the same OS user. HMAC
comparison is constant-time, duplicate or unknown attestation fields fail
closed, and signing refuses incomplete or failed results.

## ZDD rollout, compatibility, and rollback

Producer and verifier land in one commit. An old in-flight gate may finish
without version-1 fields after the update; it is safely rejected and must be
rerun, while no valid commit is interrupted. Existing readers retain the phase
and terminal lines and may ignore the appended fields. `gate-refresh` remains
useful for diagnostics but cannot mint full-gate evidence by preserving an old
test result.

Rollback reverts the Makefile and attestation script together. Older readers
ignore already-appended fields, so no status migration is required. Key files
may remain in place and contain no repository data. No API, database, deployment,
or runtime service schema changes are involved.

## Acceptance and observability

- A bare, incomplete, failed, stale, future-dated, wrong-state, body-modified,
  signature-modified, or duplicate-field status is rejected with a specific
  stderr reason.
- A successful full gate signs only its final body; verification prints the
  state prefix and evidence age without exposing key material.
- Worktree and index digests differ after an unstaged edit or new file and
  converge after staging, preventing time-of-check/time-of-commit drift.
- `git-commit` verifies twice: before hooks and again after hook fixes/staging.
- Focused unit tests, Ruff, scoped mypy, Make-target contract validation,
  duplicate-target validation, and zero collection errors are required before
  integration.
