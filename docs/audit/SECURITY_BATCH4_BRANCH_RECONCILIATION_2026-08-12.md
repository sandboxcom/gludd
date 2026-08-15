# Security batch-4 branch reconciliation

Date: 2026-08-12

## Scope and decision

`feature/security-batch4-rooted` contains two security commits whose patches
are already present on the beta4 development lineage and one later tooling
commit:

| Source commit | Reconciliation |
|---|---|
| `e285abaf925df0361dd62c1e5799ca6662eea38b` | Patch-equivalent; retain the current gateway implementation and tests. |
| `9fc2c36cc58c3101c9b48c78f3a364bde88f8543` | Patch-equivalent; retain the current connector implementation and tests. |
| `cbefcf9410e835c2310c6694bb81211fbfcb6fa1` | Do not import its global `wait-pytest-idle` Make target. |

The retained behavior covers fail-closed budget rejection and fallback
circuit handling in the model gateway, plus connector import allowlisting,
family validation, fan-out limits, and non-finite numeric normalization. The
source branch is recorded as merge ancestry so these completed commits are not
reconsidered as unmerged work.

## Why the wait target is superseded

The omitted target scans the host-wide process table for `pytest`, Molecule,
or `make gate`, then sleeps in 15-second intervals for as long as 30 minutes.
That does not distinguish this checkout from another Gludd project, provides no
ownership proof for the process it observes, and can block behind unrelated
work. It conflicts with the repository's no-wait and multi-project isolation
contracts.

The current lineage already addresses the underlying collision risk with
per-invocation `--basetemp` directories in focused test targets and the
collection lock used by `test-count`. `active-work-status` provides observable
process ownership, while `reap-orphan-pytest APPLY=0` supplies a non-destructive
orphan audit. These mechanisms isolate or identify work instead of waiting on
every matching host process.

Rollback is ancestry-only: reverting this reconciliation merge removes this
decision record without rolling back the already-landed security patches. No
runtime, configuration, schema, API, or deployment compatibility changes are
introduced by the reconciliation itself.

## Focused proof

The following checks are the acceptance evidence for retaining the current
implementations:

```console
make git-patch-equivalence PATCH_UPSTREAM=HEAD \
  PATCH_HEAD=feature/security-batch4-rooted PATCH_LIMIT=10
# patch-equivalent=2 unique=1

make test-files GLUDD_XDIST_WORKERS=0 \
  TESTFILES='tests/models/test_gateway.py tests/connectors/test_security_batch4.py'
# 18 passed
```

The sole unique patch is the intentionally omitted global wait target described
above. Repository collection must remain error-free before the merge commit.
