# Quiet Locked Node Dependency Operations

## Status

S83.131 makes Gludd's three locked Node dependency targets suppress npm's
global update notifier by default. Install, lock regeneration, and audit output
remain visible, and any nonzero npm result still fails the Make target.

## Problem

The update notifier is global CLI housekeeping rather than evidence about the
dependency operation requested by the repository. It may query or compare a
newer npm release after an otherwise deterministic locked install, relock, or
audit. In automation this adds unrelated network work and a prominent version
notice to output where operators need to see the requested command's result.

The existing targets already isolate npm's user configuration, cache, and
registry. They did not set npm's supported `update-notifier` configuration, so
the host npm default remained in control.

## Contract

`NODE_DEPS_NPM_UPDATE_NOTIFIER` defaults to `false`. Each real npm subprocess
receives that value as `NPM_CONFIG_UPDATE_NOTIFIER`, the environment form of
npm's documented configuration. An operator can explicitly set the variable to
`true` when interactive update advice is desired.

The following validate-only examples exercise the public Make contract without
network or package-tree mutation:

```console
make node-deps-sync NODE_DEPS_VALIDATE_ONLY=1 NODE_DEPS_NPM_USERCONFIG=/dev/null NODE_DEPS_NPM_CACHE=/tmp/gludd-npm-cache-public-v1 NODE_DEPS_NPM_REGISTRY=https://registry.npmjs.org NODE_DEPS_NPM_UPDATE_NOTIFIER=false
make node-deps-relock NODE_DEPS_VALIDATE_ONLY=1 NODE_DEPS_NPM_USERCONFIG=/dev/null NODE_DEPS_NPM_CACHE=/tmp/gludd-npm-cache-public-v1 NODE_DEPS_NPM_REGISTRY=https://registry.npmjs.org NODE_DEPS_NPM_UPDATE_NOTIFIER=false
make node-deps-audit NODE_DEPS_VALIDATE_ONLY=1 NODE_DEPS_NPM_USERCONFIG=/dev/null NODE_DEPS_NPM_CACHE=/tmp/gludd-npm-cache-public-v1 NODE_DEPS_NPM_REGISTRY=https://registry.npmjs.org NODE_DEPS_NPM_UPDATE_NOTIFIER=false NODE_DEPS_AUDIT_LEVEL=moderate
```

Notifier suppression is not an error filter. Recipes retain their direct npm
invocations without `|| true`, exit-code rewriting, stderr redirection, or log
level reduction. A failed `npm ci`, `npm install --package-lock-only`, or
`npm audit` therefore remains observable and nonzero.

## Upstream and Practitioner Evidence

npm documents `update-notifier` as a boolean that defaults to `true` and says
setting it to `false` suppresses the version notification:
[npm configuration reference](https://docs.npmjs.com/cli/v11/using-npm/config#update-notifier).

The npm CLI practitioner report
[#6836](https://github.com/npm/cli/issues/6836) remained open from September
2023 until February 2025. It records the notifier recommending an npm major
that was incompatible with the installed Node release. Although npm later
corrected that compatibility calculation, the report demonstrates why global
upgrade advice is not reliable evidence for a repository's locked dependency
operation. Gludd opts out through npm's public configuration instead of parsing
or hiding notices after the fact.

## Security, Resources, and Observability

- Locked manifests, the explicit registry, isolated cache, and `/dev/null`
  user configuration remain unchanged. Suppressing a CLI update suggestion
  does not disable `npm audit`, alter its threshold, or change lock resolution.
- The default removes unrelated global-version lookup work from noninteractive
  targets. No new process, cache, file, service, port, or shared namespace is
  introduced.
- Requested npm stdout and stderr remain live. The original npm exit status
  still determines Make success, including audit findings and registry,
  integrity, or lock-generation failures.
- Tests use a per-test temporary `npm` executable and log. They never contact a
  registry or mutate the tracked lockfile while proving both environment
  propagation and nonzero failure behavior.

## Zero-Downtime Delivery and Rollback

This is an invocation-only configuration change. It has no service restart,
schema, persistent-data, or deployment migration and can be merged while
running workloads continue unchanged. Existing locked dependency commands keep
the same package inputs and failure boundary; only the unrelated global update
notice is disabled.

Rollback is a code-only revert of the Make default, target environment wiring,
contract, tests, task record, and this document. Operators needing the prior
behavior before a revert can set `NODE_DEPS_NPM_UPDATE_NOTIFIER=true` explicitly.
No cache cleanup, lock regeneration, or downtime is required.
