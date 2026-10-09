# Native Materials Tolerance Analysis

S43 replaces the daemon-backed path behind the materials `tolerance_model`
role with a collection-owned controller action. The same implementation also
backs the Python compatibility API and the materials operation adapter, so the
six calculations cannot drift across entry points.

## Native boundary

The action accepts exactly `operation` and `request`. Its allowlist is:

| Operation | Result |
|---|---|
| `worst_case` | Simultaneous-extremes nominal and bilateral band. |
| `rss` | One-sigma root-sum-square band for independent contributors. |
| `thermal` | Free thermal-expansion delta. |
| `thermal_compensation` | Opposite room-temperature compensation. |
| `process_capability` | Cp and Cpk from declared limits, sigma, and optional mean. |
| `assembly` | Worst-case clearance/interference envelope. |

Requests and results may contain at most 64 KiB of strict JSON. A dimensional
chain contains at most 256 two-number pairs and a unit label contains at most
32 characters. Booleans, NaN, infinities, non-string keys, malformed pairs,
unknown fields, and unsupported operations fail closed. Computed output is
recursively checked for finite numbers before it is returned.

RSS always records the assumptions `independent contributors (no correlation)`,
normal distribution, and statistical process control. The interface rejects
every covariance or correlation field. That is intentional: this bounded
calculator has no covariance model and must not silently turn correlated
measurements into an optimistic independent result. It does not add the Python
`uncertainties` package or imply confidence beyond the existing one-sigma
equation.

The implementation is a pure Python calculation. It owns no URL, socket, file,
subprocess, listener, worker, cache, secret, or persistent state. Normal and
check mode execute the identical function and report `changed: false`; the
companion module fails closed if Ansible bypasses the controller action.

## Long-lived user evidence

The collection layout and explicit assumptions respond to recurring user
problems rather than a theoretical migration:

- The Ansible forum's 2018
  [action-plugin/module-utils discussion](https://forum.ansible.com/t/two-questions-related-to-action-plugins/28059)
  records that user-supplied role-local `module_utils` were available to
  modules but not action plugins. A 2024
  [follow-up](https://forum.ansible.com/t/what-is-a-proper-way-to-use-module-utils-in-action-plugins/11011)
  reaches the practical answer: put the shared utility in a collection. S43
  therefore uses one collection-qualified import from both the action and the
  core compatibility surface, with no path injection.
- A 2022 AskEngineers
  [tolerance-stack practice discussion](https://www.reddit.com/r/AskEngineers/comments/usqr00/how_do_everyone_do_tolerance_stack_up_analysis_at/)
  shows experienced users disagreeing when RSS is appropriate, especially for
  low-volume or safety-critical work, and emphasizing process distributions.
  Another 2020
  [worst-case discussion](https://www.reddit.com/r/AskEngineers/comments/gvzczn/gdt_tolerance_stackup_worst_case_scenario/)
  stresses that acceptable risk depends on volume and failure severity. S43
  exposes worst case and RSS as separate operations and makes the RSS
  independence assumption machine-visible instead of choosing for the user.
- The University of Washington's longstanding
  [Tolerance Stack Analysis Methods](https://faculty.washington.edu/fscholz/DATAFILES/Rtolerancing/TOLSTACK.pdf)
  explains the independence and process-control caveats behind statistical
  stacking. S43 refuses correlation data it cannot honor and returns a
  traceable equation and assumptions with every result.

## Shared implementation and compatibility

The canonical source is
`collections/ansible_collections/general_ludd/materials/plugins/module_utils/tolerance_model.py`.
`general_ludd.materials.tolerance` is an identity-preserving re-export. The
legacy materials operation adapter removes its `analysis` routing key and
delegates the remaining request to the same evaluator. There is no copied
equation implementation.

## Digest-addressed zero-downtime delivery

Build the materials collection into one immutable controller execution
environment and record its image digest. Run the dedicated
`materials_tolerance_model` Molecule syntax, prepare, converge, idempotence,
and verify phases against a canary controller using that exact digest. Admit a
bounded share of new analyses only after the canary returns byte-identical
normal/check-mode evidence and the expected explicit RSS assumptions.

For the deployment wave, stop admitting new work to one old controller, drain
its in-flight calculations, replace it with the candidate digest, verify it,
and then continue controller by controller. Old and new controllers may serve
concurrently because the action mutates no input, managed host, schema, or
durable state.

Rollback first stops new admission to the candidate, drains its in-flight
calculations, and routes new work to the previously verified immutable digest.
No database, file, daemon, or managed-host repair is needed. Receipts already
issued remain bound to the implementation assumptions they contain.

## Verification

Focused tests cover all six operations, exact allowlists, independent-RSS
evidence, request/result bounds, dimension and unit ceilings, non-finite inputs
and outputs, correlation rejection, no-I/O execution, check-mode parity,
module-bypass failure, core export identity, service delegation, role wiring,
the unique Molecule scenario, and executable-module hygiene. Scoped branch
coverage must remain at least 85% aggregate and 75% for every measured file.
