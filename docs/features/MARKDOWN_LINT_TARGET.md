# Repository Markdown Lint Target

## Purpose

Gludd now exposes one repository-owned Markdown lint command backed by the
maintained markdownlint-cli2 package. The target replaces an advertised but
missing Make rule and prevents contributors from depending on a global binary,
an unpinned npx download, or an untracked helper script.

## Behavioral contract

- Callers run make lint-markdown and set MARKDOWN_FILES plus
  MARKDOWNLINT_CONFIG explicitly.
- The target uses only the exact binary installed under
  .opencode/node_modules from the tracked package lock.
- Missing file arguments or missing configuration fail with exit code 2 and
  an actionable message.
- A missing locked binary triggers a locked dependency sync through the
  repository's node-deps-sync target with the namespaced registry and cache
  contract; if that sync fails, the target still exits 2 with a visible cause.
- The checked-in configuration disables inline Markdown suppressions.
- The initial rule set enforces heading progression and ATX form, trailing
  whitespace, hard tabs, heading spacing, and a final newline.
- Found files, linted-file count, and the final issue count remain visible.
- The target performs no write or auto-fix operation.

markdownlint-cli2 0.23.2 is pinned exactly in the existing OpenCode Node package
and lock. Its upstream documentation recommends local development dependency
installation and supports explicit configuration plus file globs:

https://www.npmjs.com/package/markdownlint-cli2

## Practitioner evidence

markdownlint-cli2 issue #130 records a user who installed a custom rule but
received a zero-error result because the configuration was not being loaded as
expected. The report demonstrates why Gludd passes one explicit tracked config
path and behavior-tests the visible file and issue counts:

https://github.com/DavidAnson/markdownlint-cli2/issues/130

markdownlint issue #45 remained active across several years and documents a
valid ordered list being reported under an inferred style mismatch. That
experience supports a small explicit initial rule set instead of enabling every
style opinion against a large legacy documentation tree:

https://github.com/DavidAnson/markdownlint/issues/45

## Security and compatibility

The target never executes repository Markdown, downloads plugins, or enables
custom rules. Inline configuration is disabled so a document cannot waive a
finding with an HTML comment. The local binary and transitive packages are
resolved by package-lock integrity hashes through the existing namespaced npm
cache and registry contract.

Existing Make callers are unaffected because the rule was previously missing.
The help entry and make-target contract now state both variables. More rules can
be enabled additively after current documents are corrected; rule expansion
must not introduce a hidden baseline or suppression.

## Zero-downtime delivery

This is development-only tooling with no runtime process, database, protocol,
or deployment mutation. It can roll out before application workers and roll
back independently. During a mixed-version development window, older checkouts
lack the target while newer checkouts sync locked dependencies automatically
and fail closed if that sync fails; production service traffic remains
uninterrupted.

## Resource and observability contract

One short-lived Node process handles only the explicit files. There is no
daemon, cache outside the existing project-namespaced npm cache, background
worker, or unbounded repository walk. Standard output identifies found files,
the number linted, and the terminal issue total.

## Verification

tests/unit/test_markdown_lint_target.py behavior-tests a successful lint, the
missing-file failure, the exact package pin, and the make-target contract.
The documented behavioral example lints the XMSS safety specification with
zero issues. The Make contract, help inventory, duplicate-target guard, Node
dependency audit, static checks, collection gate, and full release gate remain
required before promotion.
