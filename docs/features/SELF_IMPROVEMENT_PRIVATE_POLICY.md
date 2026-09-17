# Self-improvement private-policy boundary

Status: implementation and acceptance contract for S83.145. A gate result, not
this document, determines whether the feature is complete.

## User outcome

A project can reserve files and directories for business logic that Gludd's
self-improvement system must not inspect, send to a model, reproduce, modify,
cache, or learn from. The boundary applies equally to a locally hosted model
and a remote provider such as Azure OpenAI.

The project owns a versioned policy at:

```text
.gludd/self-improve-policy.json
```

For an existing project that wants to protect selected areas while leaving the
rest eligible for improvement:

```json
{
  "schema_version": 1,
  "default_access": "public",
  "private_paths": [
    "src/domain/pricing/**",
    "src/domain/risk/**",
    "tests/private_pricing/**"
  ],
  "public_paths": []
}
```

For a high-sensitivity project, default to private and allow only a dedicated
improvement surface:

```json
{
  "schema_version": 1,
  "default_access": "private",
  "private_paths": [
    "src/product/proprietary/**"
  ],
  "public_paths": [
    "docs/public/**",
    "src/improvable/**",
    "tests/improvable/**"
  ]
}
```

`private_paths` always wins. In the second example, even a path matched by
`public_paths` remains private if it also matches `private_paths`.

## Deliberately whole-file, not symbol-level

Version 1 protects complete paths only. A project that has private and public
logic in one source file must treat the entire file as private or separate the
public logic into another file.

Symbol-level rules are intentionally unsupported. A function body can leak
through imports, type information, neighboring code, stack traces, generated
tests, or a model's attempted reconstruction. Supporting every language parser
would also turn a privacy decision into a parser-version decision. Whole-file
classification gives all stages one unambiguous answer before source is read.

This policy is a self-improvement boundary, not a secret store or encryption
mechanism. Credentials still belong in a secret manager, and a policy cannot
recall content that another tool sent before the policy was enabled.

## Policy language

Patterns are repository-relative POSIX paths with Git-ignore matching
semantics, implemented with the maintained `pathspec.GitIgnoreSpec` engine.
The policy is rooted at the bound repository; it never inherits a user's global
ignore rules or another repository's `.gitignore`.

- `src/domain/pricing/**` matches descendants below that exact repository path.
- `**/trade-secret.py` matches that filename at any depth.
- `private/` matches the directory and all descendants.
- `*` does not cross `/`; `**` can cross directory boundaries.
- Matching is case-sensitive against the canonical Git tree. A case-folding
  collision on a case-insensitive worktree is an error, not a best guess.
- Negation (`!`) is rejected. The separate public/private lists and
  private-wins rule avoid order-dependent privacy changes.
- Absolute paths, drive prefixes, backslashes, NULs, empty patterns, comments,
  leading or trailing whitespace, and `.` or `..` path segments are rejected.
- Duplicate rules, including duplicates after canonical normalization, are
  rejected rather than silently collapsed.

The parser accepts UTF-8 JSON no larger than 64 KiB, at most 1,024 patterns
across both lists, and at most 512 UTF-8 bytes per pattern. Only the four keys
shown in the examples are valid. Duplicate JSON keys, an unsupported schema,
invalid types, malformed JSON, a no-op pattern, or an unreadable policy is an
invalid policy.

These limits bound parsing, matching, memory, and telemetry cardinality. The
canonical policy representation sorts object keys and pattern lists before
computing its digest; file order therefore does not change plan identity.

### Missing and invalid policy behavior

A missing policy preserves backward compatibility and is equivalent to this
canonical policy:

```json
{
  "schema_version": 1,
  "default_access": "public",
  "private_paths": [],
  "public_paths": []
}
```

Once a policy file exists, any validation or read failure disables
self-improvement for that project. Gludd must not treat an invalid policy as
"everything public." The application itself stays available; only the
self-improvement run fails closed.

The policy file, its containing `.gludd` control path, and Gludd-owned approval
records are intrinsically private and immutable to self-improvement, whether or
not a user pattern mentions them.

