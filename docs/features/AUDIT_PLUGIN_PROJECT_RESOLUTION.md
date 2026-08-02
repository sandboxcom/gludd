# Audit Plugin Project Resolution

`gludd audit-plugins --project` accepts either a logical project name or an
explicit filesystem path without confusing those two security domains. The
resolved directory is used for the owner-only artifact namespace; the original
logical name is still passed to the audit playbook as `project_name`.

## Operator contract

| Input | `project_name` | `project_root` |
|---|---|---|
| omitted | `null` | strict resolution of the current directory |
| `my-proj` | `my-proj` | strict resolution of the current directory |
| `./child` | `./child` | the existing `child` directory |
| `/srv/project` | `/srv/project` | the existing absolute directory |

A bare logical name must start with an ASCII alphanumeric character, contain
only ASCII letters, digits, `.`, `_`, or `-`, and be at most 96 characters.
To select a single-component child directory, make path intent explicit with
`./child`; an unprefixed `child` is a logical name.

Explicit paths fail closed when they contain a `..` component, contain a
symlink component, are unavailable, resolve to a different filesystem scope,
or are not directories. State allocation still goes through the existing
`SandboxState` owner, mode, namespace, and containment checks; those checks are
not relaxed for the CLI compatibility fix.

## Security rationale and upstream reports

- The [Python `pathlib` documentation](https://docs.python.org/3/library/pathlib.html#pathlib.Path.resolve)
  says `resolve()` follows symlinks and eliminates `..`, and `strict=True`
  raises for a missing path. Gludd rejects traversal and symlink components
  before strict resolution so normalization cannot silently broaden scope.
- [CWE-22](https://cwe.mitre.org/data/definitions/22.html) describes the risk
  of external input selecting filesystem locations outside the intended area.
  Separating a narrow logical-name allowlist from explicit path syntax keeps
  that selection visible and testable.
- A long-running [Python.org user discussion from 2021](https://discuss.python.org/t/pathlib-resolves-a-virtualenvs-python-to-the-global-python/7314)
  documents a surprising real-world result: `Path.resolve()` follows a virtual
  environment symlink all the way to the system interpreter. That is why Gludd
  does not use resolution itself as permission to accept a symlinked root.
- CPython issue [#99334](https://github.com/python/cpython/issues/99334), opened
  in 2022, demonstrates that lexical `is_relative_to()` checks can appear safe
  while a path containing `../` escapes after resolution. Gludd rejects the
  traversal token before performing filesystem resolution instead of relying
  on a lexical containment check.

## Verification

The unit and E2E contract covers logical names, explicit relative paths,
missing paths, non-directory paths, traversal, symlink components, secure
artifact placement, and full CLI propagation into the Ansible playbook.
