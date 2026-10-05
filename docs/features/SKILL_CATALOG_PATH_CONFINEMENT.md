# Skill Catalog Path Confinement

## Purpose

`SkillCatalog.download_skill` turns a catalog lookup key into a Markdown
filename. A compromised or extended catalog can therefore make the key an
input boundary even though the built-in catalog is curated. Gludd validates
that key before creating the destination directory, then applies the shared
canonical containment check before writing the file.

## Contract

The shared `sanitize_skill_name` primitive accepts only a non-empty,
single-segment conservative filename stem. Separators, parent references, NUL
bytes, and unsupported characters fail closed without filesystem mutation.
After the target directory exists, `is_join_within` resolves both the target
and candidate with `realpath` and compares them with `commonpath`. This second
gate rejects an existing leaf symlink whose canonical destination is outside
the requested directory. Valid curated names retain the existing `Path | None`
API and are written directly below the requested target.

## Practitioner evidence

This feature reuses evidence already maintained by the repository:

- [CPython issue 99334](https://github.com/python/cpython/issues/99334)
  documents that `Path.is_relative_to()` is lexical and does not resolve
  symlinks. The report supports using the repository's mature
  `realpath`/`commonpath` primitive instead of a string-prefix comparison.
- The long-lived Stack Overflow report
  [Git thinks a file within a symlinked directory has been deleted](https://stackoverflow.com/questions/60582087/git-thinks-a-file-within-a-symlinked-directory-has-been-deleted-after-recreating)
  records practical filesystem-identity surprises around directory symlinks.
  It supports treating the canonical destination, rather than its display
  spelling, as the authorization identity.

## Security, resources, and observability

Validation precedes directory creation, so traversal-shaped catalog keys do
not leave partial destination state. Rejection returns `None` and emits a
bounded warning; successful writes retain the existing informational event.
The path gates perform bounded in-process string and filesystem-metadata work:
they start no network request, subprocess, daemon, retry loop, or background
task. A successful call creates at most one directory tree and writes one
finite catalog document. Existing I/O failures continue to propagate instead
of being mislabeled as policy rejection.

## Zero-downtime deployment and rollback

There is no schema, protocol, listener, dependency, or background-service
change. Old and new workers can coexist during a rolling deployment; valid
catalog names produce the same file and return value, while new workers reject
unsafe keys and escaping leaf links before writing. Rollback is a normal Git
revert of the source, regression test, task evidence, and this document. No
data migration, destructive cleanup, or service interruption is required.

## Verification

Failing-first regressions cover traversal, separators, parent-like names, an
existing escaping leaf symlink, no-mutation rejection, and the valid-name
compatibility path. The adjacent catalog and remote-skill suites run with
warnings treated as errors. Branch-enabled coverage must remain at least 85%
aggregate and 75% for every measured source file; Ruff, strict mypy,
production docstrings, Markdown, task validation, collection, and the guarded
commit gate remain release requirements.