## Classification algorithm

Every candidate path is converted to its repository-relative POSIX form and
then classified:

1. Intrinsic control paths are private.
2. A match in `private_paths` is private.
3. Otherwise, a match in `public_paths` is public.
4. Otherwise, `default_access` decides.

All paths involved in an operation are classified. That includes both sides of
a rename, the preimage of a deletion, a newly created destination, test and
fixture paths, generated manifests, and every path in a multi-file proposal.
One private or indeterminate path rejects the complete candidate or proposal;
Gludd must not retain or apply only its public hunks.

For a symbolic link, Gludd classifies both the link path and the resolved target
identity before any content read. A target outside the bound repository, a
private target, a loop, a missing intermediate target, or a changed target
identity is blocked. Parent directories of a new path receive the same
containment and symlink checks. Case ambiguity, traversal, alternate separators,
and Unicode-normalization ambiguity fail closed.

## Enforcement at every self-improvement boundary

The policy is not a late filter. One project-bound policy decision must guard
every ingress, transition, and egress:

| Boundary | Required behavior |
| --- | --- |
| Discovery | Classify path metadata before opening candidate content; private candidates are ineligible. |
| Context assembly | Build an explicit public-file allowlist; do not traverse private dependencies for context. |
| Prompting | Reject a prompt containing a protected path, source fragment, objective, test diagnostic, or canary before either local or remote invocation. |
| Model response | Classify the entire declared change manifest before persisting response bodies; a mixed response is rejected atomically. |
| Plan and cache | Bind records to repository, baseline tree, canonical policy digest, and public path-set digest; never reuse across a mismatch. |
| Verification | Keep test stdout/stderr in an opaque local verifier; expose only bounded typed outcomes to the model-facing loop. |
| Apply | Reclassify create, replace, delete, rename, mode, and link operations immediately before staging writes. |
| Promotion | Re-read and re-digest policy and diff paths before approval and again before branch promotion. |
| Learning | Do not record a model outcome, training sample, failure corpus item, or memory for a policy-blocked candidate. |
| Cleanup | Remove rejected response bodies and staging artifacts through the same repository/run ownership record. |

The verifier may execute application tests that call private code, but raw test
output must never re-enter prompts, correction loops, traces, caches, or durable
evidence. It returns only a typed result such as pass, timeout, or private-boundary
failure. When a useful diagnostic cannot be proven public, the proposal is
rejected without a diagnostic-assisted retry.

Provider selection cannot weaken this contract. A local-model prompt and an
Azure request body are both external sinks from the policy engine's point of
view. Changing model, retrying, falling back between providers, resuming a
worker, or replaying a plan repeats the checks; it never inherits a previous
allow decision without revalidation.

## Plan identity and TOCTOU defense

At plan creation, Gludd binds these facts into the approval identity:

- repository identity and immutable baseline tree;
- canonical policy digest and schema version;
- classified candidate path-set digest;
- provider/model identity and bounded attempt budget;
- generated change manifest digest.

The worker independently loads the policy from the bound repository and
compares its digest with the request. Gludd recomputes the binding before model
invocation, before apply, and before promotion. Any policy, baseline, path,
link-target, or manifest drift invalidates the plan and discards its staging
area. A user must create a new plan under the new policy.

Legacy plans are eligible only under the canonical missing-policy digest and
only if all other legacy-safety checks pass. They are not grandfathered into a
newly private project.

## Observable without leaking

Privacy must not make the self-improvement loop invisible. Every run emits
structured spans, phase transitions, and heartbeats. Policy events contain
bounded enums, counts, and opaque identities, never protected text:

- `SELF_IMPROVE_POLICY_LOADED`: schema, default mode, rule counts, repository
  binding digest, and policy digest;
- `SELF_IMPROVE_POLICY_BLOCKED`: stage enum, reason enum, policy digest, and an
  opaque subject digest;
- `SELF_IMPROVE_LEARNING_SKIPPED`: plan digest and policy reason;
- normal phase events: provider class, model identity, elapsed time, attempt
  count, public file count, and bounded resource counters;
