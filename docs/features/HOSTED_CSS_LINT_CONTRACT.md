# Hosted CSS Lint Contract

## Purpose

Hosted run 37684089683 exposed that the CSS phase invoked an unversioned `npx`
tool and carried its policy as inline JSON. That bypassed the existing Node lock,
allowed the selected Stylelint release to change between otherwise identical
runs, and split local behavior from CI. The phase now calls one public Make
target backed by an exact package and tracked configuration.

## Behavioral contract

- Callers run `make lint-css` and explicitly set `CSS_FILES` and
  `STYLELINT_CONFIG`.
- Stylelint 17.16.0 is exact-pinned in `.opencode/package.json` and its lock.
- The same lock advances markdownlint-cli2 to current 0.23.3 so the relock does
  not retain already-patched parser advisories from 0.23.2.
- Exact npm overrides select patched KaTeX 0.18.10 and smol-toml 1.9.0 releases
  for advisories that their current parent package has not yet adopted.
- The target executes only `.opencode/node_modules/.bin/stylelint`; it never
  downloads a package through `npx`.
- A missing local binary is restored through the locked, namespaced
  `node-deps-sync` contract. Missing inputs or configuration fail closed.
- The tracked configuration preserves the three hosted rules: empty blocks,
  invalid hexadecimal colors, and duplicate declarations. Stylelint's documented
  consecutive-different-value exception admits intentional browser fallbacks such
  as `100vh` followed by `100svh`, while equal or separated duplicates still fail.
- `--allow-empty-input` keeps unchanged language families harmless while the
  explicit file patterns remain visible to callers.

Stylelint's [getting-started guide](https://stylelint.io/user-guide/get-started/)
recommends a local development dependency and a repository configuration file.
Its [options reference](https://stylelint.io/user-guide/options/) defines the
explicit config path and empty-input behavior used by the target, and the
[duplicate-property rule](https://stylelint.io/user-guide/rules/declaration-block-no-duplicate-properties/)
documents the browser-fallback exception. npm documents that
[`npm ci`](https://docs.npmjs.com/cli/v11/commands/npm-ci/) installs the dependency
tree from the committed lock without rewriting it.

## Practitioner evidence

The long-lived Stack Overflow discussion on
[`npm install` versus `npm ci`](https://stackoverflow.com/questions/52499617/what-is-the-difference-between-npm-install-and-npm-ci/59386596)
records practitioners relying on the lockfile-specific install for repeatable
CI. Stylelint issue
[#4195](https://github.com/stylelint/stylelint/issues/4195) records years-old
confusion over which configuration is discovered by the CLI. Those reports
support one exact local binary and one explicit tracked config path rather than
ambient package resolution or config discovery.

## Zero-downtime and resources

This change affects build validation only. It starts one bounded Node process,
no daemon, and no application, database, or deployment migration. Existing
workers continue serving during rollout; rollback is the prior tooling commit.
The existing project-scoped npm cache is reused, so concurrent projects do not
share mutable runtime state.

The October 2026 `braces` stack-exhaustion advisory has no patched release.
Both current Stylelint and markdownlint-cli2 reach it through their file-glob
libraries. Hosted inputs are fixed, reviewed glob literals rather than network
or user data, and execution is a short-lived CI process, so the unavailable
upstream patch cannot affect a serving Gludd process. The advisory remains
visible to `node-deps-audit` and must be removed as soon as upstream publishes a
compatible fix; it is not hidden with an audit suppression or version override.

## Verification

`tests/unit/test_css_lint_target.py` pins the package, lock, configuration,
Make contract, behavior, and hosted-workflow delegation. The contract's public
example lints the tracked presentation stylesheet. Make-contract validation,
YAML lint, Markdown lint, and test collection remain required promotion gates.
