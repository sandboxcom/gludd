# Repository Markdown Lint Target

## Purpose

Gludd exposes one repository-owned Markdown lint command backed by the
maintained markdownlint-cli package. The target replaces an advertised but
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
- The exact requested inputs are printed before linting, and every finding is
  emitted by the upstream CLI.
- The target performs no write or auto-fix operation.

markdownlint-cli 0.49.1 is pinned exactly in the existing OpenCode Node package
and lock. Its upstream documentation recommends local development dependency
installation and supports explicit configuration, a JSON Pointer into a nested
configuration, and file globs:

https://www.npmjs.com/package/markdownlint-cli

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

markdownlint-cli issue #650 records an npm 12 user whose pre-commit environment
passed a removed npm flag. Gludd therefore installs the tracked lock directly
through node-deps-sync instead of delegating installation to pre-commit:

https://github.com/igorshubovych/markdownlint-cli/issues/650

The braces issue #70 documents a stack-exhaustion vulnerability with no patched
release. markdownlint-cli2 pulled that package through its glob stack, so Gludd
changed to markdownlint-cli, whose tinyglobby/minimatch stack does not install
braces:

https://github.com/micromatch/braces/issues/70

## Security and compatibility

The target never executes repository Markdown, downloads plugins, or enables
custom rules. Inline configuration is disabled so a document cannot waive a
finding with an HTML comment. The local binary and transitive packages are
resolved by package-lock integrity hashes through the existing namespaced npm
cache and registry contract. Exact overrides pin the first patched releases of
js-yaml (5.4.1), KaTeX (0.18.2), and smol-toml (1.9.0); the dependency test also
proves that braces is absent from the lock.

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
worker, or implicit repository walk. Standard output identifies the requested
inputs before the upstream CLI emits any findings.

## Verification

tests/unit/test_markdown_lint_target.py behavior-tests a successful lint, the
missing-file failure, the exact package pin, and the make-target contract.
The documented behavioral example lints the XMSS safety specification with
zero issues. The Make contract, help inventory, duplicate-target guard, Node
dependency audit, static checks, collection gate, and full release gate remain
required before promotion.
