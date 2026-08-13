# Pre-commit Typecheck Resource Boundary

Status: beta4 release contract

## Contract

The commit hook runs the complete `general_ludd` mypy package check with
`--no-incremental`. The result is semantically identical to an incremental check,
but the short-lived hook does not leave an 87 MB repository cache immediately
before the 100 MB generated-scratch guard. Developer-invoked typechecking retains
its normal incremental cache; only the atomic commit transaction is non-incremental.

The hook entry is pinned by a structural regression. Removing the option must fail
before another merge can enter the resource-failure loop in which typecheck succeeds,
creates enough cache to make the following disk check fail, and aborts the commit.

## Security, compatibility, and zero downtime

No diagnostic is suppressed and no mypy package, configuration, or checked source is
changed. The hook remains fail closed on every type error. The change affects only
local/CI commit validation and cannot interrupt a running Gludd service. Promotion
remains development to master after full-gate and exact-SHA CI evidence; rollback is
the previous hook entry and requires no data migration.

## Observability and resources

Mypy output remains visible in pre-commit. The existing disk hook continues to enforce
100 MB of generated Gludd scratch and the checkout-volume ceiling after typecheck.
Non-incremental mode trades a disposable cache for bounded disk use; it does not start
a daemon or background process. The release merge itself is the behavioral example:
typecheck must pass and the immediately following disk check must also pass.

## Practitioner evidence

Mypy maintainers note in
[mypy #14618](https://github.com/python/mypy/issues/14618) that pre-commit is often a
poor fit for mypy because the lifecycle produces recurring integration issues. The
long-lived [pre-commit #1207](https://github.com/pre-commit/pre-commit/issues/1207)
discussion shows that cache location and ownership must be explicit in automated
runs rather than assumed from workstation defaults. Gludd resolves the narrower
release-hook problem by avoiding a disposable incremental cache while preserving the
full maintained mypy analysis.
