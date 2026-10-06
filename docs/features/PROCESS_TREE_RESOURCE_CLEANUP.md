# Identity-Checked Project Process-Tree Cleanup

## Problem

The repository had two unsafe extremes for active work: `kill-stray` uses
global process-name patterns, while `kill-project-pid` accepts only a narrow
set of orphan commands. During an obsolete hour-long full suite, generated
artifacts pushed disk usage to the hard threshold, but neither path could stop
that one worktree-owned tree without risking another project.

## Contract

`terminate-project-process-tree` requires both an exact root PID and a
non-root absolute project namespace. Validation, dry-run, and apply all take
the same bounded process snapshot and reject a missing or namespace-mismatched
root. Validation performs no signal. Dry-run lists only admitted descendants,
and apply signals that same child-before-parent selection. It never uses a
global `pkill` pattern.

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

## ZDD, security, and resources

This is an emergency local control-plane operation and does not touch deployed
services. It fails closed before signaling when PID identity or project
namespace differs. The default is read-only, the behavior example is
validation-only, and the target creates no helper script or persistent daemon.
Using an exact namespace prevents one checkout from killing another project's
workers.

## Verification

Unit tests pin live-process validation, missing-process and namespace-mismatch
rejection, validation/apply admission parity, and child-before-root signaling.
Make target validation pins every variable and the safe example. Resource
verification records process census, disk usage, and a clean worktree after
bounded artifact cleanup.
