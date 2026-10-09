# Hosted CSS lint

The locked Node tool profile installs an exact Stylelint version and the Build
and Release workflow invokes the same `make lint-css` target used locally. The
target uses `stylelint.config.mjs` and the checked-in npm lock, avoiding both
unbounded `npx` package selection and implicit-install warnings.

The earlier workflow passed JSON directly to the CLI `--config` option.
Stylelint defines that option as a path or module name, so the hosted runner
tried to open the JSON text as a filename and failed with `ENOENT` before it
examined any stylesheet. The tracked ES-module config keeps the enforced rules
reviewable and works with repositories that do not otherwise have a root Node
package.

The first successful config load then exposed two scope issues: the broad CSS
glob included a pinned Reveal.js distribution, and the project stylesheet used
an intentional consecutive `100vh`/`100svh` browser fallback. Third-party
distribution CSS is now excluded through `.stylelintignore`, while the rule's
documented narrow fallback option permits only consecutive duplicates with
different values. Nonconsecutive or identical duplicate properties still fail.

## Long-lived upstream context

- The [Stylelint CLI documentation](https://github.com/stylelint/stylelint/blob/main/docs/user-guide/cli.md)
  documents `--config` as a path to a JSON, YAML, or JavaScript file and shows
  a file-path example.
- The [configuration guide](https://github.com/stylelint/stylelint/blob/main/docs/user-guide/configure.md)
  recommends `stylelint.config.mjs` for an unambiguous ES-module config.
- The long-running [flat-config discussion](https://github.com/stylelint/stylelint/issues/7408)
  records the ecosystem's move toward a single explicit
  `stylelint.config.{js,cjs,mjs}` file. Keeping this repository on the explicit
  `.mjs` form avoids legacy-format ambiguity as that discussion evolves.
- The rule's [official fallback guidance](https://stylelint.io/user-guide/rules/declaration-block-no-duplicate-properties/)
  explicitly supports consecutive duplicate properties with different values
  for older-browser fallbacks.
- A long-running [ignore configuration discussion](https://github.com/stylelint/stylelint/issues/5804)
  records user confusion between config-level ignores and CLI ignore patterns.
  The conventional `.stylelintignore` keeps generated vendor scope visible and
  independent of the rule configuration.

## Verification and rollback

`tests/unit/test_ci_stylelint_workflow.py` pins the exact locked dependency,
local/hosted target parity, config path, vendor boundary, and rule set. Run
`make node-deps-sync` with its documented variables and then `make lint-css`.
Rollback is a single commit revert; the workflow makes no service or deployment
mutation.
