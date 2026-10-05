# External Directory Permission Contract

## Status

S83.111 updates Gludd's executable permission evidence for the OpenCode schema
used by the `0.1.0-beta.4` release train. It changes no runtime permission,
plugin, Make target, or deployment artifact.

On 2026-08-20, the discovery gate caught a later configuration drift that had
reintroduced unsupported per-tool absolute-path maps. The repair restores this
documented schema in both global and build scopes without changing the reviewed
external allowlist.

## Problem

`tests/unit/test_no_home_directory_access.py` still treated `read`, `write`,
`edit`, `glob`, and `grep` as five path-to-action maps. The repository has
already adopted OpenCode's current boundary: file tools are enabled for the
active worktree, and `permission.external_directory` separately decides which
paths outside that worktree may be accessed. OpenCode also routes `write`,
`edit`, and `apply_patch` through the `edit` permission; a standalone `write`
permission is not part of this repository's supported configuration.

The stale structural test therefore raised 118 failures against a configuration
that was already enforcing the reviewed boundary. Most failures were test
exceptions from calling `.items()` on a scalar tool action. The repair migrates
the evidence to the active schema; it does not edit `opencode.json` or broaden a
single allow rule.

The later drift inverted the global `read` catch-all, removed the explicit
`.env.example` exception, added a standalone `write` surface, and duplicated
external grants under each file tool. That configuration no longer matched the
supported centralized boundary. The 2026-08-20 repair changes `opencode.json`
back to the already documented contract and adds a structural guard proving
that the global and build scopes remain aligned.

## Executable contract

The regression now pins all of these invariants:

- `read` keeps its ordered allow rule plus fail-closed `.env` and `.env.*`
  denials, with `.env.example` as the only reviewed exception;
- `edit`, `glob`, and `grep` remain enabled for the active worktree, and no
  standalone `write` key is introduced;
- `external_directory` is an object whose first rule is `"*": "deny"`, so
  later, narrower rules can override it under last-match-wins evaluation;
- its complete allow set is exactly `/tmp/**`, `/private/tmp/**`,
  `/private/var/folders/**`, `.config/opencode/**`,
  `.local/share/opencode/**`, and `.cache/**` under the configured user home;
- the active Gludd worktree is internal and is not duplicated as an external
  host-path grant; and
- broad home access plus representative credential, desktop, document, media,
  container, Kubernetes, and shell-configuration paths remain forbidden.

The active worktree plus those six external prefixes preserve the same reviewed
effective boundary as before the test migration. Centralizing the external
decision removes obsolete per-tool duplication; it does not turn an earlier
deny into an allow.

## Practitioner evidence

The migration follows a durable upstream user trail:

- OpenCode [issue 4743](https://github.com/anomalyco/opencode/issues/4743),
  opened in November 2025, asks for explicit temporary-directory access through
  `external_directory`. It supports keeping `/tmp` as a narrow reviewed external
  exception rather than weakening the default boundary.
- OpenCode [issue 5395](https://github.com/anomalyco/opencode/issues/5395),
  opened in December 2025, documents that one `external_directory` permission
  currently gates both reads and writes outside the worktree and requests a
  future read/write split. Gludd therefore tests the current centralized action
  honestly and does not invent unsupported per-tool path maps.
- OpenCode [issue 7758](https://github.com/anomalyco/opencode/issues/7758),
  opened in January 2026, reports worktree content being misclassified as
  external when launched from a subdirectory. That report reinforces the
  distinction between the active worktree boundary and actual host paths.

The maintained
[OpenCode permissions documentation](https://opencode.ai/docs/permissions/)
defines `external_directory` as the permission for tools reading or writing
outside the project worktree. The forum reports are operational evidence and do
not imply that Gludd reproduces every upstream platform or symlink defect.

## Security and fail-closed behavior

The original change was an evidence migration; the 2026-08-20 follow-up is a
configuration-drift repair. The test rejects a
missing or non-object external block, a moved/removed deny catch-all, an
unreviewed allow, a broad home prefix, a narrowed temporary-directory rule, or
loss of the OpenCode-owned config prefix. It also keeps the independent `.env`
read restrictions visible so central external authorization cannot be mistaken
for permission to read secrets.

The contract intentionally does not claim that a static JSON test is a host
sandbox or a runtime symlink proof. OpenCode remains responsible for canonical
path evaluation. Gludd's defense is to authorize only exact reviewed boundaries,
keep the default deny first, retain the make-only shell policy elsewhere, and
fail the structural gate on configuration drift.

## Resources and observability

The test performs bounded reads of tracked `opencode.json` and `AGENTS.md` files
and iterates fixed tuples. It starts no daemon, subprocess, watcher, socket, or
network request and creates no checkout artifact. Parameterized failures name
the exact forbidden path; allowlist failures print the expected and observed
sets; schema failures name the missing or malformed permission.

## Versioned compatibility

This contract targets the object-form `permission.external_directory` schema in
the repository's current `opencode.json`. Older test code that expects absolute
path maps under every file tool is deliberately unsupported because it rejects
the live configuration. A future migration to OpenCode's ordered V2
`permissions` schema must change configuration and structural evidence in one
reviewed change while preserving the same effective path boundary.

No public Python API, serialized data, database schema, plugin state file, or
deployment protocol changes. Production coverage thresholds are unaffected
because no production source file changes.

## Zero-downtime rollout and rollback

Rollout changes only tracked OpenCode permission configuration plus structural
tests and documentation; it starts no service, migrates no data, and creates no
persistent artifact. Existing sessions may retain their already loaded policy,
while new or restarted sessions use the restored boundary, so they can overlap
without application downtime. Promotion requires the authoritative RED record,
the migrated regression, related current-schema tests, warnings-as-errors, and
repository static/documentation gates to pass.

Rollback is a normal revert with no runtime state or data migration to reverse,
although it reintroduces unsupported per-tool grants and must therefore remain a
fail-closed emergency action rather than a routine compatibility path.
