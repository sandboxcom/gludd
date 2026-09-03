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

Legacy compact-v3 deliberately omits `minLength` and `maxLength`: applying those
keywords to its former one-megabyte runtime text bound caused native grammar
expansion to fail. Compact-v4 uses small, parent-derived bounds instead. Its
grammar permits at most one edit per distinct allowed start coordinate, capped
at four, and caps each `z` at 768 code points so one item cannot exceed 3,072
UTF-8 bytes. The v4 decode
ceiling is 4,096 tokens; every generative grammar repetition is finite, and a
non-`stop` finish is still rejected. Python parsing remains authoritative for
all count, scope, decoded-byte, and exact-application limits. Compact-v3 retains
its historical 1,024-token policy.

## Structured proposal decoding

The implementation boundary is llama-cpp-python's documented public chat API.
Gludd passes the same trusted schema through both
`response_format={"type": "json_object", "schema": ...}` and an explicit
`grammar=LlamaGrammar.from_json_schema(...)`. The repository pins
llama-cpp-python 0.3.24, whose generic chat formatter converts `response_format`
to a sampler grammar and whose public chat signature also accepts `grammar`.
The explicit argument is a backstop for chat handlers that forward grammar but
do not perform the generic response-format conversion. A handler that honors
`response_format` may replace it with the equivalent grammar; both inputs derive
from the identical per-shard schema. Compact-v4 specializes that schema from the
validated editable-range marker, while compact-v3 retains its static schema.
Gludd does not maintain another
JSON-Schema-to-GBNF converter or depend on private formatter helpers. Grammar
construction failure aborts before inference rather than falling back to plain
JSON. The attempt identity binds the decoding mode, schema strategy, token
policy, prompt digest, and separate SHA-256 digests of the canary and canonical
proposal schemas, so constrained and unconstrained v4 attempts cannot alias.

A schema is supplied through `response_format`, not merely
`{"type": "json_object"}`; JSON-only mode proves syntax but cannot require the
proposal fields. Managed `PromptPlan` requests put immutable
`baseline_sha`, `task_id`, tests, and Make commands in a separate atomic
0600 `contract.json` file. Every prompt carries one parent-authored, validated
focus-path marker. Managed output uses compact protocol v4: the model emits only
`e` (edits), and every edit contains exactly `s`, `n`, and `z`. `s` is a
one-based line boundary in the immutable baseline, `n` is the number of
baseline lines consumed, and `z` contains logical replacement lines rather than
trusted line-separator bytes. For each shown
contiguous `Lx` through `Ly` section, a zero count may insert at any boundary
`s=x` through `s=y+1`, including immediately before or after the section. A
boundary wholly inside an omitted gap remains forbidden. Every coordinate
addresses the original snapshot, never the result of an earlier edit. The owned
worker decodes one typed `CompactSpanProposal` per shard and publishes the
complete span batch atomically. The parent resolves each span with
`splitlines(keepends=True)`, restores the immutable baseline's LF or CRLF
convention and final-newline state, and materializes the complete result once in
canonical coordinate order. It infers create, delete, or replace from that
trusted before/after state. The schema-2 manifest carries the complete immutable
file as the replace preimage, so application requires byte-exact snapshot
equality without searching for a unique local anchor. Neither complete snapshot
is copied into retry diagnostics.

Path, old text, operation, and commit subject are not model-supplied. An absent
file accepts only one non-empty insertion at `{s: 1, n: 0}`; deleting a file
requires one span covering the complete shown file with empty `z`. Existing-file
spans may arrive in any order; the parent sorts them by immutable snapshot
coordinate before requiring distinct, non-overlapping, in-range spans wholly
inside content disclosed by that prompt shard. A zero-count edit requires one of
the closed boundary intervals derived from a shown contiguous section. Duplicate
insertion boundaries, Boolean or non-integral coordinates, edits into omitted
regions, and a final file identical to the baseline are rejected. An existing
empty file may receive an insertion but is not treated as an absent-file create.
Compact v4 accepts one to four spans per shard, and the existing 3,072 UTF-8-byte
limit applies to their combined `z` text. The parent expands the validated spans
to a complete trusted snapshot manifest and calls `ProposalManifest.from_json`.
Schema 2 caps the combined complete preimage and result at 8,391,680 bytes;
schema 1 retains its historical 1 MiB content cap. The line coordinate is
therefore a compact addressing mechanism rather than new authority.
The prompt names the compact shape because llama.cpp documents that a schema
constrains sampling but is not injected into the model prompt.

