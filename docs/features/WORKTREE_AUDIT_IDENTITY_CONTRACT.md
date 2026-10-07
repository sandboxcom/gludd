# Worktree Audit Identity Contract

## Problem

Git can report one worktree through multiple lexical paths. On macOS,
**/tmp/gludd-worktrees/...** commonly resolves to
**/private/tmp/gludd-worktrees/...**; a user-created symlink can create another
alias. The health audit previously printed the path received from
**git worktree list --porcelain** and then used it as a subprocess working
directory without establishing its filesystem identity or confinement.
Traversal-shaped input and a symlink escaping the managed root therefore
crossed the audit boundary unchecked.

The live integration assertion also assumed that no secondary worktrees
existed. That assumption is false precisely when isolated development is
active, so a healthy multi-worktree environment could fail the test despite
the audit accurately reporting its state.

## Contract

Every registered path is checked before a path-scoped Git operation:

1. The value must be non-empty, absolute, free of control characters, and
   contain no parent traversal segment.
2. The path is resolved to one canonical filesystem identity. The main checkout
   is excluded by canonical identity rather than lexical spelling.
3. A secondary worktree must remain below the canonical
   **/tmp/gludd-worktrees** root after symlinks are resolved. A second lexical
   path resolving to an already-seen identity is rejected as a duplicate.
4. A rejected row is observable as
   **ACTIVE-WORKTREE identity=rejected reason=code**, fails the health audit,
   and never reaches age, merge, or remote checks.
5. A valid row is observable as
   **ACTIVE-WORKTREE path=canonical-path identity=canonical**.
   The **no active worktrees** state is emitted only when Git reports no
   validated or rejected secondary entries.
6. A failed Git inventory is observably distinct from an empty inventory. It
   emits **WORKTREE HEALTH: INCONCLUSIVE**, exits nonzero with status 2, and
   never claims that no active worktrees exist.

Tests accept either a populated active inventory or the explicit empty state.
They validate the shape and canonical identity of populated rows instead of
requiring an idle developer machine.

## Pytest linked-worktree confinement

A release-integrity gate exposed a second identity boundary. The gate was
invoked from a linked worktree, but a nested release-check process resolved the
canonical main checkout as its repository. During that interval it changed 15
tracked release files and created two generated release artifacts in main.
Later collection intervals created `tests/unit/test_replay_codex_file_changes.py`
and `scripts/replay_codex_file_changes.py`, and registered the sibling branch
and worktree `agent/codex-release-integrity`. Those generated files were removed
before a guarded replay, so no present tracked module can be attributed as the
import trigger. The surviving filesystem and Git evidence nevertheless fixes
the failure class: nested code-generation was free to select a different
checkout through its working directory, arguments, inherited project-root
environment, or its own repository discovery.

Pytest's existing current-directory restoration fixture could repair a test
worker's own directory only after a test returned. It neither denied a write
while the test was running nor constrained a child process. Treating pytest's
reported `rootdir` as a filesystem jail, and guarding only the parent's current
directory, was therefore the root cause.

When `tests/conftest.py` is loaded from a linked worktree, it now derives the
canonical checkout from Git's `.git/worktrees/<name>` administrative path and
installs one CPython audit hook. The hook resolves symlinks and denies, before
the operation occurs:

1. write-capable `open` calls and filesystem mutation events below canonical
   main;
2. `chdir` into canonical main, which would redirect later relative writes;
3. a nested process whose working directory, argument vector, or effective
   environment references canonical main;
4. branch- or worktree-creating Git and Make commands, even when their explicit
   paths do not mention main;
5. active child processes launched while pytest imports test modules, except
   for an exact two-token `executable --version` availability probe.

The effective-environment check accepts the general `Mapping` protocol, so it
covers both an explicit dictionary and Python's inherited `os._Environ`. The
collection phase is tracked by pytest's collection hook instead of inferred
from `PYTEST_CURRENT_TEST`, which is intentionally absent while modules are
imported. Canonical path checks run before the narrow version-probe exception.

Reads from main remain permitted, and writes inside the invoking linked
worktree remain permitted. A test run started in canonical main installs no
cross-checkout hook. This makes the active worktree the mutation boundary while
preserving structural tests that need read-only visibility into main metadata.

The serial gate has one additional owned identity. Its runner creates
`/tmp/gludd-<four-hex-digest>-<eight-random-characters>/`, exports that exact directory as
`TMPDIR`, and passes `TMPDIR/pytest` as pytest's explicit base directory. A Git
mutation is allowed there only when the canonical repository path is below that
exact `pytest` child, the exported root has the runner's complete name shape,
and the root is an immediate child of a canonical system temporary directory.
A broad `gludd-` prefix, another temporary directory, an alias, or a sibling of
the explicit pytest base remains insufficient.

## Practitioner evidence

