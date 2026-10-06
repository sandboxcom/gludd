# Automatic disk cleanup preflight

The v0.1.1 disk preflight replaces model-directed cleanup with a deterministic,
non-interactive safety gate. `make gate`, `make gate-fast`, `make gate-lite`,
`make preflight`, and the pre-commit hook run it before expensive validation.
It uses the existing 100 MB generated-scratch limit and 90% repository-volume
limit from `scripts/check_disk_usage.py`.

## Safety boundary

Cleanup starts only when either limit is already exceeded. Candidate discovery
comes from Git's stable porcelain worktree registry. An inactive-worktree
candidate must be a strict descendant of `/tmp/gludd-worktrees` or the main
checkout's `.claude/worktrees`, and all of these checks must pass:

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

The first recovery tier closes the measurement/cleanup boundary for the invoking
worktree itself. The disk checker counts direct-child `.pytest_cache`,
`.mypy_cache`, and `.ruff_cache` directories in every registered worktree, so the
cleanup owns those same three disposable directories. Repository-volume pressure
also makes the exact `infra/terraform/.plugin-cache` eligible; that directory is
the project's declared `TF_PLUGIN_CACHE_DIR` and contains downloaded provider
binaries, not Terraform state. When a linked worktree invokes the preflight, the
same narrow provider-cache action may target the exact Git-registered main
checkout, but no other main-checkout path is eligible. Generated provider entries
are cleared while the tracked `.gitkeep` marker and cache directory remain.
Eligibility requires an exact, unlocked, non-prunable, attached registration
under an approved worktree namespace or an exact main-checkout identity. The tier
validates every path component, performs an initial process scan, refreshes the
exact Git registration, and repeats the process scan immediately before each
removal. The invoking tier recognizes only the cleanup process and its freshly
inspected synchronous parent chain as controller-owned; this prevents the
`make`/pre-commit caller from blocking its own deterministic cleanup. A sibling,
child, or unrelated path-matching PID remains an external owner and blocks the
candidate. A new owner, changed registration, symlink, non-directory, ambiguous
process ancestry or inspection, or removal failure stops that candidate
fail-closed. `.venv`,
Terraform state and lock files, source, `.gate-logs`, `.gludd`, `dist`, and release
artifacts are never selected by this tier.

The inactive-worktree tier likewise distinguishes disposable caches from
dependency environments. Direct-child `.pytest_cache`, `.mypy_cache`, and
`.ruff_cache` directories may be removed after the cache-safe ownership checks.
A `.venv` is a tool environment, not an ordinary cache: it is preserved for
active and receipt-only registered worktrees. It becomes eligible either with
the same non-cache-only completed/expired lease proof required for registered
materialization retirement, or when an unleased worktree has an unchanged clean
HEAD and root identity across two inspections. Immediately before `.venv`
removal, the preflight again verifies the unchanged Git registration, lease
absence or lifecycle proof, and absence of matching processes. Symlinks, files
with an allowlisted name, changed registrations, proof downgrades, and inspection
failures are refused. The same double inspection now makes a clean, inactive,
unleased checkout eligible for removal after its disposable caches and tool
environment are reclaimed. A lease or PID appearing during any recheck protects
the materialization.

Under continuing pressure, at most four proven-complete registered or clean,
inactive unleased materializations are removed per cleanup pass. The preflight
remeasures both canonical limits after
each pass and performs another pass only when scratch MiB or repository-volume
percentage has strictly decreased. Eight passes is the hard orchestration bound,
so one invocation can retire no more than 32 proven-complete materializations.
An unchanged measurement, cleanup refusal/error, inspection error, or exhausted
pass bound stops fail-closed without asking a model to choose more data. This
bounded convergence matters when the first safe pass reclaims substantial space
but the remaining 100 MB scratch or 90% volume threshold is still exceeded.
Immediately before `git worktree remove` (without `--force`), the preflight
requires either the same completed/expired lease or continuing lease absence,
an unlocked registration, no matching PID, no tracked or untracked changes, and
an exact local branch ref equal to the checkout's existing commit. An unleased
candidate additionally must retain the same clean HEAD and mutation-sensitive
root identity across two inspections surrounding a fresh Git, lease, and process
revalidation. Ignored content is refused unless it is only Python bytecode cache
material, `.gate-logs` release evidence, or `.gludd` application state. Before
every retirement, a collision-proof manifest is
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

