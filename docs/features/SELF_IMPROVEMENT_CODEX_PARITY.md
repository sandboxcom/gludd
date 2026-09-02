# Self-Improvement Codex Parity

## The rule

Gludd may propose a repository change locally, but it may not promote that change
unless the candidate is at least as complete as an independent Codex reference on
the same baseline. Passing one test is insufficient. The comparison binds exact
file scope, canonical tests, warnings, aggregate and per-file coverage, Ruff,
mypy, docstrings, Markdown, resource cleanup, one atomic commit, a clean worktree,
changed-line economy, elapsed time, and Git patch identity.

The local model has no shell, Git, or direct system-tool authority. It emits one
strict, bounded proposal. Gludd applies exact replacements transactionally in an
isolated worktree and runs every operation through an explicit Make target.

## Model and tool routing

The router prefers an existing mature repository tool for a mechanically proven
change class. The first route is Markdown trailing-whitespace repair through
`make fix-docs-drift`. Gludd runs the tool in an isolated copy of the exact
baseline, derives minimal unique replacements only for Codex-scoped files, and
then evaluates those replacements through the normal Codex comparison. Python
or ambiguous changes are not guessed into this route and continue to the local
model proposal worker.

This is intentional. A small model should not regenerate a document when a
deterministic formatter can make the exact repair faster and more reliably.
Using the local model where it adds judgment, and mature tools where the change
is mechanical, matches the tool-using behavior expected from a capable coding
agent.

## Local inference lifecycle

Local GGUF inference runs in a dedicated Make worker with a parent-owned process
group. Prompt and proposal files live in a unique temporary exchange directory;
inputs reject symlinks, output is written with fsync plus atomic replacement, and
the parent always removes the exchange. The parent streams output and emits a
15-second heartbeat. Timeout or a native exit such as 139 becomes bounded
evidence and cannot terminate the comparison orchestrator.

The llama.cpp JSON grammar deliberately omits `minLength` and `maxLength`.
Those keywords caused a native grammar-expansion crash with the one-megabyte
runtime text bound. Python parsing remains authoritative for all count and byte
limits.

## Structured proposal decoding

The implementation boundary is llama-cpp-python's documented chat
`response_format` API, using `{"type": "json_object", "schema": ...}` with
the existing proposal schema. The repository pins llama-cpp-python 0.3.24. Its
chat formatter converts that public request shape to
`LlamaGrammar.from_json_schema`, and its schema converter emits object rules
from `required`, `additionalProperties`, `const`, `enum`, array bounds,
and supported patterns. Gludd must not maintain another JSON-Schema-to-GBNF
converter or depend on private formatter helpers. It also must not use the newer
OpenAI `{"type": "json_schema"}` spelling unless the pinned runtime explicitly
supports it.

The full schema is supplied through `response_format`, not merely
`{"type": "json_object"}`; JSON-only mode proves syntax but cannot require
`schema_version`, `task_id`, `tests`, or the other proposal fields. The
prompt still names the required shape because llama.cpp documents that a schema
constrains sampling but is not injected into the model prompt. This gives the
model both semantic guidance and a token-level structural boundary.

Grammar acceptance is not promotion evidence. Gludd still checks the completion
finish reason, extracts one complete object, and validates it again through
`ProposalManifest.from_json`. A length stop, missing closing object, absent
required field, extra field, wrong baseline, unsafe path, oversized value, or
invalid Make command remains a failed proposal. The runner must classify and
record that bounded failure against the immutable model identity before trying
a different eligible candidate; it must not silently retry unconstrained output
or weaken required fields.

Grammar construction and inference remain inside the owned proposal worker. A
converter exception, native crash, cancellation, or timeout therefore tears down
the same process group, exchange directory, and model lease as any other failed
attempt. The bounded schema is compiled once per worker invocation; it does not
start a persistent llama.cpp server or add another cache owner. Decode tokens,
context, elapsed time, proposal bytes, and diagnostic tails retain their existing
limits.

This change is zero-downtime because it changes only an isolated, unpromoted
candidate path. It has no daemon or database migration and cannot interrupt a
current Gludd deployment. Rollback restores the previous gateway commit; model
manifests and leases are format-independent and remain valid. If the public
structured-output path is incompatible with a pinned runtime or model, the
attempt fails closed and the previously admitted revision remains available.

## Automatic model acquisition and ownership

The normal self-improvement path no longer requires an operator to run a model
download target first. Immediately before the first non-mechanical proposal,
Gludd selects a configured coding model, resolves the Hub `main` reference to a
40-character commit, checks managed quota and filesystem headroom, and downloads
that exact revision into a dedicated cache. A mechanical task does not acquire a
model at all. `SELF_IMPROVE_MODEL_PATH` remains an optional test and operator
override, not a production prerequisite.

