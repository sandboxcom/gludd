# Pre-commit Typecheck Resource Boundary

Status: beta4 release contract

## Contract

The pre-commit hook delegates to the tracked `make _precommit-mypy` target. That
target runs the complete `general_ludd` mypy package check while selecting the
platform null device as its cache directory: `/dev/null` on Unix and `nul` on
Windows. Developer-invoked typechecking retains its normal incremental cache; only
the atomic commit transaction discards this short-lived cache.

A structural regression pins both the hook entry and the cross-platform Make
implementation. Removing either boundary must fail before another merge can enter
the resource-failure loop in which typecheck succeeds, creates an 87 MB cache, and
makes the immediately following generated-scratch guard abort the commit.

## Security, compatibility, and zero downtime

No diagnostic is suppressed and no mypy package, configuration, or checked source is
changed. The hook remains fail closed on every type error. Selecting the platform null
device is portable across the project's Unix and Windows release hosts. This affects
only local and CI commit validation, so it cannot interrupt a running Gludd service.
Promotion remains development to master after full-gate and exact-SHA CI evidence;
rollback is the previous hook entry and requires no data migration.

## Observability and resources

Mypy output remains visible in pre-commit. The existing disk hook continues to enforce
100 MB of generated Gludd scratch and the checkout-volume ceiling after typecheck.
The target does not start a daemon or background process. The release merge is the
behavioral example: complete typechecking and the following disk check must both pass.

The first attempted boundary used `--no-incremental`, passed its structural tests,
and still produced an 87 MB `.mypy_cache` during three real merge transactions.
That behavioral evidence rejected the attempt. Mypy's maintained command-line
documentation explicitly identifies `--cache-dir=/dev/null` (or `nul` on Windows)
as the way to disable cache use completely, so this contract uses that supported
interface rather than relying on an inferred side effect.

## Practitioner evidence

Mypy maintainers note in
[mypy #14618](https://github.com/python/mypy/issues/14618) that pre-commit is often a
poor fit for mypy because the lifecycle produces recurring integration issues. The
long-lived [pre-commit #1207](https://github.com/pre-commit/pre-commit/issues/1207)
discussion shows that cache location and ownership must be explicit in automated
runs rather than assumed from workstation defaults. The maintained
[mypy command-line documentation](https://mypy.readthedocs.io/en/stable/command_line.html#cmdoption-mypy-cache-dir)
provides the null-cache mechanism used here. Gludd preserves the full maintained
analysis while making the cache lifecycle explicit and bounded.
