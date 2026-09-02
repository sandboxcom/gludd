# Self-Improvement Codex Parity

## The rule

Gludd may propose a repository change locally, but it may not promote that change
unless the candidate is at least as complete as an independent Codex reference on
the same baseline. Passing one test is insufficient. The comparison binds exact
file scope, canonical tests, warnings, aggregate and per-file coverage, Ruff,
mypy, docstrings, Markdown, resource cleanup, one atomic commit, a clean worktree,
changed-line economy, elapsed time, and Git patch identity.

The local model has no shell, Git, or direct system-tool authority. It emits
strict, bounded proposal shards; Gludd validates and merges those into one
unchanged proposal schema before applying anything. Gludd applies exact
replacements transactionally in an isolated worktree and runs every operation
through an explicit Make target.

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

Local GGUF inference runs in one dedicated Make worker per candidate attempt,
with a parent-owned process group. One digest-bound request carries every ordered
`PromptPlan` shard. The worker constructs one `LocalProposalGateway`; that
gateway lazily constructs one public `Llama` instance and uses it for a
32-token structured-output canary and every sequential shard. The canary and
proposal share the same system prefix, so the pinned `Llama.generate` path can
reuse its live in-memory longest token prefix. Correctness does not require a
cache hit: each complete prompt is independently schema-constrained and parsed.
Gludd configures neither a `LlamaRAMCache` nor disk cache and starts no daemon
or server.

Prompt and proposal files live in one unique temporary exchange directory.
Inputs reject symlinks, output is written with fsync plus atomic replacement only
after every shard succeeds, and the parent always removes the exchange. The
parent streams output and emits a 15-second heartbeat. Start failure, body
failure, cancellation, timeout, or a native exit such as 139 becomes bounded
evidence, reaps the owned process group when one exists, and cannot terminate the
comparison orchestrator.

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

A schema is supplied through `response_format`, not merely
`{"type": "json_object"}`; JSON-only mode proves syntax but cannot require the
proposal fields. Managed `PromptPlan` requests put immutable
`baseline_sha`, `task_id`, tests, and Make commands in a separate atomic
0600 `contract.json` file. The model emits only compact `e` (edits) and
`c` (commit subject) fields; each edit uses `p/a/z` for path, old text, and
new text. Operation is not model-supplied: the worker deterministically infers
create from empty/non-empty text, delete from non-empty/empty text, and replace
from non-empty/non-empty text. Both-empty edits and any extra operation field
are rejected. The worker expands those keys with the trusted contract and then
calls `ProposalManifest.from_json`, so output compaction does not make model
text authoritative. Legacy single-string callers retain the complete manifest
schema. The prompt names the compact shape because llama.cpp documents that a
schema constrains sampling but is not injected into the model prompt.

Grammar acceptance is not promotion evidence. Gludd checks that the 32-token
canary and each compact proposal finish with `stop`, parses one complete object,
expands trusted fields, and validates it again through
`ProposalManifest.from_json`. A length stop, missing closing object, absent or
extra compact field, wrong baseline, unsafe path, oversized value, or invalid
Make command remains a failed proposal. A complete-looking object carrying a
`length` finish reason is still rejected. The runner must classify and record
that bounded failure against the immutable model identity before trying a
different eligible candidate; it must not silently retry unconstrained output
or weaken required fields.

The managed proposal budget is 1,536 tokens per shard, versus the legacy
4,096-token compatibility path. A July 2026 managed acceptance motivated this
bound: Metal-enabled Qwen2.5-Coder-0.5B and DeepSeek-Coder-1.3B ran for 176.62
and 154.80 seconds respectively before both consumed 4,096 tokens without
closing the proposal. Trusted-field elision plus the shorter budget gives a
minimal exact edit room to finish while rejecting the same runaway behavior in
at most 37.5% of its former decode budget. A follow-up managed acceptance showed
why operation inference must also have one authority: Qwen1.5B finished with
`stop` after 1,142 tokens and 47.31 seconds but labeled a non-empty old/new
pair as create, while SmolLM2 finished after 135 tokens and 14.62 seconds but
labeled an empty/non-empty pair as delete. Removing that redundant label makes
both shapes unambiguous without weakening the authoritative manifest validator.
The canary rejects unsupported chat or grammar behavior before reading a
task-sized completion.

