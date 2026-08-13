# FileStore Standard-Library Migration

Date: 2026-07-29

## Decision

Gludd's `FileStore` uses `pathlib`, `shutil`, and `os.walk` for its local-disk
backend. The public `FileStore` API and on-disk layout are unchanged. No data
migration or service interruption is required, so rolling instances can use
the old and new implementation against the same files during a zero-downtime
deployment.

PyFilesystem2 was unnecessary for this use case because Gludd does not expose
its alternate FTP, ZIP, S3, or memory backends. Removing it also removes its
runtime import of deprecated `pkg_resources`; Gludd no longer suppresses that
warning or pins an old setuptools solely to preserve the import.

## Long-Lived Upstream and User Reports

- The [PyFilesystem2 release list](https://github.com/PyFilesystem/pyfilesystem2/releases)
  still identifies 2.4.16 as the latest release and shows no newer supported
  migration away from `pkg_resources`.
- A [Panoramax maintainer report](https://gitlab.com/panoramax/server/api/-/issues/307)
  describes the same PyFilesystem2 warning, lack of maintenance, and observed
  performance costs. The maintainers similarly considered removing the
  abstraction when only a small operation set was used.
- The [Python packaging community discussion](https://discuss.python.org/t/pkg-resources-removal-how-to-go-from-there/106079)
  documents the ecosystem impact of `pkg_resources` removal and why retaining
  an old setuptools is only a compatibility workaround.
- The [setuptools migration documentation](https://setuptools.pypa.io/en/stable/deprecated/pkg_resources.html)
  marks `pkg_resources` deprecated and directs projects to modern APIs.

These reports favor deleting the obsolete dependency over adding another
filesystem abstraction. `fsspec` or OpenDAL would only be justified if Gludd
later adds a remote-object-store backend.

## Compatibility and Security Contract

- Main-store writes continue to override same-name overlay files.
- The overlay cannot provide anything under `binaries/`.
- Reads, writes, copies, moves, directory walks, and removals resolve symlinks
  and fail closed when the resolved target is outside the configured root.
- Directory listings retain overlay-first de-duplication and directories-first
  sorting.
- `close()` remains available as a no-op because every standard-library file
  handle is scoped to one operation.

## Verification

The regression suite imports FileStore in an isolated interpreter with
`DeprecationWarning` promoted to an error. CRUD, overlay, bootstrap,
symlink-confinement, traversal, replay, lint, type checking, package locking,
and the complete CI shard suite are release gates.
