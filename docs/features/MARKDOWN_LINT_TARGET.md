# Repository Markdown Lint Target

## Purpose

`make lint-markdown` provides one repository-owned Markdown check without a
global executable, runtime download, or Node advisory chain. The target now
uses exact-pinned Rumdl 0.2.73 from the locked `dev-quality` Python profile.
The OpenCode Node graph contains neither markdownlint-cli nor
markdownlint-cli2.

## Behavioral contract

- Callers pass `MARKDOWN_FILES` plus `RUMDL_CONFIG` explicitly.
- `MARKDOWNLINT_CONFIG` remains a supported compatibility alias for existing
  callers. Supplying both names with different values fails closed; equal
  values are accepted during migration.
- When neither config variable is supplied, the tracked `config/rumdl.toml`
  policy remains the compatibility default.
- Missing files or configuration and an unavailable locked executable fail
  visibly. The executable can be restored only through the locked
  `development` profile sync.
- The target is read-only and does not apply fixes.
- Rumdl caching is disabled, so linting leaves no workspace cache artifact.

The public Make contract exercises both config names with the same tracked
path:

```text
make lint-markdown MARKDOWN_FILES=docs/features/XMSS_BACKEND_SAFETY.md RUMDL_CONFIG=config/rumdl.toml MARKDOWNLINT_CONFIG=config/rumdl.toml
```

## Tracked policy

The allowlist preserves the seven established checks: heading progression
(MD001), ATX heading style (MD003), trailing whitespace (MD009), hard tabs
(MD010), missing heading space (MD018), excessive heading spaces (MD019), and
final newline (MD047). Rumdl's
[global settings reference](https://github.com/rvben/rumdl/blob/main/docs/global-settings.md)
documents that `enable` is a strict rule allowlist, while its
[CLI reference](https://rumdl.dev/usage/cli/) defines the explicit `check` and
`--config` interface used by the target.

Rumdl supports suppression directives in Markdown. Because the prior contract
forbade document-local waivers, the Make target rejects Rumdl, markdownlint,
and Prettier lint-control comments before invoking Rumdl. Policy exceptions
must be reviewed in the tracked configuration. The upstream
[inline configuration reference](https://github.com/rvben/rumdl/blob/main/docs/inline-configuration.md)
identifies the directive families covered by this guard.

## Tool and practitioner evidence

Rumdl is a maintained Rust implementation distributed on
[PyPI](https://pypi.org/project/rumdl/) and supports the required markdownlint
rule identifiers. A long-lived
[markdownlint-cli2 configuration issue](https://github.com/DavidAnson/markdownlint-cli2/issues/130)
records a practitioner receiving a zero-error result when the intended config
was not loaded. That experience supports an explicit tracked config path and a
behavioral test. Another multi-year
[markdownlint rule discussion](https://github.com/DavidAnson/markdownlint/issues/45)
shows false positives from inferred list style, supporting the small explicit
rule allowlist instead of enabling every opinion across legacy documents.

## Security, resources, and zero downtime

Rumdl is integrity-locked in `requirements/profiles/dev-quality/uv.lock` and
runs from the project virtual environment. Removing the two Node Markdown
CLIs eliminated their vulnerable transitive packages; the hosted Node audit
now passes at the low threshold without overrides or suppressions.

One bounded process examines only explicit files. It starts no daemon, creates
no shared cache, and changes no runtime, protocol, database, or deployment
state. Application workers continue serving throughout rollout and rollback.

## Verification

`tests/unit/test_markdown_lint_target.py` proves successful lint, missing-input
failure, exact profile and lock pins, compatibility-alias behavior,
conflict rejection, suppression rejection, and the public Make contract. The
integration-admission regression preserves both config names. The documented
behavioral example, Markdown lint, Make-contract validation, collection, and
dependency audits remain required promotion evidence.
