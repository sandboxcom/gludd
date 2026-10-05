# discover_accelerators

Loads Gludd's read-only, provider-neutral accelerator inventory into
`ansible_facts.gludd_accelerators` and the convenience fact
`gludd_accelerators`. The `local`, `slurm`, or `all` scope is selected through
`gludd_accelerator_scope`; no compute is provisioned by this role.

Hardware model names are observed values in the `resources` collection. They
are deliberately not variable names or schema keys, so new Apple, Intel,
NVIDIA, AMD, TPU, or scheduler resource types require no role change.