The long-running Stack Overflow Q&A
[How can I have multiple working directories with Git?](https://stackoverflow.com/questions/6270193/how-can-i-have-multiple-working-directories-with-git)
records more than a decade of worktree-path practice and Git's later removal of
incorrect path munging. The durable lesson is that an administrative worktree
path is an identity claim, not merely a display string.

The multi-year report
[How to use Git worktree on a host-guest file system](https://stackoverflow.com/questions/55991131/how-to-use-git-worktree-on-host-guest-file-system-in-virtual-machine)
shows absolute worktree metadata becoming invalid when the same checkout is
observed through a different host path, with symlinks suggested as an aliasing
workaround. It supports resolving aliases before comparing or operating on
worktree paths.

Pytest's long-lived user report
[`--ignore` option is not relative to `rootdir`?](https://github.com/pytest-dev/pytest/issues/6399)
was opened in 2019 and still received practitioner follow-up in 2022. It records
the same dangerous assumption: pytest's `rootdir` does not rebase every
path-sensitive operation, while the process working directory still controls
some behavior. The
[official rootdir documentation](https://docs.pytest.org/en/stable/reference/customize.html#initialization-determining-rootdir-and-configfile)
likewise limits `rootdir` to node IDs and plugin state and explicitly warns
against treating it as an import-path control. The
[CPython audit-event contract](https://docs.python.org/3/library/audit_events.html)
exposes the child process's `cwd` on `subprocess.Popen`, while the
[`sys.addaudithook` contract](https://docs.python.org/3/library/sys.html#sys.addaudithook)
allows a hook to raise and abort the operation.

The long-lived pytest practitioner report
[data loss with mistyped `--basetemp`](https://github.com/pytest-dev/pytest/issues/7119)
was opened in 2020 after an empty option value made pytest select the current
directory and erase a Git repository, including work in progress. Pytest's
[temporary-directory documentation](https://github.com/pytest-dev/pytest/blob/main/doc/en/how-to/tmp_path.rst)
now warns that an explicit base directory is cleared and must be dedicated to
that run. That history is why the gate exception follows the complete,
runner-owned `TMPDIR/pytest` identity instead of treating a convenient name
prefix as proof of ownership.

## Security and failure behavior

Validation is fail-closed for structurally unsafe identities. Traversal,
relative paths, control characters, symlink escapes, and duplicate canonical
identities produce stable reason codes and a failing terminal state. The raw
rejected value is retained only for audit evidence; it is never supplied as a
working directory. Branch and commit arguments retain their existing
list-form subprocess boundary.

Inventory acquisition is also fail-closed at the gate boundary. An unavailable
`git worktree list --porcelain` result cannot prove an empty active environment,
so it produces the dedicated inconclusive terminal state instead of a false
pass. Operators may retry after Git recovers; the audit does not cache or
invent inventory state.

The separately recorded **test_create_worktree_validates_path** red-team node
guards creation-time GitAutomation arguments and was already green. It is a
different boundary and remains unchanged; this contract covers inventory-time
audit identities.

## Resources and ZDD

Canonicalization is an in-process filesystem operation performed once per
porcelain entry. It adds no worker, daemon, temporary helper, network request,
or retry loop. Valid entries retain the existing bounded, sequential Git and
remote checks. Invalid entries skip those checks, reducing resource use.

The audit is control-plane only and does not restart or mutate an application
service, so deployment downtime remains zero. During a rolling deployment, an
old and new auditor can run independently: each reads Git state and emits its
own terminal result without shared mutable state.

The pytest confinement hook has the same ZDD properties. It creates no process,
daemon, watcher, temporary directory, network request, or persisted state. It
runs synchronously in each test interpreter and refuses an escape before any
canonical-main mutation, so there is no cleanup window and no application
restart. It also prevents test collection from starting an active code-editing
child or creating a sibling worktree; a harmless version probe remains
available for import-time skip markers. Old and new test workers can overlap
safely during rollout because each hook owns only its interpreter and enforces
the same immutable pair of resolved checkout identities.

## Rollback

Rollback is a normal code revert of the health script, focused tests, task
evidence, and this document. There is no schema, persisted format, migration,
configuration, or background process to unwind. Operators should retain the
last audit output during rollback because removing canonical identity markers
temporarily restores the earlier ambiguous evidence format.

## Verification

Focused regressions cover traversal rejection, an escaping symlink, a safe
symlink alias, fail-closed command suppression, inventory failure truth, and
active-environment output.
The complete audit test module, production coverage floors, Ruff, strict mypy,
docstring lint, Markdown/spec lint, and task-ledger validators form the bounded
acceptance set. Collection and commit are deliberately separate authorization
steps.

The pytest regressions additionally cover linked-worktree main discovery,
write-open and atomic-rename denial, process `cwd`/argv/explicit and inherited
environment denial, symlink-alias `chdir` denial, collection-phase process
denial with the exact version-probe exception, and branch-backed worktree
creation denial. They also pin the serial gate's complete `TMPDIR/pytest`
identity and deny a sibling directory or mismatched owner even when its name
looks gate-generated. The 33 focused regressions pass. The bounded guard and
fixture coverage set reports 111 passed, one intentional skip, 89% aggregate
branch coverage, and no measured file below 75%.