The generated-scratch tier reuses only the test-run namespaces already owned by
the tracked CI cleanup command (`gludd-ci-shard-*`, `gludd-gate-unit-*`, and the
`gludd-test*` families). Automatic cleanup requires an entry to be unchanged for
at least six hours. Directories retain the existing process-safety checks. A
top-level regular file is eligible only when its suffix proves it is a
regenerable test artifact, including JSON, JavaScript, Markdown, log, status,
Python, XML, YAML, text, and coverage output. Unknown suffixes, symlinks, special
files, and names containing exact `lease`, `lock`, or `pid` tokens are always
preserved. Before unlinking a regular file, the preflight repeats the process
scan and verifies the same device, inode, mode, size, and modification time.
Unix-domain sockets additionally require `lsof` to prove no process holds the
socket both before and immediately before unlinking. Missing or ambiguous
inspection, an active owner, or any identity race refuses cleanup and fails the
pass closed. This permits stale `.json`, `.js`, `.md`, and abandoned socket nodes
to converge without deleting active sockets, ownership leases, or unproven
evidence.

The owned-download tier also admits the exact
`/tmp/gludd-playwright-browsers` root used by the presentation browser target.
It uses the existing node-download cache boundary rather than a broad `/tmp`
glob: every entry must be a real file or directory, the bounded tree snapshot
must remain unchanged, the newest entry must be at least six hours old, and two
process censuses must find neither an npm-like installer nor a command naming
the exact cache path. A running Chromium, WebKit, or Firefox executable names
that root in its command path and therefore protects the installation. Only an
idle, stale installation is removed, and the pinned presentation browser install
target regenerates it before later browser acceptance. This makes the disk
preflight zero-downtime for an in-flight browser gate while allowing a
multi-gigabyte, fully reproducible download cache to participate in bounded
pressure recovery. Like the shared uv cache, it remains visible in classification
but is not charged to the 100 MB ephemeral-test-scratch budget; its bytes still
contribute to the repository-volume percentage that triggers cleanup.

The other recovery tier targets only the exact resolved
`/tmp/gludd-uv-cache-public-v2` directory. It refuses symlinks, scans the system
process table twice before mutation, and skips pruning if any `uv` owner is
visible or inspection is ambiguous. When idle, it first calls uv's lock-aware
`cache prune`, performs a third system-wide owner check, and then calls uv's
lock-aware `cache clean` for remaining regenerable entries. Both operations use
the exact cache root and bounded lock/process timeouts; files are never deleted
directly. A concurrent uv user is protected by both the three checks and uv's
cache locking.

After every cleanup pass it re-runs both canonical measurements. A healthy
measurement returns zero. Residual pressure may trigger another pass only after
measurable progress; any cleanup safety error, inspection error, stagnation, or
pressure remaining at the pass bound returns nonzero. Skipping an active or
protected worktree is expected, and residual pressure still blocks the gate when
safe candidates cannot converge it automatically.

The recovery tiers are operationally independent. If Git worktree or integration
proof discovery fails, Gludd disables only worktree-cache retirement and evidence
relocation, records `worktree-discovery:inspection-failed`, and still runs the
generated-scratch, shared-uv-cache, and owned Terraform-provider reclaimers.
Their safely removed bytes remain in the result, but the discovery error keeps
the preflight fail-closed. This prevents one unavailable control-plane signal
from suppressing unrelated, independently proven cleanup while ensuring a gate
cannot report success until every required inspection is healthy. Unit fixtures
pin their integration point explicitly, so detached hosted checkouts do not
accidentally change the behavior under test.

## Zero-downtime operation and observability

The invoking tier removes only regenerable caches after exact registration and
two-stage process ownership checks. It filters only its observed synchronous
controller ancestry and never exempts descendants or other path users; it never
treats the active checkout itself as complete and never removes its environment
or durable state. The
active-workstream lease and retained `.venv` protect model-owned work that cannot
be inferred from operating-system PIDs, including a no-PID thinking interval.
An unleased worktree needs matching clean snapshots on both sides of refreshed
Git and ownership inspection, so PID absence alone never proves it disposable.
Git cleanliness,
integration ancestry/patch identity, and integration timestamps provide the
automatic completion signal. An exact-head commit receipt provides the immediate
cache-safe signal, and its unchanged age plus the configurable minimum grace
provides a conservative retirement signal without treating a missing PID as
agent inactivity. The independent PID check protects tests, linters, servers,
and shells whose command lines name a worktree. Together these checks make
cleanup a zero-downtime operation: ambiguous worktrees remain untouched while
completed environments can be regenerated by the next `uv` run.

Every phase emits a visible marker: initial inspection, pressure detection,
the bounded pass number, each candidate removal or uv-prune start/completion,
bounded refusal/skip details, omitted-detail counts, and every recheck. At most 20
skip details and 20 refusal details are printed per kind and pass, so hundreds of
equivalent stale-scratch protections cannot flood a gate log. This makes a
multi-pass recovery distinguishable from a stalled gate without a model polling
the filesystem. The implementation
keeps no cross-run convergence state; all authority comes from fresh Git,
namespaced workstream-lease, process, and disk observations on each pass. A
non-mutating preview is available with
`make disk-cleanup-preflight DISK_CLEANUP_PREFLIGHT_VALIDATE_ONLY=0`
`DISK_CLEANUP_PREFLIGHT_DRY_RUN=1`
`DISK_CLEANUP_RECEIPT_GRACE_SECONDS=1800`; it still returns nonzero while
pressure remains, so automation cannot mistake a preview for recovered capacity.

