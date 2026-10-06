# Identity-Checked Project Process-Tree Cleanup

## Problem

The repository had two unsafe extremes for active work: `kill-stray` uses
global process-name patterns, while `kill-project-pid` accepts only a narrow
set of orphan commands. During an obsolete hour-long full suite, generated
artifacts pushed disk usage to the hard threshold, but neither path could stop
that one worktree-owned tree without risking another project.

## Contract

`terminate-project-process-tree` requires both an exact root PID and a
non-root absolute project namespace. Validation, dry-run, and apply all use the
same bounded process identity snapshot and the same admission function. A root
whose command contains the exact namespace retains the original admission
path. A plain `make` root may instead use its live cwd or a namespaced
descendant as ownership proof, because `make` commands commonly omit their
launch directory. No other root command may borrow that descendant proof, and
each selected descendant must independently match by exact command path or cwd.
Validation performs no signal. Dry-run lists only that admitted
child-before-parent selection, and apply signals it. It never uses a global
`pkill` pattern.

Cwd capture is limited to the requested tree and capped at 256 PIDs. Linux uses
`/proc/<pid>/cwd`; other POSIX hosts use one bounded `lsof` query. Missing,
inaccessible, relative, or malformed cwd data supplies no proof. Namespace
matching resolves `/tmp` aliases while rejecting sibling prefixes such as
`gludd-alpha-other`.

## Practitioner evidence

Pytest-timeout issue
[#159](https://github.com/pytest-dev/pytest-timeout/issues/159) documents pytest
termination leaving child processes orphaned. A long-lived Stack Overflow
[process cleanup report](https://stackoverflow.com/questions/52476265/killing-shell-true-process-results-in-resourcewarning-subprocess-is-still-runni)
shows that signaling a shell alone does not complete child lifecycle cleanup.
Pytest-timeout
[#134](https://github.com/pytest-dev/pytest-timeout/issues/134) separately
documents timeout paths that skip teardown and leave a subprocess running.
Those reports support tree-aware cleanup with explicit identity checks and
reject treating a timeout status or parent exit as proof of cleanup.

Two long-lived Unix & Linux practitioner threads explain the identity gap that
the cwd fallback closes. The 2013
[running-process cwd thread](https://unix.stackexchange.com/questions/94357/find-out-current-working-directory-of-a-running-process/94358)
documents external cwd discovery through `pwdx`, `lsof`, or `/proc`, while the
2018
[`ps` cwd thread](https://unix.stackexchange.com/questions/458977/does-ps-provide-the-working-directory-of-each-process)
documents that `ps` does not portably expose cwd. A 2010 Server Fault
[shell-script identity report](https://serverfault.com/questions/107126/linux-find-out-the-current-working-directory-of-a-process)
shows the practical failure mode: command text such as a relative script name
does not identify which directory owns it. Together they support combining a
bounded process table with live cwd evidence rather than broadening command
substring matching.

## ZDD, security, and resources

This is an emergency local control-plane operation and does not touch deployed
services. It fails closed before signaling when the live PID identity, narrow
`make` launcher rule, or project namespace differs. A reused PID is evaluated
only as its current snapshot identity; an unrelated replacement cannot inherit
admission from an old PID file. The default is read-only, the behavior example
is validation-only, and the target creates no helper script or persistent
daemon. Exact path boundaries and per-descendant proof prevent one checkout
from killing a sibling checkout's workers.

The rollout is ZDD because validation and dry-run remain signal-free, and apply
changes only the explicitly admitted local process tree; no listener, remote,
tag, artifact, or deployment is restarted. Rollback is a normal revert of the
isolated helper, regression, and documentation commit. It restores the prior
command-only policy without a schema migration or compensating service action.

## Verification

Unit tests pin live-process validation, cwd and descendant proof for a plain
`make` root, unrelated-parent and namespace-prefix rejection,
validation/apply admission parity, PID-reuse-safe current identity, and
child-before-root signaling. Make target validation pins every variable and
the safe example. Resource verification records process census, disk usage,
and a clean worktree after bounded artifact cleanup.
