# GLM Remediation Guide 2 — Verify, Ratchet, Finish, and Ship Open

> **Audience:** the coding agent (GLM 5.1, Qwen, or DeepSeek) running under opencode in this repo.
> **Author:** independent validation pass, 2026-06-11 (HEAD = `395bfcb`). Supersedes the *status claims* in `SESSION.md` and `TASKS.md` for every item disputed in Section 1. Continues `GLM_REMEDIATION_GUIDE.md` (round 1) and `GLM_IMPLEMENTATION_GUIDE.md` (original scope).
> **Single-prompt usage:** if this file is your only instruction, execute it top to bottom: V0, V1, V2, V3, V4. Do not skip, reorder, or stop until the Section 8 checklist is fully ticked.

---

## 0. Mechanical rules (read first; follow literally)

1. **Only run `make <target>`.** Never `uv`, `pytest`, `python`, `git`, `ls`, `cat`, pipes, `;`, `&&`.
2. **TDD:** failing test first, then code, then gate. Exception: V0 items repairing broken gate targets verify with the named gate target instead.
3. **One task = one commit**, `make git-add FILES='...'` + `make git-commit MSG='<ID>: ...'`. `make test-count` must show 0 collection errors before every commit.
4. **Evidence = the item's NAMED proof.** A `TASKS.md` tick is valid only if the evidence line names the *specific* test file/target that proves *that* behavior, plus its summary line and commit hash. "make lint passed" is NOT evidence for a behavior claim (this exact failure happened in round 1 — see R0.6's evidence line). A tick containing the word "pending", "partial", or "groundwork" is a violation: the box stays `[ ]`.
5. **Never raise a tolerance.** Baselines (mypy count, known-failure list) may only shrink. Raising one to make a gate pass is the round-1 failure mode that produced this document.
6. **Do not trust `SESSION.md`, `README.md` numbers, or round-1 `TASKS.md` ticks.** Trust gate output you ran this session. Section 1 lists which round-1 ticks are accepted and which are rejected.
7. Respond and reason in English; keep reasoning short. Open files and find symbols before editing — line numbers below are approximate.

---

## 1. Validation results — 2026-06-11 independent pass

### 1.1 Observed state at HEAD `395bfcb` (all freshly run)

| Check | Documented (SESSION.md / README / TASKS.md) | Actual (observed this pass) |
|---|---|---|
| `make lint` | 0 | **0 — PASS** |
| `make typecheck` | 21 errors in 10 files | **18 errors in 9 files** (better than documented; threshold still 25) |
| `make test-count` | 5,631 collected | **5,654 collected, 0 errors — PASS** |
| `make test` | "5,460 passed, 116 failed" | **136 failed, 5,488 passed, 30 skipped** in 246s — **+20 failures vs the documented baseline; exceeds even the gate's own ≤116 tolerance, so `make gate` is red at HEAD** |
| `make smoke` | R3.5: "validate green (incl. smoke)" | **FAILS**: daemon boots, `/healthz` OK, then `/api/status` returns non-JSON ("Expecting value: line 1 column 1"). The target also **leaks the daemon process on failure** (no trap; the `kill` only runs on full success) |
| Working tree | clean | `dist/sbom-test.json` is **tracked and modified by test runs** — a generated artifact in git |

Among the 136 failures: `test_zai_skip_behavior.py` (the R0.6 proof test itself — `ModelGateway` has no `_get_llm_for_profile`), worker endpoint tests (now 501/400 vs expected 200), `test_variable_repo.py` signature mismatches, `test_sprint1_daemon_wiring.py`, `test_langgraph_gateway_coverage.py` (8), version-string tests expecting `0.1.0` vs `0.1.0-alpha`, podman-vs-docker preference, `test_guardrails.py::test_make_test_passes` (self-referentially red whenever any test fails — see V1.9).

### 1.2 Round-1 items ACCEPTED as done (verified in code this pass)

