# Repository Markdown Lint Target

## Selected installed-runtime repair and native grammar boundary (E85)

The original E ordinary3.11 run retains85FAIL/2400PASS, including two Markdown
cases whose local .venv/bin/rumdl was absent. Read-only metadata found executable,
in-prefix Rumdl0.2.73 in both selected installed3.11/3.12 environments; that
inspection ran no installation, sync or CLI-version command.

The native target selects only UV_PROJECT_ENVIRONMENT's bin/rumdl, with .venv
when that carrier is unset or empty. An early raw freeze for the explicit
lint-markdown goal preserves the selected path as data before startup exports.
The recipe quotes the shell carrier, never searches global PATH, and refuses a
selected missing executable. No public parameter, extra goal gate or unrelated
carrier change is added. Arbitrary mixed-goal/alias behavior remains unproved.
The real CLI/config/status, locked profile, compatibility alias, suppression
guard, read-only behavior and original missing-tool assertions remain.

The actual tests-first selected-tool control produced4FAIL/4PASS with the old
Foundation. The proposed346096a523a922af76e9c8f0bb7cf08914d7ae31c09a84b60ac301ed5fe37e90
Foundation then failed the complete E include graph under GNU Make3.81 at
line1548, before pytest, on both runtimes: multiple target patterns. The
unsupported combined target-specific override/export declaration was removed.

Four separately admitted private grammar probes retain their actual outcomes.
Each used the twelve native include/provider fragments and the same owned CLI
payload under -e. Reversed export/override keywords still failed parsing.
Separate target declarations parsed but evaluated the owned payload, losing
literal equality. A distinct non-exported raw scalar also parsed but evaluated
it. The fourth candidate froze and exported the same public carrier early for
the explicit lint-markdown goal: literal equality held and the owned marker
remained absent, followed by the original missing-files Usage/native2 refusal.
That diagnostic refusal was not a successful lint or pytest run.

Root applied the independently reviewed Foundation correction
a961ef7067759d3d088dfab521e4b46e535ed4c9af79d40f0d263f264edc8ebc.
The complete-graph logical-Make AST control then passed once on each runtime:
3.11 18.94s/native20.410s;3.12 20.09s/native21.618s. The same eight selected-tool
controls passed on each runtime:3.11 2.42s/native3.831s and3.12
2.84s/native4.264s. All four changed test owners also passed canonical strict
typing separately on both runtimes, retaining their original flags/config.

Those completed receipts use the grammar5 namespace and the existing owned
environment helper. The 45-case cross-family focus subsequently passed on both
runtimes:3.11 60.08s/native61.684s;3.12 62.76s/native64.400s. Each trace retains
93events/45pairs, without failed reports or unfinished nodes. Complete90-owner
receiving and the new-source15/12-owner coverage profiles remain pending in
this fixed snapshot. Earlier85 failures, parser failures, all four probe
outcomes and prior source-only reviews remain history.
No full gate, exact-SHA hosted result, integration, coverage completion,
performance, artifact, ZDD rollout or release completion is supplied here.
Earlier practitioner/source links below retain their original scope.

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
