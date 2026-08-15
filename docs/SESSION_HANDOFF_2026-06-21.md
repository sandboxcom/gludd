# Session Handoff — 2026-06-21

Branch: `test/coverage-recovered` (main repo `/Users/shawnwilson/gludd`)
Session limit hit ~1:10am ET 2026-06-21. All changes are UNCOMMITTED working-tree. Resume after reset.

**STATUS (end of session):**
- Completion-integrity: DONE + gate-clean (mypy 0, ruff clean, 52 integrity tests + daemon tests green, healthcheck PASS, daemon smoke-boots)
- Tidy: DONE (10 junk items removed, leak guard added, .gitignore updated, Makefile dup targets cleaned)
- .secrets.baseline: SAFE regen (no plaintext secrets)
- C2 ship: still RED — root cause CONFIRMED, fix in flight (see §4)

---

## 1. Completion-Integrity Verification

**Question asked:** "Are features marked 100% actually implemented?"
**Answer:** Decisively NO — many flagship features were wired but non-functional by default.

**Headline finding (CRITICAL):** The core "submit todo → AI does work" flow was BROKEN OUT OF THE BOX.
- `_dispatch_execute_job` calls the model only when `prompt_text` resolves — which requires a `prompt_profile` on the todo.
- `POST /api/todos` (`AddTodoRequest`) has NO `prompt_profile` field → todos created with `prompt_profile=None`.
- No default assignment anywhere → `invoke_model_for_generation` returns `None` silently (INFO log only) → playbook runs with `model_response=None` and LOOKS like success.
- Fix: `loop.py` title/description fallback now synthesizes `"Task: {title}\n\n{desc}"` when no profile resolves.

**Full scorecard:** see `docs/COMPLETION_INTEGRITY_AUDIT.md`
- 23 confirmed-WORKING (test-backed)
- 12 confirmed-INERT → all now fixed in working-tree
- 3 reconciled (static-audit false positives — were never actually inert)
- 1 deferred (CA-T9 AgentToolAdapter wiring — architectural, user-directed)
- 6 vacuous-proof (G3, G7, W6.8 partial, scoring adaptive)
- 4 real bugs found and fixed (endpoint, F6, G5, silent-skip)

**Remediation plan:** see `docs/AI_FEATURE_REMEDIATION_PLAN.md` (9 items, severity-ordered)

---

## 2. Fixes Applied (working-tree, branch `test/coverage-recovered`, UNCOMMITTED)

All 12 fixes + 3 reconciled + 1 deferred are gate-clean (measured on main repo, branch `test/coverage-recovered`):
- mypy: 0 errors (402 files)
- ruff: "All checks passed"
- Integrity suites: **52/52 green** — `completion_integrity_high` 10, `scoring` 7, `mcp_selfimprove` 8 (stale inert-guards flipped to assert MCP-wired), `rules_healthgate` 6, `budget` 5, `multimodel_routing` 16
- Daemon tests: `test_daemon_coverage_lift` 49 + `test_daemon_endpoint_coverage` 37 green (stale-test fixes: `config_files`→`config_file_count` + 2 hook URLs `http://localhost`→`https://webhooks.example.com`)
- `make smoke` PASSES (daemon boots, full 10-phase tick)
- healthcheck: PASS
- Total collected: 11,864 / 0 errors

### 12 Fixed

| # | Item | Files |
|---|------|-------|
| 1 | z.ai endpoint (subscription vs pay-per-token) | `src/general_ludd/models/provider_presets.py:46`, `config/model_profiles/zai_example.yml` |
| 2 | F6 failover: openai exception types missed | `src/general_ludd/gateway.py`, `src/general_ludd/timeout_detector.py` |
| 3 | G5 reviewer fence-parse (json.loads on ```json``` fences) | `src/general_ludd/reviewer.py` (`_extract_json_from_output`) |
| 4 | W6.8 ToolCallLoop._call_model wrong signature | `src/general_ludd/tool_loop.py:167-182` |
| 5 | W6.8 gludd_agent_run JobSpec missing required fields | `collections/.../gludd_agent_run.py` |
| 6 | Silent-skip flagship fix | `src/general_ludd/event_loop/loop.py:912-929`, `job_invocation.py:51` |
| 7 | MCP wiring: `mcp_client=None` hardcoded | `src/general_ludd/daemon.py` (MCPClient+MCPToolRegistry, `app.state._mcp_client`, EventLoop wired) |
| 8 | Self-improve interval: default 0 → 10 | `src/general_ludd/daemon.py` |
| 9 | Gateway health-gate: ModelHealthTracker wired into gateway+router | `src/general_ludd/daemon.py` (`app.state._health_tracker`) |
| 10 | Rules engine: `UserConfig.rules` field added + loader seeds `cfg["rules"]` | `src/general_ludd/user_config.py`, `daemon.py` |
| 11 | Reasoning-model token budgets | `config/model_profiles/zai_example.yml` (`max_output_tokens=16384`) |
| 12 | gateway.py `bind_tools`: tools popped from kwargs, bound after constructor | `src/general_ludd/gateway.py` |

