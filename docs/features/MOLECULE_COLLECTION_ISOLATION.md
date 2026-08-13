# Molecule collection isolation

Status: implemented in the canonical `molecule-test` Make target with a
structural regression test and live scenario verification. Full gate evidence
is tracked in `TASKS.md` as S83.17.

## Failure

Molecule performs collection discovery during its prerun phase, before a
scenario's `provisioner.env` is applied. A developer with
`general_ludd.agent` installed in the user collection directory could
therefore receive a warning that multiple versions were present while Molecule
selected the global copy first. The scenario could pass against stale installed
code instead of the checked-out branch.

## Behavioral contract

1. `make molecule-test SCENARIO=<name>` sets `ANSIBLE_COLLECTIONS_PATH` to
   `$(CURDIR)/collections` on the Molecule process itself.
2. The setting exists before prerun discovery; scenario-local environment is
   still available to its Ansible playbooks but is not trusted to choose the
   collection under test.
3. The canonical `MOLECULE_GLOB` remains unchanged and no scenario source is
   copied or deleted.
4. Sharded and all-scenario targets call the canonical single-scenario target,
   so CI and local runs share the same collection identity.
5. An installed user or system copy cannot make repository changes appear
   green. Missing repository collection content fails rather than falling back.
6. `molecule-reset` invokes scenario-scoped `destroy`; it never copies, deletes,
   or resets the canonical scenario source tree.

## Zero-downtime and resource behavior

This changes only test-process environment and creates no runtime deployment,
database migration, daemon, port, or persistent state. It is safe to roll out
with existing CI workers because each invocation owns its environment. Rollback
is a one-line Makefile revert.

The target does not add a process or worker. Existing scenario cleanup remains
responsible for its exact namespaced mock-daemon PID and ephemeral directory.

## Practitioner evidence

A long-lived Ansible user report from 2021 demonstrates collection resolution
selecting the configured user collection locations while a collection-qualified
playbook could not be found. The report traces the failure into Ansible's
collection finder and shows how search-path identity can diverge from the source
tree a developer expects to execute:

- [ansible/ansible issue #74917](https://github.com/ansible/ansible/issues/74917)

That history is why Gludd pins the checkout collection at process start instead
of accepting a warning or relying on developer-global Ansible state.

## Verification

- `tests/integration/test_molecule_ci_e2e.py` requires the canonical target to
  export the checkout collection path.
- `make check-make-target-contract` validates the target contract.
- The contract's behavioral example,
  `make molecule-test SCENARIO=test_gludd_observe`, must complete syntax,
  prepare, converge, idempotence, verify, cleanup, and destroy without a
  duplicate-collection warning.
