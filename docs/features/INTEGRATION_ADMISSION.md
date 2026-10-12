# Feature-Branch Integration Admission

## Outcome

S83.177 added one fail-fast Make path for a committed feature branch before it
enters the expensive full-gate lane. S83.178 makes that path mechanically
bounded and promotes deterministic full-gate failures through one reviewed
manifest. The target composes existing repository checks; it does not add
another linter, test runner, timer, YAML parser, browser launcher, or
task-ledger implementation.

The admission path does not replace the full gate. A passing result means only
that the branch cleared the deterministic, comparatively cheap failure classes
listed below. Release evidence still requires the normal exact-head gate and
hosted checks after integration.

## Contract

`make integration-admission` runs these existing targets in order and stops on
the first nonzero result:

1. `worktree-guard` binds admission to a clean committed candidate.
2. `check-gate-failure-promotions` validates the structured ownership manifest
   before any promoted node runs. It rejects duplicate families or nodes,
   missing Make owners, stale pytest node IDs, admission wiring drift, and a
   missing mandatory full-gate phase.
3. `pytest-runtime-compatibility` runs the complete crash-recovery,
   hook-runtime split compatibility, async-launcher, and session-persistence
   files, plus the exact session-objective CI node, with `-W error` and one
   worker in the invoking Python environment. It rejects deprecated fixtures,
   runtime API snapshot drift, launcher policy drift, and invalid handoff
   metadata before broader integration checks.
   Hosted Python 3.11/3.12 jobs retain their own interpreter coverage; admission
   reuses their environment without creating another dependency matrix.
4. `ansible-role-variable-prefix` runs the exact warning-fatal
   `test_role_variables_use_namespaced_prefix` node. The control gate first
   found this deterministic failure in batch 10 after minutes of work; admission
   now reports it in seconds before integration health checks can consume their
   eight-minute bound.
5. `cloud-iam-generation-parity` serially runs
   `test_aws_prune_produces_warning_when_actions_removed` followed by
   `test_generate_aws_terraform_deploy`. This order reproduces cross-call
   mutation of nested role templates in one process, while `-W error` keeps
   warning drift fatal and `-n 0` prevents worker isolation from hiding the
   shared-state invariant.
6. `_dead-code-baseline-refresh` checks exact dead-code baseline parity without
   rewriting reviewed policy. It names added or stale entries and stops the
   admission plan before broader metadata, documentation, or quality work.
7. `check-coverage-gaps` rejects a production module without a mapped focused
   coverage test. `check-resource-ownership` then verifies the exact tracked
   acquisition-to-teardown inventory in read-only mode. These promoted checks
   catch both missing mappings after a module split and new or stale lifecycle
   sites before the expensive gate starts.
8. `validate-task-ledger`, `check-task-registration`, and
   `check-task-integrity` reject malformed or unowned work.
9. `check-generated-artifact-hygiene` catches documentation and generated-data
   drift before broader quality work.
10. `lint-markdown` checks the explicitly supplied feature documents.
11. `check-make-target-contract` rejects missing help, variables, or safe
   behavioral examples for agent-facing targets.
12. `check-duplicate-code` compares the complete bounded production snapshot at
   feature `HEAD` with the explicit development base through locked `jscpd`.
   Existing findings remain visible while only newly introduced clone
   fingerprints fail admission.
13. `yaml-lint` reuses the maintained Ansible YAML validator. It resolves only
   tracked YAML from the checkout, runs with a disposable namespaced Ansible
   home, and emits ten-second observer heartbeats. A stale user-installed
   collection therefore cannot shadow the candidate or turn a valid branch red.
14. `project-dispatch-integration` runs the exact, fast regression nodes that
   prove a committed caller-owned-session claim still dispatches its exact
   `project_id`, never reads another project's variable namespace, and preserves
   the serialized tick lifecycle:
   `test_event_loop_dispatch_includes_project_id` and
   `test_dispatch_job_contains_only_project_data`, plus
   `test_event_loop_serializes_concurrent_ticks`.
15. `mcp-workspace-jail-integration` keeps both layers of the model-callable
    project-check boundary in admission. The exact
    `test_contain_workspace_escape_returns_none` node proves canonical path
    containment, while `test_workspace_escaping_jail_is_refused` proves the
    synthetic MCP dispatch refuses an otherwise valid sibling project without
    executing it.
16. `module-graph-classification` runs the exact
    `test_all_subpackages_classified` node so every discovered top-level package
    receives an explicit architectural layer before later graph rules run.
17. `presentation-browser-test` runs with
   `PRESENTATION_BROWSER_VALIDATE_ONLY=1`, checking the pinned browser plan and
   prerequisites without launching either engine.
