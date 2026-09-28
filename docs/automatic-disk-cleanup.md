# Automatic disk cleanup preflight

The v0.1.1 disk preflight replaces model-directed cleanup with a deterministic,
non-interactive safety gate. `make gate`, `make gate-fast`, `make gate-lite`,
`make preflight`, and the pre-commit hook run it before expensive validation.
It uses the existing 100 MB generated-scratch limit and 90% repository-volume
limit from `scripts/check_disk_usage.py`.

## Safety boundary

Cleanup starts only when either limit is already exceeded. Candidate discovery
comes from Git's stable porcelain worktree registry. A candidate must be a
strict descendant of `/tmp/gludd-worktrees` or the main checkout's
`.claude/worktrees`, and all of these checks must pass:

- it is not the main or invoking checkout;
- it is not locked, prunable, or detached;
- no visible process command line references its canonical path; and
- a fresh Git, logical-workstream, and process inspection still identifies the
  same inactive worktree immediately before mutation.

An active logical-workstream registration is an ownership lease, not a permanent
disk leak. A fresh 24-hour lease protects an agent that is thinking without an OS
PID. A registered worktree becomes cache-eligible without model interaction only
when Git proves it is clean and its commits were integrated (including patch-
equivalent cherry-picks) after registration, or when the lease has expired and
the worktree is clean with every source change committed. An exact-head Git
commit-reflog receipt also makes direct generated caches immediately eligible,
while preserving the materialization so an agent may continue after committing.
Only an unchanged receipt at least 30 minutes old can prove that clean checkout
eligible for retirement; `DISK_CLEANUP_RECEIPT_GRACE_SECONDS` may lengthen but
never shorten that safety floor. Registry activity after the receipt invalidates
it until another exact-head commit resets the grace. A fresh unintegrated
worktree without that receipt remains protected. Dirty work is always protected.
A renewed or missing lease, new process, registration change, or dirtying race
detected during any reinspection cancels cleanup.

The first recovery tier removes only direct-child `.venv`, `.pytest_cache`,
`.mypy_cache`, and `.ruff_cache` directories. Symlinks, files with an allowlisted
name, changed registrations, and inspection failures are refused. An inactive
unregistered worktree can use this cache-only tier, but never qualifies for
checkout removal because it has no completion-lease proof.

Under continuing pressure, at most four proven-complete materializations are
removed per run. Immediately before `git worktree remove` (without `--force`),
the preflight again requires the same completion lease, unlocked registration,
no matching PID, no tracked or untracked changes, and an exact local branch ref
equal to the checkout's existing commit. Ignored content is refused unless it is
only Python bytecode cache material, `.gate-logs` release evidence, or `.gludd`
application state. Before every retirement, a collision-proof manifest is
fsynced under the repository Git common directory's
`.git/gludd-release-evidence` archive. It records the exact branch, commit,
original checkout path, rehydration coordinates, and any preserved paths. Gate
logs and application state are atomically moved into that archive; a removal
refusal or revalidation race moves them back. Legacy sibling archives are moved
to the canonical Git-common archive only after exact manifest validation.
Credentials, unknown ignored data, and other release artifacts remain protected.
After removal, both the exact branch ref and commit object are verified again.
The branch, commit history, tracked source, and archived evidence therefore
remain available; no branch, ref, commit, evidence, or workstream-registry record
is deleted.

The other recovery tier targets only the exact resolved
`/tmp/gludd-uv-cache-public-v2` directory. It refuses symlinks, scans the system
process table twice before mutation, and skips pruning if any `uv` owner is
visible or inspection is ambiguous. When idle, it first calls uv's lock-aware
`cache prune`, performs a third system-wide owner check, and then calls uv's
lock-aware `cache clean` for remaining regenerable entries. Both operations use
the exact cache root and bounded lock/process timeouts; files are never deleted
directly. A concurrent uv user is protected by both the three checks and uv's
cache locking.

After cleanup it re-runs both canonical measurements. Any cleanup safety error,
inspection error, scratch usage above 100 MB, or volume usage above 90% returns
nonzero. Skipping an active or protected worktree is expected, but residual
pressure still blocks the gate.

## Zero-downtime operation and observability

The active-workstream lease protects model-owned work that cannot be inferred
from operating-system PIDs, including a no-PID thinking interval. Git cleanliness,
integration ancestry/patch identity, and integration timestamps provide the
automatic completion signal. An exact-head commit receipt provides the immediate
cache-safe signal, and its unchanged age plus the configurable minimum grace
provides a conservative retirement signal without treating a missing PID as
agent inactivity. The independent PID check protects tests, linters, servers,
and shells whose command lines name a worktree. Together these checks make
cleanup a zero-downtime operation: ambiguous worktrees remain untouched while
completed environments can be regenerated by the next `uv` run.

Every phase emits a visible marker: initial inspection, pressure detection,
each candidate removal or uv-prune start/completion, every refusal, and the final
recheck. This makes a slow recovery distinguishable from a stalled gate without
a model polling the filesystem. A non-mutating preview is available with
`make disk-cleanup-preflight DISK_CLEANUP_PREFLIGHT_VALIDATE_ONLY=0`
`DISK_CLEANUP_PREFLIGHT_DRY_RUN=1`
`DISK_CLEANUP_RECEIPT_GRACE_SECONDS=1800`; it still returns nonzero while
pressure remains, so automation cannot mistake a preview for recovered capacity.

## Rollback

The change is operationally reversible because it deletes only regenerable
cache content or a checkout whose exact branch and commit remain. Re-running
`uv sync` recreates tool state; pytest, mypy, and Ruff recreate their caches; and
`git worktree add <path> <branch>` recreates a removed materialization. To roll
back the automation itself, revert the feature commit. Archived `.gate-logs`
remain under `.git/gludd-release-evidence` and can be moved into a recreated
checkout using the fsynced rehydration manifest. The prior read-only checker remains in
`scripts/check_disk_usage.py`, and no data migration or service restart is
required. Do not weaken the fail-closed threshold recheck as a rollback shortcut.

## Long-lived user reports considered

- [uv issue 11432](https://github.com/astral-sh/uv/issues/11432) reports caches
  reaching 20–40 GB and requiring recurring manual cleanup. This supports an
  automatic threshold-triggered preflight instead of relying on model memory.
- [uv issue 11694](https://github.com/astral-sh/uv/issues/11694) reports cache
  pruning breaking a still-running `uvx` process. The Gludd preflight therefore
  requires two system-wide idle checks and delegates pruning to uv's locked
  operation instead of deleting shared-cache files itself.
- [Orca issue 10562](https://github.com/stablyai/orca/issues/10562) reports that
  deleting worktrees externally can strand agent and terminal process trees.
  Gludd consequently requires a stable completion lease, repeated PID and Git
  checks, and Git's non-forced removal instead of deleting a directory directly.
- The official
  [Git worktree documentation](https://git-scm.com/docs/git-worktree) distinguishes
  worktree removal from pruning missing-worktree metadata and documents locks as
  protection from pruning. The preflight treats locks as absolute protection and
  verifies the durable branch and commit on both sides of materialization removal.

These reports are durable failure patterns rather than dependencies on a specific
tool version: unbounded generated data, cleanup racing active processes, and
workspace deletion racing agent ownership.
