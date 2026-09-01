# Cache Resource Inventory and Exact-Child Cleanup

## Purpose

Gludd must be able to explain disk pressure without creating more pressure while
it inspects the machine. The cache resource interface inventories bounded,
allowlisted user-cache roots and removes only one explicitly named immediate
child after a validate-first pass. Both operations run through tracked Make
targets and the system Python; they do not create a project virtual environment.

The initial dogfood run found three distinct resource problems: read-only Make
diagnostics could bootstrap a large virtual environment, nested feature
worktrees escaped the old one-level cleanup glob, and inactive local container
state remained after its Podman machine definition disappeared. The tracked
interface replaced the temporary diagnostic shortcut and made each decision
reviewable in Git.

## Operator contract

- `make cache-resource-inventory` accepts `~/.cache`,
  `~/.local/share/containers`, `~/Library/Caches`, or the project temp root
  `~/tmp` and emits one bounded JSON line per immediate child. The temp root
  plus its immediate project-state/resource children and their exact namespace
  children are inventory-only. Disk incidents can therefore be narrowed through
  the two project-owned namespace levels without granting deletion or traversing
  arbitrary descendants.
- Measurement failures remain visible as `status=error` entries. One protected
  Apple cache cannot hide the remaining inventory.
- `make cache-resource-remove ... CACHE_RESOURCE_VALIDATE_ONLY=1` accepts only
  the three cache roots, never `~/tmp`, and performs the same path validation
  without deletion. Apply mode removes exactly one named, existing immediate
  cache child.
- Roots and candidates must be canonical, non-symlink paths. Parent roots,
  nested descendants, missing paths, project-temp removal, and paths outside
  the operation-specific allowlist fail closed before mutation.
- Worktree cache cleanup recursively finds generated `.venv`, pytest, mypy,
  Ruff, and coverage directories without following symlinks, including nested
  branch-name directories.
- Live OpenCode data and active Lima virtual machines are outside this feature's
  deletion boundary.

## Practitioner evidence

Long-lived reports show that this is an operational contract rather than a
one-off workstation issue:

- Astral users reported self-hosted uv caches growing to roughly 40 GB and asked
  for a bounded size policy in [uv issue #5731](https://github.com/astral-sh/uv/issues/5731).
  The upstream CI guidance recommends deliberate post-job cleanup such as
  `uv cache prune --ci`, supporting Gludd's explicit tracked cleanup boundary.
- [Podman issue #15742](https://github.com/containers/podman/issues/15742)
  documents machine metadata disappearing while local images and state remain,
  matching the residual local container cache found during dogfood.
- [Lima issue #1427](https://github.com/lima-vm/lima/issues/1427) records the
  operational need to control cache placement. Gludd therefore inventories the
  download cache separately and never treats the active VM directory as a
  regenerable cache.

## Security and resource limits

Inventory is capped at 100 results, each `du` subprocess has a 30-second
deadline, and output is stable JSON suitable for audit logs. Canonical
allowlisting and immediate-child ownership prevent path traversal, symlink
escape, broad home-directory deletion, and accidental mutation of active
service data. Read-only diagnostics are in the no-uv-sync goal set so measuring
resource use cannot silently consume hundreds of megabytes.

## Zero-downtime deployment and rollback

The interface changes no running service, database schema, network route, or
release artifact. It is safe to deploy before callers adopt it. Cache cleanup
may cause a later tool invocation to redownload or rebuild data, but it does not
restart services or remove durable project state. Rollback is the normal Git
revert of the Make target, script, tests, and contract. Removed cache contents
are intentionally not recoverable from Gludd; operators must regenerate or
redownload them from their authoritative source.

## Verification

The focused test suite covers ordering, limits, unreadable children, canonical
path rejection, validate-only and applied removal, file and directory children,
system-Python startup, JSON observability, nested worktree cleanup, and the
no-bootstrap Make contract. Branch-enabled coverage must remain at least 85%
aggregate and at least 75% for the script. The Make target contract, behavioral
examples, Ruff, strict mypy, docstrings, Markdown, task integrity, collection,
and the full project gate are required before release promotion.
