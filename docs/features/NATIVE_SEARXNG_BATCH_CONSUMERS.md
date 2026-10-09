# Native SearXNG Batch Consumers

S44 removes the last 31 role-level raw SearXNG HTTP calls. Business research,
security framework updates, and infrastructure service discovery now submit
bounded request batches to `general_ludd.travel.searxng_batch`. The action
reuses S38's pinned `NativeSearxRuntime` on the controller and returns the
same `status` and `json` fields the roles consumed from `ansible.builtin.uri`.
It opens no listener, starts no subprocess, and transfers no runtime to a
managed host.

## Boundary and compatibility contract

- Native transport is the default. A request may select `transport: remote`
  only while also supplying `remote_url`; native mode rejects that URL.
- A batch contains 1 to 32 unique identifiers. Identifiers are 1 to 64
  ASCII letters, digits, or underscores and begin with a letter or underscore.
- A query is at most 2,048 characters. Categories and engines each contain at
  most 20 bounded names. Each query requests at most 100 results and all
  requested ceilings in a batch total at most 500.
- Runtime payloads must be mappings with a list of mapping results and an
  integer `number_of_results`. A runtime returning more rows than requested,
  malformed JSON shape, or a failed query aborts the whole batch. No partial
  success is published.
- Check mode performs the same input validation without importing or starting
  SearXNG. It returns keyed, skipped placeholders with the legacy URI response
  shape.
- The action releases native runtimes that it creates, including on failure.
  A runtime already owned by the S38 namespace registry remains registry-owned.
  Remote adapters are always batch-owned and released in `finally`.
- `searxng_url`, Compose/Terraform project paths, implicit localhost HTTP, and
  every transport other than `native` or `remote` fail before allocation.

Each response remains compatible with the removed URI registers:

```yaml
changed: false
failed: false
status: 200
json:
  query: bounded query
  number_of_results: 1
  results: []
```

The result map is keyed by request ID, so the roles copy each value back to
its historical register name before downstream facts are evaluated.

## Consumer inventory

| Collection and role | Task files | Removed raw URI calls |
|---|---:|---:|
| `business.entity_research` | assets, exposure, risks, associations, monitoring, discovery, demographics | 29 |
| `security.audit_framework` | framework update | 1 |
| `infrastructure.service_discovery` | search term | 1 |
| **Total** | **9** | **31** |

The entity role sends related requests through one shared task boundary and
reconstructs each historical fact by request ID. Monitoring keeps its initial
scan separate from the seven alert-topic scans because those paths have
distinct enable conditions. The security and infrastructure roles each use a
one-query batch while preserving `_sx_search_response` and `_sd_response`,
respectively.

## Long-lived user and upstream evidence

- The official [SearXNG Search API](https://github.com/searxng/searxng/blob/master/docs/dev/search_api.rst?plain=true)
  requires GET parameters in the URL query string (or form data for POST) and
  says disabled response formats return HTTP 403. The removed role tasks kept
  `query`, `categories`, and `page` only in Ansible task-local variables, not
  in URI query parameters. The action now passes typed values directly to the
  pinned application boundary.
- In [SearXNG discussion #1789](https://github.com/searxng/searxng/discussions/1789),
  users have asked since 2022 how to obtain JSON and whether a public JSON
  instance exists; maintainers point to explicitly enabling JSON, while public
  availability remains constrained. Native settings make JSON availability a
  controller-owned, release-tested fact instead of an assumption about a
  public endpoint.
- In [SearXNG discussion #3542](https://github.com/searxng/searxng/discussions/3542),
  reports from 2024 through 2025 describe JSON requests producing HTTP 403 or
  HTML despite configuration changes. The batch action therefore validates
  the returned shape and fails closed rather than treating HTML, an empty body,
  or a blocked format as zero results.
- [SearXNG issue #126](https://github.com/searxng/searxng/issues/126) records a
  direct `python searx/webapp.py` launch breaking when imports changed. S44
  imports the pinned `searx.webapp` package through S38 and never launches its
  source file as a script or child process.
- [SearXNG issue #2505](https://github.com/searxng/searxng/issues/2505) records a
  category-specific JSON request producing HTTP 500. A query exception or an
  invalid payload makes the batch fail atomically; the roles do not invent a
  successful empty result.

## Resource ownership

S44 creates no persistent file, process, socket, port, worker, cache, or
listener. `config/resource_ownership_native_searxng_batch_consumers.json` is
empty by construction. Temporary settings and retained namespace runtimes stay
under the existing S38 ownership contract. For a transient runtime, the action
is the sole acquirer and its `finally` block is the sole teardown path.

## Zero-downtime delivery and rollback

Build the immutable controller execution environment and prove the pinned
SearXNG import plus focused action tests before admitting it. Canary new jobs
for each of the three consumer roles on the candidate digest while existing
jobs drain on the previous digest. Compare only bounded counts and success
state; do not log search text. Promote new-job routing after all three canaries
pass, then retire the old digest after the rollback window.

Rollback first routes new jobs to the previous verified execution-environment
digest and drains candidate jobs. If the native integration alone is the
incident and an operator-owned endpoint is already verified, set the role's
transport to `remote` and provide its existing URL explicitly. That emergency
path is bounded by the same batch validator. It does not restore implicit
localhost HTTP, a listener, Compose, Terraform, or raw role-level URI calls.

## Verification

Implementation commit `085a76bca` is the admitted release-candidate payload.
Its bounded verification evidence is:

- Structural acceptance proves all 31 raw URI call sites are absent and all 31
  legacy result variables are reconstructed from batch responses.
- All 155 focused, S38 compatibility, native-integration, and travel-index
  tests pass with warnings fatal. They cover native runtime reuse, explicit
  remote ownership, check mode, atomic cleanup, schemas, and every ceiling.
- Branch-aware coverage is 99% aggregate; each of the four measured production
  files is 98-100%, above the 85% aggregate and 75% per-file floors.
- The SearXNG role Molecule scenario exercises a two-query batch in converge
  and verify while in check mode. Syntax, converge, idempotence, and verify pass
  4/4 without a network search.
- Strict collection boundary reports zero findings, the feature resource
  inventory reports zero owned resources, and duplicate-code delta reports zero
  new clones. Ruff and strict mypy are green.
- Repository collection selects 121513/121531 tests with 18 intentional
  deselections and zero collection errors. The train-level exact-head full gate
  remains the release promotion boundary.