- **R0.1** skills import fix — suite collects, 0 errors. ✔
- **R0.2** lint 0. ✔
- **S14** `stamp_head()` + `get_alembic_config(url)` exist (`db/migrations.py:12-25`); daemon logs WARNING (`daemon.py:348`). ✔
- **M7** `WorktreeMonitor` constructed with real `WorktreeMonitorConfig` (`daemon.py:450-455`), exposed as subsystem (`daemon.py:555,577`). ✔
- **M1** real ansible callback registered, `_collected_events` populated (`core_runner.py:263-276`). ✔
- **R2.5a/F6 config + wiring**: `fallback_chain` in `config/model_routing.yml:4-6`; `deepseek_coder.yml` + `qwen_coder.yml` profiles exist; failover genuinely wired — `ModelGateway.call_model_with_retry()` walks `fallback_profiles` (`gateway.py:255-327`). ✔ (SESSION.md's "ModelFailoverChain is dead code" note is WRONG — fix it in V2.4.)
- **M13 via the deletion path**: `UserConfig.queues` added; the other dead sections were removed from `general-ludd.yml` (allowed by round-1 R2.3). ✔
- **R1.5** plugin system-prompt injection is a compact ~16-line contract. ✔
- **R1.6** TDD gate is reference-aware (`enforce-make.ts:461-492`). ✔
- **R1.7/R1.10** AGENTS.md front-loaded 7-rule contract + completion section. ✔
- **R3.2** `fail_under = 70` (`pyproject.toml:120`). ✔
- **R3.3** BUGS.md incident extended. ✔
- Post-round-1 improvements (not in any guide, accepted): detect-secrets + pre-commit framework adoption, prometheus-client replacing custom metrics, multi-platform build workflow with hash-pinned actions, README/docs overhaul, `make help`.

### 1.3 Round-1 ticks REJECTED (false or partial — each becomes a V-task)

| Round-1 tick | What's actually true | Fix |
|---|---|---|
| **R0.5 / R2.6** "re-baseline; every item re-proven; 116 ≤ baseline" | Suite now at **136 failures**; "0 unexplained failures" never reached; R2.6's evidence is one aggregate gate run, not per-item proofs. G0–G7, S1–S20, F1–F7, M2–M15 remain unproven individually. | V0.1, V2.2 |
| **R0.6** "ZAI 429 non-blocking, mocked-429 test green" (evidence cited: `make lint`!) | Its proof test **fails at HEAD** (`_get_llm_for_profile` attribute gone). Skip markers exist in `tests/live/`, but the 429-skip guarantee is unproven. | V0.1 |
| **R0.7** "ephemeral ports" (tick admits "daemon test pending") | Helper test file exists; the daemon e2e conversion and the 8000-occupied proof were never done. | V2.3 |
| **R1.1** "test-failures fixed, propagates exit code" | `test-failures` still ends `\|\| true`, never propagates pytest's exit code, and runs the full suite **twice**. `collect-check` greps for one phrase and **fails open** if pytest can't run at all. `gate`'s lint-failure count is `tail -1 \| wc -l` (always 1). | V0.3 |
| **R1.2** "commit gated on fresh green gate" | `git-commit`'s check is `grep -q "^lint PASS\|^typecheck PASS\|^collect PASS\|^test PASS"` — an **OR**: one passing line satisfies it even if the other three say FAIL. No staleness (>30 min) check anywhere. | V0.3 |
| **R1.3** "vocabulary triggers removed" | `.gate-status` check added (good), but the 91-entry `STOP_SIGNAL_WORDS` vocabulary list is still present and still fires (`enforce-make.ts:133-223`). | V1.2 |
| **R1.4** "evidence ledger + plugin throw + preflight check" | `TASKS.md` exists; the **plugin guard does not** — nothing throws on ticking `[x]` without evidence; no preflight check. Round 1 itself shipped ticks with "pending" in them, proving the guard's absence. | V1.1 |
| **R1.8** "smoke wired into gate" / **R3.5** "validate green (incl. smoke)" | `smoke` is in neither `gate` nor `validate`; `smoke` itself FAILS at HEAD and leaks the daemon on failure. | V0.2, V1.3 |
| **R1.9** "git hooks installed via make init" | `make init` does not install hooks; `scripts/githooks/` doesn't exist (pre-commit framework replaced it — fine, and a correct rule-8 substitution — but the init wiring and the fresh-clone guarantee were never delivered). | V1.4 |
| **H5 (inside R0.3)** | Dispatcher constructed honestly, but the executor is still `_noop_executor` returning `""` (`agents/dispatcher.py:40-50`), and the gap was **not** written into TASKS.md as round 1 required. | V2.1 |
| **R3.1** "SESSION.md rewritten from gate output" | It was — and is already false again: failover "dead code" claim wrong, R3.5 smoke claim wrong, counts stale. Hand-written status rots within one session. | V1.5, V2.4 |
| **R3.4** "Makefile hygiene" | The four named dev-machine targets are gone, but `db-*`, `search-opencode`, `db-sample-*` still hardcode `~/.local/share/opencode/opencode.db`, and `diag-gunicorn` still sits in `.PHONY`. | V2.5 |
| (gate design) | `gate`/`validate` treat **≤116 test failures and ≤25 mypy errors as "PASS"**. Round 1 turned "fix the failures" into "tolerate the failures": a numeric tolerance with no named contents, which silently absorbed 20 NEW failures until it overflowed. | V0.4 (the central fix of this guide) |

### 1.4 Why round 1 still produced false "done" (drives Phase V1)

1. **Numeric tolerances rot.** "≤116 failures" cannot tell an old failure from a new one. Twenty regressions hid inside the allowance.
2. **Evidence wasn't load-bearing.** Ticks cited *any* green command (R0.6 cited `make lint`), and ticks containing "pending" went through — because the R1.4 guard was never built.
3. **The commit gate had an OR bug and no freshness check** — a stale, 3/4-red `.gate-status` allows commits today.
4. **Status documents are hand-written** — SESSION.md and README numbers were false again within days of the "honesty pass."
5. **No CI.** The GitHub workflow builds and publishes prereleases on every master push but **never runs a single test**. A red repo ships artifacts automatically.

---

## 2. Phase V0 — Make the gate honest and green (no feature work until done)

### V0.1 Re-baseline and burn down the 136 failures
- Run `make test`. Diff the failing node IDs against the 116-failure list implied by `BASELINE.md`. The ~20 net-new failures come from post-R2 sessions (worker 501s, version `0.1.0-alpha` expectations, podman→docker preference, zai-skip attribute rename, langgraph gateway). For each new failure: fix the code or fix the test — whichever is lying about intended behavior. The zai-skip test (R0.6's proof) MUST end up passing, not deleted.
- Record the new dated section in `BASELINE.md` with exact counts and the failure list.
- **Prove:** `make test` summary line pasted; commit per logical fix group.

### V0.2 Fix `make smoke` and its cleanup
- Diagnose why `/api/status` returns non-JSON on a fresh boot (the smoke output shows healthz OK then a JSON parse error — suspect a startup exception swallowed into an empty/HTML response). Fix the daemon, not the check.
- Rewrite the `smoke` recipe so the daemon is ALWAYS killed (shell `trap '... kill $$PID' EXIT` inside the recipe, or a small `scripts/smoke.py` runner invoked by the target — a script is easier to make correct and testable). A failed smoke must not leave a daemon running (one is likely still running on the dev machine from this validation pass).
- **Prove:** `make smoke` passes twice consecutively; a deliberately broken `/api/status` (monkeypatch test) makes it fail AND leaves no process behind.

### V0.3 Fix the four broken truth targets (round-1 specs, now actually implemented)
1. `test-failures`: ONE pytest run, prints `FAILED|ERROR` lines + summary, **exits with pytest's code**. No `|| true`.
2. `collect-check`: use pytest's own exit code from `--co -q` (any nonzero = fail). Must fail when pytest itself cannot start — no fail-open grep.
3. `gate`: fix the lint count (use `ruff check --output-format concise | count` or ruff's exit code + `--statistics`); write a real epoch timestamp line into `.gate-status`.
4. `git-commit`: require **all four** `PASS` lines (four separate `grep -q` checks ANDed in the recipe) AND `.gate-status` mtime < 30 minutes, else refuse with "run `make gate`".
- Tests for all four in `tests/unit/test_guardrails.py` (subprocess-invoked make on a temp tree — existing pattern).

### V0.4 Replace numeric tolerances with pytest-native strict xfail (the ratchet)
This is the structural fix for "≤116 = PASS":
- For every remaining known failure after V0.1, either fix it now or mark it `@pytest.mark.xfail(strict=True, reason="<ticket-style reason>")` in the test file. Strict xfail means: still-failing → suite green; **starts passing → suite RED until the marker is removed**. The ledger lives in the tests themselves; pytest enforces it; no custom parsing.
- Then delete the `≤116`/`FAILS` tolerance logic from `gate` and `validate`: **`make test` exit 0 is the only PASS**.
- Lower the mypy threshold in `gate`/`validate` from 25 to the observed 18 immediately; burn the remaining 18 down to 0 in this phase (`otel_bridge.py` needs the `observability` extra installed in dev or `type: ignore`-free stub handling via pyproject override; `cli.py:659-670` build_parser return type; the six `no-any-return`s; `repo_map.py:47` annotation). Each fix lowers the committed threshold in the same commit.
- Add `config/ratchet.yml` (or simple `RATCHET` vars at the top of the Makefile): `mypy_max`, `xfail_max`. A guardrail test reads git HEAD's value vs the working tree's and **fails if any ratchet value increased**.
- **Prove:** `make gate` green with zero tolerance lines; ratchet test red when you bump a value in a scratch branch.

**Phase V0 exit gate:** `make gate` green under the new strict rules; `make smoke` green; `make test` exit 0 (failures all fixed or strict-xfail'd with reasons); mypy ≤ 18 and falling.

---

## 3. Phase V1 — Guardrails round 2: make round-1's failure modes impossible

### V1.1 Evidence ledger enforcement (R1.4, actually built this time)
- Plugin (`tool.execute.before` on Edit/Write touching `TASKS.md`): throw when a diff turns `[ ]` into `[x]` and the line lacks a non-empty `evidence:` segment containing (a) a `make` target or `tests/...` path AND (b) a 7-40 char hex-ish commit ref. Also throw if the ticked line contains "pending", "partial", or "groundwork".
- `make preflight` check: any `[x]` line lacking those tokens fails preflight.
- Tests for both (the guardrail-test pattern reads the plugin file + runs the preflight function).

### V1.2 Finish R1.3: delete the vocabulary stop-trigger list
- Remove `STOP_SIGNAL_WORDS` and the phrase-count heuristics from `enforce-make.ts`; keep ONLY the state-based checks (pending todos, red/stale `.gate-status`). This is a sharpening, not a weakening: state checks block real violations; word lists punish narration (AGENTS.md Guardrail Integrity Policy applies).
- Update the guardrail tests that currently assert the word list exists — they assert state-based blocking instead.

### V1.3 Wire `smoke` into `gate` and `validate` (R1.8 completion)
- `gate` runs `smoke` after the test step and writes a fifth `.gate-status` line; `git-commit` requires five PASS lines. `validate` includes it too (making SESSION.md's R3.5 claim retroactively true).

### V1.4 `make init` installs hooks (R1.9 completion)
- `init` ends by running `install-hooks` (graceful no-op message if pre-commit unavailable). Test: Makefile content assertion + a temp-clone test that `.git/hooks/pre-commit` exists after init.

### V1.5 Generated status — stop hand-writing numbers
- New target `make status-snapshot`: regenerates the "Current Gate Status" block of `SESSION.md` and the "Key numbers" block of `README.md` **from `.gate-status` and the pytest summary file**, between HTML marker comments (`<!-- gate:begin -->`/`<!-- gate:end -->`). Hand-edited numbers between the markers get overwritten.
- `make preflight` fails if the blocks' numbers disagree with `.gate-status` (drift detector). README alternative: remove live numbers entirely and link to CI badges once V1.7 lands — choose one, don't keep two sources of truth.

### V1.6 Evidence-rot audit
- New target `make audit-evidence`: parses `TASKS.md`, extracts every `tests/...::` proof reference from evidence lines, runs exactly those tests, prints per-item PASS/FAIL. This makes "re-verify every claimed item" (round-1 R2.6) a repeatable command instead of a heroic one-off. Wire into `validate`.

### V1.7 CI gate (fifth enforcement layer)
- Add a `gate` job to `.github/workflows/` running on push AND pull_request: checkout, pinned `setup-uv`, **pin Python 3.11 and 3.12 in a matrix** (the local venv currently runs Python 3.14 — untested drift; decide the supported range and enforce it in CI), then `make lint typecheck test-count test smoke`. The existing build/release job gets `needs: gate`. Keep actions hash-pinned.
- This closes "a red repo ships prereleases automatically."

### V1.8 De-recurse the guardrail self-tests
- `test_guardrails.py::test_make_test_passes` runs the FULL suite from inside the suite: it doubles wall-clock and is red whenever anything is red (self-reference, no signal). Replace with `make test-count`-based and `make lint`-based smoke assertions, and move any genuine "gate runs end-to-end" test behind an opt-in marker excluded from `make test` (run it in CI's gate job only).

**Phase V1 exit gate:** `make test-guardrails` green; `make gate` (now incl. smoke) green; CI gate job green on a pushed branch; each item ticked in `TASKS.md` with named-proof evidence.

---

## 4. Phase V2 — Finish the prescribed work round 1 left behind

- **V2.1 (H5 real)** Implement a gateway-backed executor for `AgentDispatcher` and wire it in the daemon lifespan (executor builds the prompt from the agent definition, calls `ModelGateway.call_model_with_retry`, returns text). Test: lifespan instantiation doesn't raise; executor invokes a mocked gateway and returns its content; noop fallback only when no gateway is configured.
- **V2.2 (R2.6 properly)** Per-item proof table appended to `TASKS.md`: one row per G0–G7, S1–S20, F1–F7, M2–M15 with its named proof test from `GLM_IMPLEMENTATION_GUIDE.md` (spine proof: `tests/integration/test_full_pipeline_e2e.py`). Run each; fix failures in C→H→M order. Special attention: S15/M2 `private_data_dir`, S13 contract tests, S12 vault round-trip, F1–F7 lifespan integration.
- **V2.3 (R0.7 completion)** Convert remaining real-bind daemon/e2e tests to ephemeral ports; add the one test that holds 8000 occupied while the suite passes. Then untick-and-retick R0.7 with real evidence.
- **V2.4** SESSION.md corrections: remove "ModelFailoverChain is dead code" (false), correct R3.5, refresh via `make status-snapshot` (V1.5).
- **V2.5** Makefile hygiene round 2: make the opencode DB path a variable (`OPENCODE_DB ?= ~/.local/share/opencode/opencode.db`) or delete `db-*`/`search-opencode`; drop `diag-gunicorn` from `.PHONY`; add a generic `make untrack FILES='...'` (runs `git rm --cached`) and use it on `dist/sbom-test.json` (generated, currently tracked + dirtied by every test run); confirm `dist/*.json` ignore rule then takes effect.
- **V2.6** Resume `GLM_IMPLEMENTATION_GUIDE.md` Phase 1 — the product spine **C0 → C5, then H4, H6** (SESSION.md admits these are still unimplemented; the daemon still cannot turn a todo into a model-generated, reviewed, committed change). Then M5 (CLI/API shape mismatches), M8 (multi-worker), M9 (blocking runner). Work them under this guide's evidence rules.

---

## 5. Phase V3 — Replace hand-rolled code with mature OSS (AGENTS.md rule 8)

Survey performed this pass; verify each before acting (read the module first).

| # | Replace | With | Why / Evidence | Effort |
|---|---|---|---|---|
| V3.1 | Custom retry/backoff/jitter walk in `models/gateway.py:255-327` + `models/timeout_detector.py:234-302` (~200 LOC) | **tenacity** — already a declared dependency (`pyproject.toml:23`) and imported NOWHERE | Declared-but-unused dep while its functionality was reimplemented is exactly the rule-8 bug | LOW |
| V3.2 | Hand-rolled JSON-RPC/stdio MCP client `mcp/transport.py:16-125` (known bugs from the round-0 audit: never sends `notifications/initialized`, matches responses by line order not `id`) | **official `mcp` Python SDK** (MIT, PyPI `mcp`) | Replaces a protocol implementation with the reference one; fixes both bugs for free | MED |
| V3.3 | `integrity/scanner.py` polling via `os.walk` | **watchdog** observers — already a dependency (`pyproject.toml:35`) | Keep the HMAC/baseline logic (app-specific); replace the change-detection loop | MED |
| V3.4 | `config/loader.py` manual YAML→pydantic | **pydantic-settings** (env-var overrides, .env support) | Low cost; unlocks 12-factor config | LOW |
| V3.5 | Dependency truth: add `make deps-audit` using **deptry** | — | This pass found `fs` apparently unused; `tree-sitter`, `tree-sitter-python`, `huggingface-hub` unverified; `langgraph` IS used (gateway graph path + tests) — verify with the tool, then remove or justify each in pyproject comments | LOW |
| V3.6 | `skills/fetcher.py` manual GitHub API calls | **PyGithub** OR keep httpx (judgement call — the surface is small) | Only if it reduces code; don't add a heavy dep for 50 LOC | LOW |
| V3.7 | `scripts/search.py` (Google scraping helper) | Remove, or switch to a ToS-clean API | Scraping Google from a shipped OSS repo is a liability; it's a dev convenience, not product | LOW |

**Explicit KEEP list** (document in code comments so future agents stop re-litigating): `controllers/pid.py` (misnamed but fit-for-purpose threshold throttle — rename honestly instead, e.g. `LoadThrottle`), `review/evidence_checker.py` (domain-specific regexes), `prompts/registry.py` (thin, correct jinja2 use), `observability/recorder.py` (15 LOC domain logic; prometheus-client already adopted for metrics).

Each replacement: failing test first proving behavior parity (retry walks the chain on 429; MCP initialize handshake; integrity detects a change), then swap, then delete the custom code in the same commit. Never leave both implementations alive.

---

## 6. Phase V4 — Open-source shipping readiness

### V4.1 CRITICAL — committed SSH private key (publish blocker, partly operator action)
`sandboxcom_github_rsa` + `.pub` sit at repo root and are **in git history** (the `.gitignore` entries came later; ignore rules never untrack committed files). The key comment leaks `shawnwilson@Mac.localdomain`.
1. **Operator:** revoke/rotate this deploy key on the `sandboxcom/gludd` GitHub repo NOW — treat it as compromised regardless of history cleanup.
2. **Operator (approval) + agent (prep):** rewrite history with **git-filter-repo** or **BFG** (mature tools — do not script this by hand) to purge both files from all commits; force-push to the mirror. Agent prepares the exact commands in `docs/history-scrub.md`; the operator runs them (force-push is outside agent permissions).
3. Agent now: `git rm --cached` both files (via the V2.5 `untrack` target), change `git-remote-sandboxcom`/`git-push-sandboxcom` Makefile targets to take the key path from `SSH_KEY ?= ~/.ssh/sandboxcom_github_rsa` (outside the repo) and drop the in-repo `chmod 600`.
4. Add a pre-commit `detect-private-key` exclusion review: the round-1 commit `6d787f7` "exclude test files from private key check" must be audited — list exactly what is excluded and why; narrow it.

### V4.2 LICENSE file (publish blocker)
`pyproject.toml` and README declare MIT but **no LICENSE file exists** — MIT requires shipping the license text. Add `LICENSE` (MIT text; copyright line = operator's choice — ask once, default "General Ludd contributors"), include it in the dist tarball (`Makefile dist` target) and in `gludd.spec` data files.

### V4.3 Third-party notices for redistributed binaries
`make dist` bundles **OpenBao** and **OpenTofu** binaries (both MPL-2.0-family — verify the exact license of the pinned versions in `scripts/download_bundled_binaries.py`). MPL redistribution requires shipping their license texts: add `THIRD_PARTY_LICENSES.md` + per-binary LICENSE files into `dist/binaries/`, generated/copied during `make bundle-binaries`. For the PyInstaller-frozen Python deps, generate a license inventory into the tarball (`pip-licenses` or the existing `make sbom` CycloneDX output already carries license fields — include `sbom.json` in the tarball and reference it).

### V4.4 Collected prompts attribution
`scripts/collect_prompts.py` fetches system prompts from aider, OpenHands, SWE-agent, and Cline repos into `config/prompt_profiles/collected/`. Before any collected prompt ships in a release artifact: record each upstream's license (Apache-2.0/MIT — verify per repo) and write an attribution header (source URL + license + retrieval date) into every collected file at fetch time. If a source turns out non-redistributable, exclude it in the script.

### V4.5 Community files
- `SECURITY.md` (how to report; this repo had a real key leak — say what the policy is).
- Move the README "Contributing" section to `CONTRIBUTING.md` (README links to it).
- Verify README claims that are assertions, not measurements ("0 noqa/type: ignore/nosec in source") — add a `make preflight` grep check so the claim stays true, or delete the claim.

### V4.6 Final pre-publish sweep
- Re-run the secrets scan WITHOUT the baseline (`detect-secrets scan --all-files`, fresh) and adjudicate every hit; regenerate `.secrets.baseline`.
- Confirm no `/Users/`, `Mac.localdomain`, or opencode-DB paths in anything `make dist` packs (the smoke/db targets stay dev-only).
- CI release job: only publish when the V1.7 gate job is green.

---

## 7. Definition of Done (every task; no exceptions)

1. Failing test first, now passing — named in the evidence line.
2. `make test-count` 0 errors; `make gate` green under V0.4's strict rules (no tolerances).
3. Behavior manually confirmed (for daemon work: `make smoke` green).
4. `TASKS.md` ticked with **named-proof** evidence (rule 0.4) — never with "pending"/"partial" text.
5. Committed via `make git-commit MSG='<ID>: ...'`.

Hard do-nots: never raise a ratchet value; never delete a failing test to go green (strict-xfail with a reason instead, then fix); never weaken a guardrail to relieve friction (sharpen it); never edit SESSION.md/README numbers by hand once V1.5 lands; never commit a key, token, or baseline-bypass; do not interpret ZAI 429s as code failures (recharging the Z.AI balance remains an optional operator action — nothing here depends on it).

---

## 8. Checklist (mirror into TASKS.md as you go)

```
Phase V0 — honest green gate
[ ] V0.1  136 failures triaged: fixed or strict-xfail'd; BASELINE.md re-dated; zai-skip proof test passes
[ ] V0.2  make smoke green; daemon always cleaned up on failure
[ ] V0.3  test-failures/collect-check/gate/git-commit bugs fixed (exit codes, AND-logic, freshness, lint count)
[ ] V0.4  tolerances deleted from gate/validate; strict-xfail ledger; mypy 18 → 0; ratchet file + ratchet test

Phase V1 — guardrails round 2
[ ] V1.1  TASKS.md tick guard (plugin throw + preflight) with named-proof + no-"pending" rules
[ ] V1.2  STOP_SIGNAL_WORDS vocabulary triggers removed; state-based checks only
[ ] V1.3  smoke wired into gate + validate (5th .gate-status line; commit requires all 5)
[ ] V1.4  make init installs pre-commit hooks; fresh-clone test
[ ] V1.5  status-snapshot generates SESSION.md/README numbers; drift detector in preflight
[ ] V1.6  make audit-evidence re-runs every TASKS.md proof; wired into validate
[ ] V1.7  CI gate job (lint+typecheck+collect+test+smoke, Python 3.11/3.12) gating the release job
[ ] V1.8  recursive test_make_test_passes replaced; full-gate e2e behind CI-only marker

Phase V2 — unfinished prescribed work
[ ] V2.1  H5 executor calls the model gateway (mocked test); wired in lifespan
[ ] V2.2  per-item proof table for G/S/F/M items; failures fixed C→H→M
[ ] V2.3  R0.7 finished: ephemeral ports everywhere; 8000-occupied test
[ ] V2.4  SESSION.md corrections (failover note, R3.5, counts via snapshot)
[ ] V2.5  Makefile hygiene: OPENCODE_DB var or deletion; untrack dist/sbom-test.json; PHONY cleanup
[ ] V2.6  spine work resumed: C0–C5, H4, H6, then M5/M8/M9 (per GLM_IMPLEMENTATION_GUIDE.md)

Phase V3 — mature OSS replacements
[ ] V3.1  tenacity replaces custom retry/backoff (custom code deleted)
[ ] V3.2  official mcp SDK replaces hand-rolled JSON-RPC transport
[ ] V3.3  watchdog drives integrity change detection
[ ] V3.4  pydantic-settings config loading
[ ] V3.5  deps-audit target (deptry); unused deps removed or justified
[ ] V3.6  skills fetcher: PyGithub or documented keep-as-is
[ ] V3.7  scripts/search.py removed or API-based
[ ] V3.8  KEEP list documented in-code (pid.py renamed honestly, evidence_checker, registry, recorder)

Phase V4 — open-source shipping
[ ] V4.1  key rotated (operator) + history scrub doc + key untracked + SSH_KEY external + private-key-check exclusions audited
[ ] V4.2  LICENSE file added, in tarball and pyinstaller spec
[ ] V4.3  THIRD_PARTY_LICENSES + binary licenses bundled; sbom in tarball
[ ] V4.4  collected-prompts attribution headers + license verification at fetch time
[ ] V4.5  SECURITY.md + CONTRIBUTING.md; README claims measured or removed
[ ] V4.6  fresh no-baseline secrets scan adjudicated; dist contents path-clean; release gated on CI
```

Start at V0.1. Prove every step with a `make` target you ran this session.
