# Feature-Branch Integration Admission

## Outcome

S83.177 adds one fail-fast Make path for a committed feature branch before it
enters the expensive full-gate lane. The target composes existing repository
checks; it does not add another linter, test runner, YAML parser, browser
launcher, or task-ledger implementation.

The admission path does not replace the full gate. A passing result means only
that the branch cleared the deterministic, comparatively cheap failure classes
listed below. Release evidence still requires the normal exact-head gate and
hosted checks after integration.

## Contract

`make integration-admission` runs these existing targets in order and stops on
the first nonzero result:

1. `worktree-guard` binds admission to a clean committed candidate.
2. `_dead-code-baseline-refresh` checks exact dead-code baseline parity without
   rewriting reviewed policy. It names added or stale entries and stops the
   admission plan before broader metadata, documentation, or quality work.
3. `validate-task-ledger`, `check-task-registration`, and
   `check-task-integrity` reject malformed or unowned work.
4. `check-generated-artifact-hygiene` catches documentation and generated-data
   drift before broader quality work.
5. `lint-markdown` checks the explicitly supplied feature documents.
6. `check-make-target-contract` rejects missing help, variables, or safe
   behavioral examples for agent-facing targets.
7. `yaml-lint` reuses the maintained Ansible YAML validator. It resolves only
   tracked YAML from the checkout, runs with a disposable namespaced Ansible
   home, and emits ten-second observer heartbeats. A stale user-installed
   collection therefore cannot shadow the candidate or turn a valid branch red.
8. `presentation-browser-test` runs with
   `PRESENTATION_BROWSER_VALIDATE_ONLY=1`, checking the pinned browser plan and
   prerequisites without launching either engine.
9. `pre-commit-check` runs the existing source lint, collection, and typecheck
   boundary. Its `lint` prerequisite runs `check-file-line-limits` first.

Every documented variable is explicit at the outer invocation and forwarded to
the target that owns it. The safe behavioral example uses
`INTEGRATION_ADMISSION_VALIDATE_ONLY=1`; it prints the exact ordered plan and
does not run any child check. Set that variable to `0` only on a clean, committed
feature worktree.

The tracked browser-test outer bound is 600 seconds. A full WebKit replay
completed in 247.53 seconds and its compact replay completed in 43.73 seconds;
the prior 300-second outer bound killed the otherwise valid combined run before
the harness could report success. The doubled bound preserves failure
containment while accommodating the measured complete matrix.

## Why this is separate from the full gate

Practitioner reports show that queue latency compounds when expensive checks
run again after a branch has already waited for earlier feedback:

- GitHub's long-running [Merge Queue Feedback discussion][queue-feedback]
  records an added 10-15 minutes per pull request, multi-hour queue drain, and
  requests for distinct admission and ship checks.
- [Merge Queue running checks twice?][duplicate-checks] has accumulated reports
  from 2023 through 2026 about doubled CI time, wasted Actions capacity, and the
  safety risk of blindly skipping the integrated-head replay.
- [Merge queue specific checks][specific-checks] documents the coupling between
  PR and queue status checks and the common need for one stable aggregate check.
- Ansible's long-lived [collection-resolution report][ansible-collection-path]
  demonstrates how configured collection search state can select a different
  artifact than the source tree an operator intended to execute.
- The long-lived Stack Overflow [Vulture and Django discussion][vulture-django]
  records why dynamic Python programs need reviewed dead-code whitelists for
  false positives. Admission therefore validates the exact reviewed set; it
  never silently accepts a regenerated allowance.

Those reports support an early local admission layer, but not skipping the final
integrated-head proof. Gludd therefore rejects syntax, metadata, documentation,
static browser-plan, collection, and type failures before the scarce full-gate
slot while retaining the full gate as the authoritative integration result.

[queue-feedback]: https://github.com/orgs/community/discussions/14801
[duplicate-checks]: https://github.com/orgs/community/discussions/43988
[specific-checks]: https://github.com/orgs/community/discussions/103114
[ansible-collection-path]: https://github.com/ansible/ansible/issues/74917
[vulture-django]: https://stackoverflow.com/questions/12101463/is-there-a-simple-way-to-use-vulture-with-django

## Zero-downtime and rollback

This is a zero-downtime control-plane check. Admission starts no application
listener or daemon, changes no schema or durable runtime state, and launches no
browser in its static browser phase. All temporary browser paths remain under
the caller-supplied `/tmp/gludd-*` namespace.

Rollout is additive: branches may adopt the target before entering the existing
gate queue. Rollback removes the target, contract record, focused tests, and this
document; it requires no service restart, data migration, or traffic cutover.