- a periodic heartbeat for any phase lasting more than a few seconds.

Repository paths, objectives, source, diffs, prompts, model responses, test
output, environment values, and diagnostics from protected material are
forbidden in logs, spans, metric labels, exception strings, plan/result JSON,
approval records, caches, model-performance rows, failure corpora, and memory.
Subject identifiers crossing a trust boundary use a project-scoped keyed digest
rather than a raw path or a globally correlatable plain hash.

A local explain operation can hash a user-supplied path and compare it with an
event subject without opening that file. This keeps decisions auditable without
putting sensitive names into durable traces.

## Project isolation

Policy state is never process-global. Caches and workers are keyed by repository
identity, baseline tree, and policy digest. A worker refuses a request whose
repository binding or policy digest does not match its own materialized project.

The isolation acceptance case creates projects A and B with the same relative
path. A marks it private while B marks it public, then the test reverses those
policies and interleaves concurrent runs. Each provider sees only the project
and revision authorized for that call. No prompt, plan, cache entry, trace,
learning row, or memory from A may satisfy or influence B's private candidate.

## End-to-end acceptance matrix

The implementation is not complete until a hermetic acceptance target exercises
the full managed path, not only the matcher. Each case uses a fresh random
canary in protected source, a protected filename, and protected test output.
Sink spies search request bodies and all durable artifacts for those canaries.

### Policy and matching depth

- missing policy and valid empty policy preserve public behavior;
- exact file, directory, basename, `*`, `**`, and default-private matching;
- overlapping public/private rules prove private-wins independently of order;
- malformed JSON, duplicate keys/rules, unknown keys, unsupported schema,
  oversize documents/rules, invalid types, and no-op patterns disable the run;
- traversal, absolute/drive paths, alternate separators, case collisions, and
  Unicode-normalization ambiguity fail closed;
- the policy file and approval records remain immutable;
- link to a private target, link outside the repository, link loop, link retarget,
  and a symlinked parent for a new file all fail closed.

### Operation and lifecycle breadth

- private create, replace, delete, rename source, rename destination, mode
  change, test, fixture, and mixed multi-file proposal are rejected atomically;
- a public sibling proposal succeeds, proving policy enforcement did not simply
  disable the system;
- a private candidate performs zero model calls and writes no model-performance
  outcome;
- a model response that smuggles one private path among many public paths is
  rejected before apply and leaves every public file unchanged;
- policy changes after discovery, plan, model response, verification, apply
  staging, and approval each invalidate the run before the next side effect;
- retry, provider fallback, worker handoff, cancellation, crash recovery, and
  resume all reload and compare the same project-bound policy;
- concurrent two-project runs with inverted policies prove cache, trace, worker,
  and model-context isolation.

### Non-leak assertions

After every success, rejection, timeout, cancellation, and injected crash, the
test asserts the protected canaries and raw protected path are absent from:

- local-model prompts and fake-Azure request bodies;
- provider retries and fallback requests;
- stdout/stderr, structured events, spans, metrics, and exceptions;
- proposal, plan, result, approval, and staging files;
- prompt/result caches and retained run directories;
- evidence, model-performance, failure-corpus, training, and memory stores;
- commits, promoted diffs, cleanup diagnostics, and crash-recovery records.

The test also asserts that the original private blob and its Git identity are
unchanged. An opaque digest may exist only where this contract explicitly
allows one.

## Local and GitHub Actions execution

The required `make test-self-improve-private-policy` target must run the same
hermetic suite locally and in the standard GitHub Actions pipeline. Its default
matrix uses:

| Lane | Credentials | Model behavior | Required proof |
| --- | --- | --- | --- |
| Fake local model | None | Deterministic recorder and scripted proposals | Prompt non-leak, blocking, public success, retries, cleanup |
| Fake Azure adapter | None | Azure-shaped recorder with scripted responses | Request non-leak and provider-parity contract |
| Worker/restart | None | Serialized worker handoff and replay | Binding, cache isolation, TOCTOU, recovery |
| Two-project concurrency | None | Interleaved deterministic calls | No policy or artifact cross-talk |

