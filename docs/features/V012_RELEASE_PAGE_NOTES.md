# v0.1.2 deterministic release-page notes

## Status and outcome

The v0.1.2 GitHub release-page body now has a tracked, byte-deterministic
preview at `docs/releases/v0.1.2.md`. Its source of truth is
`config/v012_completed_backlog_reconciliation.json`, not branch names, wall
clock time, live GitHub state, or an inferred commit range. The preview remains
explicitly **Unreleased**. No tag, draft release, published release, asset, or
remote reference is created by this feature.

The page lists every formally completed item exactly once under the fixed
headings `Features` and `Improvements`, links the ledger's full evidence
commits, records the v0.1.1 baseline, and names open items excluded from
completion claims. It separately projects implementation evidence that is in
the v0.1.2 candidate scope while labeling every such entry as pending
exact-head and release proof.

That implemented-candidate projection covers native SearXNG, executable Ansible
role hardening, FreeLLMAPI rollback and environment-admission work, FFDH warning
remediation, issues #65, #75, and #77, S11, S14-S18, S23, S24, S29, and the
train's dependency, ownership, structural, TUI, and coverage repairs. S29 is
listed from its implementation receipt without claiming train integration or
an exact-head gate. The preview therefore records useful candidate contents
without converting implementation evidence into task, gate, release, or
publication completion.

## Existing-tool decision

The repository's existing `scripts/generate_release_notes.py` remains the
conventional-commit generator for an already-existing tag. It is not used to
infer the untagged v0.1.2 backlog: conventional commit categories do not encode
the formally reconciled task set or distinguish implemented-but-open work.

The output is deliberately compatible with the mature GitHub release
interfaces. The hosted release action consumes the validated v0.1.2 document
through `body_path`; manual `release-create` consumes it through `gh
release create --notes-file`. The official [`gh release create`
manual][gh-create] documents `--notes-file`, `--draft`, and `--verify-tag`; it
also warns that a missing tag is created automatically unless the caller
verifies it. Existing release authorization, tag, artifact, CI, and draft
guards remain responsible for the eventual operation. The builder and preview
targets themselves never invoke `gh`.

GitHub's server-side `--generate-notes` facility remains useful for repositories
whose release scope is defined by merged pull requests. It is intentionally not
the authoritative source here because Gludd's completed-backlog receipt is the
release admission boundary. Tags other than v0.1.2 explicitly retain that
server-generated fallback in the hosted workflow and the existing bounded
generic draft text in manual `release-create`.

## Ledger contract

The schema-v1 receipt retains its formally completed reconciliation and adds a
separate implemented-candidate projection:

- `release_page.status` must equal `unreleased`;
- `release_page.repository` must be an `owner/name` pair;
- every `completed_items` entry has one `release_page_category`, restricted to
  `Features` or `Improvements`;
- every optional `implemented_items` entry has a bounded `item_id`, title,
  category, and immutable evidence list, and is rendered with an explicit
  exact-head/release-proof-pending notice;
- both categories must be present, task IDs and evidence commits must be unique,
  implemented-item IDs must be unique, and each evidence SHA must contain 40
  lowercase hexadecimal characters;
- completed task IDs cannot also appear in `excluded_open_tasks`.

The generator preserves ledger order within each fixed category, with formally
completed entries before implemented candidates. It emits no timestamp or
host-dependent path, so identical ledger bytes and code yield the same
Markdown on every host.

## Bounded and fail-closed behavior

- Ledger input is capped at 256 KiB, completed and implemented items at 64 each,
  excluded items at 64, evidence commits at eight per item, and rendered
  Markdown at 64 KiB.
- The input and output must be regular non-symlink files. Invalid UTF-8, JSON,
  schema, semantic version, repository identity, category, task identity,
  baseline, or evidence stops generation.
- Duplicate JSON keys are rejected, and the input device, inode, byte size, and
  nanosecond modification time must remain unchanged across the read.
- Apply mode writes a private sibling temporary file, flushes and fsyncs it,
  sets the final documentation mode, and atomically replaces the preview.
- Validation mode performs no write and requires byte-for-byte equality with
  the tracked preview.
- Output is bounded to avoid the API failure practitioners reported when
  generated release bodies exceed GitHub's accepted size.

## Dry-run and ZDD contract

`release-dry-run` has `_release-page-notes-preview` as its first prerequisite.
For `TAG=v0.1.2`, that prerequisite invokes `release-page-notes` in validation
mode with every path and behavior variable explicit. Other versions retain the
existing release path. Preview validation uses no network, process daemon,
listener, database, tag, release, or application-traffic mutation; the later
release guards remain mandatory and unchanged.

The hosted release job performs the same no-write validation before handing
v0.1.2 to `softprops/action-gh-release` through `body_path`. Manual
`release-create` depends on the preview validator and passes the same file to
`gh --notes-file`. Conditional inputs keep every other tag on its prior safe
generated/generic fallback. These paths are wired but are not invoked by the
feature tests or dry run.

Generate the tracked preview:

```text
make release-page-notes TAG=v0.1.2 RELEASE_PAGE_NOTES_LEDGER=config/v012_completed_backlog_reconciliation.json RELEASE_PAGE_NOTES_OUTPUT=docs/releases/v0.1.2.md RELEASE_PAGE_NOTES_VALIDATE_ONLY=0
```

Prove it is current without modifying it:

```text
make release-page-notes TAG=v0.1.2 RELEASE_PAGE_NOTES_LEDGER=config/v012_completed_backlog_reconciliation.json RELEASE_PAGE_NOTES_OUTPUT=docs/releases/v0.1.2.md RELEASE_PAGE_NOTES_VALIDATE_ONLY=1
```

Rollback is discard-only: revert the release-note update to remove the additive
candidate projection and restore the prior preview. Because this implementation
never contacts GitHub, rollback has no remote release or tag to delete and no
traffic shift to reverse. If apply mode is interrupted before replacement, the
previous preview remains intact; its sibling temporary file is removed.

Focused regressions pin candidate-ID validation, duplicate rejection, canonical
ordering, deterministic rendering, the exact 20-item candidate inventory, and
the distinction between six formally completed backlog entries and
implementation-only entries pending exact-head/release proof.

## Practitioner evidence

Research was reviewed on 2026-10-08:

- GitHub Community [discussion #113181][community-release-branches] has remained
  unanswered since March 2024. Multiple practitioners report empty generated
  notes for release branches containing cherry-picked commits even though the
  comparison view shows those commits. This supports using the reconciled
  ledger, rather than server-side pull-request inference, for this release.
- GitHub Community [discussion #120836][community-template] has remained
  unanswered since April 2024 and requests customizable generated-note
  templates. Fixed local categories avoid depending on an unavailable generic
  output template while retaining GitHub's supported `--notes-file` ingestion.
- GitHub Community [discussion #63414][community-size] records a 2023 GitHub CLI
  workflow blocked by `body is too long` and the absence of API auto-truncation.
  Gludd fails before publication at a documented 64 KiB ceiling instead of
  truncating or discovering the problem during release creation.
- GitHub Community [discussion #50886][community-tag-config] documents that
  release-note configuration is resolved from the tagged commit when the tag
  exists, not necessarily from the current default branch. Building the preview
  from the exact tracked ledger before a tag exists removes that ambiguity.

[gh-create]: https://cli.github.com/manual/gh_release_create
[community-release-branches]: https://github.com/orgs/community/discussions/113181
[community-template]: https://github.com/orgs/community/discussions/120836
[community-size]: https://github.com/orgs/community/discussions/63414
[community-tag-config]: https://github.com/orgs/community/discussions/50886
