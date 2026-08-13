# Ansible Production Lint Contract

Status: beta4 release contract

## Contract

`make yaml-lint` runs the ansible-lint production profile against project
playbooks and the complete agent-role tree with the repository collection path
first. A release candidate requires zero fatal findings and zero warnings; an
installed user collection must not shadow the checkout under test.

Role metadata declares Ansible 2.14 compatibility and Galaxy-safe tags. Role
variables use feature namespaces. `model_evaluate_tasks` and
`model_serve_port` remain compatible with legacy externally supplied
`tasks` and `port` values while avoiding reserved-name declarations.

## Determinism, ZDD, and security

Git-backed build inputs are immutable during a deployment. The model quantizer
pins the verified llama.cpp `b10375` tag and uses the current
`ggml-org/llama.cpp` repository. Single commands use
`ansible.builtin.command`; shell remains only where pipelines, backgrounding,
or multiple commands are required.

Cleanup and health-probe paths express `failed_when` and `changed_when`
instead of blanket `ignore_errors`. Expected absence remains non-fatal, while
successful process termination records a change. Failed or missing diagnostic
logs render an explicit fallback rather than masking an unrelated task error.
These outcomes let rolling deployments retry or roll back from a known state.

## Observability

Every operational task remains named. Health polling is bounded, server
endpoints are reported with their resolved namespaced port, and production lint
prints one terminal failure/warning count. The lint target uses only the
checkout's collection namespace, eliminating duplicate-version ambiguity.

## Verification

The authoritative check is `make yaml-lint`. Role YAML parsing and the
project's integration/molecule suites cover execution wiring; the release gate
covers collection, static analysis, and unit behavior.

## Practitioner evidence

- [Ansible #23121](https://github.com/ansible/ansible/issues/23121) records a
  long-lived user report where reserved variable names generated warnings and
  changed playbook behavior, supporting explicit feature namespaces.
- [ansible-lint #457](https://github.com/ansible/ansible-lint/issues/457)
  captures the multi-year practitioner discussion around fixing lint semantics
  instead of globally suppressing rules.
- [llama.cpp #23771](https://github.com/ggml-org/llama.cpp/issues/23771)
  documents downstream breakage when consumers selected an unpinned latest
  release with an incomplete artifact matrix, supporting an explicit verified
  tag.