The standard CI suite must not require network access, download a model, or use
cloud credentials. It runs with bounded workers and namespaced temporary roots.
The GHA shard manifest pins the suite so omission is a gate failure.

A real local GGUF lane and a live Azure lane are opt-in qualification tests.
They use only a generated synthetic repository and public canaries. The Azure
lane reads credentials from the existing secret pointers, never prints them,
uses a bounded token/cost budget, and cleans up through provider ownership
records. A skipped live lane is not evidence for the hermetic contract; the
fake-provider suite remains mandatory everywhere.

Coverage for the policy implementation and sink guards must be at least 85%
aggregate branch coverage and at least 75% for every measured file. All failure
branches named in this document require explicit tests rather than coverage
exclusions.

## ZDD, resources, and rollback

Policy activation does not restart or interrupt the user's application. A run
pins one policy digest; newly started runs see a newly committed policy, while
an in-flight mismatch aborts only that improvement run. Generated changes stay
in a run-owned staging area until verification and the final policy check pass,
then promotion is atomic. No partially filtered proposal is publishable.

Every resource is namespaced by repository and run identity. Hard ceilings
cover policy bytes and rules, candidate files and bytes, prompt and response
bytes, retries, provider calls, worker concurrency, test time, subprocesses,
temporary disk, and retained evidence. Heartbeats report bounded counters.
Cancellation and every failure converge through idempotent cleanup that acts
only on recorded owned resources.

Rollback is configuration-first: setting `default_access` to `private` with no
public rules immediately makes new self-improvement work ineligible without
taking the application down. Reverting the public change commit restores code;
private paths are never part of that commit. A rollback never deletes or
rewrites the policy, private code, provider credentials, or another run's
artifacts.

## Primary and practitioner evidence (reviewed 2026-09-04)

Primary semantics and documented product boundaries:

- Git's [`gitignore` manual](https://git-scm.com/docs/gitignore) says ignore
  rules concern intentionally untracked files and do not affect files already
  tracked. Therefore `.gitignore` alone cannot protect tracked business logic.
  It also defines slash, wildcard, `**`, anchoring, and negation behavior.
- The maintained
  [`python-pathspec` documentation](https://github.com/cpburnz/python-pathspec)
  explains that `GitIgnoreSpec` follows Git's real edge-case behavior more
  closely than a hand-written or basic matcher. This supports using the mature
  library plus a smaller fail-closed policy subset.
- GitHub's official
  [Copilot content-exclusion documentation](https://docs.github.com/en/copilot/concepts/context/content-exclusion)
  currently notes that excluded-file semantic information can arrive through
  an IDE, exclusions are unsupported in some edit/agent surfaces, and symlinks
  and remote filesystems are not covered. Those are documented limitations of
  that product, not evidence that Gludd has leaked data. They motivate Gludd's
  all-stage and symlink checks.

Long-lived practitioner reports:

- GitHub Community
  [discussion #113696](https://github.com/orgs/community/discussions/113696)
  began on 2024-03-22 with requests for local file/folder exclusion. A
  2024-03-25 participant reported seeing names from an `.env` file open in a
  different project appear in suggestions; requests continued through
  2026-02-11.
- GitHub Community
  [discussion #120113](https://github.com/orgs/community/discussions/120113)
  began on 2024-04-18 asking for whitelist/default-deny behavior for licensing
  and intellectual-property risk. Follow-ups on 2025-08-06 and 2025-12-22 said
  excluded indexing or allowlisting still did not meet their needs.
- Cursor's
  [file-ignore and auditable-security thread](https://forum.cursor.com/t/file-ignore-lists-and-auditable-security/2507)
  began on 2024-01-23. The requester asked for verifiable exclusion before
  production adoption; on 2024-09-08 another user reported that ignored files
  remained referenceable after reindexing.

Forum posts are user reports, not controlled reproductions, and do not prove a
vendor defect or a Gludd defect. The defensible inference is narrower: users
need a default-deny option, consistent enforcement across product surfaces,
project isolation, observable decisions, and an automated way to prove that
protected canaries never reach any sink. Those requirements are made explicit
and testable above.
