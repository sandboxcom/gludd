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
- The long-running custom-module discussion
  [“Custom Module Output parsing”](https://forum.ansible.com/t/custom-module-output-parsing/25196)
  reinforces the standard distinction used here: `exit_json` is for an actual
  successful result, while operational failures must use `fail_json`.

Together, these reports support a fail-closed rule: check mode should be
predictive and non-mutating, while a normal run must never turn an unavailable
backend into green orchestration evidence.

## Verification

`tests/unit/test_gludd_mcp_tool_module.py` covers the live request shape,
transport failure, handler failure, malformed responses, and check-mode
network isolation. `tests/unit/test_ansible_executable_stub_checker.py`
exercises detection, false-positive exclusions, Searx ownership boundaries,
and the repository-wide zero-finding invariant.
