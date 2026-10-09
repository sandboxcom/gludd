# Hosted CSS lint

The Build and Release workflow installs an exact Stylelint version alongside
the other Node CI tools, then invokes that local binary with
`stylelint.config.mjs`. This avoids both unbounded `npx` package selection and
the implicit-install warning.

The earlier workflow passed JSON directly to the CLI `--config` option.
Stylelint defines that option as a path or module name, so the hosted runner
tried to open the JSON text as a filename and failed with `ENOENT` before it
examined any stylesheet. The tracked ES-module config keeps the enforced rules
reviewable and works with repositories that do not otherwise have a root Node
package.

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

## Verification and rollback

`tests/unit/test_ci_stylelint_workflow.py` pins the exact tool install, local
execution mode, config path, and rule set. Rollback is a single commit revert;
the workflow makes no service or deployment mutation.
