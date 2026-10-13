# Native Git control-plane acceptance

This appendix preserves the Git and advisory-role evidence linked from the
[parent Beta4 dual-track CI document](../BETA4_DUAL_TRACK_CI.md).

## Git and release control-plane branches

The hosted aggregate showed that Git history dispatch, release orchestration,
and duplicate-target enforcement were behaviorally green but split away from
the tests that exercised their branch surfaces. The beta4 regression file now
drives every allowlisted Git operation, validation and unsupported-result
boundary, non-interactive `git` and `gh` failure, CI conclusion, README version
check, release repository failure, and duplicate-target CLI outcome. The
focused replay passes 147 tests at 95% aggregate coverage; the three measured
files report 93%, 94%, and 97%, with both line and branch coverage above 75%.

These tests do not push, tag, delete, or contact GitHub. All transports are
injected at the application boundary, malformed input remains fail-closed, and
the existing ZDD ordering remains unchanged: exact-SHA CI evidence precedes
branch/tag publication, while failed publication never advances release state.
Rollback removes only the regression commit; no Git refs or release artifacts
are mutated. The upstream GitHub Actions and coverage.py practitioner evidence
recorded earlier in this document continues to govern the exact-SHA pairing and
coverage-artifact policy.

### v0.1.2 authenticated composite operations

Native input validation exposed seventeen caller/module contract failures. The
repairs retain authenticated daemon parameters, required enforcement context,
exactly one model selector and native result fields. Eight existing core Git
composites now have matching module choices, required parameters and typed HTTP
dispatch; this does not replace their maintained implementations.

The authenticated seam runs the canonical `MakeRunner.run_gate` before feature
completion or composite worktree merges. Failed, incomplete, noncanonical,
timeout and OOM results refuse mutation. `master` and `refs/heads/master` targets
require reviewed `make release-promote`; the role/API default is `development`.
Ship commit refuses `skip_gate=true`, while check-mode mutations and read-only
health checks preserve their previous contracts. Worktree creation validates
its explicit destination and optional base before native Git mutation.

Composite calls default to 3,900 seconds: the unchanged 3,600-second gate budget
plus 300 seconds for transport/cleanup. Explicit smaller composite budgets fail
before constructing a client; adequate budgets are preserved. The new role
setting `git_gate_api_timeout` applies only to the three gate-running composites.
Ordinary/read-only calls retain `git_api_timeout: 300`. The isolated collection
does not import core Python; regressions pin its protocol constant to the
maintained gate default. No retry, timeout runner or serving-process replacement
is introduced.

The [2020 unsupported-parameter report](https://forum.ansible.com/t/msg-unsupported-parameters-for-basic-py/32140)
shows caller names diverging from a custom module's argument specification.
The [2024 templated-type discussion](https://forum.ansible.com/t/custom-module-parameter-type-checking-ignored-on-linting/10174)
explains why unresolved Jinja cannot establish runtime parameter-type proof.
These are related precedents, not evidence that Gludd's failures are false
positives. Real Ansible templating and module execution supplement native lint.

Root's new argument/safety/timeout cohorts reproduce 62, 31 and 25 failures
before repair, then pass all 140 cases. The isolated coverage replay adds 21
real repository/HTTP capability/gate/topology/rollback/cleanup cases and passes
all 622 tests at 94.91% combined line/branch coverage across all four changed
owners; every owner meets unchanged 75% per-file floors. Root's Python 3.11
diagnostic/composite/native-regression replay passes all 176 cases. These are
source-level results, not deployed authentication, the full release gate or
exact-SHA hosted acceptance. S98.4 remains open; no master promotion is authorized.

## S98.4 shared advisory enforcement-model owner

The enforcement roles now reach one existing-role task include,
`enforcement_gate/tasks/model_analysis.yml`, for the authenticated model
invocation. The gate and verification entrypoints keep their original full
prompts and their original position in the deterministic guard sequence. Each
entrypoint maps the shared native result back to its existing raw-analysis,
response, and confidence facts. The model remains advisory: `failed_when: false`
does not change the deterministic verdict or either final failure condition.
The model selector expressions, daemon URL, pre-shared key, and conditional
`no_log` remain in the shared module owner. Analysis remains disabled by default.

The new focused test observes 16 failing cases before deduplication and 16 passing
cases after it, with `-W error` and one worker, on both CPython 3.14.0/Ansible
2.21.5 and CPython 3.11/Ansible 2.19.14.
It loads the actual task files, evaluates their selectors and enabled conditions
through native Ansible templating, and feeds the resulting arguments to the real
`gludd_model_call` module in check mode. The returned native text is carried into
both roles' existing artifact facts; successful and failed result-shaped inputs
also exercise text/confidence defaults. Ruff passes. This is focused source and
native argument acceptance, not a live daemon, deployed model, full-gate,
coverage, or exact-SHA hosted release proof. The unchanged 85/75 coverage floors
and remaining S98.4 proofs still apply.

The repeated-task and variable-scope concerns are long-lived community issues.
The 2024 [Reuse set of tasks](https://forum.ansible.com/t/reuse-set-of-tasks/4201)
thread describes small repeated collection tasks whose registered results must
remain available to later caller tasks. A maintainer reports organizing shared
task files within a role and selecting them through native include mechanisms.
The 2019 [repeated include_tasks thread](https://forum.ansible.com/t/using-include-tasks-with-the-same-file-several-times-inside-a-role/29753)
reports confusion about repeated includes and caller-provided variables; the
partial example did not establish an engine defect. These reports motivate
checking both callers, both routing modes, and role-specific result mapping.

Ansible's maintained [include_tasks reference](https://docs.ansible.com/projects/ansible/latest/collections/ansible/builtin/include_tasks_module.html)
provides the native task-reuse primitive. Its [search-path documentation](https://docs.ansible.com/projects/ansible/latest/playbook_guide/playbook_pathing.html)
describes role and importing-file context for relative paths. Gludd's choice is a
shared file inside the existing gate role, with an explicit relative include
from verification; it adds no runner, role catalog entry, model API, or daemon.
The actual shared include must remain in the existing native-checker input
closure when fixture groups are copied. Findings inspected 2026-10-13.

## Final native Git task acceptance (2026-10-13)

The native reference/result include now owns branch create/delete and worktree
removal publication after the existing guard and authenticated request. Exact
positive, explicit-false and missing-value result fields, original aliases,
failure messages, direct entrypoints and ordinary 300-second budgets are
preserved. Eighteen new root cases fail before the include exists and pass after
implementation. Shared guarded list/request owners and advisory model invocation
likewise retain their caller-specific arguments, results and conditions.

The real staged jscpd check reports zero new clones before broad replay, without
policy or baseline changes. Both installed Python/Ansible environments pass all
803 Git/native-advisory regression cases. The same four production owners retain
95% combined coverage and each passes the unchanged 75% line/branch floor.
One legacy inline-only default assertion failed after the block/include
refactor; it now resolves actual native task arguments and still requires
development, while retaining the core mutation-default assertion.

These are scoped source receipts. S98.4 remains pending until the full canonical
gate, installed controller health, exact-SHA hosted and deployment proofs pass.
No source commit authorizes master promotion, gate bypass or serving-runtime
replacement.