A successful managed acquisition records a bounded, atomically replaced
ownership manifest. The manifest binds model and repository IDs, filename,
immutable revision, cache-contained path, byte size, SHA-256 digest, and last-use
time. An exclusive acquisition claim prevents two Gludd processes from writing
the same repository revision concurrently. A cache hit is reusable only after
its path, size, and digest are verified again.

Every use has a lease containing the artifact digest plus the owner PID and
process birth time. The birth time prevents PID reuse from keeping a stale lease
alive. Normal return, validation failure, timeout, cancellation, and other
exceptions all release the lease in the runner's `finally` boundary. Model
inference remains separately responsible for its owned llama.cpp process group;
the model cache does not leave or adopt an inference daemon.

Explicit paths have deliberately different ownership. Gludd verifies and hashes
the file and leases it while active, but does not download, move, manifest,
quota-account, or evict it. The caller retains storage ownership. Automatic
reclaim considers only cache entries whose valid Gludd manifest proves ownership,
and it never deletes the ambient Hugging Face cache or an arbitrary directory.

## Measured historical comparison

The small fixture uses baseline
`aa740a8f0cf95c42acdbf16a84540658b871b32a` and independent Codex reference
`5f326d115045fcc3175424bd38d64e783ac1aa20`.

- The Qwen2.5 0.5B Q4 model made two deterministic attempts. Each exhausted the
  4,096-token proposal budget after about 126-128 seconds by repeating canonical
  Make commands inside edit text. Both outputs were incomplete JSON, so Gludd
  applied no repository change.
- The mechanical route completed the full comparison in 159.59 seconds,
  including a 106,912-test collection check. It scored 100/100, changed exactly
  one file and two diff lines, produced one clean commit, emitted zero warnings,
  and was Git patch-equivalent to the Codex reference.
- The larger dependency-floor fixture is rejected before inference because its
  estimated 6,430 output tokens exceed the 4,096-token local decode ceiling.
  This avoids a long attempt that cannot be complete.

These results are model/task compatibility evidence, not a claim that the 0.5B
model can implement arbitrary code changes.

## Git and system-tool efficiency

- Reference metadata is cached only for identical read-only Make operations.
  Mutations are never cached.
- Baseline and candidate worktrees are exact-SHA, namespaced, independently
  cleaned, and never merged unless every comparison blocker is absent.
- Git operations use repository Make seams for worktree creation, staging,
  commit, status, cleanup, and patch equivalence. The local model cannot emit a
  raw Git command.
- `git patch-id` equivalence distinguishes semantically identical patches from
  commit-message or object-ID differences.
- Canonical Make commands are deduplicated while preserving order. Execution
  stops at the first failure and returns its exact command, return code, and a
  bounded output tail to the next attempt.
- Secrets in retry evidence are redacted before they re-enter a prompt.
- A proposal outside the exact Codex file scope is rejected before a candidate
  worktree or tests are started.

## Zero-downtime development

This feature changes no live daemon state. A candidate is created in a new
worktree, fully validated, committed once, and cleaned before another attempt.
Promotion remains an explicit later operation. Rollback is deletion of the
unmerged candidate worktree; the development and master branches remain
unchanged.

Model revisions also follow a zero-downtime handoff. A newly downloaded revision
is not admitted as usable owned state until its immutable revision, contained
path, size, and digest have been validated and its manifest is durable. An active
lease pins the current artifact throughout inference, so reclamation cannot
remove it while a newer revision is acquired or evaluated. Failed acquisition
or validation leaves the previously admitted revision usable. Rollback stops
selecting the candidate revision and permits its removal only after its final
lease is gone.

The worker owns its temporary files and process group on normal completion,
validation failure, timeout, cancellation, and native process death. No test
harness cleanup compensates for missing application ownership.

## Resource bounds and fail-closed cleanup

- 32 edits, 64 tests, 32 Make commands, and 1 MiB of proposal edit text.
- 256 KiB prompt and roughly 1.25 MiB serialized proposal exchange.
- 4,096 decode tokens and a 900-second owned worker timeout.
- 2 MiB observable command capture with 15-second heartbeats.
- 128 Codex-reference files.
- One candidate worktree per attempt; commands stop after the first failure.
- The managed model cache defaults to an 8 GiB quota and 2 GiB free-space
  reserve. `GLUDD_SELF_IMPROVE_MODEL_QUOTA_BYTES` and
  `GLUDD_SELF_IMPROVE_MODEL_RESERVE_BYTES` provide explicit byte overrides.
- `GLUDD_SELF_IMPROVE_MODEL_CACHE` may relocate the dedicated cache without
  transferring ownership to an ambient Hugging Face cache.
