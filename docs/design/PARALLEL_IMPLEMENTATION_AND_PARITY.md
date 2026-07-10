# Parallel Implementation Strategy + Coding-Assistant Parity (2026-07-10)

Two mandates captured here, both required for the spec to deliver a genuinely
feature-complete, competitive release:

1. **Part 1 — Parallel/cooperative implementation strategy**: how to implement the
   whole backlog (Wave C designs in `WAVE_C_DESIGNS_2026-07-10.md`, Wave D, and the
   parity wave below) with maximum concurrency while keeping CI green throughout.
2. **Part 2 — Competitive parity (Wave P)**: the work items that make gludd *at
   least as useful as* opencode, aider, and goose by the time the spec is done.

Companion docs: `WAVE_C_DESIGNS_2026-07-10.md` (11 turnkey security/quality designs),
`AGENTIC_IMPLEMENTATION_SPEC.md` (master item list, waves A–F), the landing-order
analysis this doc's Part 1 is built from.

---

# Part 1 — Parallel & Cooperative Implementation Strategy

## 1.1 The orchestration model (how gludd builds gludd)

The implementation is itself an agentic, parallel workload. One **orchestrator**
(main thread) holds the plan and the merge queue; **N implementer agents** each own
one *file-disjoint* batch in an isolated git worktree, draft + targeted-test their
change, and hand back a diff. The orchestrator lands batches one at a time behind a
green gate. This is the same multitasking floor the repo already enforces
(`docs/MULTITASKING_POLICY.md`), applied to implementation.

**Roles**
- **Orchestrator (main thread)**: owns `AGENTIC_IMPLEMENTATION_SPEC.md` status, the
  landing order, the merge queue, and the CI gate. Never edits source directly for a
  batch that an agent owns — only merges.
- **Implementer agent (worktree)**: owns exactly one batch (one file-family). Reads
  its `WAVE_C_DESIGNS` section, applies the line-anchored edits, adds the listed
  tests, runs `make test-iso TESTFILE=...` on just those tests + `make lint`/
  `make typecheck` on its files, commits in its worktree, returns a summary.