Grammar construction and inference remain inside the owned proposal worker. The
model factory runs once after request admission. Each shard then invokes the
documented `create_chat_completion` method on that same live model and validates
one `ProposalManifest` before it can join the batch. Observability emits only
an allowlisted finish classification, non-negative token counts, phase, and
budget; it never includes completion text. A converter exception, native crash,
cancellation, or timeout therefore tears down the same process group, exchange
directory, and model lease as any other failed attempt. The parent applies one
300-second deadline to the complete candidate attempt; no partial batch is
published. The retained worker does not start a persistent llama.cpp server or
add another cache owner.

## Complete attempt evidence identity

A prompt-plan digest is not an attempt identity by itself. The output protocol
can change while the prompt bytes remain identical: the compact schema can add
or remove a field, the canary can change, a decode budget can move, or the
strict decoder can infer operations differently. Reusing a failure recorded
under the earlier behavior would incorrectly exclude a model that has never
tried the new contract.

The runner therefore hashes one canonical JSON descriptor containing the exact
`PromptPlan.protocol_digest` plus all managed output semantics:

- compact protocol version, full JSON Schema, and system prompt;
- canary request, expected object, schema, and token bound;
- proposal token bound, deterministic temperature and seed, required stop
  policy, and allowlisted finish classifications; and
- exact compact root/edit fields, edit bounds, empty-text operation mapping,
  authoritative manifest schema and limits, path policy, batch protocol, and
  strict parent-decoder version.

Sorted-key serialization makes retry-identical inputs stable. Per-attempt
diagnostic content, model path, cache path, hardware details, process
identifiers, timestamps, and credentials are intentionally absent because they
do not define the proposal language. The versioned validation-retry descriptor
is included because its marker selection, tail and feedback limits, canonical
classifications, and prompt framing do affect the next candidate output. Any
material protocol constant used by inference, validation, or retry changes the
digest. The runner sends that exact digest to both historical-failure loading
and every success/failure record and prints it beside the narrower prompt
digest.

Existing prompt-only outcome records are preserved for audit. Exact-digest
selection makes them ineligible to exclude a model under the new protocol; no
database clearing or evidence mutation is needed. A rollback to an earlier
protocol naturally restores that earlier identity and its matching evidence.
This is a zero-downtime evidence migration because it changes neither the store
schema nor the running daemon.