Grammar acceptance is not promotion evidence. Gludd checks that the 32-token
canary and each compact proposal finish with `stop`, parses one complete object,
resolves every span against the hash-bound snapshot, expands trusted fields, and
validates the result again through `ProposalManifest.from_json`. A length stop,
missing closing object, absent or extra compact field, invalid span, wrong
baseline, unsafe path, oversized value, or invalid Make command remains a failed
proposal. A complete-looking object carrying a `length` finish reason is still
rejected. The runner must classify and record that bounded failure against the
immutable model identity before trying a different eligible candidate; it must
not silently retry unconstrained output, search for a nearby match, or weaken
required fields.

The managed proposal budget is 1,024 tokens per single-file shard, versus
the legacy 4,096-token compatibility path. A July 2026 managed acceptance
motivated the bound: Metal-enabled Qwen2.5-Coder-0.5B and
DeepSeek-Coder-1.3B ran for 176.62 and 154.80 seconds respectively before both
consumed 4,096 tokens without closing the proposal. Trusted-field elision plus
the shorter budget preserves room for minimal exact edits while rejecting the
same runaway behavior after at most 25% of the former decode budget. A follow-up
managed acceptance showed why operation inference must have one authority:
Qwen1.5B finished with `stop` after 1,142 tokens and 47.31 seconds but labeled
a non-empty old/new pair as create, while SmolLM2 finished after 135 tokens and
14.62 seconds but labeled an empty/non-empty pair as delete.

A later live catalog run exposed the same authority defect in the remaining
fields. Qwen2.5-Coder-1.5B produced otherwise valid bounded edits but supplied
an invalid commit subject; SmolLM2-1.7B supplied a no-op replacement. Path and
commit subject were already known to the parent, so normalizing either model
value would preserve unnecessary authority. Compact protocol v3 removes both
from the wire shape, binds the trusted focus marker and fixed commit subject
into attempt identity, states the distinct-text and 3,072-byte rules in the
prompt, and keeps strict rejection for no-op edits. The canary rejects
unsupported chat or grammar behavior before reading a task-sized completion.

The 3 September 2026 compact-v4 catalog run exposed two narrower protocol
failures. Qwen returned a valid span at the edge of a shown section, but the
parent incorrectly required both adjacent baseline lines to be visible.
Closed boundary intervals now admit the deterministic before-first and
after-last coordinates while still rejecting boundaries inside hidden gaps.
DeepSeek completed 2,308 bytes with `stop` but surrounded the object with text.
Gludd still rejects that as not one complete JSON object and never extracts a
fence or substring. Explicit upstream grammar constraining addresses generation;
the strict decoder, bounded diagnostic, v4 retry classification, lease release,
and atomic exchange cleanup remain the fail-closed backstops.

The subsequent `self-improve-catalog-v4-grammar-live-20260903` run proved that
generic JSON grammar was necessary but insufficient: both Qwen and DeepSeek
returned strict compact objects whose insertion coordinate was outside every
shown section. New prompt shards therefore begin with one canonical,
parent-authored editable-range marker. The worker reads only that leading marker,
never `L...` source text or model output, and specializes `s` to a bounded integer
enum containing the closed insertion boundaries of the trusted half-open ranges.
Hidden-gap coordinates are absent from the grammar, while the unchanged parent
validator still checks `n`, consumed lines, immutable snapshots, complete-file
preimages, and exact application. More than 2,048 possible `s` values fails before grammar
construction instead of widening the schema. The marker is canonical ASCII JSON,
is independently capped at 16,384 bytes before parsing, rejects duplicate or
overlapping sections, and deduplicates the shared boundary of adjacent half-open
sections into an ordered enum. Coordinates beyond the maximum baseline byte space
also fail before enum expansion, preventing sparse huge-integer markers. Markerless
stored v4 prompts retain their prior static schema and strict parent validation
without reinterpretation; the marker, encoding and size rules, enum strategy, cap,
prompt digest, and decoder revision rotate attempt identity for new plans.

An insertion-scope rejection now carries typed parent-only telemetry: SHA-256 of
the focus path, received `s` and `n`, and a capped rendering of allowed half-open
sections and closed boundaries. The model-authored `z`, baseline source, raw path,
and raw completion are never fields. Events, terminal output, corpus replay, and
retry prompts share the same 512-byte feedback ceiling; cleanup and atomic apply
semantics are unchanged.

The following `self-improve-catalog-v4-scoped-live-20260903` run proved that
protocol compliance was not yet code quality. Both candidates produced strict,
in-scope manifests that applied exactly, then failed Python collection with a
syntax error: Qwen emitted a fragment ending in `smollm2-135M)`, while DeepSeek
copied a parent environment assignment into an assertion. The generic cause was
an overloaded editor prompt. It repeated parent-only range and focus bindings,
canonical Make commands, and repository-wide metadata beside the source that the
model was asked to emit.