18. `pre-commit-check` runs the staged line and clone guards before the existing
    source lint, collection, and typecheck boundary. Its `lint` prerequisite
    retains the complete working-tree line-limit scan.

Every documented variable is explicit at the outer invocation and forwarded to
the target that owns it. The safe behavioral example uses
`INTEGRATION_ADMISSION_VALIDATE_ONLY=1`; it prints the exact ordered plan and
does not run any child check. Set that variable to `0` only on a clean, committed
feature worktree.

## Runtime budget and evidence

Every child phase runs through the existing `scripts/stream_command.py`
observer. The manifest assigns fifteen cheap phases a 90-second `fast` ceiling,
five focused integration phases a 180-second `standard` ceiling, and
`pre-commit-check` a 600-second `slow` ceiling. Their quiet-output ceilings are
60, 120, and 300 seconds respectively. The configured child-runtime ceiling
therefore totals 2,850 seconds, with only bounded observer teardown and recipe
bookkeeping outside it; no child phase can wait indefinitely.

Before each child starts, admission emits one machine-readable
`integration_admission_phase` JSON record with its target, budget class,
runtime and quiet ceilings, run ID, and evidence label. The reused observer
writes bounded `observed_command` schema-version-1 status under
`.gate-logs/observed/integration-admission-<phase>/`, retains at most 20 runs,
and records elapsed time, exit code, and termination reason. A
`max-runtime-timeout` or `quiet-output-timeout` terminates the child process
group and exits 124, so `set -eu` prevents every later admission phase from
running. Observer I/O or process-launch failures also fail closed.

Validate-only mode emits the same ordered JSON plan without starting a child.
The promotion checker compares every manifest phase with the exact observer
invocation, target, order, budget, heartbeat, label, and retention bound in the
Make recipe. A renamed target or relaxed/unobserved phase therefore fails
before the promoted checks begin.

## Mechanical promotion ownership

`config/gate_failure_promotions.json` is the reviewed source of truth for checks
copied forward from the deterministic full-gate surface. Each entry binds one
failure family to its admission owner, exact Make target or pytest node, and the
full-gate phase that continues to own the authoritative replay. The initial
families are `dead-code-baseline-drift`, `coverage-gap-drift`,
`resource-ownership-drift`, `claim-fence`, `project-isolation`,
`concurrent-tick`, `mcp-workspace-containment`, `mcp-workspace-dispatch-jail`,
`module-graph-classification-drift`, and
`ansible-role-variable-prefix-drift`, `cloud-iam-resource-pruning-trigger`, and
`cloud-iam-generation-parity`, `pytest-class-fixture-scope`,
`hook-runtime-case-ast-portability`, and `hook-runtime-helper-ast-portability`.
The coverage and ownership families
were promoted after the full gate first discovered an unmapped durable
observability split plus new and stale process-lifecycle sites. Both now fail
closed during read-only admission instead of consuming another full-gate run.

Hosted Build run 38189779986 exposed the fixture and AST portability families
under Python 3.11. The class-scoped fixture now uses pytest's supported
`@classmethod` form. The original runtime snapshot remains unchanged: the
compatibility check parses its inert AST constructor notation with the standard
library and omits only empty list-valued fields, whose display differs across
Python versions. It never evaluates snapshot text. Names, argument order and
kinds, annotations, defaults, return annotations, and decorators still compare
structurally; focused regressions prove ten forms of API or mark drift are
rejected, including an actual empty-list default or mark value.

Hosted run `38266403588` later exposes the async launcher's obsolete
lock-first prerequisite substring in unit-2 batch 008. Canonical profile
admission must precede lock acquisition, so the conformed test now verifies
that exact ordering, conditional release-state check, one lock owner, and
unchanged nonrecursive gate execution. The original failing result is retained;
no production owner or cancellation behavior is weakened to satisfy the test.

The same compatibility owner now runs the complete async launcher file beside
the fixture/AST checks. Its new `async-gate-canonical-profile-contract`
manifest entry binds the exact previously failing node to that existing early
phase. A new test fails first on the absent mapping and verifies executable
file routing, warning-fatal one-worker policy, and the still-required full
gate. The 90-second phase budget is unchanged; these checks fail cheaply
before costly hosted shard fan-out rather than relaxing or skipping those
shards.

The `session-objective-ci-evidence` and `session-handoff-git-identity`
families retain the original tests that exposed late metadata failures. Hosted
unit-1b batch 026 first rejected the objective's missing explicit CI wording;
an earlier combined replay rejected an observer suffix mistaken for a Git
anchor. Repairing SESSION.md, not weakening those assertions, resolves both.
The complete session-persistence file and exact objective node now execute in
the same 90-second, warning-fatal, one-worker admission phase. New mapping tests
fail first and verify executable routing rather than comment-only references.