This boundary also responds to long-lived practitioner evidence.
[llama-cpp-python issue 1483](https://github.com/abetlen/llama-cpp-python/issues/1483)
reports that a structured response format can end in native process failure,
while
[discussion 614](https://github.com/abetlen/llama-cpp-python/discussions/614)
records persistent grammar/parser alignment failures and diagnostics containing
local execution details. Gludd treats schema, canary, budget, decoder, and retry
feedback as one versioned attempt rather than assuming prompt equality proves
behavioral equality or replaying backend output into another model.

Regression tests first reproduced the old identity collision, then pin every
protocol component independently, canonical ordering, invalid digest rejection,
deterministic identity under an unchanged descriptor, identity rotation when
retry semantics change, exact runner load/record wiring, and preservation but
ineligibility of prompt-only evidence.

## Typed validation retry feedback

A managed local attempt exposed an ordering defect: verbose backend
initialization occupied the beginning of a captured failure while the stable
proposal-validation marker and actionable edit-contract reason arrived at the
end. The former retry copied the first 1,000 characters, so the next candidate
received noise and lost the reason. The parity document records the behavior,
not the raw worker or model output.

The runner now selects the final stable proposal-error marker. If no marker is
available, classification examines only the final 512 UTF-8 bytes rather than
the noisy head. That bounded candidate can select an allowlisted canonical
validation phrase and type; it is never copied into the prompt. Unrecognized
detail becomes `<redacted>`. Paths, assignments that may carry credentials,
backend logs, and model completion text therefore cannot cross the retry
boundary. The complete typed feedback is capped at 256 bytes and the identical
suffix is applied to every original shard.

The immutable `PromptPlan` digest and every shard focus remain stable during
retries. The complete attempt digest additionally binds the one runtime-used
retry descriptor, so an unchanged algorithm is deterministic while any version,
marker, bound, classification, or framing change starts a new evidence identity.
This keeps the strict proposal manifest and decode-token limits unchanged.

The change follows ZDD: proposal retries run only in the isolated, unpromoted
worktree and do not mutate daemon or database state. Each attempt retains its
existing five-minute worker deadline, one model lease, bounded capture, atomic
exchange, and process-group cleanup. Rollback restores the prior runner and
protocol descriptor; earlier evidence remains immutable and naturally matches
its earlier digest. A failed or rolled-back attempt leaves the running service
and previously admitted model artifact available.

## Context capacity and overflow

For the pinned llama-cpp-python 0.3.24 high-level API, `n_ctx=0`
selects the model-native training context. The versioned `Llama`
implementation loads model metadata, replaces zero with
`self._model.n_ctx_train()`, and passes that value into context creation.
The vendored llama.cpp C interface at commit `af6528e6d` defines the same
contract: context parameter `n_ctx` is text context and zero means "from
model." Model-native therefore means the training-context value encoded in the
GGUF metadata. It does not mean unlimited context or a context automatically
sized to current memory headroom. The proposal gateway requests `n_ctx=0`, so its effective context is the
candidate GGUF's own `n_ctx_train` value. Each candidate may advertise a
smaller or much larger context, so admission must use the loaded context rather
than a catalog assumption.

The acceptance boundary is the effective context reported by the created
context, not the input byte limit. Gludd must tokenize the fully rendered chat
prompt with the same model and template that inference will use, then prove:

`rendered prompt tokens + reserved decode tokens <= effective n_ctx`.

A prompt at or beyond the context window is rejected by llama-cpp-python, and
generation is limited to the remaining context. Gludd must perform the equivalent
check before decoding so context overflow is deterministic evidence rather than
a late native failure. It must not silently truncate task text, Codex evidence,
tests, or proposal-schema guidance, and it must not rely on context shifting;
either would change the comparison being evaluated.

Zero is semantically correct when the model-native window is also
resource-admissible, but native capacity is not free capacity. llama.cpp
allocates context-dependent KV state, so a model advertising a very large
training window can materially increase RAM or VRAM use and initialization
time even for a small prompt. Resource admission must account for the proposed
effective context before worker launch. If native capacity cannot fit the
managed headroom, Gludd may select an explicit smaller context no greater than
`n_ctx_train` only when the full prompt and decode reserve still fit. It fails
closed otherwise; it never requests a context above training capacity without
a separately reviewed and tested RoPE policy.

Context construction remains inside the owned proposal subprocess. Preflight
rejection, allocation failure, timeout, cancellation, or overflow releases the
model lease, terminates the worker process group, removes the exchange
directory, and leaves no candidate change. This preserves zero downtime: the
running Gludd daemon and any previously admitted revision remain available
while a context policy is evaluated. Rollback restores the previous worker
policy without a database migration, daemon restart, or cache-format change.

## CPU-bounded prompt decomposition

The former prompt builder allocated 48,000 characters to baseline source and
then appended the task and proposal contract. In the measured eight-file
reference from baseline `80b381bd8` to `6463324cfcf6db9b9a2f9ec203e0bd3862a1e80e`,
that produced a 52,017-byte prompt. StarCoder2 3B, Qwen2.5-Coder 3B, and
CodeLlama 7B each reached the owned 300-second timeout without a proposal.
Worse, lexicographic truncation spent the source budget on the first path and
did not show later required files. Larger-model escalation cannot correct that
input-boundary defect.

Gludd now decomposes a reference into deterministic, disjoint prompt shards of
at most three focus paths. A base shard is at most 12,000 bytes and any
diagnostic retry remains at most 16,384 bytes. Every shard repeats the exact
task ID, baseline SHA, complete global changed-path list, complete test list,
and ordered canonical Make commands. Each focus file includes its full byte
count and SHA-256 identity. Files of at most 4,096 bytes are included in full.
For larger Python files, the existing
`general_ludd.planning.repo_map.RepoMapBuilder` Tree-sitter parser supplies
syntax boundaries and Gludd selects exact numbered symbol and task-relevant
line windows. Omission is explicit, digest-bound, and never presented as full
source. A shard may edit every and only its focus paths; the merger rejects
missing, duplicate, broadened, wrong-baseline, wrong-task, wrong-test, or
reordered-command output through the original strict `ProposalManifest`
parser before candidate worktree creation. The immutable task, baseline, global
paths, tests, and commands precede a named shard-specific suffix, so every
inference shares a long byte-identical prefix. The single retained model receives
those prompts in canonical order, enabling the pinned runtime's live longest-
prefix reuse without another cache owner. Tests pin exact prefix bytes and order;
correctness and promotion never depend on a measured cache hit.

Each plan publishes a lowercase SHA-256 protocol digest over canonical JSON
containing its protocol version, ordered shard focus paths, exact prompts, and
measured source bytes. Identical inputs produce the same digest, a material
prompt or partition change produces a different digest, and validation retries
carry the original digest. The runner emits it with plan and shard events. A
follow-up evidence-key migration can therefore distinguish the old 52,017-byte
protocol's failures from this bounded protocol without guessing from timestamps.

This decomposition preserves the comparison rather than summarizing it. Gludd's
conversation `ContextCompactor` was considered but is intentionally not used:
its lossy summary cannot supply verbatim unique `old_text`. If exact excerpts
are insufficient, the generated replacement cannot validate against the
baseline and the attempt fails closed. It never silently guesses omitted patch
text. The model candidate preflight uses the largest individual rendered shard,
not the old aggregate/truncated prompt.

Shards execute sequentially under one acquired-model lease, inside one
parent-owned worker process and one total 300-second candidate-attempt deadline,
so neither model instances nor resident workers multiply with shard count. One
live heartbeat covers the attempt. Start failure removes the exchange; body
failure, timeout, or cancellation also terminates and reaps the worker process
group. The worker publishes one atomic batch only after every shard validates.
Failure stops all remaining shards, leaves no stale exchange or proposal file,
and the enclosing `finally` releases the lease. No persistent llama.cpp server
is introduced.

The production llama-cpp-python constructor now asks that exact loaded runtime's
`llama_supports_gpu_offload()` function before setting `n_gpu_layers`. A
supporting build receives `-1` (all layers subject to llama.cpp's own
admission); an unsupported, missing, or failed probe receives `0` and remains
on CPU. It does not infer Metal capability from PyTorch MPS. `n_ctx=0`
continues to select the GGUF native context. The explicit constructor seam is
available to later hardware policy, while the default remains gated by the
runtime that will actually execute inference. On the measured 8 GiB host, the
ordered catalog still prefers 3B Q4 models and treats 7B Q4 as the upper
resource guidance rather than assuming accelerator capacity.

The change is zero-downtime. Prompt planning and inference occur only in an
unpromoted isolated worktree, with no daemon-state or database migration.
Rollback restores the previous runner/comparison commit; the parent terminates
and reaps an in-flight retained worker, removes its sole exchange, and leaves the
admitted model artifact valid for later lease-governed use.

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

## Catalog admission and Hub authentication resilience

A catalog name is discovery input, not proof that a public repository, requested
file, or immutable revision exists. The eight configured coding artifacts were
audited against the public Hub on 2026-09-02. Six configured repository/file
pairs resolved:

- `bartowski/Qwen2.5-Coder-0.5B-Instruct-GGUF` /
  `Qwen2.5-Coder-0.5B-Instruct-Q4_K_M.gguf`: `main` resolved to
  [`69a2c192eed24297fb09a34d8ba948b8624cc3e2`](https://huggingface.co/bartowski/Qwen2.5-Coder-0.5B-Instruct-GGUF/commit/69a2c192eed24297fb09a34d8ba948b8624cc3e2).
- `bartowski/Qwen2.5-Coder-1.5B-Instruct-GGUF` /
  `Qwen2.5-Coder-1.5B-Instruct-Q4_K_M.gguf`: `main` resolved to
  [`1af47f78b1f9b0c242fabe43f7a365d5a67f3207`](https://huggingface.co/bartowski/Qwen2.5-Coder-1.5B-Instruct-GGUF/commit/1af47f78b1f9b0c242fabe43f7a365d5a67f3207).
- `TheBloke/CodeLlama-7B-Instruct-GGUF` /
  `codellama-7b-instruct.Q4_K_M.gguf`: `main` resolved to
  [`2f064ee0c6ae3f025ec4e392c6ba5dd049c77969`](https://huggingface.co/TheBloke/CodeLlama-7B-Instruct-GGUF/commit/2f064ee0c6ae3f025ec4e392c6ba5dd049c77969).
- `bartowski/Qwen2.5-Coder-3B-Instruct-GGUF` /
  `Qwen2.5-Coder-3B-Instruct-Q4_K_M.gguf`: `main` resolved to
  [`7c137640ef0332dfedb229f2504c58d83ed4307a`](https://huggingface.co/bartowski/Qwen2.5-Coder-3B-Instruct-GGUF/commit/7c137640ef0332dfedb229f2504c58d83ed4307a).
- `bartowski/Phi-3-mini-4k-instruct-GGUF` /
  `Phi-3-mini-4k-instruct-Q4_K_M.gguf`: `main` resolved to
  [`e1447f6da0be91f91683c5d19f938d4f51122d88`](https://huggingface.co/bartowski/Phi-3-mini-4k-instruct-GGUF/commit/e1447f6da0be91f91683c5d19f938d4f51122d88).
- `bartowski/SmolLM2-1.7B-Instruct-GGUF` /
  `SmolLM2-1.7B-Instruct-Q4_K_M.gguf`: `main` resolved to
  [`1f03464768bfcc0319fc50da8ff5fb20b6417ba2`](https://huggingface.co/bartowski/SmolLM2-1.7B-Instruct-GGUF/commit/1f03464768bfcc0319fc50da8ff5fb20b6417ba2).

Two configured repository IDs could not be resolved as public repositories by
direct lookup or Hub model search during that audit. Exact public artifacts that
currently resolve are available, but changing publisher or artifact identity is
a reviewed catalog migration rather than an automatic fallback:

- `bartowski/DeepSeek-Coder-1.3B-Instruct-GGUF` /
  `DeepSeek-Coder-1.3B-Instruct-Q4_K_M.gguf` was unresolved. The verified
  replacement is
  [`TheBloke/deepseek-coder-1.3b-instruct-GGUF`](https://huggingface.co/TheBloke/deepseek-coder-1.3b-instruct-GGUF/tree/main)
  / `deepseek-coder-1.3b-instruct.Q4_K_M.gguf`, whose `main` resolved to
  [`4595af8c3dff738094bd6c86054dfb5a90d5c41e`](https://huggingface.co/TheBloke/deepseek-coder-1.3b-instruct-GGUF/commit/4595af8c3dff738094bd6c86054dfb5a90d5c41e).
- `bartowski/StarCoder2-3B-Instruct-GGUF` /
  `StarCoder2-3B-Instruct-Q4_K_M.gguf` was unresolved. The verified
  replacement is
  [`QuantFactory/starcoder2-3b-instruct-GGUF`](https://huggingface.co/QuantFactory/starcoder2-3b-instruct-GGUF/tree/main)
  / `starcoder2-3b-instruct.Q4_K_M.gguf`, whose `main` resolved to
  [`6eb3cec2979e1e2275e14dc07032a4f69af78aaa`](https://huggingface.co/QuantFactory/starcoder2-3b-instruct-GGUF/commit/6eb3cec2979e1e2275e14dc07032a4f69af78aaa).

These `main` values are dated observations, not durable identities. Admission
must resolve the selected repository and exact filename to a full commit, then
bind that immutable revision and the downloaded digest in Gludd's ownership
manifest. An unresolved configured entry is discarded before model-byte
transfer; Gludd must not silently substitute a search result because publisher,
license, quantization, and trust identity may differ.

Authentication failure is also not sufficient evidence that a catalog entry is
private. Hugging Face sends a locally saved token by default even for requests
that do not require authentication, and its repository-not-found exception uses
the same 401 response for an invalid repository and an inaccessible private
repository. For a catalog entry explicitly declared public, one authenticated
metadata 401 may therefore be followed by exactly one anonymous metadata probe.
That retry must use the same repository and filename. It is forbidden for a
gated or private entry, and failure of both probes leaves the candidate
ineligible. This bounded retry never authorizes an arbitrary replacement or a
download before identity validation.

Offline reuse is narrower still. A previously admitted artifact may be used
without resolving mutable `main` only when Gludd's durable ownership manifest
already binds the exact immutable revision, contained path, size, and digest and
all are revalidated. A Hub `refs` entry, partial snapshot, or ambient shared
cache alone is not ownership evidence. An offline cache miss or validation
failure stops before a worker starts.

Catalog correction follows a zero-downtime handoff. Gludd retains the leased
current artifact while a reviewed replacement is resolved, downloaded, hashed,
and durably admitted; selection changes only after validation. Failure leaves
the old admitted revision usable. Rollback restores the prior catalog selection,
and reclamation cannot remove either revision until its final lease ends. Each
candidate consumes at most one normal metadata probe and one anonymous retry,
does not start an inference process while ineligible, and transfers no model
bytes until repository, filename, and revision admission succeeds.

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
- 12,000-byte base prompt shards, at most three focus paths per shard, a
  16,384-byte hard retry boundary, 4,096 bytes of exact context per file, and a
  262,144-byte total request admission bound.
- One owned worker and one lazily constructed `Llama` instance per candidate
  attempt; shards execute sequentially with no daemon, server, or explicit cache.
- One 32-token same-instance canary, then 1,536 compact decode tokens per managed
  shard and one 300-second total owned worker timeout per candidate attempt. The
  legacy single-string compatibility path remains 4,096 tokens; the strict merged
  proposal remains roughly 1.25 MiB at most.
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
- [llama-cpp-python 0.3.24](https://github.com/abetlen/llama-cpp-python/releases/tag/v0.3.24)
  is the exact pinned release at commit
  `26633bd1a2eaf7fd0567cc5eaec8b0165a7ea0bd`. Its
  [versioned `Llama` source](https://github.com/abetlen/llama-cpp-python/blob/26633bd1a2eaf7fd0567cc5eaec8b0165a7ea0bd/llama_cpp/llama.py)
  resolves `n_ctx=0` through `LlamaModel.n_ctx_train()`; the maintained
  [API reference](https://llama-cpp-python.readthedocs.io/en/latest/api-reference/#llama_cpp.Llama)
  describes zero as selecting context from the model. The same versioned
  `Llama.generate` implementation compares the next tokenized prompt with its
  live input IDs, preserves the longest matching prefix, removes only the
  divergent tail from the context sequence, and evaluates the suffix. Gludd
  reaches that behavior through the public `Llama` object and
  `create_chat_completion`; it does not call private KV-state helpers. The
  versioned
  [low-level binding](https://github.com/abetlen/llama-cpp-python/blob/26633bd1a2eaf7fd0567cc5eaec8b0165a7ea0bd/llama_cpp/llama_cpp.py)
  exposes `llama_supports_gpu_offload()`; Gludd queries that binding instead
  of inferring support from an unrelated framework. The
  [versioned server settings](https://github.com/abetlen/llama-cpp-python/blob/26633bd1a2eaf7fd0567cc5eaec8b0165a7ea0bd/llama_cpp/server/settings.py)
  define `n_gpu_layers=0` as CPU and `-1` as moving all layers to the GPU.
- The
  [0.3.24 changelog](https://github.com/abetlen/llama-cpp-python/blob/26633bd1a2eaf7fd0567cc5eaec8b0165a7ea0bd/CHANGELOG.md)
  pins bundled llama.cpp commit `af6528e6d`. That commit's
  [C API header](https://github.com/ggml-org/llama.cpp/blob/af6528e6d/include/llama.h)
  gives `llama_context_params.n_ctx` the same zero-from-model contract and
  separately exposes the model training-context accessor.
- [SWE-bench evaluation](https://github.com/SWE-bench/SWE-bench) evaluates a
  generated patch by applying it to a reproducible repository environment and
  running its tests. Gludd adds repository-specific static, resource, and Git
  identity evidence.
- [Git patch-id](https://git-scm.com/docs/git-patch-id.html) documents the stable
  patch identity used for Codex equivalence.
- [Aider architect/editor mode](https://aider.chat/docs/) separates planning
  from file editing; Gludd similarly separates local proposal generation from
  the Make-mediated executor.
- [Aider's repository-map documentation](https://aider.chat/docs/repomap.html)
  documents budgeted source maps built from important classes, functions,
  signatures, and exact critical lines. Gludd reuses its existing Tree-sitter
  repository-map boundary, but preserves file digests and exact replacement
  validation because its output is an executable patch proposal.
- [Tree-sitter query syntax](https://tree-sitter.github.io/tree-sitter/using-parsers/queries/1-syntax.html)
  documents typed syntax-node matching and explicit `ERROR` nodes. Gludd uses
  the maintained parser already locked by the project instead of inventing a
  Python source parser.
- [Hugging Face cache-system reference](https://huggingface.co/docs/huggingface_hub/en/package_reference/cache)
  documents scanning an explicit cache directory and preparing immutable
  revision deletion through `delete_revisions`. The returned strategy exposes
  expected freed space before its separate `execute` step.
- [Hugging Face cache guide](https://huggingface.co/docs/huggingface_hub/guides/manage-cache)
  explains shared blob and snapshot storage, revision-aware deletion, and
  incomplete downloads. Gludd uses the supported revision graph but narrows it
  further with application ownership and live leases.
- [Hugging Face authentication quickstart](https://huggingface.co/docs/huggingface_hub/en/quick-start#authentication)
  documents that a saved token is sent by default even for requests that do not
  require authentication, and that `HF_HUB_DISABLE_IMPLICIT_TOKEN=1` disables
  that implicit credential.
- [Hugging Face Hub error definitions](https://github.com/huggingface/huggingface_hub/blob/main/src/huggingface_hub/errors.py)
  document that `RepositoryNotFoundError` covers both an invalid repository
  identifier and an inaccessible private repository, with a 401 response in the
  example. Gludd therefore does not infer privacy from status alone.
- The dated repository, artifact, and immutable commit links in the catalog
  admission section are direct Hugging Face Hub evidence for every resolving
  configured artifact and both reviewed replacement candidates.

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
- [llama-cpp-python issue 416](https://github.com/abetlen/llama-cpp-python/issues/416)
  records a 2023 application reaching the binding's deterministic
  `Requested tokens exceed context window` exception at 512 tokens. This
  requires Gludd to preflight the fully rendered prompt instead of discovering
  that boundary after starting proposal inference.
- [llama-cpp-python issue 1692](https://github.com/abetlen/llama-cpp-python/issues/1692)
  has tracked a practitioner's GPU-build mismatch since August 2024: the binding
  reported no offload despite other GPU evidence. Gludd treats a false or failed
  binding probe as a safe CPU fallback; it never forces layers based on PyTorch
  or device-name inference.
- [llama.cpp discussion 229](https://github.com/ggml-org/llama.cpp/discussions/229)
  has recorded CPU prompt-ingestion latency and repeated-prefix work since
  March 2023. Participants reported multi-minute reprocessing when a prompt
  prefix changes, so immutable small shards are preferable to repeatedly
  decoding a monolithic 52 KiB prompt.
- [llama-cpp-python issue 1369](https://github.com/abetlen/llama-cpp-python/issues/1369)
  records a 2024 prefix-cache miss caused by token-boundary differences near a
  changing prompt suffix. Gludd pins byte-identical complete prefixes and
  deterministic order, but it never makes correctness depend on reuse or claims
  a latency hit without measurement.
- [llama.cpp discussion 1394](https://github.com/ggml-org/llama.cpp/discussions/1394)
  records long-lived practitioner complexity around shifting and reusing KV
  state. Gludd avoids direct KV manipulation and owns only the public model
  lifetime within its bounded worker.
- [llama.cpp discussion 8652](https://github.com/ggml-org/llama.cpp/discussions/8652)
  is a July 2024 unanswered practitioner report of CPU responses slowing as
  prompt context grows despite smaller configured contexts. The unresolved
  report supports measuring and bounding the actual rendered input rather than
  treating model capacity as a latency guarantee.
- [llama.cpp issue 865](https://github.com/ggml-org/llama.cpp/issues/865)
  records a Ryzen CPU user observing roughly a minute of delay after adding
  retrieved context while a tiny prompt began almost immediately. This
  long-lived input-size failure class is pinned by the 51,859-byte regression.
- [llama.cpp discussion 4054](https://github.com/ggml-org/llama.cpp/discussions/4054)
  records practitioners confirming in November 2023 that changing `n_ctx`
  changes preallocated memory, while actual prompt length does not reduce that
  initial reservation. Native context is therefore included in admission
  accounting even for short tasks.
- [llama.cpp issue 3716](https://github.com/ggml-org/llama.cpp/issues/3716)
  records an October 2023 M1 Max report where a 16,224-token context crashed
  while loading a long-context model. The owned subprocess and headroom gate
  keep this failure class outside the running daemon.
- [huggingface_hub issue 3390](https://github.com/huggingface/huggingface_hub/issues/3390)
  records a long-running practitioner report of apparent duplicate disk use
  between Hub files and the Xet chunk cache. Gludd therefore owns a dedicated
  model root and never treats an ambient multi-library cache as reclaimable.
- [huggingface_hub issue 4420](https://github.com/huggingface/huggingface_hub/issues/4420)
  demonstrates why cache-reported totals are insufficient: a model repository
  consuming about 915 MiB was omitted while the listing reported only 416.3 KiB.
  Independent filesystem headroom and fail-closed metadata handling cover that
  class of accounting gap.
- [huggingface_hub issue 3445](https://github.com/huggingface/huggingface_hub/issues/3445)
  reproduces a 401 for public `Qwen/Qwen2.5-Coder-7B-Instruct` metadata when an
  empty token is supplied, while the anonymous path works. This is direct
  evidence for a same-identity, one-shot anonymous metadata probe.
- [Hugging Face forum thread 19714](https://discuss.huggingface.co/t/error-401-client-error-unauthorized-for-url/19714)
  has collected reports since June 2022 of 401 responses for private, gated,
  misspelled, and browser-readable public artifacts. The long-lived ambiguity is
  why status code alone cannot drive catalog mutation.
- [huggingface_hub issue 1305](https://github.com/huggingface/huggingface_hub/issues/1305)
  records a missing cache reference surfacing as `FileNotFoundError` in 2023.
  Gludd consequently requires its own complete immutable manifest and fails
  closed on an offline cache miss instead of treating cache presence as enough.
- [Aider issue 3010](https://github.com/Aider-AI/aider/issues/3010)
  records January 2025 reports from local Ollama/DeepSeek users whose model
  denied seeing a file already added to the coding chat. Gludd therefore derives
  required paths from the live Codex reference and rejects a proposal that omits
  any of them; the model's returned edit list is never proof of complete scope.
- [podman-compose issue 1061](https://github.com/containers/podman-compose/issues/1061)
  has remained open since October 2024 after a nested `podman build` reported
  status 5 while its wrapper returned status 0. Gludd treats the exact exit
  status of every Make-mediated subprocess as authoritative and cannot publish
  a candidate merely because a wrapper returned normally.

The operational consequence is fail-closed validation, bounded raw-output
diagnostics, isolated native inference, deterministic tool routing, and
lease-aware application ownership instead of treating syntactically plausible
model text or cache-library totals as sufficient evidence.