New compact-v4 inference keeps the task objective and exact focus path visible,
but sends a deliberately small editor body between explicit task, requirements,
and baseline delimiters. The worker validates and removes the canonical parent
range/focus prefix before chat; that prefix still derives the grammar and strict
decoder scope. Make commands, environment-style bindings, tests, baseline IDs,
and unrelated paths never enter model-visible content. The existing
`create_chat_completion` path remains responsible for applying each GGUF's native
`tokenizer.chat_template`; Gludd does not guess or duplicate a model-specific chat
format.

After exact atomic apply, changed `.py` files receive a parent syntax preflight
before any Make command. `tokenize.detect_encoding` selects the declared Python
source encoding and `ast.parse` performs the language check; non-Python paths and
whole-file deletes are skipped safely. Failure produces at most 192 ASCII bytes:
a stable type, SHA-256 path identity, and bounded line/offset. Replacement text,
source, raw model output, paths, and environment values are never included. The
normal comparison, persistent failed-outcome recording, retry path, and worktree
cleanup remain authoritative; successful preflight does not replace the full
test and static evaluation.

Model planning now derives a bounded `CodeTaskShape` only from the immutable
prompt plan. A multi-file change, any changed test file, or more than 8,192 source
bytes requires a coding artifact of at least 900 MiB, so the maintained catalog
starts this class at Qwen2.5-Coder-1.5B (936 MiB) instead of allowing positive
historical evidence to anchor Qwen2.5-Coder-0.5B. A single non-test file of at
most 8,192 bytes may still use a smaller evidence-backed candidate. An exact
60-point failure remains persistent same-identity evidence and raises the next
plan above the failed artifact size. The capability policy is part of compact-v4
attempt identity. Pre-policy compact-v4 approvals therefore require reapproval;
stored v1/v2 plans and compact-v3 prompts retain their prior identity and model
selection semantics without reinterpretation.

The next live acceptance must show the scoped catalog task selecting no artifact
below 900 MiB, model-visible prompts containing the objective and focus baseline
but no parent Make/environment bindings, every applied Python candidate passing
the syntax preflight before Make begins, a 60-point failure selecting a strictly
larger next candidate, and unchanged exact-scope rejection and cleanup evidence.

The `self-improve-catalog-quality-live-20260903` run verified the capability
floor and grammar/scope controls but exposed two shared termination defects.
Qwen2.5-Coder-1.5B and SmolLM2-1.7B each reached `finish=length` at exactly the
inherited 1,024-token ceiling on shard one, so neither proposal was decoded or
applied. The dynamic schema bounded `s`, but its fixed sixteen-item array and
unbounded `z` still admitted runaway output. The per-attempt events correctly
said retry-v4; the terminal handler then inspected only the wrapped generic
decode error and regressed to retry-v3.

Compact-v4 now uses a concise completion instruction, a 4,096-token ceiling,
and a finite per-shard schema. Adjacent shown ranges are canonicalized and
`maxItems` is the number of distinct allowed `s` coordinates, capped at four.
This preserves several independent changes in one shard without exposing the
sixteen-item overgeneration surface. Each `z.maxLength` is 768 code points, a useful
single-edit ceiling that is no larger than 3,072 UTF-8 bytes even at four bytes
per code point. A subprocess
test against locked llama-cpp-python 0.3.24 calls
`LlamaGrammar.from_json_schema` and verifies that `maxItems` and `maxLength`
become finite nested GBNF optionals with no unbounded repetition. The unchanged
parent decoder measures the aggregate actual decoded bytes across all items, so
multiple strings, JSON escapes, and Unicode cannot bypass the authoritative
3,072-byte total.

Terminal failures now carry an internal, validated compact protocol through at
most eight cause/context wrappers. Public feedback unwraps only that private
typed carrier; model names, source, replacement text, and raw output remain
absent. Arbitrary error text containing an old v4 digest remains retry-v3, so
classification never guesses from strings. The prompt, schema strategy,
decoding mode, and token policy rotate new v4 attempt identity. Compact-v3 keeps
its 1,024-token and retry-v3 behavior without reinterpretation. Within one exact
identity, persisted 60-point failures for both former candidates advance the
next plan to Qwen2.5-Coder-3B and CodeLlama-7B; evidence is not migrated across
the rotated identity.

The next live acceptance must show both proposal shards ending with `stop`
before 4,096 tokens, one strict object per shard, no scope or decoded-byte
rejection, and either an exact atomic apply followed by syntax/full evaluation
or bounded retry-v4 feedback. Repeating the same approved identity after both
60-point failures must select only larger capable artifacts. Exchange cleanup
and lease release must remain visible on every exit.

The follow-up `self-improve-catalog-finite-live-20260903` run proved that finite
was not yet small enough. Qwen stopped normally after 3,217 completion tokens,
then its valid object was rejected only because spans arrived out of order.
SmolLM2 stopped after 3,850 tokens but returned 12,629 bytes that were not one
complete object. Both failures occurred under a grammar that still admitted up
to sixteen edits. Compact v4 now caps a shard at four edits in both the static
and range-specialized schemas. Locked llama-cpp-python 0.3.24 tests compile that
exact four-item bound; v3 retains its historical sixteen-item schema and decoder.

