# Ansible Executable Hardening

Status: active hardening work  
Scope: shipped `general_ludd` collection modules and roles, excluding every
Searx-owned path

## Problem

An executable collection path must not report success when it only emits a
placeholder, logs the name of an API, or returns `not_implemented`. Those
results are especially hazardous in orchestration because later tasks treat a
green task as evidence that the requested operation happened.

The first repaired path was
`general_ludd.agent.gludd_mcp_tool`. The daemon already exposes a bounded,
authenticated `/api/dispatch` route backed by its live MCP client, while the
Ansible module still returned `failed=false` and `not_implemented=true`
without attempting the call.

## Implemented contract

`gludd_mcp_tool` now:

- sends exactly one `{kind: mcp, name: server/tool, args: ...}` request through
  the shared standard-library `GluddClient`;
- bounds the caller-selected timeout to 1–300 seconds;
- rejects transport errors, non-200 HTTP responses, malformed envelopes, and
  dispatcher or handler failures through `fail_json`;
- returns the MCP handler output with `changed=false`; and
- makes no network request in check mode, but returns the exact planned call
  in a stable result shape.

The repository-specific checker in
`scripts/check_ansible_executable_stubs.py` scans executable collection paths
mechanically. It excludes tests, Molecule fixtures, documentation, and every
path component containing `searx`, and reports only conservative structural
signatures such as a successful `not_implemented` result or a role whose only
runtime behavior is naming a service API in `debug`.

The networking dissector role also had a green C-language branch that only
logged “not yet supported,” and its rescue block suppressed template errors.
The role now renders real Lua or native Wireshark C source with validated
identifier, field, offset, size, and UDP-port inputs. Both templates use
Wireshark's registered dissector APIs, template failures propagate normally,
and the receipt omits wall-clock-only content so a repeated identical render
stays idempotent.

The Windows automation role now reconciles scheduled tasks with
`community.windows.win_scheduled_task` and unattended answer files with
`ansible.windows.win_template`. Inputs are bounded and validated before
mutation, native module failures propagate, and sensitive answer-file render
details are hidden. Its PSRemoting and DSC mutation scripts now declare
PowerShell `SupportsShouldProcess`, predict changes before mutation, and no
longer suppress operational failures.

The 15 AI/ML service roles now share one live request composition role. Each
wrapper maps to an allowlisted `ExpertTask`, validates request and resource
bounds, and calls the authenticated `/api/ai_ml/query` endpoint through
`ansible.builtin.uri`. Normal runs validate the response before publishing it;
check mode performs no network I/O and publishes the same deterministic
request plan that a normal run would submit.

The 15 chemistry service roles now share a typed request composition role over
the existing `general_ludd.chemistry.chemistry_operation` module. Each wrapper
maps to a valid chemistry task kind, validates collection and payload bounds,
derives a stable request identity, and uses the module's idempotency key and
check-mode contract. A normal run publishes only a validated service result;
check mode publishes the complete non-mutating request plan.

All 16 materials roles now execute their existing typed service logic through
`general_ludd.materials.materials_operation` and the authenticated
`/api/materials/resolve` endpoint. This also replaces five older paths that
copied inputs into files labeled as verdicts. The allowlisted adapter delegates
to the existing materials modules, rejects booleans and non-finite engineering
numbers, bounds lists, payloads, quantities, and timeouts, validates full
simulation-plan contracts, derives stable route IDs, and uses idempotent replay.
Every role remains default-off and check mode returns the exact operation plan
without contacting the daemon.

Seven governance lookup roles already ran their packaged Python module
utilities with `ansible.builtin.command`, but each unconditionally replaced
Ansible's nonzero-return handling with `failed_when: false`. They now retain
the mature command module's normal failure propagation: successful read-only
lookups remain `changed=false`, while missing interpreters, invalid utility
inputs, and runtime faults stop the play instead of publishing a default empty
verdict.

The 11 previously debug-only Git Release Captain roles now call one typed
`git_release_operation` module over the authenticated
`/api/git_release/resolve` endpoint. Helper roles execute the existing
discovery and ranking engine; artifact roles build and verify the existing
CycloneDX/in-toto provenance records from bounded repository-contained files;
and deployment consumes the existing canary/rolling/blue-green health-gate
orchestrator. Recovery and release-planning roles publish evidence-derived
proposals with `mutation_performed=false`, so a proposal is never mislabeled
as completed Git or release work. Requests are idempotently replayed, check
mode performs no network access, missing telemetry holds promotion, and every
role remains default-off until explicitly enabled.

