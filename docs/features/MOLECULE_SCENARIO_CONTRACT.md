# Molecule scenario contract

Status: implemented for repository-owned scenarios, including daemon-backed
scenarios, and enforced by the deep structural and lifecycle suites.

## Problem

The release gate found three different concerns collapsed into one brittle
structural rule. Two active scenarios omitted lifecycle metadata, canonical
`ansible.builtin` action names were rejected even though they are FQCNs, and
repository-owned shared cleanup or destroy playbooks were rejected because
their paths were not under each scenario's `default` directory.

That rule encouraged dummy playbooks and less explicit module names. It also
failed to distinguish an intentionally inactive discovery scenario from an
active scenario whose test sequence was accidentally incomplete.

The 2026-08-20 GitHub Actions run 32437385366 exposed a second lifecycle gap:
15 scenarios proved the daemon healthy during prepare, but converge received
connection refused after Molecule completed the prepare play. Fixed shared
ports, process ownership split across playbook stages, and missing cleanup or
destroy mappings made the failure order-dependent and left orphan processes on
cancelled jobs.

## Behavioral contract

1. Every repository scenario has a parseable `molecule.yml` document.
2. Active scenarios with an explicit `test_sequence` have a non-empty sequence
   containing `syntax`; prepare precedes converge and converge precedes verify
   whenever both stages exist.
3. The canonical `default` discovery anchor and the `travel` placeholder are
   the only inactive scenarios with an explicit empty sequence.
4. A dotted task action, including `ansible.builtin.*`, is an FQCN module call.
   Role includes and imported playbooks remain valid orchestration boundaries.
5. Provisioner playbooks may use either the scenario-local
   `default/<action>.yml` path or the exact repository-owned
   `${MOLECULE_PROJECT_DIRECTORY}/molecule/shared/<action>.yml` path. A shared
   path is valid only when that canonical tracked file exists.
6. Scenarios that intentionally omit prepare, converge, verify, or the scenario
   key are named in explicit exception sets. No empty or dummy playbook is
   created merely to satisfy a filename assertion.
7. Daemon-backed scenarios acquire their daemon in the first normal converge
   task, notify a stop handler, and set `force_handlers: true`. Starting in
   `pre_tasks` is forbidden because Ansible flushes notified handlers at the end
   of that section, before ordinary tasks execute.
8. The shared start play requests kernel-assigned loopback port `0` and receives
   an atomic ready-file lease containing the exact instance ID, PID, host, port,
   and base URL. Every FQCN module or included role receives that discovered URL
   and its explicit PSK; ambient fixed ports are not part of the contract.
9. Shared stop, cleanup, and destroy playbooks validate scenario-namespaced
   lifecycle files before signalling their owned PID. The daemon also has a
   bounded self-termination lease, so success, failure, and cancelled-controller
   paths all converge on cleanup.
10. An ephemeral controller fixture is acquired once per converge. A scenario
    must not claim `idempotence` or `side_effect` support when those stages would
    run without the owned fixture.

## Zero-downtime, security, and resource boundary

The daemon lifecycle is test-only and loopback-only. Kernel-assigned ports avoid
cross-project collisions, while scenario-name-derived files make ownership
observable without sharing a global process registry. The ready file is written
only after the socket binds, and consumers read the published endpoint instead
of racing a sleep or retry loop.

Rollback is layered and idempotent. A converge handler owns the normal and
failed-task path; Molecule cleanup and destroy own repeated invocation and
pre/post-sequence paths; the bounded daemon lease owns controller cancellation.
Each layer validates the instance namespace and PID record, tolerates an already
stopped process, and removes only its own lifecycle files. Mock worktrees and
reload candidates are restricted to private `/tmp` roots and fail closed on
path escape or ambiguous source layouts.

The shared-playbook rule is fail closed: arbitrary absolute paths, traversal,
and untracked shared files are not accepted. The deep suite runs in one bounded
pytest process and emits per-test progress; it does not add workers or retain
Molecule state.

## Practitioner evidence

An early Molecule user report, opened 2018-05-17 and reviewed 2026-08-20, shows a delegated scenario intentionally
registering a non-default converge filename in `provisioner.playbooks`. It is
direct evidence that configured playbook paths are part of Molecule's public
scenario contract rather than an implementation detail:

- [ansible/molecule issue #1292](https://github.com/ansible/molecule/issues/1292)

A later report, opened 2025-02-03 and reviewed 2026-08-20, documents Molecule 25
changing role-path behavior and breaking previously green CI across multiple
users. The thread includes pinned-version and explicit-path workarounds,
demonstrating why Gludd validates the resolved tracked path instead of assuming
one release's directory convention:

- [ansible/molecule issue #4391](https://github.com/ansible/molecule/issues/4391)

Molecule's maintained constants define cleanup and destroy at both ends of the
default test sequence. Reviewed 2026-08-20, this is the upstream basis for
mapping real shared cleanup and destroy playbooks instead of accepting the
missing-playbook warnings seen in the failed CI run:

- [Molecule default sequence constants](https://github.com/ansible/molecule/blob/main/src/molecule/constants.py)

Ansible documents that `force_handlers` runs notified handlers even after a
later task fails. Reviewed 2026-08-20, this is the supported failure-path seam
for stopping a scenario-owned daemon without warning suppression or retries:

- [Ansible handlers and failure](https://docs.ansible.com/projects/ansible-core/devel/playbook_guide/playbooks_error_handling.html#handlers-and-failure)

Ansible's maintained FQCN rule explicitly recommends `ansible.builtin` for
internal actions because canonical names avoid ambiguity and redirect overhead:

- [Ansible FQCN rule](https://docs.ansible.com/projects/lint/rules/fqcn/)

## Verification

- `tests/unit/test_molecule_playbooks_deep.py` validates all registered
  scenario YAML, lifecycle ordering, action identity, and configured paths.
- `tests/unit/test_molecule_mock_daemon_lifecycle.py` validates namespaced
  allocation, readiness publication, handler timing, explicit endpoint
  forwarding, and cleanup/destroy coverage for every daemon-backed scenario.
- `tests/unit/test_mock_daemon_server.py` validates the bounded control-plane
  facade used by the real collection modules.
- `make yaml-lint` validates repository YAML without warning suppressions.
- `make ansible-syntax` validates playbook syntax before release.
- The full release gate remains authoritative for promotion.
