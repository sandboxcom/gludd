# Molecule scenario contract

Status: implemented for repository-owned scenarios and enforced by the deep
structural suite. Release-gate evidence is tracked in `TASKS.md` as S83.51.

## Problem

The release gate found three different concerns collapsed into one brittle
structural rule. Two active scenarios omitted lifecycle metadata, canonical
`ansible.builtin` action names were rejected even though they are FQCNs, and
repository-owned shared cleanup or destroy playbooks were rejected because
their paths were not under each scenario's `default` directory.

That rule encouraged dummy playbooks and less explicit module names. It also
failed to distinguish an intentionally inactive discovery scenario from an
active scenario whose test sequence was accidentally incomplete.

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

## Zero-downtime, security, and resource boundary

This change affects test orchestration metadata only. It starts no service,
opens no port, mutates no database, and changes no deployed request path. The
new syntax stages execute before create or converge, so malformed active
content fails before any scenario resource is allocated. Existing cleanup and
destroy playbooks retain their scenario namespaces and rollback behavior.

The shared-playbook rule is fail closed: arbitrary absolute paths, traversal,
and untracked shared files are not accepted. The deep suite runs in one bounded
pytest process and emits per-test progress; it does not add workers or retain
Molecule state.

## Practitioner evidence

An early Molecule user report shows a delegated scenario intentionally
registering a non-default converge filename in `provisioner.playbooks`. It is
direct evidence that configured playbook paths are part of Molecule's public
scenario contract rather than an implementation detail:

- [ansible/molecule issue #1292](https://github.com/ansible/molecule/issues/1292)

A later report documents Molecule 25 changing role-path behavior and breaking
previously green CI across multiple users. The thread includes pinned-version
and explicit-path workarounds, demonstrating why Gludd validates the resolved
tracked path instead of assuming one release's directory convention:

- [ansible/molecule issue #4391](https://github.com/ansible/molecule/issues/4391)

Ansible's maintained FQCN rule explicitly recommends `ansible.builtin` for
internal actions because canonical names avoid ambiguity and redirect overhead:

- [Ansible FQCN rule](https://docs.ansible.com/projects/lint/rules/fqcn/)

## Verification

- `tests/unit/test_molecule_playbooks_deep.py` validates all registered
  scenario YAML, lifecycle ordering, action identity, and configured paths.
- `make yaml-lint` validates repository YAML without warning suppressions.
- `make ansible-syntax` validates playbook syntax before release.
- The full release gate remains authoritative for promotion.