The dogfood playbook suite no longer treats filenames as sufficient smoke
coverage. Quality gates and task validation run bounded allowlisted Make
targets; Molecule runs one bounded scenario; dependency locking uses `uv` and
then requires lint/type checks; release artifacts are checksummed; pip bundles
use `ansible.builtin.pip`; runtime and Podman image state are probed before
work; and gap analysis, log auditing, and reloads call their authenticated
daemon endpoints. Self-improvement now requires a real repository and todo
instead of writing a fallback success artifact. Every normal-run failure
propagates, while network and mutation paths publish deterministic plans in
check mode without executing.

## Long-lived practitioner evidence

This contract follows problems Ansible practitioners have reported for more
than a decade:

- In the 2013 Ansible Project thread
  [“Adding --check mode to core modules”](https://groups.google.com/g/ansible-project/c/v638okDSzvo),
  maintainers explained that declaring check-mode support is insufficient by
  itself: a module must branch before mutation and return an accurate changed
  prediction.
- In the 2014 thread
  [“variables in --check mode”](https://groups.google.com/g/ansible-project/c/xF-v6edS-EQ/m/5Bmr1potodEJ),
  operators described downstream failures when skipped tasks did not populate
  registered data. That is why this module returns a stable planned-call
  schema instead of allowing Ansible to skip it implicitly.
- A 2017 practitioner thread,
  [“How to avoid error when using a registered variable in check mode?”](https://groups.google.com/g/ansible-project/c/H1JwC9i4y3U),
  shows the recurring failure when a skipped task leaves its registered result
  unavailable to dependent tasks. The shared AI/ML role separates its normal
  response validation from check mode and publishes a complete planned result
  instead of dereferencing a skipped `uri` result.
- The long-running custom-module discussion
  [“Custom Module Output parsing”](https://forum.ansible.com/t/custom-module-output-parsing/25196)
  reinforces the standard distinction used here: `exit_json` is for an actual
  successful result, while operational failures must use `fail_json`.
- A 2018 Ansible forum thread about
  [running a scheduled task every minute](https://forum.ansible.com/t/win-scheduled-task-run-the-scheduled-task-every-minute/26741)
  records the fragility of building idempotence around `schtasks` or COM
  commands. The repaired role therefore delegates reconciliation to the
  maintained `win_scheduled_task` module instead of adding another shell
  wrapper.
- The long-lived Stack Overflow discussion
  [“Why is bool a subclass of int?”](https://stackoverflow.com/questions/8169001/why-is-bool-a-subclass-of-int)
  documents the Python compatibility behavior that lets `True` pass ordinary
  integer checks. The materials boundary therefore rejects booleans before
  accepting engineering numbers, preventing `True` from silently becoming a
  load, tolerance, quantity, or process parameter.
- In the 2015 Ansible Project thread
  [“Ignoring errors in check mode”](https://groups.google.com/g/ansible-project/c/PqthqyRrIuM/m/z_Tuy0fSJQAJ),
  practitioners noted that blanket error suppression also discards real
  normal-run failures. The governance roles therefore preserve
  `changed_when: false` for read-only execution without disabling the command
  module's nonzero-return failure contract.
- The 2017 Ansible Project discussion
  [“Output Failed even if command gets executed successfully”](https://groups.google.com/g/ansible-project/c/_NnsHPwj5kU)
  distinguishes an expected domain return code from an interpreter or command
  failure. The dogfood playbooks therefore use narrow, explicit return-code
  contracts only for read-only existence probes and propagate every other
  failure.
- In the 2013 thread
  [“Abort entire run at one task failure”](https://groups.google.com/g/ansible-project/c/MU_ws7zynnI),
  operators explicitly rejected blanket `ignore_errors` when later tasks must
  not run after a failed prerequisite. Validation and Molecule playbooks now
  write success evidence only after their real commands complete successfully.

Together, these reports support a fail-closed rule: check mode should be
predictive and non-mutating, while a normal run must never turn an unavailable
backend into green orchestration evidence.

## Verification

`tests/unit/test_gludd_mcp_tool_module.py` covers the live request shape,
transport failure, handler failure, malformed responses, and check-mode
network isolation. `tests/unit/test_ansible_executable_stub_checker.py`
exercises detection, false-positive exclusions, Searx ownership boundaries,
the named dogfood playbooks, and the repository-wide zero-finding invariant.
The Git-release service, module, router, and role-structure suites verify real
helper/provenance/ZDD dispatch, path and resource bounds, idempotent replay,
failure propagation, and check-mode network isolation.
