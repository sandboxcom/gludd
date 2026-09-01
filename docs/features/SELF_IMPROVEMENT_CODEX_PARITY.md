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

The worker owns its temporary files and process group on normal completion,
validation failure, timeout, cancellation, and native process death. No test
harness cleanup compensates for missing application ownership.

## Resource bounds

- 32 edits, 64 tests, 32 Make commands, and 1 MiB of proposal edit text.
- 256 KiB prompt and roughly 1.25 MiB serialized proposal exchange.
- 4,096 decode tokens and a 900-second owned worker timeout.
- 2 MiB observable command capture with 15-second heartbeats.
- 128 Codex-reference files.
- One candidate worktree per attempt; commands stop after the first failure.
- Disk checks run before additional model acquisition. The validated 0.5B model
  is reused rather than downloaded per attempt.

## Evidence and prior art

Official sources:

- [SWE-bench evaluation](https://github.com/SWE-bench/SWE-bench) evaluates a
  generated patch by applying it to a reproducible repository environment and
  running its tests. Gludd adds repository-specific static, resource, and Git
  identity evidence.
- [Git patch-id](https://git-scm.com/docs/git-patch-id.html) documents the stable
  patch identity used for Codex equivalence.
- [Aider architect/editor mode](https://aider.chat/docs/) separates planning
  from file editing; Gludd similarly separates local proposal generation from
  the Make-mediated executor.

Practitioner evidence:

- [llama.cpp issue 20164](https://github.com/ggml-org/llama.cpp/issues/20164)
  reports long-lived structured/tool-use reliability problems.
- [llama.cpp issue 15012](https://github.com/ggml-org/llama.cpp/issues/15012)
  records JSON/schema-constrained generation difficulties in real integrations.

The operational consequence is fail-closed validation, bounded raw-output
diagnostics, isolated native inference, and deterministic tool routing instead
of treating syntactically plausible model text as a usable change.
