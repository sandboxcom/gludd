# Python 3.14 filestore import-order incident

**Date:** 2026-08-20  
**Status:** Resolved in the `0.1.0-beta.4` compatibility line

## Impact and reproduction

A Python 3.14 test worker could validate an invalid renderer schema and then
fail while importing the event loop. The failing import chain was:

```text
general_ludd.event_loop.loop
  -> general_ludd.filestore.store
  -> fs.osfs
  -> fs.opener.parse
  -> six.moves.urllib.parse
```

The two EventLoop wiring checks passed alone but failed after the renderer
schema-validation failure path with `ModuleNotFoundError` for
`six.moves.urllib`. The focused regression keeps both tests in one xdist group
so the test boundary and interpreter state are exercised deterministically.

## Root cause and dependency decision

Gludd used PyFilesystem2 only for `OSFS`, and `FileStore` has always exposed a
local-disk contract. PyFilesystem2 2.4.16 imports URL parsing through the
synthetic `six.moves` meta-path package. In the observed Python 3.14 test order,
the next test boundary could no longer resolve that pseudo-package. Importing
the EventLoop therefore depended on unrelated earlier imports.

The store now uses the standard-library `pathlib`, `os`, and `shutil` APIs. Its
public CRUD, overlay, metadata, tree, copy, move, and no-op `close()` contract
is unchanged. Symlink and traversal checks remain fail-closed. The direct `fs`
dependency, its `appdirs` closure, and warning suppressions specific to `fs`
were removed. No replacement package was added: `fsspec` would preserve an
abstraction Gludd does not use, while the standard library is the mature local
filesystem implementation.

## Long-lived upstream and practitioner evidence

- PyFilesystem2
  [2.4.16 was released on 2022-05-02](https://github.com/PyFilesystem/pyfilesystem2/releases/tag/2.4.16)
  and remains the latest published release as of 2026-08-20.
- The upstream
  [`pkg_resources` removal PR](https://github.com/PyFilesystem/pyfilesystem2/pull/590)
  opened on 2024-09-16 and was still open on 2026-08-20. Practitioner comments
  continued through 2026, including requests for an installable workaround;
  the thread records 22 reactions on the original report and later downstream
  migrations away from PyFilesystem2.
- The related upstream
  [runtime-warning report](https://github.com/PyFilesystem/pyfilesystem2/issues/597)
  opened on 2025-06-17 and remained open on 2026-08-20. It shows that merely
  importing `fs==2.4.16` relies on deprecated package machinery.
- Six's
  [meta-path importer issue](https://github.com/benjaminp/six/issues/341),
  opened on 2020-11-21, documents how its synthetic modules needed newer
  import-protocol support. The current
  [`six.moves` implementation](https://github.com/benjaminp/six/blob/main/six.py)
  still constructs URL modules through `_SixMetaPathImporter`, including
  Python 3.14-specific URL handling.

Together, these dated reports show a multi-year compatibility boundary rather
than an isolated Gludd test quirk. Removing the unused abstraction is safer
than pinning setuptools, preloading pseudo-modules, or patching `sys.modules`.

## Zero-downtime deployment and rollback

There is no data migration or file-layout change. Existing and upgraded Gludd
workers can read and write the same store concurrently, so deployments may
replace workers incrementally behind the health gate. No ports, subprocesses,
temporary artifacts, or services are added. Import startup does less work and
the environment contains two fewer packages (`fs` and `appdirs`).

Rollback is the inverse application-package deployment: restore the prior
Gludd wheel and lock file while leaving the filestore directory untouched.
Because both implementations use the same on-disk paths and bytes, rollback
does not require conversion or downtime. If an upgraded worker fails its
health check, keep serving from old workers, remove that worker, and retain the
shared store unchanged.

## Verification contract

- The Python 3.14 schema-to-EventLoop regression runs with warnings as errors
  in one worker group.
- FileStore unit, overlay, traversal, and symlink suites verify the preserved
  API and fail-closed boundary.
- The original mixed batch verifies that both order-dependent EventLoop nodes
  remain green in their prior neighborhood.
- Focused coverage must be at least 85 percent aggregate and at least 75
  percent for every changed production file.
