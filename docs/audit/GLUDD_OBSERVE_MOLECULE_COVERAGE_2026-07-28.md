# `gludd_observe` Molecule Coverage — 2026-07-28

## Outcome

`molecule/playbooks/test_gludd_observe` now executes the real
`general_ludd.agent.gludd_observe` collection module against the reusable local
mock daemon. Only the daemon and its connector backends are simulated; Ansible
module loading, capability enforcement, HTTP requests, response handling,
fact injection, correlation, and error isolation all run unchanged.

The scenario covers:

- `query_sources`, including trace grouping;
- a bounded `timeline`;
- JSON-safe service/host `topology`;
- `correlate_incident`, including the incident seed;
- isolation of an intentionally unavailable source while two healthy sources
  still return data; and
- exact HTTP evidence: four discovery calls and twelve source-query calls.

## Community evidence and design response

Long-lived Ansible and Molecule user reports show that implicit discovery and
delegated inventory assumptions are fragile:

- [Molecule issue #1292](https://github.com/ansible/molecule/issues/1292)
  documents delegated-driver inventory and connection details that users had
  to supply themselves. The scenario therefore declares a concrete localhost
  platform and local connection rather than relying on implicit inventory.
- [Molecule issue #4391](https://github.com/ansible/molecule/issues/4391)
  records a Molecule 25 role-discovery regression and path-based workarounds.
  The scenario sets its collection path explicitly and limits its test matrix
  to playbooks it actually provides.
- [Ansible forum: role in collection cannot find module in the same
  collection](https://forum.ansible.com/t/role-in-collection-cannot-find-module-in-the-same-collection/45676)
  identifies `COLLECTIONS_PATH` placement as the key discovery requirement.
  `ANSIBLE_COLLECTIONS_PATH` points directly at this checkout's `collections`
  directory, while `PYTHONPATH` points at `src` for the module's application
  imports.

These choices keep the scenario independent of a developer's global Ansible
installation and avoid downloading or installing collection copies during a
test.

## Zero-downtime and isolation properties

- The HTTP server binds only to `127.0.0.1` on the scenario-specific port
  `8898`.
- Cleanup runs before and after the scenario and removes its PID, log, and
  result files.
- All temporary files include the scenario port, allowing unrelated project
  scenarios to run without sharing process state.
- The module uses the existing `molecule_observe_probe` local-only capability
  grant; no remote observability host is authorized.
- Read-only tasks report no changes, and Molecule's idempotence phase passes.

## TDD and verification evidence

The initial focused test failed with:

```text
modules with no scenario and not on the checklist: ['gludd_observe']
```

After implementation:

```text
tests/integration/test_molecule_coverage.py: 15 passed
Molecule test matrix: cleanup, syntax, prepare, converge, idempotence,
verify, cleanup
Molecule executed 1 scenario (1 successful)
```

The verification play also asserts the exact source count, record grouping,
bounded ordering, topology adjacency, source-error redaction, incident group,
and daemon request counts.