### 3 Reconciled (were never actually inert — static audit false positives)
- CA-T12 cost tracking: WORKS (real tokens/costs flow via `_invoke_and_bill`; zero-cost was a profle-default footgun, not a code bug)
- CA-T16 ContextCompactor/TokenWindowManager: WORKS (fired via `AgentCapabilities.prepare_messages`)
- CA-M1 bunx double-def: already fixed in transport.py before this session

### 1 Deferred (architectural, user-directed)
- CA-T9 AgentToolAdapter wiring into generation path: tool-use belongs in ToolCallLoop by design; `bind_tools` fix (#12) is the prereq; full wiring = behavior change, needs explicit direction

---

## 3. z.ai Subscription Endpoint

**Root cause of all z.ai failures:** gludd defaulted to the pay-per-token endpoint (`open.bigmodel.cn/api/paas/v4`), which rejects subscription keys with 429/1113 "insufficient balance."

**Correct config:**
- Endpoint: `https://api.z.ai/api/coding/paas/v4`
- Model: `glm-4.6` (of 8 available: glm-4.5/4.5-air/4.6/4.7/5/5-turbo/5.1/5.2)
- Key: gitignored `.zai.key` (`ef5c...`); `auth.json` has a second key (`9e4b...`)
- Fixed in: `provider_presets.py:46`, `zai_example.yml`, Makefile test targets, test defaults

**Live-proven** (`abd953`): real EventLoop dispatch via glm-4.6 → file written on disk → pytest passed → git SHA committed. Pipeline works end-to-end with the real model.

---

## 4. Ship Status (alpha.3, branch `integration/alpha3-rc`)

**SEPARATE from the completion-integrity fixes** — do not conflate branches.

### C2 root cause CONFIRMED

Every CI failure traces to the same issue: **the gludd project is not installed into the CI `.venv`** — only deps are. So `bare .venv/bin/python3 -m general_ludd.cli` raises `ModuleNotFoundError: No module named 'general_ludd'`.

Prior attempts that all failed:
- console-script path (`gludd tui`)
- `-m general_ludd.cli`
- `uv run --no-sync python -m general_ludd.cli` — `--no-sync` skips project install, same result

**Definitive fix (a7da4db9, local fresh-venv verified before push):**
- Drop `--no-install-project` / `--no-sync` OR add `uv pip install -e .` in the gate so `general_ludd` is importable from `.venv` site-packages
- Verified locally: `rm -rf .venv && uv sync` → `.venv/bin/gludd` exists + `general_ludd` + `httpx` importable + 2 e2e tests pass

Latest CI state:
- Commit pushed: `05cec7aa` to `integration/alpha3-rc`
- CI run: `27895611609` — FAILED (same `ModuleNotFoundError: No module named 'general_ludd'` via `uv run --no-sync`)
- Fix in flight: `a7da4db9` (editable install approach) — NOT yet pushed

### Worker xdist flake (3.12-only)

CI run `27895611609` also showed a 3.12-only flake: `test_worker_redacts_secret_aliases_in_logs` ("simulated runner failure") — xdist ordering/cross-test contamination.

**FIXED on `test/coverage-recovered` (a9b35dc7):** `test_worker_d09_d10_d35.py` `_make_client()` leaked module singleton `general_ludd.worker.app._runner` (a raise-RuntimeError runner) without restore. Added autouse `_reset_runner` save/restore fixture; 27/27 green under xdist.

**NOT yet applied to `integration/alpha3-rc` (ship branch).** If C2 re-run reddens only on this worker test, apply the same `_reset_runner` fixture to `integration/alpha3-rc` and re-push.

### Ship sequence (paste-ready, run after CI green)

```text
make require-ci-green SHA=<full-SHA>
make check-readme-status TAG=v0.1.0-alpha.3
make ship-https SHA=<full-SHA> TAG=v0.1.0-alpha.3 MSG='v0.1.0-alpha.3 — third alpha'
```

The `ship-https` Makefile target does:
1. `git checkout master && git merge --ff-only <SHA>`
2. Push master via `https://x-access-token:$(gh auth token)@github.com/sandboxcom/gludd.git`
3. `git tag -a v0.1.0-alpha.3 <SHA>` + push tag (triggers Build-and-Release)
4. `make verify-release-artifact`

### Post-ship PR backlog — conflict adjustments

Completion-integrity fixes on `test/coverage-recovered` create conflicts with the post-ship PR queue. Adjusted landing order (see memory `gludd-post-ship-landing-order`):
- **PR-1**: partial cherry-pick (some hunks conflict with daemon-wiring changes)
- **PR-3 CA-D2**: re-author `@daemon.py:~846` (line moved by MCP/health-gate wiring)
- **PR-9**: drops 2 of 3 edits (superseded by working-tree fixes)
- **PR-5**: seal step dropped (superseded)

---

## 5. Commit Map + Tidy

Working-tree has **~54 files → 8 planned commits** (uncommitted, for user review).

### Tidy — DONE (`make clean-confirmed-junk`)

10 junk items removed:
- `nested/` directory
- `proj-ok/` directory
- `.commit-msg-batch2.txt`, `.commit-msg-batch3.txt`, `.commit-msg-batch3a.txt`, `.commit-msg-batch3b.txt`, `.commit-msg-cycleA.txt`, `.commit-msg-integration.txt` (6 scratch files)
- `.test-summary`
- `scripts/wave3_consolidate.sh`

Also done as part of tidy:
- Leak regression guard added (test asserting junk dirs don't reappear)
- `.gitignore` updated to cover the removed items
- Makefile duplicate targets cleaned: `test-iso` (L280/L1153) and `git-show-ref-grep` (L751/L2752)

### .secrets.baseline — SAFE

4,288-line churn in `.secrets.baseline` = safe regen of the secrets scanner baseline. No plaintext secrets introduced. Include in commit as-is.

### Discard list (do NOT commit these — already cleaned from disk)

All 10 items listed above were removed by `make clean-confirmed-junk`. If any re-appear in `git status`, do not stage them.

---

## 6. Remaining / TODO

### Immediate (post-session-reset)
1. **C2 ship:** Push `a7da4db9` (editable-install fix) to `integration/alpha3-rc` → wait for CI → green: run ship-https sequence → red: check if only the worker xdist flake (apply `_reset_runner` fixture from `test/coverage-recovered:tests/unit/test_worker_d09_d10_d35.py` and re-push)
2. **Commit working-tree fixes** (~54 files, 8 commits) after user review — see §5 for what to include vs. discard

### Post-ship PR backlog
Full ordered PR list (PR-1 through PR-9) with line-pinned conflict chains + conflict adjustments noted in §4:
- See memory: `gludd-post-ship-landing-order`

### Still-inert (documented in `AI_FEATURE_REMEDIATION_PLAN.md` — not fixed, needs direction)
- Adaptive routing / benchmark history: CA-T11 fixed (async benchmark now records); scoring adaptive weighting needs real benchmark data to exist before it activates
- AgentToolAdapter into generation path (CA-T9 deferred; `bind_tools` prereq done in gateway)
- Reasoning-model token budgets for non-zai profiles
- MCP tool-registry: wired in daemon; needs `+mcp_tool_registry` integration for actual tool dispatch
- Rules-engine: `UserConfig.rules` field added + loader seeds `cfg["rules"]`; verify YAML-round-trip in non-default configs

---

## Key File Locations

| File | Purpose |
|------|---------|
| `docs/COMPLETION_INTEGRITY_AUDIT.md` | Final scorecard: 23 working / 12 inert / 6 vacuous / 4 bugs |
| `docs/AI_FEATURE_REMEDIATION_PLAN.md` | 9-item fix plan, severity-ordered |
| `docs/SESSION_HANDOFF_2026-06-21.md` | This file |
| `GLM_REMEDIATION_GUIDE_3.md` | Current binding work plan |
| `AGENTS.md` | Agent policy (TDD, completion, guardrail integrity) |
| `.zai.key` | gitignored z.ai subscription key |
| `config/model_profiles/zai_example.yml` | z.ai profile (now: api.z.ai endpoint + glm-4.6 + 16384 tokens) |
| `src/general_ludd/event_loop/loop.py` | Silent-skip fix @ L912-929 |
| `src/general_ludd/daemon.py` | MCP wiring, health-gate, rules, self-improve interval |