The parent accepts unordered input only by sorting parsed spans against the same
immutable snapshot. It then rejects duplicate starts and overlaps exactly as
before; no fuzzy or relative interpretation is introduced. Decoded aggregate
UTF-8 size is checked before canonical ordering, so an oversized response cannot
produce the misleading order diagnosis seen in the Qwen run. Count and byte
failures expose only fixed bounded states such as `received_edits=>4` and
`received_content_bytes=>3072`; model text, source, raw paths, and token payloads
remain absent. The changed schema, prompt, decoder ordering, and grammar strategy
rotate v4 attempt identity without changing v1-v3 storage or public exception
behavior.

The next live acceptance must show each shard using at most four edits, completing
as one strict object before the 4,096-token ceiling, and either applying the
canonically sorted non-overlapping spans or returning bounded retry-v5 feedback.
Duplicate or overlapping spans, aggregate content above 3,072 decoded bytes, and
partial output must remain rejected with exchange cleanup and lease release.

A subsequent catalog live run showed that valid syntax-bounded output was still
not operationally diagnosable. StarCoder2 and Qwen3B both produced valid compact
proposals. Qwen3B changed the exact two approved files and five lines, but its
first quality command failed in about one second; the parent reported only the
derived 60-point score and generic blockers. The raw command tail was retained as
retry diagnostics, which was simultaneously too opaque for operators and too
broad to pass safely to another model.

Evaluation now publishes one bounded `SELF_IMPROVE_EVALUATION_EVENT` after atomic
apply, syntax preflight, every approved Make command, the parent-owned test-count,
stage, commit, clean check, patch comparison, and cleanup. Events contain only an
allowlisted phase, command kind, SHA-256 command identity, bounded return code and
duration, and a fixed failure class. They never contain replacement/source text,
stdout, stderr, secrets, or absolute paths. The first actionable failure is run
through `compact_failure_diagnosis` and persisted as canonical
`self-improve-evaluation-diagnosis-v2` JSON. Only that exact bounded object may be
included in the next compact-v4 prompt; malformed or injected diagnostics become
a fixed `diagnosis_unavailable` object. Approved paths, commands, and scope remain
unchanged.

This diagnosis/retry contract is bound only into compact-v4 attempt identity, so
pre-observability v4 approvals require reapproval and opaque historical failures
cannot alias the corrected retry behavior. Stored v1-v3 identities and retry
semantics remain unchanged. Post-sort duplicate starts and genuine overlaps now
have separate typed classes; neither is mislabeled as input ordering. The next
live acceptance must show the exact failed phase and command hash after any
60-point result, a bounded diagnosis in the next candidate prompt, cleanup on
every exit, and no raw command output or authored text in logs or artifacts.

The diagnosis-aware live run whose identifier begins `e190` then proved that the
retry artifact was consumed—the SmolLM2 retry prompt grew by 560 bytes—but its
parent `run-watched` stream contained zero `SELF_IMPROVE_EVALUATION_EVENT` lines.
The CLI composition root had explicitly passed `evaluate_attempt` back into the
factory. That selected the compatibility adapter, which intentionally lacks the
new progress-sink parameter, instead of the factory's sink-bound default adapter.
The CLI now uses that default adapter, whose sink flushes each lifecycle line to
standard output. When a compact-v4 retry consumes a validated diagnosis, the same
composition root emits exactly one bounded `SELF_IMPROVE_RETRY_DIAGNOSIS` line
containing only protocol, phase, fixed failure class, return code, duration, and
command SHA-256. Invalid input first becomes the fixed `diagnosis_unavailable`
record; raw commands, output, authored text, secrets, and paths are never copied.
That output wiring change alone left compact-v4 protocol and attempt identity
unchanged, so durable diagnosis-aware failures from before the wiring fix remained
applicable.
The next live run must show evaluation lines before the retry record and the next
attempt, with both attempts retaining the same approved identity.

The flushed `self-improve-catalog-quality-live-20260903` follow-up exposed a
different usefulness limit. StarCoder2 stopped at the syntax preflight. Qwen3B
consumed the safe diagnosis but then proposed 360 changed lines, while the approved
Codex reference `80b381bd87f32487d784964ce93566e3b016b191` changes only two files
with 33 insertions and four deletions. Compact v4 now permits at most 64 consumed
old lines and 64 replacement lines in one edit, and at most 96 changed lines across
all shards, counting consumed plus replacement lines. Sixty-four is larger than
the reference's complete 37-line change, and 96 provides more than 2.5 times that
observed budget while rejecting the 360-line rewrite before snapshot materialization,
worktree creation, or syntax evaluation.

