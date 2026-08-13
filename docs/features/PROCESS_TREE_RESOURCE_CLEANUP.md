# Identity-Checked Project Process-Tree Cleanup

## Problem

The repository had two unsafe extremes for active work: `kill-stray` uses
global process-name patterns, while `kill-project-pid` accepts only a narrow
set of orphan commands. During an obsolete hour-long full suite, generated
artifacts pushed disk usage to the hard threshold, but neither path could stop
that one worktree-owned tree without risking another project.

## Contract

`terminate-project-process-tree` requires both an exact root PID and a
non-root absolute project namespace. Validation mode does not inspect the
process table. Dry-run is the default and lists only matching descendants.
Apply mode snapshots process identity once, rejects a missing or mismatched
root, and signals matching descendants before their root. It never uses a
global `pkill` pattern.

## Practitioner evidence

Pytest-timeout issue
[#159](https://github.com/pytest-dev/pytest-timeout/issues/159) documents pytest
termination leaving child processes orphaned. A long-lived Stack Overflow
[process cleanup report](https://stackoverflow.com/questions/52476265/killing-shell-true-process-results-in-resourcewarning-subprocess-is-still-runni)
shows that signaling a shell alone does not complete child lifecycle cleanup.
Those reports support tree-aware cleanup with explicit identity checks.

## ZDD, security, and resources

This is an emergency local control-plane operation and does not touch deployed
services. It fails closed before signaling when PID identity or project
namespace differs. The default is read-only, the behavior example is
validation-only, and the target creates no helper script or persistent daemon.
Using an exact namespace prevents one checkout from killing another project's
workers.

## Verification

Unit tests pin no-read validation, namespace-mismatch rejection, and
child-before-root signaling. Make target validation pins every variable and the
safe example. Resource verification records process census, disk usage, and a
clean worktree after bounded artifact cleanup.
