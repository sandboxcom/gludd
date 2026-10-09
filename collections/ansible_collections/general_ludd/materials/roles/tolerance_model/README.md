# Tolerance model role

`general_ludd.materials.tolerance_model` runs one deterministic tolerance
analysis inside the Ansible controller. It never delegates to a daemon or
managed host.

## Variables

| Variable | Contract |
|---|---|
| `tolerance_model_enabled` | Must be explicitly `true`. |
| `tolerance_model_operation` | One of `worst_case`, `rss`, `thermal`, `thermal_compensation`, `process_capability`, or `assembly`. |
| `tolerance_model_request` | Operation-specific strict-JSON request, at most 64 KiB. |
| `tolerance_model_result_fact` | Optional valid Ansible fact name. |

Dimension chains accept no more than 256 `[nominal, tolerance]` pairs and one
unit label of at most 32 characters. Every number and computed output must be
finite. RSS is explicitly an independent-contributor calculation; covariance
or correlation input is rejected rather than silently discarded. Normal and
check mode execute the same pure function and return `changed: false`.

## Zero-downtime delivery

Publish the collection in a digest-addressed execution-environment candidate,
run the dedicated `materials_tolerance_model` Molecule scenario on a canary,
then admit new analyses to that immutable digest. Drain each old controller's
in-flight analyses before moving it. Rollback routes new work to the prior
verified digest; the action has no file, network, listener, process, or durable
state to reconcile.
