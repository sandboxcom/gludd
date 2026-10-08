# Collection-native chemical lot admission

## Outcome

The `general_ludd.chemistry.inventory_check` role evaluates one explicitly
declared chemical lot with the collection-native
`chemical_lot_admission` module. The evaluator is local, deterministic, and
fail-closed. It replaces the former generic service request, so inventory
validation no longer depends on daemon availability or an HTTP request.

The result never proposes, selects, or names a substitute. An unsuitable lot
returns `requires_review: true` and a bounded subset of these reason codes:

| Code | Meaning |
|---|---|
| `lot_expired` | The strict ISO expiry date precedes the declared evaluation date. |
| `lot_restricted` | At least one declared restriction is present. |
| `lot_purity_insufficient` | Declared purity is below the required purity floor. |

## Fail-closed input contract

- Exactly one non-empty lot identifier, at most 128 characters.
- Zero to 32 restrictions; every restriction is non-empty and at most 128
  characters.
- Actual and required purity are finite numbers in the inclusive range `[0, 1]`;
  booleans, NaN, and infinities are rejected.
- Expiry and evaluation dates must be real calendar dates in exact
  `YYYY-MM-DD` form. Datetimes, padding, and normalized shortcuts are rejected.
- Expiry on the evaluation day remains valid; a prior date is expired.
- Malformed input fails the Ansible task with `changed: false` instead of
  producing a partial verdict.

The implementation imports only Python standard-library validation helpers.
It has no network, URL, filesystem, subprocess, listener, worker, cache, or
persistent-state path. Normal and check mode execute the same function and
always return `changed: false`.

## Collection ownership and compatibility

The canonical implementation is
`plugins/module_utils/lot_admission.py` inside the chemistry collection. The
Ansible module imports it by its collection-qualified package name.
`general_ludd.chemistry.inventory` is only a compatibility re-export, so Python
callers and Ansible callers hold the same class and function objects instead of
drifting implementations.

This layout responds to long-lived ecosystem evidence reviewed on 2026-10-08:

- [ansible/ansible#50579](https://github.com/ansible/ansible/issues/50579),
  opened in 2019, records role-local `module_utils` lookup failures. Gludd uses
  collection-qualified imports and tests the installed collection boundary.
- [ansible/ansible#77935](https://github.com/ansible/ansible/issues/77935),
  opened in 2022, records a runtime break after an internal
  `ansible.module_utils.common.yaml` path disappeared. Gludd imports only the
  public `AnsibleModule` surface from Ansible and owns all evaluator helpers.
- The archived forum thread
  [Cannot get customized facts when pushing with --check](https://forum.ansible.com/t/cannot-get-customized-facts-when-pushing-with-check/17103)
  dates to 2014 and documents that custom modules must explicitly declare check
  mode support. The native module declares `supports_check_mode=True`, and
  behavioral tests require the normal and check-mode payloads to agree.
- The forum discussion
  [Add condition when command/shell modules should return OK](https://forum.ansible.com/t/add-condition-when-command-shell-modules-should-return-ok-not-changed/37686)
  recommends separating read-only tests from update commands and notes that a
  custom module is often the clearer boundary. The role now calls a read-only
  module directly and contains no shell or command task.

## Zero-downtime rollout and rollback

The change is additive at the collection boundary: the native module lands
before the role switches to it, and the core import remains available as an
identity-preserving re-export. Existing Python callers therefore do not need a
flag day. The module owns no daemon, listener, migration, file, or durable
state, so deployment requires no drain and concurrent old/new controllers
cannot corrupt shared state.

Roll out by publishing the collection artifact and then updating controllers
to that exact version. Verify one rejected and one admitted lot in both normal
and check mode before broadening inventory scope. Roll back by pinning the
previous collection artifact; there is no data rollback or cleanup. Any lot
rejected during a rollback window remains rejected until a qualified human
reviews it—automation never compensates by substituting another lot.

## Verification

Focused unit coverage exercises every rejection, combined ordering, exact
bounds, malformed dates/numbers, the no-I/O boundary, check-mode parity, and
core export identity. The `chemistry_expert` Molecule scenario exercises the
role and module without a mock daemon and proves both rejection and admission.

Verification on 2026-10-08 produced the following evidence:

- 27 focused native-admission tests passed with warnings treated as errors;
  the focused native, compatibility, and role set passed all 71 tests.
- The complete chemistry unit slice passed 1,265 tests with 10 explicit skips,
  and the repository collection check found 121,272 selected tests with zero
  collection errors.
- Measured implementation coverage was 95% aggregate: 100% for the evaluator,
  95% for the Ansible module, 100% for the package and inventory compatibility
  files, and 88% for the deduplicated protocol bootstrap. Every measured file
  exceeded the 75% per-file floor.
- The `chemistry_expert` Molecule syntax, prepare, converge, and verify phases
  passed, as did the complete collection test target, Ruff, strict mypy for the
  typed S41 scope, YAML and Markdown lint, and the executable-stub check.
- Collection Python-boundary analysis reported zero findings; the evaluator
  exposes no network, file, subprocess, listener, worker, or state path.