Promotion only moves early feedback forward. The exact full gate remains mandatory.
The checker fails closed when a node is renamed, deleted, duplicated,
removed from its owner, removed from admission, or detached from its declared
full-gate phase. Adding another deterministic family therefore requires one
manifest record and a real owned node rather than another undocumented recipe
fragment.

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
- The 2022 [merge queue and optional checks discussion][optional-checks] records
  that a failing but non-required integration job could still allow a merge and
  that dummy duplicate jobs made ownership difficult to reason about. The
  explicit promotion manifest makes early checks auditable without weakening
  the required integrated-head replay.
- Ansible's long-lived [collection-resolution report][ansible-collection-path]
  demonstrates how configured collection search state can select a different
  artifact than the source tree an operator intended to execute.
- The long-lived Stack Overflow [Vulture and Django discussion][vulture-django]
  records why dynamic Python programs need reviewed dead-code whitelists for
  false positives. Admission therefore validates the exact reviewed set; it
  never silently accepts a regenerated allowance.
- The long-running MCP filesystem [path-validation report][mcp-path-validation]
  shows valid child paths being rejected as outside their configured directory
  when canonical forms differ. The paired MCP admission nodes preserve both
  sides of the boundary: real escapes remain closed while an in-jail project
  still reaches the callable handler.
- Import Linter's long-lived [missing-package report][import-graph-completeness]
  records a graph check silently missing a root when package discovery was
  incomplete. The promoted classification node fails as soon as a discovered
  top-level package lacks an explicit layer instead of letting graph policy
  silently narrow.
- GitHub's long-running [Limit runtime? discussion][bounded-ci-runtime] began
  with a test hanging until the six-hour default killed it and later recorded
  the organization-wide cost of one mistake exhausting available minutes.
  Per-phase local ceilings make the slow owner attributable and stop that class
  of runaway before the exact full gate consumes the scarce queue slot.
- The long-lived Stack Overflow [nested dictionaries copy discussion][nested-copy]
  records the same shallow-copy trap behind order-dependent template failures:
  nested mutable values remain shared until copied recursively. The promoted
  two-node sequence therefore proves pruning cannot alter a later full-role
  generation, instead of trusting either node in process isolation.
- The 2020 practitioner [class-scoped fixture discussion][class-fixture-scope]
  explains that pytest creates distinct fixture and test instances and recommends
  a class method for class-owned setup. Current [pytest deprecation guidance][pytest-class-fixture]
  makes that declaration mandatory before pytest 10; `-W error` retains early
  rejection of the deprecated form. Python's [AST dump documentation][ast-dump]
  records the `show_empty` display option added in Python 3.13. Comparing parsed
  constructor structure keeps the historical snapshot portable while preserving
  semantic signature and decorator changes.

Those reports support an early local admission layer, but not skipping the final
integrated-head proof. Gludd therefore rejects syntax, metadata, documentation,
static browser-plan, collection, project-dispatch compatibility, and type
failures, MCP workspace-jail drift, and unclassified module-graph additions
before the scarce full-gate slot while retaining the full gate as the
authoritative integration result.

[queue-feedback]: https://github.com/orgs/community/discussions/14801
[duplicate-checks]: https://github.com/orgs/community/discussions/43988
[specific-checks]: https://github.com/orgs/community/discussions/103114
[optional-checks]: https://github.com/orgs/community/discussions/41726
[ansible-collection-path]: https://github.com/ansible/ansible/issues/74917
[vulture-django]: https://stackoverflow.com/questions/12101463/is-there-a-simple-way-to-use-vulture-with-django
[mcp-path-validation]: https://github.com/modelcontextprotocol/servers/issues/1838
[import-graph-completeness]: https://github.com/seddonym/import-linter/issues/93
[bounded-ci-runtime]: https://github.com/orgs/community/discussions/25631
[nested-copy]: https://stackoverflow.com/questions/39474959/nested-dictionaries-copy-or-deepcopy
[class-fixture-scope]: https://stackoverflow.com/questions/62289556/pytest-fixture-with-scope-class-doesnt-works-with-setup-class-method
[pytest-class-fixture]: https://docs.pytest.org/en/stable/deprecations.html#class-scoped-fixture-as-instance-method
[ast-dump]: https://docs.python.org/3/library/ast.html#ast.dump

## Zero-downtime and rollback

This is a zero-downtime control-plane check. Admission starts no application
listener or daemon, changes no schema or durable runtime state, and launches no
browser in its static browser phase. All temporary browser paths remain under
the caller-supplied `/tmp/gludd-*` namespace.

Rollout is additive: branches may adopt the target before entering the existing
gate queue. Rollback removes the target, promotion manifest and checker,
contract record, focused tests, and this document; it requires no service
restart, data migration, or traffic cutover.
