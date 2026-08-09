# OpenCode Proficiency Optimization Plan

**Created:** 2026-07-25 | **Session:** Ongoing | **Status:** Active

## Audit Results (2026-07-25)

### Plugin Count & Overhead

| Metric | Current | Target | Saving |
|--------|---------|--------|--------|
| `tool.execute.before` plugins | 27 | 18-20 | 25-30% fewer hooks per tool call |
| `isSubagent()` calls per tool call | 27 | 1 (cached) | ~26 fs.existsSync calls saved |
| TASKS.md reads per tool call | 3 (task-tracking, release-deadline, stop) | 1 (shared cache) | 2 reads saved |
| Plugin LoC total | ~8,000+ | ~6,000 | 25% reduction via merges |

### Config Gaps

| Setting | Current | Recommended | Why |
|---------|---------|-------------|-----|
| `compaction.prune` | not set (default false) | `true` | Removes old tool outputs, saves tokens |
| `compaction.reserved` | not set (default 10000) | `5000` | Tighter token buffer |
| `formatter` | not set (disabled) | `true` | Auto-format code on write → fewer fixup cycles |
| `lsp` | not set (disabled) | `true` | Real-time error detection → higher first-pass quality |
| `snapshot` | not set (default true) | `false` | Disabled for large repo performance |
| `provider.*.timeout` | not set (default 300s) | `600s` | Prevents agent timeout on long ops |

### Token Efficiency

| Source | Est. tokens | Target | Saving |
|--------|------------|--------|--------|
| AGENTS.md (system prompt) | ~15,000 tokens | ~10,000 tokens | 33% reduction |
| Plugin boilerplate (shared.ts imports) | ~500 tokens per plugin | ~200 tokens | 60% reduction per plugin |
| Subagent dispatch prompts | varies | ≤20 lines enforced | ~30% average |

---

## Optimization Targets & Actions

### 1. Dispatch Latency (Target: sub-30s start-to-code)

**Actions:**
- [ ] **Merge overlapping floor plugins** — `enforce-floor.ts` + `enforce-floor-v2.ts` + dispatch counting from `enforce-multitask.ts` → single `enforce-dispatch.ts`. Reduces tool.execute.before hooks by 3.
- [ ] **Cache `isSubagent()` result** — Move to module-level singleton in `shared.ts`. The result doesn't change within a process lifetime. Saves 27 filesystem calls per tool execution.
- [ ] **Remove `enforce-deliverable.ts` from tool.execute.before** — This only fires on `task`/`agent`/`workflow` dispatch tools. Move to a dispatch-only hook or fold into enforce-multitask.
- [ ] **Add provider timeout** — `"timeout": 600000` to global config. Prevents subagent timeouts on code generation.

**Expected gain:** 15-25% reduction in per-tool-execution hook latency (fs calls dominate).

### 2. Code Quality First-Pass (Target: >90% clean rate)

**Actions:**
- [x] **Enable formatter** — `"formatter": true` in opencode.json. Auto-formats code on write/edit.
- [x] **Enable LSP** — `"lsp": true` in opencode.json. Real-time diagnostics during editing.
- [x] **Enable compaction pruning** — `"compaction": {"prune": true}`. Keeps context tight → fewer hallucinated imports/syntax.
- [ ] **Add pre-write lint check plugin** — Before allowing a write/edit, run ruff on the file and block if lint errors. Catches eg. unused imports before commit.
- [ ] **Add type-stub generation on import** — When agent writes `from x import y` without importing x first, auto-suggest or type-check.

**Expected gain:** 10-20% reduction in lint/typecheck fixup cycles.

### 3. Token Efficiency (Target: 20% token reduction)

