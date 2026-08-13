# Make Target Contract

Agent work is driven through Make targets. Before any tool call, read `make help`,
select the narrowest appropriate target, and set every required variable explicitly.
The target's usage line and `config/make_target_contract.json` are the source of truth.

Every agent-facing target has a behavioral smoke example in the contract. Run that
behavioral smoke after changing a target or its variables; a successful parse is not
enough. Keep long-running work observable through the target's normal output.

Do not issue bare shell commands when a Make target exists. If a needed operation has
no target, add the target, document its variables and safe example, and add a behavioral
test before using it. `make check-make-target-contract` enforces this contract and is
part of the release gate.

For status claims, use `make ps` for auditable test/audit PIDs. `make ps-gludd` covers
only namespaced Gludd daemons; it is not evidence that delegated model work is idle.
Use `make active-work-status` for one JSON snapshot combining PIDs, gate state, git
hashes, and unchecked task IDs. Each process is tagged with a logical `task`, and
the `workstreams` map groups controller and child PIDs so a second terminal can
verify whether one target has spawned real parallel workers. The snapshot
intentionally reports `agent_pids: false`: model-agent turns are not OS processes;
only their spawned Make/pytest work can have auditable PIDs.

Release-candidate pushes use `make ci-push-committed-head`, whose
`ci-trigger-committed-head` step is the idempotent exact-SHA signal documented
in [CI_EXACT_SHA_SIGNAL.md](CI_EXACT_SHA_SIGNAL.md). It must return a confirmed
`GHA_RUN_URL`; a successful push by itself is not evidence that CI started.

## Scoped Python type checking

`make typecheck-scope FILES=...` resolves both `src/` modules and sibling
modules under `scripts/`. This keeps an explicit script check from failing at
import discovery before it can report the script's real typing defects. The
target remains strict: it does not ignore missing imports or discard errors.

This path handling follows a long-lived [mypy practitioner report about module
detection](https://github.com/python/mypy/issues/10428), where users described
standalone and configuration scripts becoming difficult to type-check and a
mypy collaborator suggested colon-separated `MYPYPATH` entries with explicit
package bases. Gludd applies that narrow path configuration through the tracked
Make target instead of per-file suppressions.

## Integration temporary paths

`make integration-health` gives pytest a short, process-unique `/tmp/gi-*`
`--basetemp`. The name includes a stable hash of the project namespace, so
parallel checkouts cannot clear each other's test directories, and the process
suffix keeps concurrent runs isolated. The target removes only its own directory
on exit. Keep this prefix short: pytest and xdist append worker and sanitized test
names before an AF_UNIX socket filename is added.

This follows long-lived upstream reports rather than weakening socket tests.
CPython users documented that AF_UNIX tests fail once a temporary root pushes the
socket name beyond the platform limit ([CPython #93852](https://github.com/python/cpython/issues/93852)).
Pytest documents that its default layout adds `pytest-of-{user}/pytest-{num}` and
that `--basetemp` directly controls the root ([pytest temporary-directory guide](https://docs.pytest.org/en/stable/how-to/tmp_path.html#temporary-directory-location-and-retention)).
A pytest user report also shows xdist adds a `popen-gwN` layer under the chosen
base ([pytest #10679](https://github.com/pytest-dev/pytest/issues/10679)).

## Transactional development merge-forward

Use `make development-merge-forward SOURCE=<ref> MODE=content|ancestry-only
APPLY=0|1` to reconcile beta/release work into `development`. The target verifies
the source ref in every mode and defaults to a non-mutating dry-run when `APPLY`
is omitted. `APPLY=1` requires the current branch to be `development` and the
worktree to be clean.

`MODE=content` performs a `--no-ff --no-commit` merge. Overlapping content keeps
the current development version; unresolved structural conflicts abort the merge.
The target also aborts if test collection fails, and creates the merge commit only
after collection succeeds. `MODE=ancestry-only` is a deliberately exceptional
choice: it emits a warning and uses Git's `ours` strategy to retain the current
tree while recording the source as a parent. Its commit message records the mode,
source, and resolved SHA. Ancestry-only reconciliation from `master` is forbidden.

Long-lived Git Q&A reports explain the operational risk this target addresses:

- [Merging back a cherry-picked commit can conflict after nearby follow-up edits](https://stackoverflow.com/questions/45690696/git-conflict-upon-merging-back-a-cherry-picked-commit): a cherry-pick creates a different commit identity even when its patch initially matches.
- [Cherry-picks do not provide merge tracking](https://stackoverflow.com/questions/3757075/merge-tracking-for-git-cherry-picking): a merge commit, unlike a copied patch, records where histories converged.

These reports support preserving auditable ancestry instead of repeatedly copying
already-reconciled patches. They do not make `ancestry-only` a routine choice: use
it only when development's complete tree is intentionally authoritative.