- Reclaim orders only Gludd-owned, unleased revisions by least recent use. It
  invokes Hugging Face revision deletion, verifies the owned artifact vanished,
  and then removes the ownership manifest.
- Admission checks both manifest-accounted bytes and independent filesystem free
  space. Cache-library totals alone cannot authorize another download.
- Malformed manifests or leases, escaped paths, unverifiable process owners,
  digest drift, deletion that leaves the artifact present, and exhausted
  headroom with all candidates leased stop the operation. Gludd does not guess
  ownership or widen deletion scope to recover space.
- Interrupted files or third-party cache content without a valid Gludd ownership
  manifest are never silently removed. They still reduce measured filesystem
  headroom, so acquisition stops instead of consuming the reserve.

## Evidence and prior art

Official sources:

- [llama-cpp-python JSON and JSON Schema mode](https://github.com/abetlen/llama-cpp-python#json-and-json-schema-mode)
  documents `create_chat_completion(response_format={"type": "json_object",
  "schema": ...})` as the public schema-constrained chat API.
- [llama-cpp-python chat-format source](https://github.com/abetlen/llama-cpp-python/blob/main/llama_cpp/llama_chat_format.py)
  converts that request shape to `LlamaGrammar.from_json_schema`; the
  [schema-converter source](https://github.com/abetlen/llama-cpp-python/blob/main/llama_cpp/llama_grammar.py)
  constructs the required-property and additional-property grammar rules. These
  are upstream seams Gludd reuses rather than reimplementing.
- [llama.cpp grammar documentation](https://github.com/ggml-org/llama.cpp/blob/master/grammars/README.md)
  defines the supported JSON Schema subset and warns that the schema constrains
  output without being shown to the model, which is why Gludd also describes the
  contract in the prompt and validates the result independently.
- [SWE-bench evaluation](https://github.com/SWE-bench/SWE-bench) evaluates a
  generated patch by applying it to a reproducible repository environment and
  running its tests. Gludd adds repository-specific static, resource, and Git
  identity evidence.
- [Git patch-id](https://git-scm.com/docs/git-patch-id.html) documents the stable
  patch identity used for Codex equivalence.
- [Aider architect/editor mode](https://aider.chat/docs/) separates planning
  from file editing; Gludd similarly separates local proposal generation from
  the Make-mediated executor.
- [Hugging Face cache-system reference](https://huggingface.co/docs/huggingface_hub/en/package_reference/cache)
  documents scanning an explicit cache directory and preparing immutable
  revision deletion through `delete_revisions`. The returned strategy exposes
  expected freed space before its separate `execute` step.
- [Hugging Face cache guide](https://huggingface.co/docs/huggingface_hub/guides/manage-cache)
  explains shared blob and snapshot storage, revision-aware deletion, and
  incomplete downloads. Gludd uses the supported revision graph but narrows it
  further with application ownership and live leases.

Practitioner evidence:

- [llama-cpp-python issue 1483](https://github.com/abetlen/llama-cpp-python/issues/1483)
  has remained open since May 2024 after a user supplied a schema with four
  required fields and observed the Python kernel die during streamed structured
  generation. Gludd therefore compiles and runs grammar-constrained inference
  only in its owned subprocess and treats native death as bounded evidence.
- [llama-cpp-python discussion 614](https://github.com/abetlen/llama-cpp-python/discussions/614)
  records unresolved user difficulty hand-building and parsing GBNF since
  August 2023. Gludd uses the maintained schema API instead of asking models or
  operators to construct grammar text.
- [llama.cpp issue 20164](https://github.com/ggml-org/llama.cpp/issues/20164)
  reports long-lived structured/tool-use reliability problems.
- [llama.cpp issue 15012](https://github.com/ggml-org/llama.cpp/issues/15012)
  records JSON/schema-constrained generation difficulties in real integrations.
- [huggingface_hub issue 3390](https://github.com/huggingface/huggingface_hub/issues/3390)
  records a long-running practitioner report of apparent duplicate disk use
  between Hub files and the Xet chunk cache. Gludd therefore owns a dedicated
  model root and never treats an ambient multi-library cache as reclaimable.
- [huggingface_hub issue 4420](https://github.com/huggingface/huggingface_hub/issues/4420)
  demonstrates why cache-reported totals are insufficient: a model repository
  consuming about 915 MiB was omitted while the listing reported only 416.3 KiB.
  Independent filesystem headroom and fail-closed metadata handling cover that
  class of accounting gap.

The operational consequence is fail-closed validation, bounded raw-output
diagnostics, isolated native inference, deterministic tool routing, and
lease-aware application ownership instead of treating syntactically plausible
model text or cache-library totals as sufficient evidence.