**Actions:**
- [ ] **Compress AGENTS.md** — Remove redundant policy repetitions. Many rules are stated 3+ times (eg. "don't stop" appears in 6+ sections). Consolidate into a single "Operational Rules" section with cross-references. Target: 33% reduction (~5000 tokens saved per session startup).
- [x] **Enable compaction pruning** — Removes old tool outputs from context. Combined with tighter reserved buffer (5000), saves ~5-10% context tokens.
- [ ] **Pre-compute subagent context** — Create a `SUBAGENT_CONTEXT.md` that's a compressed version of AGENTS.md (only what subagents actually need: bash policy, TDD, guardrails, nothing about orchestration or session management).
- [ ] **Add prompt template for subagent dispatch** — Standardize dispatch prompt format to always be ≤20 lines. Include only: (1) what file to work on, (2) what to produce, (3) verification command. Skip all policy reminders (they're in AGENTS.md).

**Expected gain:** 25-35% token reduction per subagent.

### 4. Parallel Throughput (Target: 10 agents producing code in 2 min)

**Actions:**
- [ ] **Remove per-tool-call file I/O from critical path** — `enforce-task-tracking.ts` reads TASKS.md on every non-read tool call. `enforce-release-deadline.ts` also scans TASKS.md. Cache TASKS.md content with a 5-second TTL.
- [ ] **Add tool.execute.before hook priority** — Fast-path plugins (subagent check, dispatch classification) should run first. Heavy plugins (TASKS.md readers) should run last or be async.
- [ ] **Reduce plugin count via merging** — See Dispatch Latency section. Fewer hooks = faster tool execution = more tool calls per second.

**Expected gain:** 10-15% throughput improvement (fewer blocking I/O ops on tool calls).

### 5. Error Recovery Speed (Target: sub-60s fix cycle)

**Actions:**
- [ ] **Add hook-level error telemetry** — Track which plugins deny tool calls and why. Surface in `/tmp/gludd-plugin-denials.jsonl` for the orchestrator to learn patterns.
- [ ] **Auto-disengage on repeated denials** — If the same plugin denies 5+ consecutive tool calls with the same reason, emit a disengage-next signal automatically. Prevents the "grinding against enforcement" anti-pattern.
- [ ] **Add `make quick-fix ERR=<error-type>` target** — Pre-built fix templates for common failures (lint: run ruff --fix, typecheck: add annotation, collection: add __init__.py).

**Expected gain:** 30-50% faster recovery from common failures.

---

## Immediate Changes Applied (2026-07-25)

### Config Changes (opencode.json)

1. **Compaction pruning enabled** — `"compaction": {"prune": true, "reserved": 5000}`
2. **Formatter enabled** — `"formatter": true`
3. **LSP enabled** — `"lsp": true`
4. **Snapshot disabled** — `"snapshot": false` (large repo performance)

### Before/After Tracking

| Metric | Before | After (target) | Measured |
|--------|--------|---------------|----------|
| Plugin hooks per tool call | 27 | 20 | TBD |
| fs.existsSync calls per tool call | 27 | 5 | TBD |
| formatter enabled | false | true | ✓ |
| compaction pruning | false | true | ✓ |
| TASKS.md readers per call | 3 | 1 | TBD |
| AGENTS.md tokens | ~15,000 | ~10,000 | TBD |

---

## Phase 2: Plugin Merge Roadmap

### Merge Group A: Dispatch/Floor (4→1)
- `enforce-floor.ts` (620 lines)
- `enforce-floor-v2.ts` (185 lines)
- `enforce-multitask.ts` (dispatch counting portion)
- `enforce-delegate.ts` (mainthread streak portion)
→ **`enforce-dispatch-discipline.ts`** (~600 lines, down from ~1700 total)

### Merge Group B: Text Output Analysis (3→1)
- `enforce-anti-essay.ts` (essay length detection)
- `enforce-audit.ts` (done-words detection)
- `enforce-objective.ts` (primary objective tracking)
→ **`enforce-output-quality.ts`** (~300 lines, down from ~500 total)

### Merge Group C: Git Operations (3→1)
- `enforce-commit-lock.ts`
- `enforce-branch-discipline.ts`
- `enforce-worktree.ts`
→ **`enforce-git-discipline.ts`** (~300 lines, down from ~500 total)

### Net: 29 plugins → 22 plugins (-24%)
Estimated: 24% fewer `tool.execute.before` hooks, ~20% less startup time.

---

## Tracking

Optimization log: `/tmp/gludd-optimization-log.jsonl`
Review trigger: re-dispatch this task each session until gains plateau.

## Database maintenance

Database and session-storage optimization is operationally separate from prompt
compaction. Use the
[OpenCode database maintenance runbook](opencode-database-maintenance.md) for
the guarded inspection, retention, offline compaction, and recovery sequence.
The runbook also records the upstream OpenCode issues and user-forum evidence
behind the fail-closed design.
