# Native Frictionless Dataset Admission

S40 replaces the `ai_ml.dataset_engineer` role's generic service request with a
controller-local admission boundary. The role calls
`general_ludd.ai_ml.dataset_admit`; its action plugin uses the pinned
`frictionless==5.19.1` Python API and never invokes a CLI, subprocess, network
client, or listener. The remote module exists only to reject any action-plugin
bypass.

## Admission contract

The caller supplies one absolute controller root, one root-confined JSON Table
Schema, and one to 32 root-confined CSV resources. Before Frictionless receives
a path, admission rejects URLs, parent traversal, symlinks, duplicate resources,
devices, sockets, and every non-regular file. It also enforces these hard bounds:

- at most 32 resources;
- at most 512 MiB for the schema and resources together;
- at most 256 schema or reported data fields;
- at most 100 sanitized validation errors; and
- at most 64 KiB for the canonical data card.

All limits are fixed ceilings. Internal test seams may lower but cannot raise
them. Frictionless receives an explicit table type, CSV format, local-file
scheme, validated in-memory schema, safe relative resource path, and the already
validated root as `basepath`. Admission requires one structured validation task.
An empty, malformed, truncated, inconsistent, invalid, or task-error report is
a rejection. Check mode performs the same bounded reads and validation without
writing files or changing state.

The success result is read-only (`changed: false`). It includes a canonical
`gludd.dataset-card/v1` object containing relative paths, byte counts, row and
field counts, the exact Frictionless version, and SHA-256 digests for every
resource and the schema. The `data_card_sha256` is the SHA-256 of sorted compact
UTF-8 JSON. There are no timestamps or controller-absolute paths, so unchanged
inputs yield an identical card. Files are fingerprinted before validation and
rechecked afterward; a concurrent change fails closed.

## Operational evidence from upstream users

The boundary deliberately responds to long-lived Frictionless community
reports instead of depending on source autodetection:

- [Discussion #675](https://github.com/frictionlessdata/frictionlessdata.io/discussions/675)
  confirms that a reusable Table Schema may be stored separately from incoming
  data. Gludd reads the selected local schema itself and constructs a
  `Schema` from its descriptor; it never asks Frictionless to fetch a schema.
- [Discussion #653](https://github.com/frictionlessdata/frictionlessdata.io/discussions/653)
  records years of ambiguity around optional columns and `schema_sync`. Gludd
  does not enable schema synchronization: the supplied schema is authoritative,
  so missing labels and constraint failures remain admission errors.
- [Issue #609](https://github.com/frictionlessdata/frictionless-py/issues/609)
  reports external schema paths being interpreted as invalid metadata. Gludd
  avoids that path/string ambiguity by loading root-confined JSON and passing a
  validated in-memory `Schema` object.
- [Issue #1646](https://github.com/frictionlessdata/frictionless-py/issues/1646)
  reports schema-invalid CSV data receiving a valid file-level report with no
  labels. Gludd forces `type="table"`, `format="csv"`, and `scheme="file"`, then
  requires exactly one table task with coherent validity and statistics.

These reports are design evidence, not claims that every upstream version has
the same behavior. The pin and executable acceptance fixtures define Gludd's
runtime contract.

## Execution-environment rollout and rollback

The AI/ML collection artifact and exact Frictionless requirement are staged in
the controller execution environment (EE) and recorded in the runtime lock.
Build the candidate once, identify it by immutable image digest, and run the
Molecule scenario plus one production-shaped local dataset against that digest.
Canary only new dataset-engineer jobs on the candidate while existing jobs drain
on the prior digest. Compare admission verdicts and card digests, then expand
traffic in bounded steps.

Rollback stops assigning new jobs to the candidate digest, drains its active
jobs, and restores the previous known-good digest. A card admitted by the
candidate remains content-addressed evidence; rollback does not rewrite it.
This feature has no database migration, remote service, schema mutation, or
persistent state transition. The only deployment unit is the digest-addressed
controller EE, so rollback never requires data repair.

## Verification

Unit tests use the real Frictionless API for valid and invalid CSV rows and
inject structured reports only to cover empty, truncated, and task-error edge
paths. They also prove root confinement, resource caps, check mode, sanitized
errors, stable hashes, dependency pins, and module-bypass failure. The
`playbooks/ai_ml_expert` Molecule scenario admits a real fixture, reruns
idempotently, and verifies the persisted receipt and card digest.
