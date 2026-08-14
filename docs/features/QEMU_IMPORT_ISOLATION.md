# QEMU Detection Import Isolation

## Problem

The deep-import suite temporarily removed the `general_ludd` package family from
`sys.modules`, imported one module, and then restored only the entries that
existed before the probe. Modules created by the probe remained cached. For
`general_ludd.infra.qemu_detect`, the cache could therefore contain both
`general_ludd.infra` and `general_ludd.infra.qemu_detect` while the restored
top-level `general_ludd` object had no `infra` attribute.

Pytest's dotted monkeypatch resolver correctly follows parent attributes. Every
functional QEMU test that patched `general_ludd.infra.qemu_detect` consequently
failed with `AttributeError`, while tests that used an already imported class or
module reference still passed. The failure was execution-order dependent and did
not represent a QEMU platform-detection defect.

## Contract

The isolated-import probe snapshots only the `general_ludd` entries in Python's
documented [`sys.modules`](https://docs.python.org/3/library/sys.html#sys.modules)
cache. A `try`/`finally` boundary removes that family for the probe, deletes every
new family descendant on exit, and restores every pre-existing family entry. This
preserves the package-to-submodule attribute graph seen by later tests while
leaving normally imported third-party dependencies cached.

The standard-library
[`unittest.mock.patch.dict`](https://docs.python.org/3/library/unittest.mock.html#unittest.mock.patch.dict)
was evaluated first, but it restores the entire mapping. Using it directly for
this prefix-scoped operation evicted newly loaded dependencies and made extension
modules such as NumPy reload. The narrow `sys.modules` mapping operation avoids
that broader side effect; it does not parse or synthesize import names.

QEMU detection semantics remain unchanged: unsupported platforms and
architectures fail closed to `unknown`, missing executables remain unavailable,
and acceleration is enabled only when the existing host checks succeed. The
repair changes test isolation, not production discovery or command execution.

## Long-lived practitioner evidence

- [CPython issue 27515](https://bugs.python.org/issue27515), open since 2016,
  demonstrates that retaining a dotted child in `sys.modules` while deleting its
  parent binding does not make a later import rebuild that binding.
- [CPython issue 24029](https://bugs.python.org/issue24029), open since 2015,
  records the import invariant that a cached child module must also be exposed as
  an attribute of its parent package.
- A [Stack Overflow report from 2011](https://stackoverflow.com/questions/6048786/from-module-import-in-init-py-makes-module-name-visible)
  describes duplicate or unreachable module objects when code manipulates
  `sys.modules` without maintaining the corresponding package attributes.

These reports support using Python's documented import registry and guaranteed
`finally` cleanup instead of implementing another import mechanism or name parser.

## Security, resources, and observability

The context is process-local and permits no external module names, paths, or
commands. It preserves QEMU's fail-closed runtime decisions and cannot make an
untrusted binary executable. The snapshot is bounded by the current `general_ludd`
module family, is released at function exit, and creates no workers, retries,
files, or persistent services. Deterministic regressions assert that both QEMU
descendants and the parent attribute are absent after a probe and that newly
loaded external dependencies remain cached, so graph pollution is reported at
its source instead of surfacing later as 25 misleading failures or third-party
reload warnings.

## Zero-downtime rollout and rollback

This is test-only infrastructure with no runtime artifact, API, schema, process,
or deployment change. Promotion runs the polluting import immediately before the
QEMU suite, then the adjacent QEMU/Terraform slice under warnings-as-errors. It is
safe alongside a serving deployment and requires no restart. Rollback is a single
test-harness revert; the unchanged QEMU detector remains deployable throughout.

## Verification

The exact gate ordering must pass the isolated QEMU import followed by the full
QEMU detection file. The isolation regression, all isolated-import parameters,
the QEMU/Terraform adjacency, branch coverage floors, Ruff, strict type checking,
docstring, Markdown, specification, task, and repository collection gates are the
release evidence for this contract.