## Rollback

The change is operationally reversible because it deletes only regenerable
cache/test output, an unowned socket node, or a clean inactive checkout whose
exact branch and commit remain. A rollback of unleased materialization reclaim
simply restores the prior completion-lease-only eligibility rule; it does not
require a data migration, threshold change, or evidence deletion. Re-running the
owning test recreates allowlisted scratch
files, and rebinding recreates an inactive socket node; unknown evidence and
lease markers are never selected. Re-running `uv sync` recreates tool state;
pytest, mypy, and Ruff recreate their caches; and
`git worktree add <path> <branch>` recreates a removed materialization. To roll
back the automation itself, revert the feature commit. Archived `.gate-logs`
remain under `.git/gludd-release-evidence` and can be moved into a recreated
checkout using the fsynced rehydration manifest. The prior read-only checker remains in
`scripts/check_disk_usage.py`, and no data migration or service restart is
required. Reverting the Playwright allowlist stops future automatic browser-cache
removal; an already reclaimed installation is restored with
`make presentation-browser-install PRESENTATION_BROWSER_INSTALL_VALIDATE_ONLY=0`.
Do not weaken the fail-closed threshold recheck as a rollback shortcut.

## Long-lived user reports considered

- [uv issue 11432](https://github.com/astral-sh/uv/issues/11432) reports caches
  reaching 20–40 GB and requiring recurring manual cleanup. This supports an
  automatic threshold-triggered preflight and bounded progress-driven repetition
  instead of relying on model memory after one incomplete cleanup pass.
- [uv issue 11694](https://github.com/astral-sh/uv/issues/11694) reports cache
  pruning breaking a still-running `uvx` process. The Gludd preflight therefore
  requires two system-wide idle checks and delegates pruning to uv's locked
  operation instead of deleting shared-cache files itself.
- [Orca issue 10562](https://github.com/stablyai/orca/issues/10562) reports that
  deleting worktrees externally can strand agent and terminal process trees.
  Gludd consequently requires a stable completion lease, repeated PID and Git
  checks, and Git's non-forced removal instead of deleting a directory directly.
- [Claude Code issue 65645](https://github.com/anthropics/claude-code/issues/65645)
  documents unchanged workflow-agent worktrees and branches accumulating when
  teardown never runs. This supports a deterministic unleased-worktree fallback
  instead of assuming the creating agent always performs cleanup.
- [Claude Code issue 78350](https://github.com/anthropics/claude-code/issues/78350)
  reports a worktree pool reaping a checkout while its leasing session was still
  active. Gludd therefore treats every live lease, matching PID, dirty status,
  or ownership race as a hard preservation signal even under disk pressure.
- [CPython issue 111246](https://github.com/python/cpython/issues/111246)
  records the Unix-socket cleanup race in which a path can be replaced between
  observation and unlink. Its practitioner discussion specifically recommends
  checking the inode before removal; Gludd combines that identity check with two
  `lsof` owner inspections and refuses ambiguity.
- [pytest discussion 10325](https://github.com/pytest-dev/pytest/discussions/10325)
  documents long-lived operational demand to remove retained temporary test
  directories on automated hosts. Gludd therefore owns bounded cleanup for its
  explicit test namespaces and its own registered disposable worktree caches,
  while using registration, process, age, type, and identity proofs rather than
  deleting arbitrary `/tmp` content.
- [Playwright issue 15990](https://github.com/microsoft/playwright/issues/15990),
  opened in 2022, reports automatic browser garbage collection removing binaries
  that another project still needed. Gludd therefore requires two exact-path
  process checks and an unchanged tree instead of treating browser downloads as
  disposable merely because they are old.
- [Playwright issue 7249](https://github.com/microsoft/playwright/issues/7249),
  opened in 2021 and still receiving CI cache guidance years later, documents
  the recurring practice of caching Playwright browser binaries and reinstalling
  them on a miss. This supports classifying the exact Gludd browser root as
  regenerable while keeping installation explicit and pinned.
- [Terraform issue 38376](https://github.com/hashicorp/terraform/issues/38376)
  records sustained demand to reuse providers across modules without repeated
  downloads. Gludd retains that supported shared-cache design during normal use,
  but treats the cache as regenerable under measured volume pressure while
  preserving dependency locks and every state path.
- The official
  [Git worktree documentation](https://git-scm.com/docs/git-worktree) distinguishes
  worktree removal from pruning missing-worktree metadata and documents locks as
  protection from pruning. The preflight treats locks as absolute protection and
  verifies the durable branch and commit on both sides of materialization removal.

These reports are durable failure patterns rather than dependencies on a specific
tool version: unbounded generated data, cleanup racing active processes, and
workspace deletion racing agent ownership.
