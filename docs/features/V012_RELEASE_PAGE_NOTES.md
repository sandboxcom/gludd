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
remediation, issues #65, #75, and #77, S11, S14-S18, S23, S24, S29, S30, and
S31-S45 plus S47, along with the train's dependency, ownership, structural,
TUI, and coverage repairs. S29-S45 and S47 are listed from immutable
implementation receipts without
claiming train integration or an exact-head gate. The preview therefore records
useful candidate contents without converting implementation evidence into task,
gate, release, or publication completion.

S31-S37 describe the user-visible outcome as safer model-performance,
benchmark, memory, and agent-message repository lifecycles. S34 rejects expired
messages before its deterministic 100-message inbox bound, so stale head rows
cannot hide live work. S35 makes an omitted memory `project_id` select only the
global partition instead of exposing project-scoped records. S36 persists
generated-tool results only in the validated originating project and
quarantines legacy-global tool outputs from project reads. Their detailed
feature documents retain the mature upstream basis for that wording:
SQLAlchemy's
[session-per-task guidance][sqlalchemy-async], the long-running shared-session
failure reports in [discussion #8554][sqlalchemy-8554], the detached-result
reports in [discussion #8731][sqlalchemy-8731], and the stateful-connection
clarification in [discussion #10808][sqlalchemy-10808]. The S34 evidence also
retains RabbitMQ's long-running [head-of-line expiry report][rabbitmq-3852] and
[resource-retention report][rabbitmq-14524]; S35 retains the SQLAlchemy
[tenant-criteria discussion][sqlalchemy-11389] and FastAPI
[tenant-context discussion][fastapi-7564]. S36 retains FastAPI's
[parallel-session report][fastapi-11625] and MCP's
[cross-user-state report][mcp-1087]. The release page links immutable Gludd
commits, while those feature documents remain the fuller source for practitioner
findings and resource bounds.

S37 makes variable, feature, and prompt-profile upserts work with the native
conflict-capable statement for the caller's bound SQLite or PostgreSQL session,
while unsupported dialects fail before database I/O. Its design follows the
maintainer guidance in [SQLAlchemy discussion #7199][sqlalchemy-7199] to select
the builder after discovering the live dialect and in
[SQLAlchemy discussion #7007][sqlalchemy-7007] to use SQLite's dialect insert
instead of attaching conflict methods to the generic statement.

Both additions are zero-downtime, application-image-only changes: they add no
schema, dependency, persistent process, or ownership transfer. Mixed-version
processes retain compatible rows and payloads. Rollback routes new requests to
the preceding image and lets updated requests drain; it does not migrate,
rewrite, or purge data. S34 evidence includes 4 focused and 214 adjacent
warning-fatal tests with 89% aggregate coverage (`messaging.py` 85%, message
router 93%). S35 evidence includes 6 focused tests plus adjacent 170-test and
247-test warning-fatal runs with 99% aggregate coverage (memory repository 99%,
memory router 100%). Both recorded clean collection evidence.

S36 is also a schema-free, application-image-only rolling change, but its
rollback is deliberately fail-closed: reverting the reader would re-expose
quarantined legacy-global `tool_results` to project reads. Operators should keep
the isolated reader serving and repair forward; a compatibility rollback must
first stop new tool dispatch, preserve evidence, and explicitly accept that
confidentiality risk. Its evidence includes 34 warning-fatal focused tests and
a 583-test coverage run with one intentional skip, reaching 91.0% aggregate
line and 85.1% branch coverage (`projects.py` 98.2%/90.0%, dispatch
88.7%/84.2%), plus clean repository collection.

S37 is a schema-free, application-image-only rolling change with no new
dependency, worker, listener, or ownership transfer. Old and new workers retain
the same tables, conflict keys, and row formats. Rollback stops new work, drains
or rolls back active transactions, and returns traffic to the preceding image;
no migration downgrade, row rewrite, cache flush, or data deletion is needed.
Repair forward is preferred while PostgreSQL writes are active because the
preceding implementation hard-codes SQLite's statement subclass. Its evidence
includes 354 warning-fatal tests and 94% aggregate coverage (`shared.py` 87%,
`projects.py` 98%, `metrics.py` 91%), with native SQL compilation, unsupported
dialect rejection before I/O, and bounded two-session PostgreSQL convergence.

S38-S45 move eight collection workflows onto native, owned library boundaries;
S47 hardens and accelerates their final local gate admission:

- **S38 — Native SearXNG controller runtime.** Travel searches now call the
  official pinned `searx.webapp` WSGI application inside the controller EE;
  unavailable backends can no longer become invented `.example.com` results.
  The digest-addressed EE rolls out beside the active digest, takes only new
  jobs, and drains before retirement. Rollback returns new jobs to the prior EE
  or uses the explicit bounded remote transport without restoring Compose,
  Terraform, or an implicit listener. Its 212 warning-fatal tests pass at 91%
  aggregate coverage with all nine measured files above 75%; all four Molecule
  phases and the 121,242-test collection pass. SearXNG
  [discussion #1789][searxng-1789] records disabled JSON output and bot-abuse
  pressure, [discussion #3106][searxng-3106] records an untested ARM source
  build, and issues [#3896][searxng-3896] and [#3474][searxng-3474] record
  dependency and concurrency failures addressed by the pin and fail-closed
  adapter.
- **S39 — Native git-release artifact verification.** The managed host now
  verifies the release artifact and dependency lock itself with root-confined,
  streamed SHA-256 reads; the role no longer delegates the promotion decision
  to a daemon. Verification is read-only, so a rejected candidate never alters
  the active artifact, and rollback keeps or restores traffic to the previously
  admitted digest without undoing verifier state. All 74 warning-fatal tests,
  four `git_release_expert` Molecule phases, and the 121,217-test collection
  pass; coverage is 89% aggregate and every measured file is at least 88%.
  The policy is grounded in the [Ansible Release 1.2.3][ansible-123] symlink
  advisory, an [AWX escaping-link report][awx-linkname], the long-lived Ansible
  [FIPS/MD5 report #9429][ansible-9429], and
  [privilege-boundary checksum report #69383][ansible-69383].
- **S40 — Native Frictionless dataset admission.** Dataset-engineer jobs now
  validate root-confined CSV resources and an explicit Table Schema with pinned
  `frictionless==5.19.1`, returning a stable SHA-256-bound data card without a
  service call, listener, subprocess, or state mutation. The controller EE uses
  an immutable-digest canary and drain; rollback routes new jobs to the previous
  digest and requires no data repair. Focused tests pass 26/26, adjacent tests
  pass 112/112, coverage is 89% aggregate with every measured file at least
  88%, all five Molecule phases pass, and collection selects 121,269 tests with
  zero errors. [Frictionless discussion #675][frictionless-675] supports a
  separately stored schema, [discussion #653][frictionless-653] records schema
  synchronization ambiguity, and issues [#609][frictionless-609] and
  [#1646][frictionless-1646] document schema-path and misleading-report failure
  modes covered by explicit in-memory schema and task-shape admission.
- **S41 — Collection-native chemical lot admission.** Inventory checks now use
  one deterministic collection evaluator for expiry, restrictions, and purity;
  unsuitable lots require human review and automation never proposes a
  substitute. The additive module owns no durable state, so old and new
  controllers coexist and rollback only repins the prior collection artifact.
  The evidence includes 27 focused warning-fatal tests, 71 focused and adjacent
  tests, and a 1,265-test chemistry slice with 10 explicit skips. Measured code
  has 95% aggregate coverage, with every file above the 75% floor; all Molecule
  phases and a 121,272-test zero-error collection also pass. Long-lived
  [ansible/ansible#50579][ansible-50579] and
  [ansible/ansible#77935][ansible-77935] motivate collection-qualified public
  imports, while the archived [check-mode report][ansible-check-mode] and
  [read-only module discussion][ansible-read-only-module] motivate explicit
  check-mode parity without shell tasks.
- **S42 — Native fail-closed JUnit pipeline triage.** Git-release controllers
  now parse one root-confined JUnit report through the collection-owned action
  and locked `defusedxml.ElementTree`; the companion module fails closed if the
  action boundary is bypassed. Receipts expose only counts and SHA-256
  identities, never failure bodies, properties, stdout, stderr, or raw test
  names. Admission rejects reports over 16 MiB, more than 100,000 testcases,
  more than 64 failures or errors, links, unstable file identity, DTDs,
  entities, duplicate identities, and ambiguous outcomes.
  A digest-addressed canary takes only bounded new work; old controllers drain
  before replacement, and rollback drains the candidate before routing new
  reports to the prior verified digest; no report, schema, or managed-host state
  needs repair. The
  evidence includes 26 focused tests and 54 warning-fatal focused/compatibility
  tests, 97% aggregate coverage (100% action and module, 97% parser), all five
  Molecule phases, strict boundary and resource-ownership checks, and a
  121,418-test zero-error collection with 18 intentional deselections. A
  decade-old [Ansible JUnit integration thread][ansible-junit-thread], an
  [unbounded callback-payload report][ansible-junit-payload], and pytest's
  [parameter identity issue #469][pytest-469] motivate the native artifact,
  hard bounds, and digest-only identities.
- **S43 — Native fail-closed materials tolerance analysis.** Materials jobs now
  execute exactly six pure operations through one collection-owned controller
  action and shared evaluator, with normal and check mode byte-identical. The
  interface accepts at most 256 dimension pairs, 64 KiB request and result JSON,
  and 32 characters per unit label; it rejects non-finite values and any
  covariance or correlation input it cannot model. A digest-addressed canary
  takes only bounded new work, old controllers drain before replacement, and
  rollback drains the candidate before routing new analyses to the prior
  verified digest; the stateless calculation needs no data repair. The evidence
  includes 27 focused warning-fatal tests and all 150 focused, legacy, service,
  and role compatibility tests, 94% aggregate coverage with all five measured
  files at least 92%, all five Molecule phases, and a
  121,469-test zero-error collection with 18 intentional deselections from
  121,487 tests. The
  [Ansible action-plugin/module-utils thread][ansible-action-module-utils], its
  [collection-qualified follow-up][ansible-action-collections], and practitioner
  [RSS uncertainty discussion][materials-rss-practice] motivate the shared
  collection boundary and explicit independent-contributor assumption.
- **S44 — Collection-native SearXNG batch consumers.** Business research,
  security updates, and infrastructure discovery now route
  all 31 raw SearXNG HTTP calls through one bounded controller batch action
  while retaining
  their legacy `status` and `json` result shapes. Native execution reuses one
  S38 runtime per batch; malformed, excessive, or failed results abort the batch
  atomically, and check mode allocates no listener, subprocess, or runtime.
  Delivery canaries the immutable controller image across all three consumer
  roles while prior jobs drain. Rollback routes new jobs to the prior digest or,
  for a native-only incident, to an explicitly configured verified remote
  endpoint; it never restores implicit localhost HTTP, Compose, Terraform, or
  role-level URI calls. All 155 focused and compatibility tests pass with
  warnings fatal, coverage is 99% aggregate with every measured production file
  at 98-100%, all four Molecule phases pass, and collection selects
  121,513/121,531 tests with 18 intentional deselections and zero errors. The
  official [SearXNG Search API][searxng-api], long-running JSON-format reports in
  discussions [#1789][searxng-1789] and [#3542][searxng-3542], direct-launch
  breakage in issue [#126][searxng-126], and category failure in issue
  [#2505][searxng-2505] motivate typed native inputs and fail-closed output
  validation.
- **S45 — Fail-closed native OpenAPI service registration.** Infrastructure
  jobs now admit one SHA-256-bound, root-confined OpenAPI 3.1 contract and invoke
  one unique GET/HEAD `operationId` through pinned `openapi-core` and
  `jsonpointer`. Named environment references keep secrets outside task data;
  internal-only references, DNS-pinned HTTPS, zero redirects, and bounded
  request/response validation replace generated connector files, source
  mutation, ignored health checks, and synthetic success. A digest-addressed
  controller image first runs every contract in validation-only mode, then takes
  a small live cohort while old requests drain within their 30-second ceiling.
  Rollback routes new work to the preceding image and needs no listener, schema,
  generated file, or database repair. The focused suite passes 31 tests at 95%
  aggregate coverage with every measured production file at 94-100%; the full
  Molecule sequence, dependency and ownership checks, controller boundaries,
  and 121,567/121,585-test zero-error collection are green. Upstream reference
  failures in openapi-core issues [#154][openapi-core-154],
  [#297][openapi-core-297], and [#893][openapi-core-893], practitioner reports
  on [validation errors][openapi-core-768],
  [runtime response validation][openapi-response-drift], and
  [large-schema cost][openapi-schema-cost], plus the OpenAPI `$ref`
  [SSRF advisory][openapi-ref-ssrf] and DNS check/use report
  [#14312][semantic-kernel-14312], define that trust boundary.
- **S47 — Exact-SHA resumable, fail-fast local gate admission.** Local retries
  admit only authenticated passing receipts from the same clean SHA, restore
  branch-aware coverage before enforcing the unchanged 85% aggregate and 75%
  per-file floors, and fail closed on stale or mismatched evidence. Preflight
  and batch failures terminalize immediately, release owned leases, and leave
  later work not started. Cold execution uses at most two isolated xdist
  `loadfile` workers; summaries distinguish executed, resumed, and unstarted
  batches and report receipt-derived time saved. The evidence includes 506
  warning-fatal focused and compatibility tests, 471 coverage tests at 88%
  aggregate with every measured implementation file at least 84%, and a
  121,496-test zero-error collection with 18 intentional deselections.

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
ordering, deterministic rendering, the exact 37-item candidate inventory, and
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
[sqlalchemy-async]: https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#using-asyncsession-with-concurrent-tasks
[sqlalchemy-8554]: https://github.com/sqlalchemy/sqlalchemy/discussions/8554
[sqlalchemy-8731]: https://github.com/sqlalchemy/sqlalchemy/discussions/8731
[sqlalchemy-10808]: https://github.com/sqlalchemy/sqlalchemy/discussions/10808
[rabbitmq-3852]: https://github.com/rabbitmq/rabbitmq-server/discussions/3852
[rabbitmq-14524]: https://github.com/rabbitmq/rabbitmq-server/discussions/14524
[sqlalchemy-11389]: https://github.com/sqlalchemy/sqlalchemy/discussions/11389
[fastapi-7564]: https://github.com/fastapi/fastapi/discussions/7564
[fastapi-11625]: https://github.com/fastapi/fastapi/discussions/11625
[mcp-1087]: https://github.com/modelcontextprotocol/modelcontextprotocol/issues/1087
[sqlalchemy-7199]: https://github.com/sqlalchemy/sqlalchemy/discussions/7199
[sqlalchemy-7007]: https://github.com/sqlalchemy/sqlalchemy/discussions/7007
[searxng-1789]: https://github.com/searxng/searxng/discussions/1789
[searxng-api]: https://github.com/searxng/searxng/blob/master/docs/dev/search_api.rst
[searxng-3106]: https://github.com/searxng/searxng/discussions/3106
[searxng-3542]: https://github.com/searxng/searxng/discussions/3542
[searxng-126]: https://github.com/searxng/searxng/issues/126
[searxng-2505]: https://github.com/searxng/searxng/issues/2505
[searxng-3896]: https://github.com/searxng/searxng/issues/3896
[searxng-3474]: https://github.com/searxng/searxng/issues/3474
[ansible-123]: https://forum.ansible.com/t/ansible-release-1-2-3/13342
[awx-linkname]: https://forum.ansible.com/t/awx-23-8-1-getting-error-invalid-linkname-for-tarfile-member/6242/2
[ansible-9429]: https://github.com/ansible/ansible/issues/9429
[ansible-69383]: https://github.com/ansible/ansible/issues/69383
[frictionless-675]: https://github.com/frictionlessdata/frictionlessdata.io/discussions/675
[frictionless-653]: https://github.com/frictionlessdata/frictionlessdata.io/discussions/653
[frictionless-609]: https://github.com/frictionlessdata/frictionless-py/issues/609
[frictionless-1646]: https://github.com/frictionlessdata/frictionless-py/issues/1646
[ansible-50579]: https://github.com/ansible/ansible/issues/50579
[ansible-77935]: https://github.com/ansible/ansible/issues/77935
[ansible-check-mode]: https://forum.ansible.com/t/cannot-get-customized-facts-when-pushing-with-check/17103
[ansible-read-only-module]: https://forum.ansible.com/t/add-condition-when-command-shell-modules-should-return-ok-not-changed/37686
[ansible-junit-thread]: https://groups.google.com/g/ansible-project/c/0ic8kasUqbQ
[ansible-junit-payload]: https://www.reddit.com/r/ansible/comments/1cus9rt
[pytest-469]: https://github.com/pytest-dev/pytest/issues/469
[ansible-action-module-utils]: https://forum.ansible.com/t/two-questions-related-to-action-plugins/28059
[ansible-action-collections]: https://forum.ansible.com/t/what-is-a-proper-way-to-use-module-utils-in-action-plugins/11011
[materials-rss-practice]: https://www.reddit.com/r/AskEngineers/comments/usqr00/how_do_everyone_do_tolerance_stack_up_analysis_at/
[openapi-core-154]: https://github.com/python-openapi/openapi-core/issues/154
[openapi-core-297]: https://github.com/python-openapi/openapi-core/issues/297
[openapi-core-893]: https://github.com/python-openapi/openapi-core/issues/893
[openapi-core-768]: https://github.com/python-openapi/openapi-core/discussions/768
[openapi-response-drift]: https://www.reddit.com/r/webdev/comments/1fxqkl6/how_do_you_enforce_that_your_api_actually_fulfills/
[openapi-schema-cost]: https://www.reddit.com/r/PHP/comments/s5ko42/do_you_use_open_api_specs/
[openapi-ref-ssrf]: https://github.com/advisories/ghsa-v6ph-xcq9-qxxj
[semantic-kernel-14312]: https://github.com/microsoft/semantic-kernel/issues/14312
