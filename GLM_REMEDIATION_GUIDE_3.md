# GLM Remediation Guide 3 — Guide-2 Validation, Remaining Gaps, Ship Plan

> **Audience:** the coding agent (GLM 5.1) running under opencode in this repo.
> **Author:** independent validation pass, 2026-06-12 (HEAD = `65fc28b`). Supersedes the *status claims* in `SESSION.md` and `TASKS.md` for every item adjudicated in Section 1. Continues `GLM_REMEDIATION_GUIDE_2.md`.
> **Single-prompt usage:** if this file is your only instruction, execute it top to bottom: W0, W1, W2, W3, W4, W5, W6. (Exception: W6 may be interleaved with W3 — see W6.9's spine note.) Do not skip, reorder, or stop until the Section 9 checklist is fully ticked.

---

## 0. Mechanical rules (read first; follow literally)

1. **Only run `make <target>`.** Never `uv`, `pytest`, `python`, `git`, `ls`, `cat`, pipes, `;`, `&&`.
2. **TDD:** failing test first, then code, then gate.
3. **One task = one commit.** `make git-add FILES='...'` then `make git-commit MSG='<ID>: ...'`. `make test-count` must show 0 collection errors before every commit. `make git-commit` requires a fresh (<30 min) all-PASS `.gate-status` — run `make gate` first.
4. **Evidence = the item's NAMED proof.** A `TASKS.md` tick needs the specific test file/target that proves *that* behavior + its summary line + commit hash. Ticks containing "pending", "partial", or "groundwork" are violations.
5. **Never raise a ratchet.** `config/ratchet.yml` may only shrink. Removing an entry means the test now PASSES, not that you deleted the test. The mypy threshold (18, hardcoded in `Makefile` gate and validate) may only go down.
6. **Do not trust `SESSION.md` or old `TASKS.md` ticks.** Trust gate output you ran this session, and Section 1 below.
7. Open files and find symbols before editing — line numbers below are approximate and will drift.

---

## 1. Validation results — 2026-06-12 independent pass

### 1.1 Observed state at HEAD `65fc28b` (all freshly run)

| Check | Result |
|---|---|
| `make lint` | **0 errors — PASS** |
| `make typecheck` | **18 errors (= baseline 18) — PASS** |
| `make test-count` | **5,695 collected, 0 errors — PASS** |
| `make gate` | **ALL PASSED** (lint PASS 0, typecheck PASS 18, collect PASS 0, test PASS 0, smoke PASS) |
| `config/ratchet.yml` | **23 entries** (17 strict + 6 flaky) |
| Working tree | clean |

**The headline guide-2 goal — an honest green gate with zero numeric tolerances — is genuinely achieved.** `make test` exit 0 is the only PASS; known failures live in `config/ratchet.yml` as strict xfail; `git-commit` requires five ANDed PASS lines plus a 30-minute freshness check. This was verified by running the gate, not by reading notes.

SESSION.md's stale self-contradictions (claimed 23, 32, and 41 ratchet entries in three different sections) were corrected in this pass: the true count is **23**.

### 1.2 Guide-2 checklist adjudication

✔ = verified done (code/gate evidence). ◐ = partial. ✗ = not done. ‼ = tick exists but is FALSE.

| Item | Verdict | Evidence |
|---|---|---|
| V0.1 failures triaged | ✔ | `make gate` test PASS 0; 23 entries remain in `config/ratchet.yml` |
| V0.2 smoke green + cleanup | ✔ | smoke PASS in gate; `trap ... EXIT` in `Makefile` smoke recipe |
| V0.3 truth targets fixed | ✔ | `test-failures` propagates exit; `collect-check` uses pytest exit code; `git-commit` ANDs 5 PASS lines + epoch freshness (`Makefile:432-450`) |
| V0.4 tolerances → strict xfail | ◐ | Ratchet live (`tests/conftest.py:23-48`, strict xfail, `strict=False` for "flaky" reasons). **Missing:** mypy still 18 (target was 0) and **no ratchet-growth guardrail test exists** (nothing fails if `ratchet.yml` gains entries vs HEAD) → W1.1, W5.4 |
| V1.1 TASKS.md tick guard | ✗ | No guard in `enforce-make.ts`; no preflight check. Round-1 failure mode still possible → W1.2 |
| V1.2 delete STOP_SIGNAL_WORDS | ✗ (inverted) | List GREW to ~148 entries (`enforce-make.ts:133-281`). State-based checks exist alongside (`enforce-make.ts:617-686`). Every BUGS.md incident is "pattern not in list" — proving word lists lose → W1.3 |
| V1.3 smoke in gate/validate | ✔ | 5th `.gate-status` line; commit requires all 5 |
| V1.4 init installs hooks | ✔ | `Makefile` init ends with `install-hooks` |
| V1.5 generated status | ◐ | `status-snapshot` writes a snippet to /tmp and asks for a **manual paste**; no in-place replace, no README block, no preflight drift detector. SESSION.md drifted false again within one day — proving the need → W1.4 |
| V1.6 audit-evidence | ◐ | Target exists (`Makefile:319-329`) but is **not wired into `validate`** → W1.5 |
| V1.7 CI gate job | ✔ | `.github/workflows/build.yml:28-46`: 3.11/3.12 matrix, `make lint typecheck test-count test smoke`; all build jobs + release `needs: gate`; actions hash-pinned |
| V1.8 de-recurse guardrail test | ✔ | `test_make_test_count_passes` is collection-only (`tests/unit/test_guardrails.py:41-47`) |
| V2.1 H5 gateway executor | ✔ | `daemon.py:463-496` builds gateway-backed executor when profiles exist; noop only as fallback |
| V2.2 per-item proof table | ✗ | No G/S/F/M proof table in `TASKS.md` → W3.6 |
| V2.3 ephemeral ports | ◐ | e2e conftest helper landed; the 8000-occupied proof is still a ratchet entry → W2.6 |
| V2.4 SESSION.md corrections | ◐ | Corrected, then drifted again (23/32/41 contradiction); fixed in this pass; W1.4 is the durable fix |
| V2.5 Makefile hygiene | ◐ | `untrack` target exists; `dist/sbom-test.json` gitignored. **Still hardcoded:** opencode DB path in `db-*`/`search-opencode` targets; `diag-gunicorn` still in `.PHONY` → W1.6 |
| V2.6 spine C0–C5/H4/H6 | ◐ | C0/C2/C3/C4/H6 ticked with evidence. **C1 NOT done** (worker never calls a model — see W3.1), **H4 NOT done** (`ReturnReviewer` has zero production instantiations; `apply_decision` has no caller), C5 partial (destroy tests still ratcheted), M5 partial (`models list` reads `profiles` now; rest unaudited), **M8/M9 NOT done** (`run_playbook` called synchronously at `event_loop/loop.py:~602`) |
| V3.1 tenacity | ‼ **FALSE TICK** | `call_with_tenacity` (`gateway.py:446-473`) is a parallel demo with **no production caller**; `call_model_with_retry` (`gateway.py:256-327`) is still the hand-rolled loop used by `daemon.py`. Guide 2 §5: "Never leave both implementations alive." Tick reverted in this pass → W4.1 |
| V3.2 mcp SDK | ✗ | Hand-rolled transport remains — **but both named protocol bugs were fixed in place** (`transport.py:98` sends `notifications/initialized`; `transport.py:52` matches by id). Re-scoped → W4.2 |
| V3.3 watchdog | ✗ | `integrity/scanner.py:100-110` still `os.walk` polling → W4.3 |
| V3.4 pydantic-settings | ✗ | `config/loader.py:17-19` manual `yaml.safe_load` → W4.4 |
| V3.5 deptry | ◐ | `deps-audit` target exists but **deptry is not in dev deps** (target always prints the install hint); `fs>=2.4.0` still listed (`pyproject.toml:36`) and apparently unused → W4.5 |
| V3.6 fetcher keep-as-is | ✔ | Documented |
| V3.7 search.py removed | ✔ | Deleted |
| V3.8 KEEP list / pid rename | ? | Unverified — confirm in W4.6 |
| V4.1 SSH key | ◐ **PUBLISH BLOCKER** | `docs/history-scrub.md` exists (prep done). **The private key is still at the repo root and in git history.** `.gitignore` lists it (does not untrack). Makefile still chmods/uses the in-repo path; `scan-secrets-baseline` *excludes* the key files from scanning → W5.1 |
| V4.2 LICENSE | ◐ | `LICENSE` exists (MIT). **Not packed**: neither `make dist` nor any CI package step copies LICENSE into artifacts → W5.2 |
| V4.3 third-party notices | ◐ | `THIRD_PARTY_LICENSES.md` exists. Not copied into `make dist` tarball or CI release archives; sbom.json not included → W5.2 |
| V4.4 prompt attribution | ✔ | `scripts/collect_prompts.py:233-243` writes source/license/date headers at fetch time |
| V4.5 community files | ✔ | `SECURITY.md` + `CONTRIBUTING.md` exist |
| V4.6 final sweep | ✗ | No evidence of a fresh no-baseline secrets scan; release-gated-on-CI part is done → W5.3 |

### 1.3 New findings (not in any prior guide)

| # | Finding | Fix |
|---|---|---|
| N1 | **`/healthz` reports healthy while the daemon is degraded.** `daemon.py:530-537` catches startup exceptions, sets `app.state._degraded`, but no endpoint reflects it. A dead event loop serves green health checks — the C6 failure mode survives in softer form. | W3.4 |
| N2 | **Gate/validate test logs lose stderr.** `Makefile:225` and `:636` use `2>&1 > /tmp/...` — redirections apply left-to-right, so stderr goes to the *terminal* and only stdout reaches the file. Crash tracebacks (stderr) never land in the log the agent reads. Use `> /tmp/... 2>&1`. | W1.6 |
| N3 | **The mypy threshold `18` is hardcoded twice** (`Makefile` gate `:221` and validate `:635`). Two copies will diverge. Single-source it (one `MYPY_MAX := 18` variable). | W1.6 |
| N4 | **6 "flaky" ratchet entries paper over real races.** The 2 hvac xdist races are mock-patch races under `pytest-xdist`; `@pytest.mark.xdist_group` (suite already runs `--dist loadgroup`) pins them to one worker — a root-cause fix, not an xfail. | W2.7 |
| N5 | The detect-secrets baseline target hardcodes an exclusion for the committed key (`Makefile scan-secrets-baseline`) — the scanner is configured not to see the worst secret in the repo. Remove the exclusion when W5.1 untracks the key. | W5.1 |

### 1.4 Second sweep — original-guide H/M items re-adjudicated (2026-06-12, same pass)

The H/M defects from `GLM_IMPLEMENTATION_GUIDE.md` §1 that no later guide re-checked. Verified against current code.

**Already FIXED — do NOT redo these (verify only if your edit touches them):**

| Item | Evidence |
|---|---|
| H1 benchmark routers/session factory | `routers/benchmark.py:17-27` uses `_get_session_factory()` + context-managed sessions |
| H7 benchmark recorder starved | `daemon.py:443-446` instantiates `AutoBenchmarkRecorder` and attaches it to the loop |
| H9 skills discovery/skill_body | `skills/loader.py:61` uses recursive `glob("**/*.md")`; `schemas/job.py:28` has `skill_body` |
| H10 templates/autoescape | `prompts/registry.py:33` — templates_dir has env fallback; autoescape is False |
| H11 budget guard | `daemon.py:389-393` constructs `RunBudgetGuard` from the `budget:` config |
| M4 slurm error swallow | `routers/slurm.py:101-102` raises HTTPException instead of returning `{"jobs": []}` |
| Live ZAI skip | `tests/live/test_zai_live.py:85` skipif when key absent |
| M3 `_inject_auth_env` | UNVERIFIED — symbol no longer found; confirm renamed-or-removed when touching infra/ |

**STILL BROKEN — new tasks below:**

| Item | Evidence | Task |
|---|---|---|
| H2 self-improvement discards its own todos | `daemon.py:395-419` never passes `self_improve_interval`; `loop.py:~747` enqueues into a throwaway in-memory harness | W3.7 |
| H3 worker validate/policy-validate/reload endpoints are ack stubs | `worker/app.py:101-111` | W3.8 |
| H8 MCP never constructed; gateway has no tools param | `daemon.py:403` (`mcp_client=None`); mcp_servers cfg loaded at `daemon.py:140-154` then unused | W3.9 |
| H12 router-built gateway records no metrics | `daemon.py:472` passes `metrics_collector`; `routers/models.py` does NOT | W3.10 |
| H13 nothing ever clones `repo_url` — a dispatched job has no code to edit | `projects/manager.py:19-39` stores it; zero `clone` callers in src/ | W3.11 (spine-critical) |
| H14 hot-reload theater | `reload/hot_reloader.py:103-109` returns `models_reloaded: True` after a bare existence check; `ReloadManager.execute_reload` reports success doing nothing | W3.12 |
| H16 preflight passes unknown criteria | `quality/preflight.py:182-184` `met=True, "assumed_met"` — and commit `ecaeedf` updated tests to EXPECT this, cementing the bug | W1.7 |
| H17 secrets `auto` mode never tries OpenBao; no read-back of migrated secrets | `daemon.py:185-197` falls straight to env | W2.9 |
| H18 Postgres cannot work | `db/session.py:114-118` create_all SQLite-only; `alembic.ini:3` hardcodes `sqlite:///./test.db`; stamp_head SQLite-only (`daemon.py:~341`) | W3.5 (document) |
| M2 no deployments listing; fresh `DeploymentManager` per request | `routers/compute.py:31-46`; no `/api/deployments` endpoint exists | W2.3 (same registry fix) |
| M11 — **CORRECTED 2026-06-12 (second look)**: the `/admin/code/*` endpoints DO exist (`routers/models.py:150-182` blocks/graph/search, `:260-318` complexity/suggest-model); the earlier "phantom endpoints" verdict in commit `4262064`'s message was wrong (they live in models.py, not a dedicated router). Remaining gap is unproven parity: no test exercises CLI→endpoint shapes end-to-end | `routers/models.py:150-182,260-318`; `cli.py` `_cmd_code_*` | W3.13 (re-scoped) |
| H1-residual — `/admin/observability/comparison` reads `app.state._session`, which the lifespan never sets → permanently returns "No DB session available" (the H1 bug pattern survives in this one endpoint; benchmark.py was fixed, this wasn't) | `routers/models.py:143-148` | W3.10 |
| M14 phases pick different random projects in one tick | `event_loop/loop.py:349,489` independent `select_project()` calls | W3.14 |
| AUTH worker endpoints are unauthenticated | `worker/app.py:45-112` no PSK/auth check; the daemon HAS auth (`daemon.py:674-689`) | W5.6 |
| COV the local gate never enforces coverage | pytest `addopts` has no `--cov` (`pyproject.toml:~113`) and the `gate` test step runs without `--cov` — `fail_under=70` only binds in `make test`/CI | W1.6 item 5 |

---

## 2. Phase W0 — Truth repairs — **DONE in this validation pass**

- W0.1 SESSION.md stale ratchet counts (23/32/41 contradiction) corrected to 23. ✔
- W0.2 TASKS.md V3.1 false tick reverted with adjudication note. ✔
- W0.3 This guide written; CLAUDE.md points to it. ✔

Nothing for the agent to do here except: **re-verify these files were not hand-edited back** before starting W1.

---

## 3. Phase W1 — Guardrail completion (small, do first, ~1 commit each)

### W1.1 Ratchet-growth guardrail test (V0.4's missing piece)
- New test in `tests/unit/test_guardrails.py`: parse `config/ratchet.yml` in the working tree, count entries; compare against a committed constant `RATCHET_MAX = 23` defined at the top of the test file. Fail if count > constant. Every time you burn entries, lower the constant **in the same commit**.
- Why a constant and not git-HEAD comparison: `make`-only policy gives you no `git show`; a committed constant is enforceable and reviewable.
- **Prove:** test passes at 23; temporarily add a dummy entry → test fails → remove it.
- Commit: `W1.1: ratchet-growth guard — config/ratchet.yml may only shrink`

### W1.2 TASKS.md tick guard (V1.1, still unbuilt after two guides)
- `make preflight` (i.e. `quality/preflight.py`): add a check that every `- [x]` line in `TASKS.md` contains (a) `evidence:`, (b) a `make ` target or `tests/` path, (c) a 7-40 char hex commit ref, and (d) none of the words "pending", "partial", "groundwork". Any violation = preflight FAIL with the offending line printed.
- Plugin half (`.opencode/plugin/enforce-make.ts`, `tool.execute.before` on Edit/Write touching TASKS.md): throw on a `[ ]`→`[x]` diff lacking the same tokens. Note plugin changes only load on opencode restart — the preflight check is the load-bearing one.
- Tests: unit test feeding good/bad TASKS.md content to the preflight function.
- Commit: `W1.2: TASKS.md tick guard in preflight + plugin`

### W1.3 Resolve the stop-guardrail contradiction (V1.2)
- Guide 2 ordered STOP_SIGNAL_WORDS deleted; sessions since grew it to ~148 entries, and every BUGS.md incident since is "pattern not in list" — the list demonstrably loses. Keep ONLY the state-based checks (pending todos, red/stale `.gate-status`, non-empty `config/ratchet.yml` + completion-sounding response). Delete the vocabulary list and phrase heuristics from `enforce-make.ts`.
- Retarget `tests/unit/test_anti_stop_fuzz.py`: it must assert the STATE checks block BUGS.md incident messages (run each message through the detector with a non-empty ratchet fixture), not that specific words are listed.
- Commit: `W1.3: V1.2 done — state-based stop checks only, vocabulary list deleted`

### W1.4 status-snapshot writes in place + drift detector (V1.5)
- Change `make status-snapshot` to rewrite the `<!-- gate:begin -->`/`<!-- gate:end -->` block **inside `SESSION.md` directly** (a small `scripts/status_snapshot.py` invoked by the target; stdlib only). Add the marker comments to SESSION.md around the "Current Gate Status" section.
- Preflight: fail if the numbers inside the markers disagree with `.gate-status` (drift detector).
- **Prove:** hand-edit a number between markers → preflight fails → `make status-snapshot` fixes it.
- Commit: `W1.4: status-snapshot in-place + preflight drift detector`

### W1.5 Wire audit-evidence into validate (V1.6)
- Add `audit-evidence` to the `validate` recipe after the test step. It must propagate failure (currently it ends with `|| echo ...` — same fail-open bug class V0.3 fixed elsewhere; make a failed evidence test fail the target).
- Commit: `W1.5: audit-evidence in validate, fail-closed`

### W1.6 Makefile hygiene round 3 (V2.5 leftovers + N2 + N3)
1. Fix redirect order in gate and validate test steps: `> /tmp/... 2>&1` (N2).
2. `MYPY_MAX := 18` variable at top; gate and validate both use it (N3). Lower it as W5.4 burns errors.
3. `OPENCODE_DB ?= ~/.local/share/opencode/opencode.db` variable for `db-*`/`search-opencode`, or delete those targets.
4. Remove `diag-gunicorn` from `.PHONY` (target doesn't exist).
5. Coverage in the gate (COV, §1.4): the `gate` test step runs pytest without `--cov`, so `fail_under=70` never binds locally — add `--cov=general_ludd` to the gate's test invocation (accept the runtime cost; the gate is THE truth target).
- Commit: `W1.6: Makefile hygiene — stderr capture, MYPY_MAX, OPENCODE_DB, PHONY, gate coverage`

### W1.7 Preflight must fail-closed on unknown criteria (H16)
- `quality/preflight.py:182-184`: an unrecognized completion criterion currently gets `met=True, reason="assumed_met"` — the quality gate literally cannot say no to a typo'd criterion. Flip it: unknown criterion → `met=False, reason="unknown_criterion"`.
- Commit `ecaeedf` updated tests to EXPECT `assumed_met` — those tests codified the bug; rewrite them to expect fail-closed (this is fixing a test that lies about intended behavior, not deleting a guardrail).
- **Prove:** preflight test with a made-up criterion name asserts FAIL.
- Commit: `W1.7: H16 — preflight fails closed on unknown criteria`

**Phase W1 exit gate:** `make gate` green; `make preflight` includes tick-guard + drift checks; ratchet-growth test live.

---

## 4. Phase W2 — Burn the 23 ratchet entries (root-cause groups)

Read `config/ratchet.yml` first; entries may have moved. For each fixed test: remove its ratchet line, lower `RATCHET_MAX` (W1.1), run `make gate`, commit. **Never delete a test to remove an entry.**

| Group | Entries (~) | Strategy |
|---|---|---|
| W2.1 Daemon lifespan real-DB | 2 (`test_daemon_lifespan.py`) | The lifespan test needs a real (tmp-path SQLite) DB: build the app with a `config_dir` fixture pointing at a temp config with a sqlite URL; assert the event loop ticks and todo CRUD round-trips. This also pins C2/C3 behavior. |
| W2.2 Container/binary-resolver | 5 (`test_new_features_e2e.py` binary/secrets-wiring, secrets manager container start/fail, `test_obj09` image scan) | These need a container runtime. Make the tests runtime-aware: a fixture that skips (`pytest.skip`, not xfail) when neither podman nor docker is on PATH, and genuinely runs when one is. On the dev machine podman exists — the tests must then pass. Fix `SecretsManager._fetch_remote_digest` returning a random sha (M15) while here. |
| W2.3 Deploy-before-destroy | 3 (`test_deployment.py`, e2e lifecycle) | Finish C5: `DeploymentManager.destroy(instance_id)` must look up the deployment's persisted working dir/state (persist a registry keyed by instance_id at deploy time), refuse when unknown, and run destroy in THAT dir. Rewrite the tests to deploy (mocked terraform) then destroy. |
| W2.4 Worker full pipeline | 1 (`test_obj03_worker.py`) | Depends on W3.1 (C1). Do after W3.1. |
| W2.5 Event loop lease reclaim | 1 (`test_obj04_event_loop.py`) | Implement lease acquisition (H15): create `BucketLeaseModel` rows on claim; reclaim expired ones in the reclaim phase. TDD with the existing xfailed test as the target. |
| W2.6 Port 8000 + runtime validator + bandit | 3 | (a) Convert remaining real-bind daemon e2e tests to the ephemeral-port helper already in `tests/e2e/conftest.py`; un-xfail the 8000-occupied proof. (b) Fix relative container path resolution in the runtime validator. (c) Add `bandit` to dev dependencies so `make sast` and its test run. |
| W2.7 Flaky six | 6 | hvac xdist races → `@pytest.mark.xdist_group(name="hvac")` on both tests (suite already uses `--dist loadgroup`), then make the entries strict or remove them once 5 consecutive green runs. TUI builders + StopIteration → find the nondeterminism (likely shared iterator/screen state); fix the test isolation. PTY daemon-start pair → keep as env-dependent `skipif` (no PTY) instead of flaky-xfail. |
| W2.8 Compute secrets resolver | 2 (`test_compute_launch_and_remote_slurm.py`) | Wire the secrets resolver from `app.state` into compute deploy (H17-adjacent); pass `None` cleanly when absent. |
| W2.9 Secrets `auto` mode (H16's sibling, §1.4 H17) | 0 ratchet entries but spine-relevant | `daemon.py:185-197`: `mode: auto` (the shipped default) must TRY OpenBao (bounded health check) before falling back to env vars, and log which path won. Add a startup read-back test: migrate a secret, delete the env var, resolution still returns it. |

Also fold into W2.3: persist a deployment registry keyed by `instance_id` (that fix) and expose it as `GET /api/deployments` (M2, currently no endpoint exists; `routers/compute.py:31-46` builds a fresh `DeploymentManager` per request).

**Phase W2 exit gate:** `config/ratchet.yml` ≤ 8 entries (the spine-dependent ones may remain until W3), `RATCHET_MAX` lowered to match, gate green.

---

## 5. Phase W3 — Product spine: make a todo produce a model-driven, reviewed, committed change

This is the actual product. SESSION.md ticks cover C0/C2/C3/C4/H5/H6 — the remaining breaks:

### W3.1 (C1) The worker must call the model
- Today `worker/app.py` execute writes `prompt_text`/`model_profile` into job vars and runs an ansible playbook; **no model is ever invoked**. Decide the call path explicitly (recommended: the worker builds a `ModelGateway` from its config and calls it when the job's work_type maps to a generation playbook; the playbook consumes the generated diff/answer from extravars).
- TDD: a worker test posting an execute job with a mocked gateway asserts the gateway was called with the job's prompt and that the response lands in the playbook extravars / job result. Then un-ratchet `test_obj03_worker.py` (W2.4).
- Commit: `W3.1: C1 — worker invokes ModelGateway for generation jobs`

### W3.2 (H4) Wire the real reviewer
- `review/reviewer.py::ReturnReviewer` and `review/decision_applier.py::apply_decision` have zero production callers. Instantiate ReturnReviewer in the daemon/loop review phase when a gateway exists; route its decision through `apply_decision`. On model failure the decision must be **escalate/hold, never a silent pass** (the old `str(prompt)` → "ignore_duplicate" path is the bug).
- TDD: review-phase test with mocked gateway: success → decision applied; failure → todo NOT marked complete.
- Commit: `W3.2: H4 — ReturnReviewer + apply_decision wired into review phase`

### W3.3 (M9) Stop blocking the event loop
- `event_loop/loop.py` calls `runner.run_playbook(...)` synchronously inside async phases (~L602): HTTP stalls during every playbook run. Wrap in `await asyncio.to_thread(...)`. Add a shutdown drain: on cancel, finish or abort the in-flight run cleanly.
- TDD: async test asserting the loop yields control during a slow (sleeping) fake runner.
- Commit: `W3.3: M9 — playbook runs via asyncio.to_thread + shutdown drain`

### W3.4 (N1/C6) Honest health
- Add `/readyz`: 503 when `app.state._degraded` is set or the event-loop task is done/cancelled; 200 otherwise. Keep `/healthz` as liveness. `make smoke` must hit `/readyz` too.
- TDD: degraded app → `/readyz` 503, `/healthz` 200.
- Commit: `W3.4: readiness endpoint reflects degraded state; smoke checks it`

### W3.5 (M8) Multi-worker honesty
- gunicorn `--workers N` spawns N event loops + N in-memory stores today. Minimum honest fix: default workers to 1 and **refuse** N>1 with SQLite (log + clamp), documenting the limit; full fix (cross-process claims via DB locking) only if Postgres support (H18) is actually pursued.
- H18 honesty in the same commit: `db/session.py:114-118` create_all and `daemon.py` stamp_head are SQLite-only, and `alembic.ini:3` hardcodes `sqlite:///./test.db` — Postgres does not work today. Either fix the alembic URL plumbing or state "SQLite only" in README/docs and make the daemon refuse a non-SQLite URL with a clear error. Do not leave it half-claimed.
- Commit: `W3.5: M8/H18 — single-worker clamp, sqlite-only stated and enforced`

### W3.6 (V2.2) Per-item proof table
- Append to `TASKS.md`: one row per G0–G7, S1–S20, F1–F7, M1–M15 with its named proof test (specs in `GLM_IMPLEMENTATION_GUIDE.md`). Run each via `make test-specific`; fix C→H→M. This is mechanical but long — it is the only way "every claimed item re-proven" stops being folklore. Pre-fill the §1.2/§1.4 adjudications from this guide.
- Commit per fixed group.

### W3.7 (H2) Self-improvement must persist its todos
- `daemon.py:395-419` never passes `self_improve_interval`; the loop's `_phase_self_improve` enqueues into a throwaway in-memory `SelfImprovementHarness` (`loop.py:~747`) — "todos_enqueued: N" reports todos that were discarded. Wire the interval from config and make `enqueue_todos` write through `TodoRepository`.
- TDD: with a session factory, self-improve phase → todos exist in the DB afterwards.
- Commit: `W3.7: H2 — self-improvement todos persisted via TodoRepository`

### W3.8 (H3) Worker stub endpoints — implement or 501 honestly
- `worker/app.py:101-111`: `/jobs/validate`, `/jobs/policy-validate`, `/jobs/reload-request` log and ack without doing anything. For each: either implement the behavior (validate → run the named validation playbook; reload-request → call the worker's actual reload path) or return HTTP 501 with a clear body so callers cannot mistake an ack for work. Pick per endpoint; no silent acks remain.
- Commit: `W3.8: H3 — worker endpoints real or explicit 501`

### W3.9 (H8) MCP wiring decision
- `daemon.py:140-154` loads `mcp_servers` config, then `daemon.py:403` passes `mcp_client=None` — config consumed by nothing, and `ModelGateway.call_model` has no tools parameter, so tools could not reach a model even if wired. This is a DESIGN task: either (a) wire `MCPClient` construction from config + add a tools pass-through to the gateway call path (large), or (b) mark MCP experimental: refuse `mcp_servers` config with a warning, document the gap. Decide once, in writing, in TASKS.md.
- Commit: `W3.9: H8 — MCP wired (a) or honestly fenced (b)`

### W3.10 (H12 + H1-residual) Router construction hygiene
- `routers/models.py` constructs `ModelGateway` without `metrics_collector` while `daemon.py:472` passes it — model calls made through the API are invisible to cost/metrics. Build the router's gateway from the same factory/app.state the daemon uses.
- Same file, same pattern: `/admin/observability/comparison` (`routers/models.py:143-148`) reads `app.state._session` which the lifespan never sets — switch it to the `_session_factory` pattern benchmark.py already uses.
- TDD: API model call → collector saw it; comparison endpoint returns rankings with a factory-backed test app.
- Commit: `W3.10: H12/H1-residual — one gateway construction path; comparison uses session factory`

### W3.11 (H13) Projects must actually have code — spine-critical
- `projects/manager.py:19-39` stores `repo_url`; **nothing in src/ ever clones it.** A dispatched job has no repository to edit — this nullifies the whole spine for remote projects. Implement workspace materialization: on project add/startup, clone (or verify) `repo_url` into `workspace_path` via the existing `git_automation/repo.py` (it already has real worktree/branch support — use it, don't shell out anew). Persist projects through `ProjectRepository` so restarts keep them.
- TDD: add project with a file:// fixture repo → workspace contains a checkout; restart (new manager from DB) → project still listed.
- Commit: `W3.11: H13 — project workspaces materialized from repo_url, persisted`

### W3.12 (H14) Delete hot-reload theater
- `reload/hot_reloader.py:103-109` returns `models_reloaded: True` after a bare existence check; `ReloadManager.execute_reload` reports success while doing nothing. Implement the model-routing reload for real (parse + swap the routing config the gateway reads) and make every other fake reload path return `{"reloaded": false, "reason": "not implemented"}`. No success reports for no-ops.
- Commit: `W3.12: H14 — reload reports only what actually reloaded`

### W3.13 (M11, re-scoped after correction) Prove CLI ↔ code-endpoint parity
- The `/admin/code/*` endpoints exist (`routers/models.py:150-182,260-318`) — the gap is that nothing proves the CLI's request/response shapes match them (the M5 failure class). Note the endpoints take `source` as a query/body param (raw code text), while a CLI user passes file paths — verify `_cmd_code_search`/`_cmd_code_graph` read files and send `source` correctly.
- TDD: test app + CLI functions end-to-end: `code search` over a fixture file returns results; `code graph` returns nodes/edges. Fix whichever side lies.
- Commit: `W3.13: M11 — CLI/code-endpoint parity proven by test`

### W3.14 (M14) One project per tick
- `event_loop/loop.py:349,489`: claim/review phases each call `select_project()` independently — one tick can claim from project A and review project B. Select once per tick, pass it to the phases.
- Commit: `W3.14: M14 — single project selection per tick`

**Phase W3 exit gate:** spine e2e proof (`tests/integration/test_full_pipeline_e2e.py` or equivalent): submitted todo → claim → worker executes with model call (mocked) → review decision applied → git commit created → reconciled. Ratchet near 0.

---

## 6. Phase W4 — Finish the OSS replacements honestly (V3 leftovers)

### W4.1 (V3.1, reverted tick) tenacity for real
- Port the retry semantics of `call_model_with_retry` (`gateway.py:256-327`) onto tenacity: `TimeoutRetryPolicy` decision logic becomes the `retry=` predicate / `wait=` strategy; fallback-profile walking stays app code around the tenacity-wrapped single-profile call. **Delete the hand-rolled loop and `call_with_tenacity` demo in the same commit** — one implementation lives.
- Existing retry tests must pass unchanged (they are the parity proof). 429-walks-fallback-chain test must stay green.
- Commit: `W4.1: tenacity is THE retry path; hand-rolled loop deleted`

### W4.2 (V3.2) MCP transport decision
- The two protocol bugs guide 2 cited are already fixed in the hand-rolled client (`transport.py:52` id-matching, `:98` initialized notification). Adopting the official `mcp` SDK is still preferred (reference implementation, free protocol updates), but it is now a judgement call, not a bug fix. Do ONE of: (a) swap to the SDK behind the existing transport interface, tests unchanged; or (b) write a 5-line KEEP rationale comment at the top of `transport.py` and tick V3.2 as "keep-as-is, bugs fixed" with that evidence. Do not leave it undecided.

### W4.3 (V3.3) watchdog for integrity scanning
- Keep HMAC/baseline logic; replace the `os.walk` change-detection loop in `integrity/scanner.py:100-110` with a watchdog observer (watchdog is already a dependency). TDD: touch a file in a tmp tree → change event detected without a full rescan.

### W4.4 (V3.4) pydantic-settings
- `config/loader.py` → `BaseSettings`-based `UserConfig` with YAML source + env-var override (`GLUDD_` prefix). TDD: env var overrides a YAML value.

### W4.5 (V3.5) deps truth
- Add `deptry` to dev deps so `make deps-audit` actually runs. Adjudicate: `fs` (apparently unused — remove), `tree-sitter`, `tree-sitter-python`, `huggingface-hub` (verify with the tool; remove or add a justifying comment in pyproject).

### W4.6 (V3.8) KEEP list
- Verify the in-code KEEP comments exist (`controllers/pid.py` honest rename e.g. `LoadThrottle`, `review/evidence_checker.py`, `prompts/registry.py`, `observability/recorder.py`). Add any missing.

---

## 7. Phase W5 — Ship blockers (publish gate)

### W5.1 (V4.1) The SSH key — still the #1 blocker
1. **Operator:** rotate/revoke the `sandboxcom/gludd` deploy key (treat as compromised) and run `docs/history-scrub.md` (filter-repo + force-push). The agent cannot do these.
2. **Agent, now:** `make untrack FILES='sandboxcom_github_rsa sandboxcom_github_rsa.pub'`; change `git-remote-sandboxcom`/`git-push-sandboxcom`/`git-pull-sandboxcom`/`git-fetch-sandboxcom` to `SSH_KEY ?= ~/.ssh/sandboxcom_github_rsa`; delete the in-repo `chmod 600`; remove the key exclusion from `scan-secrets-baseline` (N5); regenerate the baseline; add a guardrail test asserting no OpenSSH private-key header (the BEGIN/END armor lines) matches in tracked files.
3. Commit: `W5.1: key untracked, SSH_KEY external, secrets scan sees everything`

### W5.2 (V4.2/V4.3) Ship the license texts
- `make dist`: copy `LICENSE`, `THIRD_PARTY_LICENSES.md`, and `dist/sbom.json` (run `make sbom` as a dist dependency) into the tarball; add LICENSE to `gludd.spec` datas.
- CI (`build.yml`): every package step (linux/macos/windows/termux) copies `LICENSE` + `THIRD_PARTY_LICENSES.md` into `dist/release/` before archiving. Also place per-binary license files into `dist/binaries/` during `bundle-binaries`.
- Proof: a guardrail test that builds the tarball file list (or inspects the Makefile recipe) and asserts both files are included.

### W5.3 (V4.6) Final sweep
- `detect-secrets scan --all-files` **without** baseline (add a `make scan-secrets-fresh` target); adjudicate every hit; regenerate baseline.
- Grep the dist file list for `/Users/`, `Mac.localdomain`, opencode DB paths — must be clean.
- Dependency vulnerabilities: `make pip-audit` (run 2026-06-12) reports **diskcache 5.6.3 CVE-2025-69872** (pickle deserialization → arbitrary code execution for anyone with cache-dir write access) and **pip 26.1.1 PYSEC-2026-196** (fix: 26.1.2) — and the target ends `\|\| true`, so it gates nothing. Upgrade/replace or document why each is unexploitable here; then decide whether pip-audit should fail the security target on known-exploitable hits.

### W5.4 mypy 18 → 0
- Burn the 18 errors down; lower `MYPY_MAX` (W1.6) in the same commit as each fix group. Known clusters from guide 2: `otel_bridge.py` (observability extra / stubs), `cli.py` build_parser return type, six `no-any-return`s, `repo_map.py:47`.

### W5.5 README claims stay measured
- Any README number/claim ("0 noqa", test counts) gets a preflight grep check or gets deleted. One source of truth: the generated gate block (W1.4).

### W5.6 Worker endpoints need auth before shipping
- The daemon enforces auth (`daemon.py:674-689`); the worker does not (`worker/app.py:45-112` — `/jobs/execute` et al. accept anyone who can reach the port). Anyone on the network can make the worker run arbitrary registered playbooks. Apply the same PSK check (the `GLUDD_PSK` mechanism CI already sets) to all worker job endpoints; unauthenticated → 401.
- TDD: worker test client without the header → 401; with it → current behavior.
- Commit: `W5.6: worker job endpoints require PSK auth`

---

## 7b. Phase W6 — Agent-as-Playbook: the `general_ludd.agent` Ansible collection

**Goal:** a deployed agent IS an Ansible playbook. One role invocation performs a full task: git worktree → skill selection (with templated variables) → model call (routed) → tool/MCP use → quality gate → git commit → result artifact. Today none of this is callable from Ansible.

**Verified starting state (2026-06-12):**
- `roles/` and `collections/` are empty; **zero custom Ansible modules/action plugins/module_utils exist anywhere** (the repo drives ansible-core from Python via `ansible/core_runner.py`, never the reverse).
- `langchain>=0.3.0`, `langchain-openai>=0.3.0`, `langgraph>=0.2.0` are all declared (`pyproject.toml:28-29,34`) but **langgraph is imported nowhere in src/** — the actual tool-calling loop is hand-rolled (`execution/tool_loop.py:21-116`, `ToolCallLoop`, MAX_TOOL_ITERATIONS=10, gateway + MCP aware).
- Skill bodies flow raw: `event_loop/loop.py:575,594` resolves `skill_body` into playbook extravars; `execution/engine.py:48` injects it into the system prompt. **No Jinja2 templating of skill bodies exists** (`skills/loader.py` parses frontmatter only).
- Extravars reach plays via `core_runner.py:228,259` (CLIARGS + VariableManager); per-run env via `ansible/runner.py:117-129`.
- `_WORK_TYPE_PLAYBOOK_MAP` (`loop.py:103-127`) maps work types to playbooks; several targets are stubs writing canned JSON (`self_improve_harness.yml:18-29`), some are real (`validate_task.yml`, `git_automate_change.yml`).
- **No HTTP endpoint exists for model generation** — `routers/models.py` is admin-only (add/list/discover/health); an Ansible task has no way to ask for a completion today.

**Architecture rules for this phase:**
- Collection lives at `collections/ansible_collections/general_ludd/agent/` (galaxy.yml, `plugins/modules/`, `plugins/module_utils/`, `roles/`). `core_runner` sets the collections path so plays resolve `general_ludd.agent.*`.
- Modules run on `hosts: localhost` and import `general_ludd` directly through one `module_utils/gludd.py` shim (same venv — verified the runner executes in-process). Every module ALSO accepts `daemon_url` to operate over HTTP instead, so a play can run on a remote worker. One shim, two transports; never per-module ad-hoc imports.
- Modules return JSON facts (`register:`-able); they never print, never exit nonzero for domain-level "no result" (only for errors). Result artifact contract stays the existing `{"status": ...}` JSON the reconcile phase reads.

### W6.1 Collection skeleton + ping
- Create the collection skeleton; teach `core_runner.py` the collections path; add `general_ludd.agent.gludd_ping` (returns `{"pong": true, "daemon_reachable": <bool>}`).
- TDD: unit test runs a fixture playbook through `AnsibleRunnerAdapter` and asserts the module's fact. Add `make ansible-collection-test`.
- Commit: `W6.1: general_ludd.agent collection skeleton + ping module`

### W6.2 `gludd_model_call` module + generation endpoint
- New daemon endpoint `POST /admin/models/call` (prompt, model_profile OR task_type for adaptive routing, max_tokens) → gateway call (the W4.1 tenacity path) → `{text, model_profile_id, usage}`. This endpoint is required because none exists (verified). Auth: same PSK as other admin routes.
- Module params: `prompt`, `model_profile`, `route_task_type` (mutually exclusive with model_profile; uses AdaptiveRouter / `/admin/code/suggest-model` logic), `max_tokens`, `daemon_url`. Local transport calls the gateway directly via the shim.
- TDD: module test with mocked gateway → text returned; routed variant picks the recommended profile.
- Commit: `W6.2: gludd_model_call module + /admin/models/call endpoint`

### W6.3 `gludd_worktree` + `gludd_git` modules
- Thin wrappers over `git_automation/repo.py` (it already does worktree/branch/commit/push — verified real, the H6 wiring uses it). `gludd_worktree`: state=present/absent, repo_path, branch → returns `worktree_path`. `gludd_git`: op=commit/push/branch, path, message → returns sha. Modules are idempotent (`changed` only when something happened).
- TDD: fixture repo → worktree created, file edited, committed; second run reports `changed: false`.
- Commit: `W6.3: gludd_worktree + gludd_git modules over git_automation`

### W6.4 `gludd_db` module — todo/resource state from plays
- Ops: `todo_get`, `todo_update_status`, `resource_preference` (model/queue preference reads). HTTP transport ONLY (daemon API: `/api/todos` etc.) — plays must never open the SQLite file (single-writer rule, see W3.5). Add the missing read endpoints if a listed op has none, in the same commit.
- TDD: against the test app: play reads a seeded todo, flips status, reads it back.
- Commit: `W6.4: gludd_db module over daemon API`

### W6.5 `gludd_skill` module — selection + **templated variables**
- This is where "skills with templated variables become obvious." Add `render_skill(body, variables)` to `skills/` (Jinja2 `StrictUndefined` — an unknown variable is an error, not silent text). The module: `name` OR `trigger` (match via SkillCatalog), `variables` (dict) → returns `{skill_name, rendered_body, required_vars}`. Frontmatter may declare `required_vars`; missing ones fail the task with a clear message.
- Wire the SAME `render_skill` into `execution/engine.py:48` so prompt-injection and playbook paths render identically — one renderer, two consumers.
- TDD: skill fixture with `{{ project_name }}` renders; missing var → module fails with the var named; engine path renders identically.
- Commit: `W6.5: skill rendering with templated variables; gludd_skill module`

### W6.6 `gludd_mcp_tool` module
- `server`, `tool`, `arguments` → result. Blocked on the W3.9 decision: if W3.9 chose (a) wiring, the module calls through the daemon's MCP client; if (b) fenced, the module exists but fails with `not_implemented` and the W3.9 rationale — an honest placeholder, not a silent ack.
- Commit: `W6.6: gludd_mcp_tool module (per W3.9 decision)`

### W6.7 The `agent_task` role — full task from one playbook
- Role `general_ludd.agent.agent_task` with defaults: `repo_path`, `work_type`, `todo_id`, `model_profile`/`route_by_complexity`, `skill_name`, `skill_vars`, `quality_gate_cmd` (default `make test-count`). Task flow: `gludd_db todo_get` → `gludd_worktree present` → `gludd_skill` render → `gludd_agent_run` (W6.8) → quality gate `command` → `gludd_git commit` → `gludd_db todo_update_status` → write the standard JSON result artifact. `block/rescue/always` so the worktree is removed and the todo released on ANY failure.
- Convert one stub playbook end-to-end as proof: `self_improve_harness.yml` (today canned JSON, `:18-29`) becomes a 5-line invocation of the role. Then migrate the rest of `_WORK_TYPE_PLAYBOOK_MAP` targets one per commit.
- TDD: role run against a fixture repo + mocked gateway produces a real commit; failure inside the model step still cleans up the worktree.
- Commit: `W6.7: agent_task role; self_improve_harness is a role invocation`

### W6.8 `gludd_agent_run` — the agent loop, and the langgraph/langchain decision
- The role's brain: prompt (with rendered skill) + tools → iterate model/tool calls → final answer/diff. Two honest options, pick ONE:
  - **(a) langgraph (recommended — the deps are already declared and the user intent is langgraph/langchain-native roles):** build the loop on `langgraph.prebuilt.create_react_agent` with langchain tool wrappers around the gludd tools (git, db, mcp, code-intelligence endpoints); the existing `ToolCallLoop` (`execution/tool_loop.py:21-116`) is then DELETED in the same commit (one implementation lives — the V3.1 lesson).
  - **(b) keep `ToolCallLoop`:** then remove `langgraph` (and `langchain`/`langchain-openai` if `make deps-audit` (W4.5) confirms they are unused) from pyproject — declared-unused deps are the rule-8 failure mode.
- Either way the module's interface is identical: `prompt`, `tools` (list), `max_iterations`, returns `{answer, tool_calls, usage}`.
- TDD: mocked gateway scripted to request one tool call then answer — loop executes the tool and returns the final text.
- Commit: `W6.8: gludd_agent_run on langgraph (ToolCallLoop deleted)` or `W6.8: ToolCallLoop kept; langgraph/langchain removed per deps-audit`

### W6.9 Molecule proof + spine tie-in
- Molecule scenario `agent_task`: converge runs the role against a fixture repo with the gateway endpoint stubbed (local HTTP fixture); verify asserts the commit exists and the result artifact parses. Register in `make molecule-test SCENARIO=agent_task`.
- Spine note: **W6 is an implementation strategy for W3.1 (C1)** — the worker's "call the model" path can be exactly "run a playbook that uses these modules." If you reach W3.1 first, implement it minimally (direct gateway call) and migrate to the role here; do not build two parallel architectures silently — record the choice in TASKS.md.
- Commit: `W6.9: molecule agent_task scenario green`

**Phase W6 exit gate:** `make gate` green; molecule `agent_task` green; at least `self_improve_harness.yml` and one code-work playbook are role invocations; a todo dispatched through the event loop produces a model-driven commit using only Ansible-visible steps.

---

## 8. Definition of Done (every task)

1. Failing test first, now passing — named in the evidence line.
2. `make test-count` 0 errors; `make gate` ALL PASSED (5 lines).
3. Daemon-affecting work: `make smoke` green.
4. `TASKS.md` ticked with named-proof evidence — the W1.2 guard will reject anything less.
5. Committed via `make git-commit MSG='<ID>: ...'` (requires fresh green gate).
6. Hard do-nots: never raise `RATCHET_MAX`/`MYPY_MAX`; never delete a failing test; never weaken a guardrail to relieve friction; never commit a key or baseline-bypass; ZAI 429s are not code failures.

## 9. Checklist (mirror into TASKS.md as you go)

```
Phase W0 — truth repairs (done in validation pass 2026-06-12)
[x] W0.1  SESSION.md ratchet-count contradictions corrected (23)
[x] W0.2  TASKS.md V3.1 false tick reverted
[x] W0.3  this guide + CLAUDE.md pointer

Phase W1 — guardrail completion
[ ] W1.1  ratchet-growth guard test (RATCHET_MAX constant)
[ ] W1.2  TASKS.md tick guard in preflight + plugin
[ ] W1.3  STOP_SIGNAL_WORDS deleted; state-based checks only; fuzz test retargeted
[ ] W1.4  status-snapshot writes SESSION.md in place; preflight drift detector
[ ] W1.5  audit-evidence wired into validate, fail-closed
[ ] W1.6  Makefile: stderr capture order, MYPY_MAX var, OPENCODE_DB var, PHONY cleanup, gate enforces coverage
[ ] W1.7  H16: preflight fails closed on unknown criteria (rewrite ecaeedf's assumed_met tests)

Phase W2 — ratchet burn-down (23 → ~0)
[ ] W2.1  daemon lifespan real-DB tests (2)
[ ] W2.2  container/binary-resolver/image-scan group (5) + M15 random-sha fix
[ ] W2.3  deploy-before-destroy (C5 finished) (3)
[ ] W2.4  worker full pipeline (after W3.1) (1)
[ ] W2.5  lease acquisition + reclaim (H15) (1)
[ ] W2.6  ephemeral ports + 8000-occupied + runtime validator path + bandit dep (3)
[ ] W2.7  flaky six root-caused (xdist_group, test isolation, PTY skipif)
[ ] W2.8  compute deploy secrets resolver (2)
[ ] W2.9  H17: secrets auto mode tries OpenBao; migrated-secret read-back proven

Phase W3 — product spine
[ ] W3.1  C1: worker invokes ModelGateway
[ ] W3.2  H4: ReturnReviewer + apply_decision wired; failure ≠ silent pass
[ ] W3.3  M9: asyncio.to_thread around playbook runs + shutdown drain
[ ] W3.4  /readyz reflects degraded state; smoke checks it
[ ] W3.5  M8/H18: single-worker clamp; sqlite-only stated and enforced (or alembic fixed)
[ ] W3.6  V2.2: per-item proof table for G/S/F/M, failures fixed C→H→M
[ ] W3.7  H2: self-improvement todos persisted via TodoRepository
[ ] W3.8  H3: worker stub endpoints real or explicit 501
[ ] W3.9  H8: MCP wired or honestly fenced (decision in TASKS.md)
[ ] W3.10 H12/H1-residual: router gateway gets metrics_collector; comparison endpoint uses session factory
[ ] W3.11 H13: project workspaces cloned from repo_url + persisted (spine-critical)
[ ] W3.12 H14: hot-reload reports only real reloads
[ ] W3.13 M11 re-scoped: CLI ↔ /admin/code/* parity proven by test (endpoints exist — corrected)
[ ] W3.14 M14: one select_project() per tick

Phase W4 — OSS replacements finished honestly
[ ] W4.1  tenacity IS the retry path; hand-rolled loop + demo deleted
[ ] W4.2  MCP: SDK swap OR documented keep (bugs already fixed)
[ ] W4.3  watchdog drives integrity change detection
[ ] W4.4  pydantic-settings config loading (env overrides)
[ ] W4.5  deptry installed; fs/tree-sitter/hf-hub adjudicated
[ ] W4.6  KEEP comments verified; pid.py renamed honestly

Phase W5 — ship
[ ] W5.1  key untracked + SSH_KEY external + scan exclusion removed (+ operator rotate/scrub)
[ ] W5.2  LICENSE + THIRD_PARTY_LICENSES + sbom packed into ALL artifacts (Makefile + CI)
[ ] W5.3  fresh no-baseline secrets scan adjudicated; dist path-clean
[ ] W5.4  mypy 18 → 0, MYPY_MAX lowered stepwise
[ ] W5.5  README claims measured (preflight) or removed
[ ] W5.6  worker job endpoints require PSK auth

Phase W6 — agent-as-playbook (general_ludd.agent collection)
[ ] W6.1  collection skeleton + gludd_ping + collections path in core_runner
[ ] W6.2  gludd_model_call module + POST /admin/models/call endpoint
[ ] W6.3  gludd_worktree + gludd_git modules over git_automation
[ ] W6.4  gludd_db module over daemon API (never raw sqlite)
[ ] W6.5  skill rendering with templated variables (StrictUndefined); gludd_skill module; engine uses same renderer
[ ] W6.6  gludd_mcp_tool module (honest not_implemented if W3.9 fenced)
[ ] W6.7  agent_task role; self_improve_harness.yml converted; map targets migrated
[ ] W6.8  gludd_agent_run: langgraph ReAct agent (ToolCallLoop deleted) OR ToolCallLoop kept + langgraph/langchain removed
[ ] W6.9  molecule agent_task scenario green; W3.1 strategy recorded in TASKS.md
```

Start at W1.1. Prove every step with a `make` target you ran this session.
