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

Tests accept either a populated active inventory or the explicit empty state.
They validate the shape and canonical identity of populated rows instead of
requiring an idle developer machine.

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

## Security and failure behavior

Validation is fail-closed for structurally unsafe identities. Traversal,
relative paths, control characters, symlink escapes, and duplicate canonical
identities produce stable reason codes and a failing terminal state. The raw
rejected value is retained only for audit evidence; it is never supplied as a
working directory. Branch and commit arguments retain their existing
list-form subprocess boundary.

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

## Rollback

Rollback is a normal code revert of the health script, focused tests, task
evidence, and this document. There is no schema, persisted format, migration,
configuration, or background process to unwind. Operators should retain the
last audit output during rollback because removing canonical identity markers
temporarily restores the earlier ambiguous evidence format.

## Verification

Focused regressions cover traversal rejection, an escaping symlink, a safe
symlink alias, fail-closed command suppression, and active-environment output.
The complete audit test module, production coverage floors, Ruff, strict mypy,
docstring lint, Markdown/spec lint, and task-ledger validators form the bounded
acceptance set. Collection and commit are deliberately separate authorization
steps.