The range-specialized llama.cpp schema binds `n.maximum` to the smaller of 64 and
the shown section size. The existing 768-code-point `z` ceiling remains grammar
bound; newline count and the cross-shard sum remain authoritative parent checks
because JSON Schema does not express those relational limits. Retry-v5 tells the
next model that the prior candidate failed and to solve independently from the
trusted baseline, never to repair unseen output, while retaining the smallest-diff
instruction. Syntax diagnosis schema 3 adds only an allowlisted category, path
SHA-256, and bounded line and column; it omits parser message, source, and path.
These prompt, schema, validation, and diagnosis-v2 semantics rotate compact-v4
attempt identity and require reapproval. Legacy v1-v3 serialization and identity
branches remain byte-stable.

This keeps the strict-editor lesson from the long-lived
[Aider issue 3651](https://github.com/Aider-AI/aider/issues/3651) and
[Aider editing guidance](https://aider.chat/docs/troubleshooting/edit-errors.html):
give a struggling editor a smaller, focused job. It does not copy or fuzzily repair
the prior model's failed patch. The next live acceptance must admit a proposal no
larger than the 37-line reference, reject any over-96-line proposal with only safe
count telemetry, publish syntax coordinates without authored text, and release all
leases and temporary worktrees on either path.

A later bounded live pass exposed a byte-boundary defect after those controls:
Qwen3B proposed 48 changed lines against the 37-line reference but still reached
syntax failure, while StarCoder2 supplied valid bounded immutable coordinates that
were rejected because a repeated baseline slice was not a unique string. The old
compiler concatenated `z` directly with the untouched suffix. Thus an interior
logical replacement such as `changed` could become `changednext\n`; insertion at
EOF in a file without a final newline could similarly join two logical lines.

Compact v4 now makes newline materialization a trusted-parent responsibility.
Model text is split into logical lines, rendered with the baseline's LF or CRLF
separator, and joined to untouched neighbors while preserving whether the file
ended in a newline. Immutable coordinates are then compiled into one schema-2
whole-file replacement (or complete create/delete), and both comparison and apply
require the current file to equal that complete preimage. Repeated blank lines,
repeated closing lines, and empty files therefore need no fuzzy search or unique
substring. The approved-plan limit remains 4,194,304 bytes, and schema 2's
8,391,680-byte aggregate preimage/result ceiling bounds materialization and its
serialized artifact. Schema-1 replace semantics and public error text remain
unchanged.

The production retry record now includes diagnosis-v2's allowlisted category,
path SHA-256, line, and column; absent coordinates use fixed sentinel values.
Replacement text, source, raw paths, parser messages, and command output remain
excluded. The line-materialization policy, schema version, complete-preimage
rule, byte ceiling, prompt digest, and strict decoder revision rotate compact-v4
attempt identity without changing stored v1-v3 evidence. The next live acceptance
must apply the 37-line reference-shaped change without token concatenation or a
unique-anchor rejection, preserve LF/CRLF and final-newline state, expose only
bounded safe syntax coordinates on failure, and clean every temporary worktree.

That acceptance run reached a narrower recoverable state: Qwen3B proposed 15
changed lines, exact apply returned zero, the parent parser reported line 35,
column 9, and cleanup completed. Compact v4 now permits exactly one same-model
syntax regeneration when an approved attempt remains. Eligibility requires the
canonical diagnosis-v2 `syntax_preflight`/`python_syntax` tuple, a path SHA-256
matching one compact focus path, and successful cleanup of the rejected attempt.
The repair consumes the next approved attempt; it is never an extra inference.

The parent retains the already validated compact objects only in a repr-hidden,
in-memory carrier. Each repair shard receives its own canonical `{"e":[...]}`
object, capped at 4,096 ASCII bytes after JSON escaping, plus the source-free
diagnosis. The original baseline snapshots, focus paths, half-open shown ranges,
prompt-protocol digest, four-edit ceiling, 3,072-byte aggregate replacement cap,
and 96-line budget remain authoritative. The same artifact is reacquired and its
lease released for regeneration. Candidate outcome persistence waits until the
repair resolves; after a failed repair, a different model receives the original
independent-candidate prompt and never the rejected `z` text.

This follows the bounded-job lesson in the long-lived
[Aider issue 3651](https://github.com/Aider-AI/aider/issues/3651): give the model
the exact failed edit and actionable location without weakening exact matching or
asking another model to infer unseen output. The repair policy is now part of the
compact-v4 attempt identity, so stored pre-repair v4 approvals require reapproval
and their outcomes cannot poison this lifecycle. Approved-plan storage and the
manifest-only `generate_local_proposal_plan` API are unchanged; v1-v3 identities
and serialized bytes remain unchanged. Live acceptance requires either a syntax-
valid repair within the original attempt count or a clean bounded rejection, two
balanced acquire/release events, one final candidate outcome, and no compact text,
source, raw path, parser message, or secret in parent-readable telemetry.

The first repair-enabled live run proved that lifecycle but not useful diversity.
Qwen-1.5B repeated the same 1,168-byte object and line 11/column 18 failure; Qwen-3B
repeated the same 1,954-byte object and line 28/column 120 failure. Both repair calls
were still greedy with seed zero, so identical prompt plus grammar selected the same
tokens. Compact v4 now carries one parent-owned sampling profile through the
ephemeral repair `PromptPlan` and confined worker contract. Only repair proposal
decoding uses temperature 0.25, top-p 0.9, top-k 20, and seed 104729. The structured
canary and every ordinary or legacy proposal retain their prior keyword arguments
and serialized bytes.

The fixed seed makes a given repair identity reproducible while nonzero temperature
and finite top-k/top-p materially differ from the rejected greedy decode. Grammar,
the 4,096-token stop requirement, four-edit limit, 3,072 replacement-byte cap, line
budgets, immutable scope, lease release, and total approved-attempt count are
unchanged. The profile name and all four controls are hashed into the compact-v4
attempt identity; changing any one requires reapproval, while v1-v3 identity remains
byte-stable. Unknown, non-string, or legacy-bound profile values fail before model
construction. Live acceptance requires two runs of the same identity to select the
same repair sampler arguments, a repair decode that is not the greedy profile, and
no additional inference beyond the approved attempt ceiling.

A September 2026 DeepSeek catalog attempt then exposed a separate parent-side
gap. Both compact-v3 shards completed and passed worker schema validation, but a
replacement precondition did not apply exactly to the immutable baseline. That
error was discovered only during the later attempt worktree mutation, outside
the proposal-retry boundary, so the public classification lost the safe cause.
The prompt plan now retains an in-memory parent snapshot for every focus path.
After decoding and identity checks, strict merge simulates all edits in order
against those snapshots. A missing or repeated replacement, create of an
existing file, or delete whose old text is not the complete file fails before
an attempt worktree is created. Validation-retry v4 reports only a path-free
typed precondition and never the model text.

This behavior matches long-lived practitioner reports rather than treating
valid JSON as an applicable patch. Aider users have reported
[exact SEARCH/REPLACE mismatches since July 2024](https://github.com/Aider-AI/aider/issues/770)
and an
[open 26 March 2025 report](https://github.com/Aider-AI/aider/issues/3651)
from Aider 0.77.1 using GPT-4o-mini in architect/diff mode. The reporter said the
model identified the intended repository changes but could not apply them
because the emitted search block did not match the file exactly, including
whitespace. The follow-up lifecycle protocol also makes the parent application or
quality phase attributable without exposing the model-authored block. Compact v4
removes that duplicated-baseline transcription from the
model response. It does not treat the report as justification for fuzzy
matching: the parent resolves a bounded numeric span against the exact snapshot
or rejects it with typed feedback.

### Rejected patch-format alternatives

The repository's existing `general_ludd.diff_engine.DiffEngine` remains useful
for computing and presenting differences after both versions are trusted, but
it is not the managed model protocol. Its `EditOp` carries positions and counts
without replacement text, while its text-bearing route parses unified hunks and
searches for matching context, optionally with fuzz. Putting either route on the
model boundary would require another adapter and would weaken the simple rule
that one coordinate selects one immutable baseline slice.

Invoking the platform `patch` program was also rejected. Unified patches give
the model filename headers, hunk grammar, context, offsets, and newline markers
to reproduce, and common implementations may relocate a hunk or apply fuzzy
context. Disabling those conveniences still leaves a platform-dependent
subprocess and reject-file lifecycle before Gludd can construct and validate its
authoritative manifest.

The mature Python `unidiff` package was considered rather than inventing another
unified-diff parser. It parses patch syntax but does not supply Gludd's trusted
path policy, immutable-snapshot applicability, operation inference, or
transactional application. Adopting it would add a direct runtime dependency
and still require a security-sensitive applier. The three-field JSON span reuses
the already pinned schema decoder and strict manifest executor, has fewer tokens
and grammar states, and adds no package or system-tool owner.

### Compatibility, rollout, and rollback

Compact v4 is a managed-proposal wire migration plus an internal manifest schema
extension, not a database migration. Each accepted span is expanded to a
schema-2 complete-snapshot `ProposalManifest` consumed by comparison and
application. Legacy single-string callers continue to submit schema-1 manifests
on their separate byte-stable path.
Managed compact-v3 `{a,z}` objects are not guessed into v4 coordinates and v4
objects are not accepted by a v3 decoder; the versioned schema, prompt, decoder,
and span rules all rotate the complete attempt digest. Historical v3 outcome
records remain immutable but cannot exclude a model under v4.

New compact-v4 approvals write approved-plan schema 3. That schema carries
exactly one repository identity: a host-independent binding digest for managed
storage, or the canonical repository root for the local CLI form (which alone
may retain an explicit local-model path). Readers still accept schema-1 local
and schema-2 repository-bound artifacts only with compact v3, reserialize them
without reinterpretation, and reject compact-v4 fields in either legacy schema.
The retained worker publishes only
`self-improve-local-proposal-batch-v2`; the parent rejects a v1 batch for a v4
attempt rather than probing both decoders.

Rollout occurs inside the owned proposal worker and unpromoted attempt worktree.
All shards are decoded and simulated against their parent snapshots before a
candidate file changes, and no partial batch is published. The running daemon,
database, admitted model artifact, and development branch remain available if a
span is rejected or the worker exits. Rollback restores the v3 protocol
descriptor and decoder; its digest naturally selects the earlier compatible
evidence without rewriting records or restarting a data service. This preserves
zero downtime while making the migration fail closed in both directions.

Approved-plan input is bounded at 4,194,304 bytes and 32 paths; a schema-2
manifest additionally caps its combined complete preimages and results at
8,391,680 bytes. Snapshots are excluded from diagnostics and dataclass
representations and live only for the one runner invocation. They add no daemon,
database, file lease, or persistent artifact. Rollback restores the prior
materialization policy and attempt identity; existing immutable outcome evidence
remains scoped to the protocol digest that produced it, preserving ZDD during
either version.

Grammar construction and inference remain inside the owned proposal worker. The
model factory runs once after request admission. Each shard then invokes the
documented `create_chat_completion` method on that same live model and validates
one `CompactSpanProposal` before it can join the atomic worker batch. The parent
accepts only the expected v4 batch protocol, digest, and shard count, then
expands it against snapshots that never cross into worker output. Observability
emits only an allowlisted finish classification, non-negative token counts,
phase, and budget; it never includes completion text. Public proposal-rejection
and terminal-error events contain only the final bounded typed classification;
they never replay Metal initialization, model paths, child logs, or model text.
A converter exception, native crash,
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
- exact compact root/edit fields, one-based span convention, insertion boundary,
  ordering and overlap policy, snapshot/excerpt authorization, trusted newline
  materialization, complete-file preimage comparison, operation mapping,
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
backend logs, and model completion text therefore cannot cross the retry or
public CLI boundary. The complete typed feedback is capped at 256 bytes; the
same marker is printed for the rejection, preserved in the terminal exception,
and applied to every original shard.

The immutable `PromptPlan` digest and every shard focus remain stable during
retries. The complete attempt digest additionally binds the one runtime-used
retry descriptor, so an unchanged algorithm is deterministic while any version,
marker, bound, classification, framing, trusted derivation, or decode limit
change starts a new evidence identity.

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

Gludd now decomposes a reference into deterministic prompt shards with exactly
one focus path each. A base shard is at most 12,000 bytes and any diagnostic
retry remains at most 16,384 bytes. Every shard repeats the exact
task ID, baseline SHA, complete global changed-path list, complete test list,
and ordered canonical Make commands. Each focus file includes its full byte
count and SHA-256 identity. Files of at most 4,096 bytes are included in full.
For larger Python files, the existing
`general_ludd.planning.repo_map.RepoMapBuilder` Tree-sitter parser supplies
syntax boundaries and Gludd selects exact numbered symbol and task-relevant
line windows. Every disclosed source line is rendered with an unambiguous
`L<one-based>|` prefix, and the prompt plan retains the corresponding half-open
editable line ranges outside model-controlled output. Omission is explicit,
digest-bound, and never presented as full source. A shard may edit every and
only its focus path; the parent expands only authorized spans and rejects
missing, duplicate, broadened, wrong-baseline, wrong-task, wrong-test, or
reordered-command output through the original strict `ProposalManifest` parser
before candidate worktree creation. The immutable task, baseline, global paths,
tests, and commands precede a named shard-specific suffix, so every
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
its lossy summary cannot authorize an exact baseline line span. If numbered
excerpts are insufficient, the model cannot address omitted content and the
attempt fails closed. It never silently guesses an omitted coordinate or patch
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

- One compact span per distinct allowed start coordinate, at most four spans, and
  3,072 combined replacement bytes per managed shard. The grammar caps each
  string at 768 code points; the parent authoritatively checks aggregate decoded
  bytes across all items. At most 32 expanded manifest edits, 64 tests, 32 Make
  commands, and 1 MiB of proposal edit text remain available downstream.
- 12,000-byte base prompt shards, exactly one focus path per shard, a
  16,384-byte hard retry boundary, 4,096 bytes of exact context per file, and a
  262,144-byte total request admission bound.
- One owned worker and one lazily constructed `Llama` instance per candidate
  attempt; shards execute sequentially with no daemon, server, or explicit cache.
- One 32-token same-instance canary, then 4,096 compact-v4 decode tokens per
  managed shard and one 300-second total owned worker timeout per candidate
  attempt. Compact-v3 retains 1,024 tokens, the legacy single-string compatibility
  path remains 4,096 tokens, and the strict merged proposal remains roughly
  1.25 MiB at most.
- At most 2,048 parent-derived integer `s` values in one per-shard grammar and at
  most four displayed ranges inside a 256-byte scope-telemetry field; complete
  typed retry feedback remains bounded to 512 bytes.
- Code-task shape is capped at 32 files and 64 MiB of source evidence. Complex
  shapes require a coding artifact of at least 900 MiB. Python syntax diagnostics
  are ASCII and capped at 192 bytes.
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
- [llama-cpp-python chat completion](https://github.com/abetlen/llama-cpp-python#chat-completion)
  documents the formatter precedence: explicit handler, explicit format, the
  GGUF `tokenizer.chat_template`, then a llama-2 fallback. Gludd supplies neither
  override, preserving the model artifact's native instruction template.
- Python's [`tokenize.detect_encoding`](https://docs.python.org/3/library/tokenize.html#tokenize.detect_encoding)
  and [`ast.parse`](https://docs.python.org/3/library/ast.html#ast.parse) provide
  the maintained encoding-aware syntax preflight; no source parser is added.
- [llama-cpp-python discussion 1173](https://github.com/abetlen/llama-cpp-python/discussions/1173)
  records the maintainer clarification that `response_format` selects sampler
  grammar rather than adding schema text to the prompt.
- [llama-cpp-python issue 1478](https://github.com/abetlen/llama-cpp-python/issues/1478)
  records the practitioner limitation that `response_format` is absent from raw
  `create_completion`. Gludd stays on `create_chat_completion` and supplies its
  already-supported explicit `grammar` argument instead of accepting prose or
  fenced output.
- [llama-cpp-python issue 1097](https://github.com/abetlen/llama-cpp-python/issues/1097)
  records a long-lived practitioner failure where JSON-schema conversion rejected
  `oneOf`/`anyOf`. Gludd therefore emits a bounded integer enum for disjoint scope
  rather than depending on combinator support, and tests the real locked 0.3.24
  `LlamaGrammar.from_json_schema` path.
- [llama.cpp grammar documentation](https://github.com/ggml-org/llama.cpp/blob/master/grammars/README.md)
  defines bounded `minLength`/`maxLength` and `minItems`/`maxItems` conversion and
  warns that the schema constrains output without being shown to the model. Gludd
  therefore keeps concise contract instructions in the prompt and validates the
  result independently.
- [llama.cpp issue 26596](https://github.com/ggml-org/llama.cpp/issues/26596)
  reports an opaque grammar failure when a practitioner supplied a very large
  `maxLength`. Gludd never feeds its parent megabyte bound to the converter: the
  per-item v4 bound is 768 code points, the array bound is four items, and real
  locked-runtime tests cover the generated finite grammar rather than assuming
  every nominally finite schema is operationally useful.
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
- The same locked
  [`create_chat_completion` path](https://github.com/abetlen/llama-cpp-python/blob/26633bd1a2eaf7fd0567cc5eaec8b0165a7ea0bd/llama_cpp/llama.py)
  accepts `temperature`, `top_p`, `top_k`, and `seed`, and forwards them through
  its chat formatter to constrained completion. Its sampler uses greedy selection
  at temperature zero and a seeded distribution after finite filters at positive
  temperature, which is the maintained mechanism used for repair diversity.
- [llama.cpp issue 7381](https://github.com/ggml-org/llama.cpp/issues/7381)
  records a long-lived practitioner failure where the HTTP server ignored a custom
  seed while the direct `llama-cpp-python` binding honored it. Gludd therefore keeps
  this policy in its existing single retained in-process binding and tests the exact
  call arguments; it does not move repair generation to the affected server path.
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
  repository-map boundary, but preserves file digests and exact
  span-to-snapshot validation because its output is an executable patch
  proposal.
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
- [Aider issue 3651](https://github.com/Aider-AI/aider/issues/3651)
  remains an open practitioner report from March 2025: Aider 0.77.1 with
  GPT-4o-mini identified the requested change in architect/diff mode, but the
  emitted search text did not exactly match the file. Compact v4 removes that
  redundant old-text transcription while retaining exact parent-side snapshot
  validation. The report also demonstrates that a model can identify the right
  change yet fail the mechanical editor step, supporting Gludd's smaller
  model-visible editor prompt and independent syntax preflight rather than a
  relaxed apply rule.
- [Aider's file-editing troubleshooting guide](https://aider.chat/docs/troubleshooting/edit-errors.html)
  recommends separating planning from a focused editor request when a model has
  difficulty following edit instructions. Gludd adopts the bounded prompt
  separation, but not another unconstrained model call or fuzzy interpretation.
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
