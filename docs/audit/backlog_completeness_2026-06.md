# Backlog Completeness Audit — 2026-06 (#66)

**Scope:** evidence-based re-adjudication of the on-disk backlog (`TASKS.md` +
`GLM_REMEDIATION_GUIDE*.md`) against the actual code at HEAD `ed294c4`, the
working-tree connectors, and the security fixes. Every verdict is grounded in a
`file:line` or commit. The orchestrator task list (#12–#82) is mapped where it
diverges from `TASKS.md`.

**Method note (constraints honored):** doc-only, no commit, no sub-agents. A
`make gate` was already running (`.gate-status` shows `test` with no result
yet), so I avoided pytest except one isolated `make test-specific` on a
separate `--basetemp` to confirm W4.1; I then stopped invoking pytest to avoid
the tmp-rotation collision the memory warns about. Verdicts otherwise rest on
Read of source + `make git-log` / `make playbook-list` / `make git-diff`.

Legend: **GROUNDED** (claim proven by cited evidence) · **PARTIAL** (real but
incomplete / not wired) · **UNSUBSTANTIATED** (claim with no evidence or
contradicted by code).

---

## 0. Premise corrections (the audit brief's own framing is partly wrong)

These are stated up front because several downstream verdicts depend on them.

| Brief claim | Reality | Evidence |
|---|---|---|
| "16 connectors committed in HEAD + 16 uncommitted in the working tree" | **All 38 connector modules are UNTRACKED** (`??`). Zero are committed. The committed tree has only `connectors/__init__.py` + `connectors/base.py` (the contract). | `make git-status`: every `src/general_ludd/connectors/<name>.py` and matching `tests/unit/test_connector_*.py` is listed `??`. 38 connector impls + 38 test files + `ingest_formats.py` + `normalize.py` + `infra/model_deploy_check.py`. |
| "the security fixes in commit ed294c4" | ed294c4's message says "Security hardening + observability connector layer…", but the connector layer is **untracked** (not in that commit), and the core PSK security fixes (A-1/A-2/A-3, S-1, F-1) actually landed in **81fcc33**, not ed294c4. ed294c4's only tracked change vs working tree is `Makefile`. | `make git-log` (81fcc33 message lists A-1..F-1); `make git-diff` shows only `Makefile` modified in the working tree; connectors are `??`. |
| "The orchestrator's task list (#12–#82)" | `TASKS.md` does **not** track #12–#82 by number. The `#NN` ids appear only in recent **commit messages** (e.g. `#42 saturation controller`, `#29 feature-db dogfood`, `#44 capability_policy`, `#27 event-loop-wiring`, `#49 spend`). `TASKS.md` tracks the V/R/W series. The two numbering schemes are disjoint and unreconciled. | `make git-log` commits 8afe2bd, 9d487ab, 5ee5b36 carry the `#NN` ids; `TASKS.md` carries V/R/W ids only. |

**Implication:** the headline deliverable of the most recent work ("observability
connector layer") is **not committed and not wired** — see Gap G1 below, the
single highest-value finding.

---

## 1. Highest-value gaps (prioritized — close these first)

### G1 — UNSUBSTANTIATED as "delivered": the 38-connector observability layer is dead code (no production call path)
The connector layer is genuinely well-engineered (self-contained modules, SSRF
guards, injected transports, `health()` never raises, thorough mocked tests),
**but nothing in production constructs a `SourceRegistry`/`Observability` or
registers any connector.**
- `daemon.py` imports `observability.dashboard_data`, `observability.otel_bridge`,
  `observability.recorder`, `observability.trace_store`
  (`daemon.py:46-48`, `:763`) — it never imports `general_ludd.connectors`.
- No connector router exists: the router include list is explicit and
  connector-free (`daemon.py:1048-1073`: accounting, ansible, benchmark,
  compute, facts, features, filestore, integrity, maintenance, mcp, messages,
  models, projects, quantization, reload, schedule, self_improve, signing,
  skills, slurm, spend, todos, webmcp, worktree, dispatch).
- No config loader reads connector/source config: `load_startup_config`
  (`daemon.py:72-160+`) loads model_routing, user_config, binary_paths, openbao,
  isolation, mcp_servers, task_definitions, model_profiles — never connectors.
- `/api/facts` (the one telemetry-facing API) reuses repos/collectors and does
  **not** surface connector data (`routers/facts.py:1-45` docstring + imports —
  work/todos/models/history/messages/metrics/traces only).
- The package docstring itself says connectors "register themselves into a
  `SourceRegistry` at runtime" (`connectors/__init__.py:11`) — but the runtime
  that would do so does not exist.

**Verdict:** UNSUBSTANTIATED as a shipped capability. The code is real and
tested; the *feature* (operator can query external telemetry through gludd) is
not reachable. This is exactly the "feature isn't wired into the daemon" failure
class the brief asked to hunt. **Highest priority:** either (a) add a
`connectors` config section + a `routers/connectors.py` that builds an
`Observability` facade on `app.state` and exposes `find()/associate()`, with an
e2e proof that a configured source is queryable through the daemon; or (b)
explicitly fence the layer as experimental in `TASKS.md` with a decision note
(the W3.9/W6.8 precedent) rather than leaving it as silently-dead committed-soon
code. Until one of those lands, do **not** tick any "connector layer delivered"
item.

### G2 — Working tree is dirty with non-connector artifacts that look like test pollution
`make git-status` shows stray top-level dirs and a plugin file that are not part
of any backlog item: `nested/`, `proj-ok/`, `.opencode/plugin/enforce-floor.ts`,
and `.claude/`. `nested/` and `proj-ok/` are almost certainly leftover fixtures
from a guardrail/preflight test that writes scratch dirs to CWD (a known smell
— the make-only policy + worktree CWD reset encourage tmp-in-repo). **Action:**
confirm they are test scratch and gitignore or clean them before any commit;
they must never be committed. (The brief says leave the audit uncommitted —
this is a flag for the next working session, not for this audit.)

### G3 — `RATCHET_MAX` ledger vs reality: verify the constant tracks the live count
`config/ratchet.yml` now has **~11 entries** (1 sast + 1 port-8000 + 3 watchdog
FSEvents + 4 TUI/PTY + 2 hvac xdist). Guide-3 measured 23 at `65fc28b`; the
burn-down is real. But W2.x ticks claim specific lowerings of `RATCHET_MAX`
(e.g. "23→…→16→14"). **Action for next session:** confirm `RATCHET_MAX` in
`tests/unit/test_guardrails.py` equals the live count (≈11) — a stale-high
constant would silently re-admit growth, defeating W1.1. (Not re-run here to
avoid colliding with the running gate.)

---

## 2. Per-item verdicts — claimed-done items re-adjudicated

Only items where the evidence is load-bearing or the verdict is non-trivial are
listed. Items not listed were spot-checked as consistent with their evidence
lines.

### Guardrails (W1 / R1) — GROUNDED
| Item | Verdict | Evidence |
|---|---|---|
| W1.7 preflight fail-closed on unknown criteria | GROUNDED | The H16 fix reverses the `assumed_met` bug the guide flagged; recent commit `e865e31` message confirms "preflight fails-closed on unknown criteria". Cross-check `quality/preflight.py` unknown-criterion path returns `met=False`. |
| W1.6 MYPY_MAX single-var + stderr capture | GROUNDED | `e865e31` ("MYPY_MAX var, stderr capture fix"); `.gate-status` shows `typecheck PASS 0` (the var was burned to 0, consistent with W5.4). |
| W5.4 mypy 18→0 | GROUNDED | `.gate-status:3` `typecheck PASS 0`. The MYPY_MAX threshold is met at 0. |
| A-1/A-2/A-3 PSK security | GROUNDED | `daemon.py:856-945`: `hmac.compare_digest` (A-1, `:934`); PSK never logged, only `psk_configured` boolean (A-2, `:922-929`); `GLUDD_REQUIRE_AUTH` opt-in fail-closed 503 + loud no-auth warning + `/healthz` `auth_degraded` field (A-3, `:864-976`). Attributed to **81fcc33**, not ed294c4. |

### Tenacity (V3.1 → W4.1) — was a FALSE TICK, now GROUNDED
| Item | Verdict | Evidence |
|---|---|---|
| W4.1 tenacity is THE retry path; demo deleted | GROUNDED | `make test-specific tests/unit/test_w4_1_tenacity_retry.py` → **5 passed**, including `test_call_with_tenacity_demo_deleted` and `test_retry_then_succeed_uses_tenacity_internally`. This is the item Guide-3 §1.2 marked `‼ FALSE TICK`; it is now genuinely closed. (Run on isolated basetemp.) |
| V3.1 (original) | superseded | Correctly REJECTED in `TASKS.md:60` with adjudication note pointing to W4.1; the ledger handled this honestly. |

### Product spine (W3) — mostly GROUNDED, one residual
| Item | Verdict | Evidence |
|---|---|---|
| W3.4 /readyz reflects degraded | GROUNDED | `daemon.py:978-1002`: 503 on `_degraded` or done/cancelled event-loop task; 200 otherwise. Matches the claim exactly. |
| W3.1 C1 worker invokes gateway | GROUNDED (by design decision) | `TASKS.md:67-71` records the direct-ModelGateway-from-worker decision + e2e proof `test_obj03_worker.py::TestWorkerModelGatewayCall`. The gateway-backed executor is also wired in the daemon (`daemon.py:657-688`). Consistent. |
| W3.9 MCP fenced (DEFER) | GROUNDED | `daemon.py` passes `mcp_client=None` historically; the decision note (`TASKS.md:324-342`) is explicit and honest (no silent success). MCP loader is imported (`daemon.py:42,140-149`) but config→client wiring is deferred by written decision, not omission. |
| H17 secrets auto mode | GROUNDED | `TASKS.md:80` W2.9 cites `test_secrets_auto_mode.py` 4 passed with a real read-back test; consistent with the brief's spine concern. |

### W9 completion_audit "100%, 29 classes wired" — PARTIAL (verify the wiring is behavioral, not reference-only)
| Item | Verdict | Evidence |
|---|---|---|
| W9.1 completion_audit 83%→100% | PARTIAL | The per-class table (`TASKS.md:287-318`) maps every class to a call path, and `test_completion_audit_wiring.py` exists. **But** the audit only checks *import/reference* wiring, not behavior — the agent itself flagged this for `AnsibleTemplater` and added a *behavioral* test only for that one class (`TASKS.md:312`, W9.1-AnsibleTemplater). The other 28 "wired" classes rest on reference-wiring proofs. Several (e.g. `LangGraphGateway`, `PromptScoringEngine` wired via `AgentCapabilities.make_graph_gateway`) are plausibly reachable only from a capabilities helper that may itself have no production caller — **the same dead-path-one-level-up risk** the connector layer exhibits. **Action:** spot-check that `AgentCapabilities.make_graph_gateway` / `make_tool_loop` / `failover` are invoked on a real request path, not only by the audit-wiring test. Until then this is GROUNDED-as-imported, PARTIAL-as-behavior. |

### W10–W15 molecule scenarios + Ansible roles — GROUNDED structurally, UNVERIFIED-in-CI
| Item | Verdict | Evidence |
|---|---|---|
| W10–W15 (49 molecule scenarios, 12+ roles, secure-SDLC, agile roles) | GROUNDED (local) | Each W1x item cites `make molecule-test-all "ALL scenarios passed"` + a fresh gate. `make playbook-list` confirms 31 playbooks on disk including `agent_coordination_demo.yml`, `system_report.yml`. Structural reality is solid. |
| W16.1 CI Event-loop-is-closed fix | UNSUBSTANTIATED-in-CI (honest) | `TASKS.md:450` *itself* states: "the 'Event loop is closed' failure could NOT be reproduced locally … CI-green is therefore UNVERIFIED-in-CI at commit time; must be confirmed by the next sandboxcom run." This is an honest non-tick of CI greenness. The later commit `308793c` ("revert 2 molecule scenarios that FAIL … in CI") confirms CI does surface failures the local gate misses — so the "all green" posture is local-only. **Treat any 'CI green' claim as UNSUBSTANTIATED until a sandboxcom run id is pasted.** This matches the user's standing rule: no "green" without the measurement. |

### W5.1 SSH key "SATISFIED, not a ship-blocker" — GROUNDED but design-dependent
| Item | Verdict | Evidence |
|---|---|---|
| W5.1 key present-but-gitignored | GROUNDED | `TASKS.md:127-152` documents the reversal of Guide-3's "PUBLISH BLOCKER" verdict with two enforcement layers (detect-secrets hook + `TestNoTrackedPrivateKeys`) and `make git-tracked-keys "NONE TRACKED"`. The verdict flip (history was always clean) is internally consistent and test-backed. Residual operator action (key rotation) is correctly marked out-of-agent-scope. |

---

## 3. Connector layer — spot-check depth (to ground G1)

Read in full: `connectors/__init__.py`, `connectors/base.py`, `connectors/okta.py`,
`tests/unit/test_connector_okta.py`.
- **Quality is high.** `base.py` is a clean Protocol + registry + resilient
  fan-out facade (`Observability.find` captures a failing source as an error
  record rather than aborting, `base.py:214-231`). `okta.py` reads its token only
  from an env var named in config (`okta.py:171-177`), SSRF-guards `org_url`
  with a no-DNS literal-host check (`okta.py:160-169`), bounds pagination, and
  `health()` never raises (`okta.py:229-244`). Tests inject a fake transport —
  no network (`test_connector_okta.py:1-12,33-45`).
- **But it is unreachable.** Confirmed connector-free in: daemon imports
  (`daemon.py:19-61`), config loader (`daemon.py:72-160`), router includes
  (`daemon.py:1048-1073`), facts API (`routers/facts.py:1-45`).
- **Net:** ~38 modules + ~38 test files of green-but-orphaned code. They inflate
  the test count and the "delivered" surface without delivering an operator
  capability. This is the single largest GROUNDED-false-as-feature finding.

---

## 4. Prioritized close-next list

1. **G1 — wire or fence the connector layer.** Add a `connectors:` config
   section + `routers/connectors.py` (build `Observability` on `app.state`,
   expose `find/associate`, register configured `Source`s) **with an e2e proof
   that a configured connector is queryable through the daemon** — OR write an
   explicit "experimental, unwired" decision in `TASKS.md` (W3.9 precedent).
   Do not commit the 38 modules as "delivered" without one of these.
2. **W9 behavioral verification.** Prove `AgentCapabilities.make_graph_gateway`
   / `make_tool_loop` / `failover` (and the other reference-only "wired"
   classes) are invoked on a real request path, not only by
   `test_completion_audit_wiring.py`. Add behavioral tests like the one already
   added for `AnsibleTemplater`. Risk: the connector dead-path pattern repeated
   one level up.
3. **CI greenness for W16/W10.** Paste a real sandboxcom run id with a green
   gate+molecule job before claiming CI green. Until then W16.1's own honesty
   note stands: UNVERIFIED-in-CI.
4. **G3 — confirm `RATCHET_MAX` == live count (~11)** so W1.1's growth guard is
   not silently slack.
5. **G2 — clean `nested/`, `proj-ok/`, `enforce-floor.ts`** scratch
   from the working tree (gitignore or remove); never let them be committed.
6. **Reconcile the two id schemes.** The `#12–#82` commit-message ids and the
   V/R/W `TASKS.md` ids are disjoint. A single mapping table (or dropping one
   scheme) would stop the "which backlog is authoritative" ambiguity that this
   audit had to untangle.

---

## 5. Summary scorecard

| Bucket | Count | Notes |
|---|---|---|
| GROUNDED | guardrails (W1.6/W1.7/W5.4), security A-1..A-3, tenacity W4.1, W3.1/W3.4/W3.9, W5.1, W10–W15 (local) | Evidence cited per row above. |
| PARTIAL | W9.1 (reference-wired, behavior unproven for 28/29 classes) | Dead-path-one-level-up risk. |
| UNSUBSTANTIATED | **connector layer "delivered"** (G1, untracked + unwired); "CI green" for W16/W10 (UNVERIFIED-in-CI by the ledger's own admission) | The two biggest gaps. |
| BRIEF PREMISE FALSE | "16+16 connectors", "security in ed294c4", "#12–#82 is the on-disk backlog" | Corrected in §0. |

**Bottom line:** the V/R/W backlog is unusually honest for its size (false ticks
like V3.1 were caught and reverted; CI greenness is explicitly disclaimed). The
real completeness gap is **not** in the ticked items — it is the most recent,
*not-yet-ticked* work sitting untracked in the working tree: a large, high-quality,
**completely unwired** connector/observability layer that no production path can
reach. Wire it or fence it before it gets ticked as "delivered".
