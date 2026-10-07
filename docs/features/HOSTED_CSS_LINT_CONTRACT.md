# Hosted CSS Lint Contract

## Purpose

Hosted run 37684089683 invoked an unversioned CSS linter through `npx` and
embedded policy in the workflow. Identical source revisions could therefore
resolve different tools. The hosted phase now delegates to the public
`lint-css` Make target, an exact Node lock, and a tracked ESLint CSS policy.

## Tool decision

The exact development dependencies are ESLint 10.11.0 and `@eslint/css`
2.0.0. The repository contains neither Stylelint nor Biome. Current Stylelint
pulled the unpatched `braces` advisory into the Node graph. Biome provides
empty-block and duplicate-property checks, but its maintainers describe CSS
parser validation gaps in [discussion 2422](https://github.com/biomejs/biome/discussions/2422),
so it could not prove the required invalid-hex failure.

The official [`@eslint/css` package](https://github.com/eslint/css) supplies
[`no-empty-blocks`](https://github.com/eslint/css/blob/main/docs/rules/no-empty-blocks.md)
and [`no-invalid-properties`](https://github.com/eslint/css/blob/main/docs/rules/no-invalid-properties.md).
Its parser runs with tolerant mode disabled, and the invalid-property rule uses
CSS syntax validation while allowing custom-variable values. Inline ESLint
configuration is disabled.

The upstream package does not yet have a duplicate-property rule. That gap is
tracked in [`@eslint/css` issue 495](https://github.com/eslint/css/issues/495).
The tracked config therefore supplies one narrow ESLint rule following the
official [custom-rule API](https://eslint.org/docs/latest/extend/custom-rule-tutorial).
Equal or separated duplicate declarations fail. Consecutive declarations with
different values remain valid browser fallbacks, including the existing
`100vh` then `100svh` pattern.

## Behavioral contract

- Callers explicitly pass `CSS_FILES` and `ESLINT_CSS_CONFIG` to
  `make lint-css`.
- The target runs only the lock-installed ESLint executable and explicit
  config. It never downloads an unpinned package.
- Empty blocks, invalid hexadecimal colors, equal duplicates, and separated
  duplicates fail. Valid colors and consecutive different-value fallbacks
  pass.
- Missing arguments or configuration fail closed. A missing executable is
  restored only through the locked, namespaced `node-deps-sync` target.
- The tracked ignore list excludes `.venv` and locked Node dependency content,
  so hosted repository globs cannot lint installed third-party stylesheets.
- `--no-error-on-unmatched-pattern` lets an explicit hosted language glob be
  empty without weakening checks for files that exist.

The documented behavioral example is:

```text
make lint-css CSS_FILES=docs/presentation/deck/presentation.css ESLINT_CSS_CONFIG=config/eslint-css.config.mjs
```

## Hosted dependency audit

The workflow performs the locked Node sync, then immediately calls the public
`node-deps-audit` target with an explicit public registry, namespaced cache,
disabled update notifier, and `NODE_DEPS_AUDIT_LEVEL=low`. Failure stops the
job before CSS lint. The exact workflow ordering and variables have a
structural regression test.

Moving Markdown lint to exact-pinned Rumdl also removed markdownlint-cli2's
path to the same `braces` advisory. The final `.opencode` dependency graph
reports zero vulnerabilities at the low threshold. There is no advisory
suppression, npm override, lowered threshold, or unpinned `npx` fallback.
[`npm ci`](https://docs.npmjs.com/cli/v11/commands/npm-ci/) documents the
lock-preserving install behavior. A long-running
[Stack Overflow practitioner discussion](https://stackoverflow.com/questions/52499617/what-is-the-difference-between-npm-install-and-npm-ci/59386596)
records why teams use that behavior for reproducible CI.

## Zero-downtime and resources

This contract changes validation only. Each invocation starts one bounded Node
process and no daemon, database migration, or serving process. The npm cache is
project-namespaced, so concurrent Gludd checkouts do not share mutable runtime
state. Rollout and rollback are independent of application workers and do not
interrupt requests.

## Verification

`tests/unit/test_css_lint_target.py` exercises all three required checks, the
browser-fallback exception, exact package and lock entries, public Make
contract, hosted delegation, and sync-before-audit ordering. Promotion also
requires the real CSS glob, Make-contract validation, YAML and Markdown lint,
focused tests, collection without errors, and a low-threshold Node audit.