- **Verifier agent (read-only)**: adversarially reviews a landed batch (or an
  agent's returned diff before merge) against its acceptance criteria.

**Concurrency discipline** (already codified in the repo, restated for implementers):
- Keep ≥10 agents live while independent work exists; dispatch in one message so
  they run concurrently.
- ~2:1 sonnet:opus. Cap ~5–6 concurrent *worktree* agents (each builds a ~320 MB
  venv; run `make disk-guard` before a wave; `make clean-worktree-venvs` after).
- A 429/529/quota error is the **only** acceptable reason to be below floor — retry
  with backoff, do not tight-loop.

## 1.2 The one hard rule: single-writer-per-file, ordered by the conflict matrix

Two agents must **never** edit the same source file in the same wave. The landing
order below is derived from the file→design matrix; every file touched by >1 design
is a **serialization point** and those designs land sequentially, not concurrently.

**Serialization points (must land in the given order):**

| File | Designs (in landing order) |
|---|---|
| `security/permissions.py`, `security/sts.py` | C-SEC-1 **then** C-SEC-1b (re-read for fresh lines before C-SEC-1b) |
| `models/gateway.py` | C-GATEWAY **then** C-BUDGET (C-BUDGET re-reads after C-GATEWAY shifts lines) |
| `event_loop/loop.py` | C-SPD1 **then** C-EVENTLOOP + C-LANGGRAPH (both re-verify their line ranges after SPD-1 inserts `_phase_flush_spend_ledger`; 1558-1578 vs 2363-2369 are disjoint → can be parallel *to each other* once SPD-1 lands) |
| the 4 phase-count tests | only C-SPD1 touches them (17→18 / 16→17) — atomic 4-file update in that one batch |
| `secrets/manager.py` | C-SEC-1 sole owner |
| test files | no cross-design test-file collision found; `test_completion_audit_wiring.py` + `test_self_improve_wiring.py` have must-**flip** (not add) tests for C-SELFIMP |

## 1.3 Wave schedule (maximum parallel width, HIGH-severity first)

**Wave 1 — fully parallel (mutually file-disjoint), highest value.** All three
HIGH live bugs are here and touch different files, so implement + gate concurrently:
- C-SEC-1 (permissions/sts/secrets denied-inert) — HIGH live
- C-SELFIMP (self_improve code-tier approval bypass) — HIGH
- C-RELOAD (hot_reloader TOCTOU + capability_lattice symlink) — HIGH/MED
- C-TOOLLOOP (tool_loop + variable_store) — MED
- C-GITAUTO (git_automation/repo.py) — MED
- C-INTEGRITY (scanner HMAC canonicalization) — MED *(note: fail-closed migration
  invalidates pending approvals — call out in release notes)*
- C-CONNECTORS (`_associate_by_window`), C-TODOMODEL+migration 027, C-FILESTORE —
  all disjoint, parallel-safe

**Wave 2 — parallel, disjoint from Wave 1 and each other:**
- C-GATEWAY (models/gateway.py) — land before any C-BUDGET work
- C-SPD1 (event_loop/loop.py + 4 phase-count tests) — land before C-EVENTLOOP/C-LANGGRAPH
- C-ENGINE (execution/engine.py + daemon.py shutdown wiring) — the only design
  touching daemon.py

**Wave 3 — dependent (finalize line numbers against post-Wave-2 source):**
- C-BUDGET (after C-GATEWAY)
- C-EVENTLOOP + C-LANGGRAPH (after C-SPD1; parallel to each other)
- `allow_auto_promote` delete (after C-SELFIMP, same subsystem)

**Wave 1 alone is ~9 concurrent batches** — that is the target parallel width.
C-SEC-1b, C-BUDGET, C-EVENTLOOP, C-LANGGRAPH are the only forced-sequential tails.

## 1.4 Landing protocol (keeps CI green batch-by-batch)

Per batch, in order:
1. Agent applies edits + tests in its worktree; runs `make test-iso TESTFILE=<its new/changed tests>` (targeted, fast), `make lint`, `make typecheck` on its files.
2. Agent returns the diff + its test evidence (counts).
3. Verifier agent (read-only) checks the diff against the design's acceptance criteria + confirms no scope bleed into another batch's files.
4. Orchestrator merges the worktree branch (disjoint files → clean merge), runs `make collect-check`, and lands via a gated commit.
5. After each *wave*, one `make gate-async` → `make gate-status` must be green before the next wave starts. Never start Wave N+1 on a red gate.
6. Push in batches (respect the push rate-guard); CI is the final gate — confirm the run green for the exact SHA before the next push.

**Invariant:** every checked box in `TASKS.md` carries `| evidence:` (test count / CI
run id / commit hash). No "done" without the measurement — enforced by the repo hooks.

## 1.5 Parallelism the *spec's own features* must add (cooperative execution)

The user asked that the implemented product also maximize cooperative parallel work.
These are product-level items (fold into Wave C/D + Wave P), not just process:
- **P-PAR-1 — Bounded dispatch semaphore + dedicated executors** (already designed as
  C-EVENTLOOP items 12/13): the daemon dispatches independent todos concurrently with
  a real concurrency cap and per-workload thread pools. This IS the product's
  parallel-execution engine — prioritize it.
- **P-PAR-2 — File-claim coordination for concurrent edits** (`coordination/file_claims.py`
  TTL reaping, #53): lets multiple agents work the same repo without clobbering each
  other — the cooperative-edit substrate. Verify it gates the interactive/edit paths too.
- **P-PAR-3 — Worktree-per-agent isolation for user tasks**: expose the same
  git-worktree isolation gludd uses internally so a user can fan a task across N agents
  on N worktrees and auto-merge (the "aggressive parallelism" the orchestrator already
  practices, made a product feature).
- **P-PAR-4 — Dependency-aware scheduler**: the event-loop scheduler should plan
  batches by file/dependency disjointness (mirror §1.2's conflict matrix in code) so
  concurrently-dispatched work never contends. Extends the existing `Scheduler.plan()`.

---

# Part 2 — Coding-Assistant Parity: Wave P (opencode / aider / goose)

**Goal:** by spec completion, a developer can point gludd at *any* repo and get an
experience at least as capable as aider/opencode/goose. Today gludd is architected as
an autonomous SDLC *daemon for its own repo* (make-only toolchain, ruff/mypy/pytest
hardcoded); the parity gaps below are what separate that from a general coding
assistant. Each item says **verify-first** where gludd may already have a partial
implementation — an implementer must confirm against source (grep/read) before
building, and mark FIXED items into the spec's Already-Done list instead of rebuilding.

## 2.1 What each tool is good at (parity target)

| Capability | aider | opencode | goose | gludd today |
|---|---|---|---|---|
| Interactive human-in-the-loop coding session (chat/TUI on any repo) | ✅ chat | ✅ TUI | ✅ CLI+desktop | ⚠️ TUI is daemon-monitoring, not pair-coding |
| Point-at-ANY project (any language/toolchain) | ✅ | ✅ | ✅ | ❌ make-only + hardcoded ruff/mypy/pytest |
| Repo map / ranked context retrieval | ✅ tree-sitter repo map | via LSP+grep | via extensions | ⚠️ has retrieval (G3) — verify it's a general repo map |
| Reliable multi-format edit application + repair | ✅ diff/SR/whole + reflection | ✅ patch/edit | ✅ | ⚠️ engine applies edits — verify formats + malformed-edit repair |
| LSP diagnostics integration | ⚠️ partial | ✅ | via extension | ❌ none |
| Per-edit auto-commit + undo | ✅ | ✅ | ✅ | ⚠️ git automation exists — verify per-edit UX + undo |
| Reasoner+editor model split (/architect) | ✅ | model routing | model routing | ✅ router role→model (richer) |
| MCP / extensible tools | ⚠️ | ✅ | ✅ (extensions) | ✅ MCP client + tool-loop |
| Multi-model / provider-agnostic / local models | ✅ | ✅ | ✅ | ✅ 24-provider gateway |
| Autonomous multi-step + scheduler/recipes | ⚠️ | ⚠️ | ✅ recipes+cron | ✅ event-loop + self-improve (stronger) |
| Session resume / share | via chat history | ✅ shareable | ✅ | ✅ hibernation (dehydrate/hydrate) |
| Cost/token reporting | ✅ | ✅ | ✅ | ✅ budget/spend + token accounting (stronger) |
| Watch mode (react to code comments/file changes) | ✅ --watch | ⚠️ | ⚠️ | ❌ |

**gludd already meets or exceeds** the parity bar on: model routing, MCP
extensibility, autonomous orchestration + scheduler, budget/cost controls, session
hibernation, guardrails/security. **The gaps are the interactive coding surface and
general-project support** — that is where Wave P concentrates.

## 2.2 Wave P work items (priority-ordered)

**P-1 (KEYSTONE) — Generic target-project toolchain runner.** *Why:* the single
biggest blocker to "useful on any repo like aider/opencode." Today execution is
make-only + hardcoded ruff/mypy/pytest for gludd's own tree. *What:* a
`project_runner` that detects language/build/test/lint per project (package.json →
npm/pnpm; pyproject/setup → pytest/ruff/mypy; go.mod → go test; Cargo.toml → cargo;
Makefile; etc.), with a config override, and routes the execution engine's test/lint
steps through it instead of the hardcoded gludd commands. *Where:* new
`src/general_ludd/projects/toolchain.py`; wire into `execution/engine.py` `_run_tests`
and the lint/typecheck steps; `projects/manager.py` already resolves repo identity.
*Acceptance:* gludd runs the correct test/lint/build for a JS, Go, and Rust sample
repo; a project config can override detection. *(This is the MVP keystone already
flagged in project memory.)*

**P-2 — Interactive coding session (`gludd code`).** *Why:* aider/opencode/goose are
fundamentally interactive; gludd's UX is daemon+API+monitoring TUI. *What:* an
interactive REPL/TUI command that opens a coding session against the cwd repo:
free-form instructions → plan → edits → run tests → show diff → commit, with
`/add /drop /run /undo /diff /model /architect` controls. *Where:* extend
`general_ludd.cli` (there is an existing `tui` — verify-first whether it can host a
coding mode or a new `code` subcommand is cleaner); reuse the tool-loop + execution
engine + router; drive the human-in-the-loop review gate that already exists.
*Acceptance:* a user can `gludd code`, ask for a change, review the diff, accept/reject,
and have it committed — no daemon required.

**P-3 — Repo map / ranked context retrieval (verify-first).** *Why:* aider's
tree-sitter repo map is its key advantage on large/unfamiliar codebases. *What:* a
ranked whole-repo symbol map (tree-sitter or ctags) that feeds the model relevant
files/symbols without dumping the tree. *Verify-first:* gludd has G3 retrieval wiring
— confirm whether it is a general cross-language repo map or gludd-specific; if
partial, generalize it. *Where:* `renderers`/retrieval subsystem + a new
`context/repo_map.py`. *Acceptance:* on a 500-file repo, the model is given a ranked
map, not the whole tree, and edits land in the right files.

**P-4 — Robust multi-format edit application + repair (verify-first).** *Why:*
unreliable edit application is the #1 failure mode of coding assistants; aider invests
heavily here (search/replace + unified-diff + whole-file, with a repair reflection
loop). *Verify-first:* read `execution/engine.py` + the sandbox `safe_writer` to see
what edit formats are parsed and whether malformed edits are repaired or dropped
silently. *What:* support search/replace blocks + unified diff + whole-file, validate
each hunk applies, and on failure re-prompt the model with the exact mismatch (repair
loop) rather than failing the job. *Acceptance:* a malformed/whitespace-drifted diff is
repaired and applied; an unrepairable one surfaces a clear error, never a silent no-op.

**P-5 — LSP diagnostics integration.** *Why:* opencode uses LSP for real diagnostics;
gludd has none. *What:* an optional LSP client that, per target-project language,
surfaces diagnostics after an edit and feeds errors back into the fix loop.
*Where:* new `lsp/` module; hook into P-1's toolchain detection (language → LSP
server). *Acceptance:* after an edit that breaks types, the LSP diagnostic drives an
auto-fix iteration.

**P-6 — Per-edit commit + undo UX (verify-first).** *Why:* aider commits each change
with a good message and one-command undo. *Verify-first:* `git_automation` already
does commits — confirm per-edit granularity + a user-facing `undo`. *What:* commit each
accepted edit with a generated message tied to the instruction; `gludd code`'s `/undo`
reverts the last edit-commit. *Acceptance:* every accepted change is its own revertable
commit with a meaningful message.

**P-7 — Watch mode.** *Why:* aider `--watch` reacts to `# ai:`-style comments / file
changes. *What:* a file-watcher that triggers a coding action from an in-code
directive or on save. *Where:* reuse the reload file-watcher infra. *Acceptance:* adding
a `# gludd: <instruction>` comment and saving triggers the change.

**P-8 — User-facing recipes + scheduler (verify-first).** *Why:* goose's recipes
(reusable task templates) + cron scheduling. *Verify-first:* gludd has an event-loop
scheduler + SDD recipe-ish flows — confirm a *user* can define a reusable task recipe
and schedule it. *What:* a recipe format + `gludd run <recipe>` + schedule via the
existing scheduler. *Acceptance:* a user defines a recipe once and runs/schedules it.

**P-9 — Parallel multi-agent user tasks (ties to P-PAR-3).** *Why:* gludd's real
differentiator vs all three is native multi-agent parallelism — expose it. *What:* a
user can fan one task across N worktree agents (disjoint files per §1.2's conflict
model) and auto-merge, with the file-claim coordinator preventing clobber.
*Acceptance:* `gludd code --parallel N` splits an independent multi-file task across N
agents and merges cleanly. This is where gludd should *exceed* the competition, not
just match it.

## 2.3 Sequencing Wave P vs Wave C/D

- **P-1 (toolchain runner) is the keystone** and unblocks P-2/P-4/P-5 being useful on
  real projects — schedule it first, in parallel with Wave C security work (disjoint
  files: new `projects/toolchain.py` + `execution/engine.py`, the latter also touched
  by C-ENGINE → sequence P-1's engine wiring **after** C-ENGINE lands, or coordinate as
  one owner).
- **P-2/P-3/P-4** form the interactive core; land after P-1.
- **P-5/P-6/P-7/P-8** are enhancements; parallel-safe once the core exists.
- **P-9** last — it composes P-1..P-4 + the existing worktree/file-claim infra.

## 2.4 Definition of "at least as useful as opencode/aider/goose"

The release meets the bar when, on an arbitrary external repo, gludd can: (1) run that
project's real tests/lint (P-1); (2) hold an interactive coding session that plans,
edits, tests, shows a diff, and commits with undo (P-2/P-4/P-6); (3) select context via
a repo map (P-3); (4) surface LSP diagnostics into the fix loop (P-5); (5) do all of the
above across multiple providers/local models (already ✅); and (6) additionally fan work
across parallel agents (P-9) — a capability none of the three has natively.

---

## Verification pass — RESULTS (2026-07-10, verified against source)

- **P-2 interactive coding session — MISSING (build from scratch).** `cli.py:590 tui`
  → `tui/runner.py` is a daemon-monitoring dashboard; its `code` view is
  search/call-graph, not an edit surface. No `chat`/`interactive`/`session`
  subcommand; all edits go through the daemon's async autonomous dispatch, never a
  synchronous human turn. → new `gludd code` command; reuse tool-loop + engine +
  router + the existing human-review gate.
- **P-3 repo map / retrieval — PARTIAL, and the wired path is UNFED.** Two disjoint
  subsystems: `planning/repo_map.py` (`RepoMapBuilder`) is Python-only and **dead
  code** (no `src/` caller); `retrieval/{indexer,searcher}.py` IS wired
  (`daemon.py:1037/1273/1279` → `engine.py:122-159 _inject_retrieval_context`) but
  `CodebaseIndexer.index_files()` is **never called in `src/`** — the daemon builds an
  empty index and `search()` returns `[]`, so retrieval is a silent no-op in prod.
  → wire an indexing trigger (on job dispatch / project switch / CLI) over the target
  workspace; generalize chunking beyond Python (indexer already falls back to
  blank-line chunking for other languages — add cross-language symbol awareness).
- **P-4 edit apply + repair — PARTIAL, no repair loop.** `engine.py` applies whole-file
  `FILE:` blocks (`_extract_file_paths`, `_write_file`) and unified diff via system
  `patch` (`_apply_unified_diff:886-937`), path-jailed. **No SEARCH/REPLACE format**
  (aider's primary) and **no repair loop**: on `patch` failure it logs + returns `[]`
  (silently drops the edit); "no changes parsed" → terminal `TaskReturn(exit_code=1)`
  with no re-prompt. The only "reflection" is post-hoc `TaskReturn` review, not
  edit-repair. → add SEARCH/REPLACE (fuzzy) format + a capped re-prompt-on-failure
  repair cycle.
- **P-6 per-edit commit + undo — PARTIAL.** `GitAutomation.commit()` is flat
  `git add -A`; engine commits once per todo (auto message `engine.py:687-695`);
  `cli_core_changes.py` can commit a single FIM record offline — but there is **no
  per-edit interactive commit and no user-facing `undo`/`revert` command** (the
  `git-revert` Makefile target is referenced in a test allowlist but not actually
  declared). → build the interactive-accept→commit loop (rides P-2) + an `undo` command.
- **P-8 recipes + scheduler — PARTIAL (scheduler DONE).** The cron/one-shot engine is
  fully built + wired: `Todo.cron` (`db/models.py:236`) validated 5-field,
  `POST /todos` computes `next_run_at` via `croniter`, `TodoScheduler`
  (`event_loop/scheduler.py:132-238`) spawns due cron children each tick
  (`loop.py:1348-1354`). Gaps: **no user-facing "recipe" abstraction** ("recipe" in
  repo = Makefile jargon; `templates`/`playbooks` = Ansible cache; SDD = fixed make
  sequence) and scheduling is **HTTP-API-only** (no `gludd add --cron/--scheduled-at`).
  → add a named/parameterized reusable task-template + surface the working scheduler
  through the CLI.

**Wave P re-scoping from these results:** the scheduler backend (P-8 core) is done;
everything else is new. Highest-leverage new work = **P-2 interactive `gludd code`
session** + **P-4 edit-repair loop** + **P-3 indexer wiring**, all on top of the
keystone **P-1 toolchain runner**. P-6 undo/commit rides P-2. P-9 composes them.
