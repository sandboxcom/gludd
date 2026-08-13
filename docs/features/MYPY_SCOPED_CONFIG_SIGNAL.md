# Scoped Mypy Configuration Signal

## Problem

`typecheck-scope` checks an explicit list of files but loads the repository's
full mypy configuration. With `warn_unused_configs` enabled globally, every
per-module override unrelated to that small list is reported as an unused
section even when the selected files have no type errors. The repeated note
obscures the result and looks like project-wide configuration drift.

## Contract

The full mypy configuration keeps `warn_unused_configs = true`. Only the
explicit-file target passes mypy's
`--no-warn-unused-configs` command-line mode. Strict checks, explicit package
bases, non-incremental analysis, error exit codes, and all applicable module
settings remain active. The target has no success-forcing shell path.

## Practitioner evidence

Mypy issue [#11401](https://github.com/python/mypy/issues/11401) is a long-lived
user report documenting that `warn_unused_configs` is global and interacts
poorly with per-module configuration. Users specifically describe phased or
partial type-checking workflows being blocked by that global behavior. The
scoped target therefore narrows only this configuration-liveness diagnostic;
the full target remains authoritative for the whole configuration.

## ZDD, security, and resources

This local quality-target change causes no application downtime and launches no
services. It does not add an inline suppression or disable any type error code.
The command stays fail-closed on mypy's exit status and uses the existing
project interpreter and bounded explicit file list.

## Verification

A structural contract pins the retained non-incremental error-checking command,
the scoped diagnostic mode, and the absence of `|| true`. The registered
behavioral example runs against a tracked script and must finish with no warning,
error, or informational configuration note.
