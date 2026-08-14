# Git Worktree Creation Path Contract

## Problem and scope

`GitAutomation.create_worktree()` accepts a destination that Git will populate.
The beta.4 gate exposed a compatibility regression: the established
`gludd-worktree-<uuid>` allocator below Python's platform temp directory was
rejected after project-state namespacing became the only accepted temporary
root. At the same boundary, lexical `abspath` containment accepted a directory
symlink below the repository parent even when its canonical target escaped.

This is distinct from S83.108. That feature validates identities read from
`git worktree list` inside the health audit. S83.103 validates a requested
creation destination before any mutating Git command.

## Contract

Worktree creation uses the following fail-closed order:

1. Branch and path values beginning with a dash are rejected, and a raw `..`
   path component is rejected before normalization.
2. The repository, its parent, and the requested destination are resolved with
   `pathlib.Path.resolve()`. The destination may be the repository, its parent,
   or a canonical descendant. A symlink redirecting outside is rejected.
3. A secure project-state worktree root is accepted only when its canonical
   identity belongs to the namespace derived from that repository. A sibling
   project's namespace is not authority.
4. The compatibility allocator is deliberately narrow: the canonical target
   must remain below `tempfile.gettempdir()` and its first component must match
   exactly `gludd-worktree-` plus 32 lowercase hexadecimal UUID characters.
   Names such as `not-gludd-*`, traversal, and an escaping root symlink fail.
5. Only an authorized path reaches Git, still as a list-form argument after
   `--`; shell parsing remains disabled.

The project-state namespace is preferred for durable orchestration. The UUID
compatibility root supports existing ephemeral callers without granting all of
the system temp directory.

## Practitioner evidence

The long-running Stack Overflow discussion
[How can I have multiple working directories with Git?](https://stackoverflow.com/questions/6270193/how-can-i-have-multiple-working-directories-with-git)
records more than a decade of worktree lifecycle and path-identity fixes. It
supports treating a worktree path as persistent administrative identity rather
than a display string.

The seven-year-old explanation
[`.git` directory in git worktree: not a directory](https://stackoverflow.com/questions/53796823/git-directory-in-git-worktree-not-a-directory)
documents that linked worktrees point back into the common Git directory. A
creation destination therefore affects both filesystem content and shared
repository metadata.

The six-year-old symlink report
[Git thinks a file within a symlinked directory has been deleted](https://stackoverflow.com/questions/60582087/git-thinks-a-file-within-a-symlinked-directory-has-been-deleted-after-recreating)
explains the security reason Git avoids writes beyond directory symlinks. That
experience supports canonical confinement before a worktree-populating command.

## Security and resources

Validation performs bounded in-process path and namespace checks before the
existing bounded Git subprocess. It starts no daemon, retry loop, network
request, helper script, or additional worker. Canonical comparison closes the
symlink escape; exact temp-root syntax avoids a broad `/tmp` capability; and
project-derived namespaces prevent cross-project authorization.

The filesystem can change after validation, so callers should prefer the
owner-only project-state allocator, whose directory validation narrows that
race. The compatibility branch intentionally accepts only ephemeral UUID roots
under the canonical platform temp identity.

## ZDD and rollback

The change is control-plane validation only. It does not restart services,
change persisted Git formats, migrate data, or alter an existing worktree.
Old and new processes can overlap during rolling deployment because each
validates only its own requested destination before mutation. Invalid requests
fail before Git and leave no partial worktree.

Rollback is a normal revert of the source, focused tests, task evidence, and
this document. No cleanup or schema rollback is required. Operators should keep
the last rejected-path message when investigating a rollback because restoring
lexical comparison reopens the symlink-escape behavior and restoring the
namespaced-only helper reintroduces the Gludd temp-root compatibility failure.

## Verification

The acceptance set includes the original beta.4 temp-root regression, raw
traversal, canonical symlink escape, matching and foreign project namespaces,
the adjacent Git automation suites under warnings-as-errors, line-and-branch
coverage floors, Ruff, strict mypy, source docstrings, Markdown/spec lint, task
ledger integrity, full collection, and the guarded commit gate.
