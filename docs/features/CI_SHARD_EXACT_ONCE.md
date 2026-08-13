# CI Shard Exact-Once Contract

## Problem

The GitHub Actions matrix drifted from the tracked local shard selector. Two
nested unit directories were absent from every workflow lane, while the
resource-sensitive Node plugin runtime suite was no longer declared as an
isolated fresh-process lane. A green matrix could therefore omit tests or run a
suite with the wrong process/coverage lifetime.

## Contract

- Every `tests/unit/**/test_*.py` file has exactly one workflow owner.
- `tests/unit/auth/`, `tests/unit/sts/`, and
  `tests/unit/test_e2e_test_generation/` belong to the `other` shard.
- The named shard matrix runs once on canonical Python 3.11; the blocking gate
  separately exercises both supported Python versions.
- `test_all_plugins_runtime.py` is excluded from the long-lived unit shard
  and executed once in a fresh process without coverage instrumentation.
- Workflow matrix metadata and `scripts/ci_named_shard_files.py` remain
  mechanically identical.

## Practitioner evidence

GitHub Actions users report that changing or expanding matrix checks requires
constant coordination because required checks do not support wildcard
registration, making explicit, auditable matrix ownership important:
<https://github.com/orgs/community/discussions/12377>.

pytest-xdist issue 868 records process-lifecycle differences around distributed
fail-fast execution; maintainers note that setup occurs per process. That
supports retaining a deliberate fresh-process lane for the plugin runtime:
<https://github.com/pytest-dev/pytest-xdist/issues/868>.

## ZDD, security, and resources

This changes only CI routing and causes no service downtime. Exact-once
ownership prevents security suites from silently disappearing and avoids
duplicate resource use. The isolated lane is bounded to one file, one process,
and five minutes; the existing shard worker limits remain unchanged.

## Verification

The named-shard parity test, every-unit-file exact-once security contract, and
isolated-lane contract must all pass. Workflow YAML must parse, action pins and
least-privilege checks must remain green, and collection must have zero errors.
