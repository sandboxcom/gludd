# Lint-Suppression Enforcement Lexing

## Scope

Gludd rejects lint-suppression comments at the edit boundary so agents repair
the underlying typing or lint defect instead of hiding it. The rule covers
`noqa`, `type: ignore`, pylint controls, formatter controls, and isort
controls. This contract distinguishes executable comments from the same bytes
stored as string or docstring data, pins the hard-on policy, and keeps runtime
tests isolated from mutable hot-module state.

## Comment-boundary contract

The edit hook scans single-quoted, double-quoted, and triple-quoted regions
before evaluating a hash comment. A suppression-shaped token is denied only
when its hash begins outside those quoted regions. Escaped quotes remain inside
their string. Plain comments and quoted fixtures remain allowed; actual inline
and whole-line suppressions remain denied.

The matcher validates empty or non-string paths fail open so a malformed tool
payload cannot wedge the editor. Its policy decision remains fail closed for
recognized suppression comments. The allowlist stays limited to the two
repository fixtures that encode suppression patterns as data.

Structural tests parse JavaScript regular-expression literals with escaped
characters and accept either quote style. This keeps source-shape checks from
silently reporting missing rules that are present and active.

## Security and hard-on policy

There is no environment-variable bypass for this guardrail. Only the explicit
subagent-isolation boundary skips it, because the orchestrator owns enforcement
for delegated work. Removing an environment bypass prevents an inherited shell
setting from silently weakening repository integrity.

The hook logs liveness through the existing plugin heartbeat but must not log
edited source, string contents, credentials, or denial payloads. Operators
should monitor plugin load failures, deny counts by rule family, and unexpected
fallback-to-default events.

## Runtime isolation and resource safety

OpenCode may load a mutable hot module from a namespaced path. Tests set a
process- and case-specific `GLUDD_HOT_MODULE_PREFIX`, ensuring a live session's
older compiled module cannot change committed-source verification. The tests
create no daemon and remove their temporary TypeScript entry files.

This is especially important for false-positive tests: without isolation, the
same checkout can alternate between old and new behavior depending on which
interactive session last rebuilt a hot module.

## Zero-downtime rollout and rollback

Plugin source is loaded at OpenCode startup. After this change lands, restart
OpenCode before treating the new lexer or hard-on policy as active in an
existing session. Canary the restarted process with one actual suppression
comment, one quoted suppression token, one docstring token, one allowlisted
fixture, and one subagent invocation before restarting the remaining agent
processes.

During a mixed-version rollout, the repository gate uses the committed fallback
under isolated prefixes while older interactive processes keep their loaded
behavior. Rollback restores the prior commit and restarts OpenCode processes in
the same canary-first order. No persisted project data or wire schema changes.

## Practitioner evidence

A long-lived Ruff issue records maintainers and users preferring false negatives
over noisy false positives when classifying comment-shaped text, because valid
comments that trigger enforcement are especially disruptive:
[Ruff issue #6100](https://github.com/astral-sh/ruff/issues/6100).
Gludd applies that principle narrowly to lexical classification while retaining
hard denials for real suppression comments.

Ruff's open baseline discussion documents demand for incremental suppression of
existing findings:
[Ruff issue #1149](https://github.com/astral-sh/ruff/issues/1149).
Gludd deliberately does not add a runtime baseline or environment bypass; its
existing source backlog is repaired separately through typed code changes.

## Verification

The focused enforcement selection passes 323 tests with one intentional skip
under strict warnings. It includes the 81-test linter contract, direct Node E2E
invocation, shared matcher invocation, TDD allowlist compatibility, hot-module
isolation, hard-on behavior, quoted-data acceptance, and denial of every
supported suppression family.
