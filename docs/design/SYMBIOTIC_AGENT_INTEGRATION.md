# Symbiotic Integration of Self-Improving Coding Agents with Gludd

**Status:** Design proposal (read-only research deliverable)
**Date:** 2026-06-29
**Author:** Agent research session

---

## 1. Executive summary

**Recommendation: integrate DeepReinforce's Ornith-1.0 (MIT-licensed, self-improving agentic-coding
LLM family: 9B / 35B / 397B) into gludd as a peer agent + self-improvement substrate.**

Ornith's defining feature — a closed RL loop that **jointly optimizes the agent's own scaffold (the
tool-orchestration / prompt code) and the resulting solution rollouts** — is exactly the capability
gludd cannot grow on its own: gludd's playbooks, ansible roles, and agent prompts are currently
hand-authored. Conversely, gludd possesses exactly what Ornith lacks: an OS-level permission system
(`PermissionSpec` + `path_prefix` / `allowed_hosts`), six sandbox backends (Landlock, bubblewrap,
AppArmor, SELinux, macOS Seatbelt, FreeBSD Jail, Windows AppContainer), an STS + audit-log capability
system, and a daemon-managed worktree dispatcher. The two systems are complementary at the architectural
seam: **Ornith emits scaffolds + solutions; gludd executes them inside a permission-scoped sandbox and
feeds outcomes back into Ornith's training loop.** This doc specifies that seam, the failure modes, and
a three-phase rollout.

---

## 2. Candidate matrix

| Agent | License | Architecture | Self-improvement | Output | Tool surface | State | Sandbox | Reasoning | Source |
|---|---|---|---|---|---|---|---|---|---|
| **Ornith-1.0** (9B/35B/397B) | **MIT** | model-agnostic, OpenAI-compatible API; dense + MoE | **Yes (RL on scaffold+solution)** | tool_calls, reasoning blocks, code | OpenAI ChatCompletions, function-calling | ephemeral (stateless) | none — consumer's job | scaffold-then-act (plans via `<think>`) | https://huggingface.co/deepreinforce-ai/Ornith-1.0-9B |
| **SWE-agent / mini-SWE-agent** (Princeton) | MIT | single-agent, ACI (custom shell tool set); SoTA on SWE-bench among OSS | No (fixed config YAML) | patches, diff files | Python CLI, custom ACI | per-task | Docker container | reactive | https://github.com/SWE-agent/SWE-agent |
| **OpenHands** (ex-OpenDevin) | MIT (sandbox) + mixed | multi-backend agent server (local/Docker/VM/cloud); ACP-compatible | No (skill plugins are hand-written) | PRs, automations | REST API, ACP, MCP | persistent (server) | Docker sandbox (recommended) | plan + act | https://github.com/OpenHands/OpenHands |
| **Aider** | Apache-2.0 | pair-programming CLI, repo-map, edit formats | No (no scaffold evolution) | inline edits + git commits | CLI; LLM-only | per-session (git history) | none | reactive | https://github.com/Aider-AI/aider |
| **Continue.dev** | Apache-2.0 | VSCode / JetBrains / CLI | No | inline edits, chat | MCP, custom | ephemeral | none | reactive | https://github.com/continuedev/continue (**archived/EOL**) |
| **Devin** (Cognition) | proprietary SaaS | black-box, RL-trained | unknown (claimed) | SaaS only | SaaS API | server-side | Cognition-owned | plan-ahead | https://devin.ai/ |
| **Sweep.dev** | proprietary (SOC2) | JetBrains plugin + hosted agent | No | inline edits | JetBrains plugin | server-side | Sweep cloud | reactive | https://www.sweep.dev/ |
| **AutoCodeRover** | MIT (research) | AST-aware code search + patch | No | patch file | Python CLI | per-task | Docker | two-phase (search→repair) | https://github.com/AutoCodeRoverSG/auto-code-rover |
| **Agentless** | MIT | three-phase (localize→repair→validate), **no agent loop** | No (no scaffold) | patch file | Python CLI | per-task | none | linear | https://github.com/OpenAutoCoder/Agentless |
| **CodeAct** | MIT (paper + model) | LLM-as-code-action paradigm; CodeActInstruct dataset | No (training data is fixed) | code actions | Python interpreter + Docker | per-session | per-session Docker | code-as-plan | https://github.com/xingyaoww/code-act |

**Key filter results:**
- *Permissive license + stable interface + actual self-improvement loop* ⇒ only **Ornith** qualifies.
- *Permissive license + stable interface, no self-improvement but excellent scaffolding for code-gen* ⇒ SWE-agent, AutoCodeRover, Aider, OpenHands.
- *Proprietary / SaaS-only* ⇒ Devin, Sweep — disqualified for any symbiotic integration (no local control plane).
- *Archived / EOL* ⇒ Continue.dev — disqualified.

---

## 3. Ornith — detailed profile (the chosen candidate)

Source: https://huggingface.co/deepreinforce-ai/Ornith-1.0-9B (and the 35B / 397B siblings).

### 3.1 Architecture
- Post-trained on top of Gemma 4 (12B / 31B) and Qwen 3.5 (9B / 35B-MoE / 397B-MoE).
- Reasoning model: every assistant turn opens with `<think> ... </think>` then the answer.
- OpenAI-compatible Chat Completions endpoint. Serves via vLLM ≥ 0.19.1, SGLang ≥ 0.5.9, or llama.cpp
  (GGUF quants). Single 80 GB GPU suffices for the 9B; the 35B-MoE fits one H100; the 397B-MoE needs
  8×H100 or the FP8 build.
- Tool calls emitted as OpenAI-style `tool_calls` (server parses `<tool_call>` blocks via
  `--tool-call-parser qwen3_xml`).

### 3.2 Self-improvement mechanism (the differentiator)
> "Ornith-1.0 employs RL to learn to generate not only solution rollouts, but also the **scaffold**
> that drives those rollouts. By jointly optimizing the scaffold and the resulting solution, the
> model discovers better search trajectories and generates higher-quality solutions."
> — Ornith-1.0 model card

This is a **closed loop**: the model writes its own prompt-orchestration code, runs it, observes the
result, and is rewarded for both solution quality AND scaffold quality. A scaffold is, structurally,
exactly what an ansible role or an opencode plugin is — a piece of code that orchestrates tool calls.
This is the property that makes Ornith uniquely symbiotic with gludd: **the artifacts Ornith produces
when it "improves" are artifacts gludd already understands.**

### 3.3 Benchmark evidence (Ornith-1.0-9B vs. baselines, from the model card)

| Benchmark | Ornith-9B | Qwen3.5-9B | Qwen3.5-35B |
|---|---|---|---|
| SWE-bench Verified | **69.4** | 53.2 | 70.0 |
| SWE-bench Pro | **42.9** | 31.3 | 44.6 |
| Terminal-Bench 2.1 (Terminus-2) | **43.1** | 21.3 | 41.4 |
| NL2Repo | **27.2** | 16.2 | 20.5 |

### 3.4 License & accessibility
- **MIT**, "globally accessible, free from regional limitations." No copyleft conflict with gludd's
  stack, no enterprise-tier gating.
- Weights downloadable from Hugging Face. Inference is consumer-side.

### 3.5 Limitations relevant to gludd
- **No built-in sandbox.** Ornith emits code that the consumer must execute. This is *the* integration
  point with gludd's sandbox stack.
- **No permission system.** Ornith will happily call `rm -rf /` if a scaffold tells it to. Gludd's
  `PermissionSpec` is *required*.
- **No persistent memory across sessions.** Scaffolds are per-rollout. Gludd's daemon can persist
  successful scaffolds as canonical ansible roles (the bidirectional loop).
- **No audit trail.** Every Ornith call must be wrapped so the audit log captures `{tool, args,
  PermissionSpec, STS, result}`.

---

## 4. Symbiotic coexistence analysis

For the candidates that passed the license + stable-API filter, the coexistence modes:

### 4.1 Ornith × gludd (the recommended pair) — all five modes apply

| Mode | Ornith's role | Gludd's role |
|---|---|---|
| **(a) Tool gludd dispatches** | Solves a single sub-task (write a function, fix a bug) when gludd's ansible/worker stack would be heavier | Dispatches Ornith via OpenAI-compatible provider, scoped under a `path_prefix=/worktree/<task>/`, `allowed_hosts={localhost:8000}` |
| **(b) Higher-level orchestrator** | Owns end-to-end coding tasks (issue → PR) | Provides sandboxing, secrets, `HumanTodo` escalation, permission intersection |
| **(c) Peer agent** | Code-gen & refactoring | Ops, ansible, security review, CI gating |
| **(d) Self-improvement substrate** | Optimizes gludd's prompts/playbooks based on observed outcomes | Provides the eval harness (gate green/red, SWE-bench-style held-out set) and the safety boundary |
| **(e) Bidirectional** | Generates new ansible roles / OPA policies / opencode plugins as "discovered scaffolds" | Wraps execution in `bubblewrap`/`Landlock`, audits every call, persists validated scaffolds into `.gludd/collections/` (the project collection precedence system) |

### 4.2 Other candidates (secondary tier)

- **SWE-agent × gludd** — viable as a *tool* only (mode a). No self-improvement ⇒ no bidirectional
  benefit. Useful as a baseline to *measure* Ornith's improvement contribution against.
- **OpenHands × gludd** — could share the daemon's permission system (it has none of its own beyond
  Docker). But OpenHands's ACP/MCP plugin model overlaps with gludd's MCP server; the integration
  would be a *competitor merge*, not symbiotic.
- **Aider × gludd** — pair-programming surface for a single human dev. No daemon integration story.
- **AutoCodeRover / Agentless / CodeAct** — research artifacts; useful as one-shot *tools* but no
  recurring loop. Worth keeping a thin CLI shim for ablation studies.

---

## 5. Recommended integration

### 5.1 Architecture diagram (ASCII)

```
┌──────────────────────────────────────────────────────────────────────────┐
│                              gludd daemon                                │
│                                                                          │
│   ┌────────────┐    dispatch     ┌────────────────────────────────────┐  │
│   │ EventLoop  │ ──────────────► │   OrnithAdapter (new module)       │  │
│   │  (loop.py) │                 │   - OpenAI-compatible client       │  │
│   │            │                 │   - scaffold capture               │  │
│   │            │                 │   - rollout outcome → feedback     │  │
│   └────────────┘                 └─────────────┬──────────────────────┘  │
│        ▲                                        │                        │
│        │ PermissionSpec intersection            │ vLLM/SGLang/llama.cpp  │
│        │ (human ∩ agent ∩ requested)            │ (local inference)      │
│        ▼                                        ▼                        │
│   ┌────────────┐    enforce      ┌────────────────────────────────────┐  │
│   │Permission/ │ ──────────────► │  SandboxBackend (existing)         │  │
│   │ STS / audit│                 │  Landlock | bubblewrap | AppArmor  │  │
│   │  log       │                 │  Seatbelt | Jail | AppContainer    │  │
│   └────────────┘                 └─────────────┬──────────────────────┘  │
│        ▲                                        │ exec Ornith scaffold   │
│        │                                        ▼                        │
│        │                              ┌────────────────────┐             │
│        └──────────────────────────────│  Feedback store    │             │
│                  outcome (gate/PR)    │  (Postgres + S3)   │             │
│                                       └────────────────────┘             │
│                                                  │                       │
└──────────────────────────────────────────────────┼───────────────────────┘
                                                   │ curated scaffolds
                                                   ▼
                                       ┌────────────────────────────┐
                                       │ .gludd/collections/        │
                                       │   general_ludd/agent/      │
                                       │     roles/<scaffold_name>/ │
                                       │   (project tier shadows    │
                                       │    bundled)                │
                                       └────────────────────────────┘
                                                   │
                                                   ▼  (offline, batched)
                                       ┌────────────────────────────┐
                                       │  Ornith RL trainer         │
                                       │  (DeepReinforce side)      │
                                       └────────────────────────────┘
```

### 5.2 Interface boundary

**MCP server** is the right boundary (gludd already speaks MCP for opencode plugins). Sketch — §7.

Internally, the daemon also exposes Ornith as an OpenAI-compatible provider so any existing
agent/worker can route to it via `LLM_MODEL="ornith/Ornith-1.0-9B"`. No code change in the worker.

### 5.3 Permission scope

Every Ornith call gets a `PermissionSpec` derived by intersection (per AGENTS.md "Human Permission
Subjects + Intersection Policy"):

```
effective = intersection(human_spec, gludd_agent_spec, ornith_requested_spec)
```

Default `ornith_requested_spec` for a code-gen sub-task:

```yaml
capabilities:
  - scheme: file
    constraints: { path_prefix: "/worktree/<task_id>/" }
  - scheme: net
    constraints:
      allowed_hosts: ["localhost", "127.0.0.1"]
      allowed_ports: [8000, 3000]
  - scheme: exec              # new scheme — see open questions
    constraints: { binaries: ["python3", "git", "make"] }
```

For code-gen the intersection narrows `path_prefix` to the task worktree; `allowed_hosts` is closed
to the local inference server (no exfiltration). Anything Ornith requests *outside* the intersection
must go through the existing escalation flow (`POST /admin/perm/escalation-request`, requires ≥3
`alternatives_tried`).

### 5.4 Sandbox backend selection

Map to the existing `detect_best_backend()` (`src/general_ludd/security/sandboxes/detect.py`):

| Host platform | Backend wrapping Ornith's code emission | Why |
|---|---|---|
| Linux ≥ 6.7, pylandlock importable | **Landlock** (preferred) | In-process, finest filesystem ACL |
| Linux, `bwrap` present | **bubblewrap** + seccomp advisory | Namespace isolation; pairs with nftables for hostname ACL |
| Linux w/ AppArmor | **AppArmor** profile `gludd-ornith` | Path + net restrictions |
| macOS | **Seatbelt** (`sandbox-exec`) | Already supported in `macos_seatbelt.py` |
| FreeBSD | **jail** | `freebsd_jail.py` |
| Windows | **AppContainer** | `windows_appcontainer.py` |

All backends FAIL OPEN today — for Ornith-emitted code we **MUST fail closed** (new policy in §5.6).

### 5.5 Audit trail

Each Ornith invocation appends one row to the audit log with:

```json
{
  "actor": "agent:ornith",
  "model": "deepreinforce-ai/Ornith-1.0-9B",
  "task_id": "...",
  "permission_spec": {...},
  "sts_token": "sts_abc...",
  "scaffold_sha256": "...",
  "tool_calls": [...],
  "outcome": "success|failure|timeout|blocked_by_permission",
  "sandbox_backend": "landlock",
  "sandbox_applied": true,
  "tokens_in": ..., "tokens_out": ...,
  "wall_ms": ...
}
```

The STS token is scoped to `(human ∩ gludd ∩ ornith_requested)` as in the existing escalation flow.
The `scaffold_sha256` lets us correlate outcomes with scaffold revisions over time → feeds §5.7.

### 5.6 Failure-mode matrix

| Failure | Detection | Mitigation |
|---|---|---|
| Ornith server (vLLM/SGLang) crashes | healthcheck probe every 5 s, fails 3× → degrade | Re-route to fallback provider (Anthropic/OpenAI), file `HumanTodo` if persistent |
| Infinite tool-call loop | Per-task tool-call budget (default 50) enforced by `OrnithAdapter` | Hard kill at budget + revert worktree, mark task `failed` |
| Malformed `<tool_call>` JSON | vLLM parser error or our schema validator | Retry with repair prompt (1×); if still bad, capture scaffold for offline RL negative example |
| Permission violation (Ornith requests out-of-intersection capability) | Existing escalation gate | Auto-deny, log, surface `HumanTodo(category=permission_escalation)` |
| Sandbox fail-open on a host without Landlock/bwrap | `sandbox_applied=false` in audit log | **For Ornith-emitted code: hard deny** (new policy — currently the daemon only warns) |
| Scaffold attempts destructive op (`rm -rf`, force-push) | Substring + AST pattern match in adapter pre-exec | Block + record as negative RL example |
| Hallucinated file paths outside `path_prefix` | Sandbox backend enforces; post-hoc scan of `tool_calls` | Block; treat as negative example |
| Token-budget runaway (Ornith's `<think>` grows unbounded) | Per-call `max_tokens` + per-task sum | Truncate, log, file `HumanTodo` if recurring |
| Network exfiltration attempt | `allowed_hosts` enforcement + audit scan | Block at sandbox, escalate |
| Training-set poisoning (curated scaffold is wrong) | Held-out eval set (gate + SWE-bench sample) before promotion | Revert promoted role, drop from RL positive set |

### 5.7 Self-improvement loop — how gludd measures Ornith is actually helping

Three orthogonal signals, all written to the feedback store:

1. **Task-level outcome** — for each dispatched Ornith task: `success|failure|timeout|blocked`.
   Success = the patch passed `make gate` on the target worktree AND a human (or higher-tier review
   agent) marked it `done`. Tracked as a rolling 30-day success rate per (task_type, model_size).
2. **Token efficiency** — `tokens_to_completion` vs. the gludd-baseline (the existing sonnet dispatch
   path on the same task class). Ornith must beat baseline by ≥X% to justify GPU cost.
3. **Regression rate** — when a promoted Ornith-generated scaffold (a new ansible role in
   `.gludd/collections/`) causes a previously-green test to fail, that's a regression. Rate is
   `regressions / promoted_scaffolds`. Hard ceiling: if >5%, freeze promotions for a human review.

These three metrics ARE the eval harness Ornith's RL trainer consumes offline. Gludd never runs the
trainer; it ships labeled `(scaffold, outcome)` pairs to a training job the operator runs separately
out-of-band (the bidirectional seam).

---

## 6. Permission / sandbox / audit surface (consolidated)

| Surface | Existing gludd component | Change required |
|---|---|---|
| Capability intersection | `src/general_ludd/security/permissions.py:PermissionSpec.intersect` | None — already correct |
| Sandbox fail-closed policy | `src/general_ludd/event_loop/loop.py:1285` ("No sandbox backend … dispatching UNSANDBOXED") | Add `REQUIRE_SANDBOX_FOR="ornith"` env; for Ornith-emitted code, hard-deny instead of warn |
| Audit log | existing audit table | Add `scaffold_sha256`, `model`, `tokens_in/out` fields |
| STS | existing STS mint | Scope new tokens to `agent:ornith` principal |
| MCP server registry | opencode plugin config | New `OrnithMCPProvider` (§7) |
| HumanTodo escalation | `HumanTodo` model + `POST /api/human-todos` | Reuse unchanged |

---

## 7. MCP server sketch (DO NOT IMPLEMENT — design only)

The MCP server surface gludd exposes to (and consumes from) Ornith-aware clients:

```yaml
# Conceptual; real impl would be a TS or Python MCP server.
server: gludd.ornith
tools:
  - name: ornith_solve
    description: >
      Dispatch a code-gen sub-task to a local Ornith endpoint under a per-task
      PermissionSpec. Returns a patch + scaffold_sha256 + outcome.
    inputSchema:
      type: object
      required: [task_description, repo_context_path]
      properties:
        task_description: { type: string }
        repo_context_path: { type: string }       # MUST be inside an existing worktree
        max_iterations: { type: integer, default: 50 }
        requested_permissions:                     # what the scaffold thinks it needs
          type: object
          properties:
            file_prefix: { type: string }
            net_hosts: { type: array, items: { type: string } }
        model: { type: string, default: "deepreinforce-ai/Ornith-1.0-9B" }
    outputSchema:
      type: object
      properties:
        patch_path: { type: string }
        scaffold_sha256: { type: string }
        outcome: { type: string, enum: [success, failure, timeout, blocked] }
        effective_permission_spec: { type: object }
        audit_row_id: { type: string }

  - name: ornith_improve
    description: >
      Submit a curated (scaffold, outcome) pair to the feedback store for the
      offline RL trainer. Does NOT mutate weights.
    inputSchema:
      type: object
      required: [target_playbook_path, feedback]
      properties:
        target_playbook_path: { type: string }    # the scaffold being evaluated
        feedback:
          type: object
          required: [outcome]
          properties:
            outcome: { type: string, enum: [success, failure, regression] }
            gate_output: { type: string }
            tokens_used: { type: integer }
            notes: { type: string }

resources:
  - name: ornith_status
    description: Current Ornith endpoint health, last-24h success rate, queue depth.
    mimeType: application/json

prompts:
  - name: ornith_meta
    description: >
      System-prompt fragment injected when a gludd agent dispatches to Ornith —
      restates the permission boundary, the failure budget, and the audit contract.
```

The `ornith_solve` tool is the only entry point that lets arbitrary agent code invoke Ornith. Every
call goes through: (1) capability intersection, (2) sandbox detection, (3) audit row, (4) outcome
feedback — in that order.

---

## 8. Phased rollout

### Phase 1 — Tool mode (read-only-ish, 2 weeks)
- Gludd dispatches Ornith as an OpenAI-compatible provider for **code-gen sub-tasks only** (no
  scaffold promotion).
- `OrnithAdapter` in `src/general_ludd/integrations/ornith.py`. Wraps vLLM/SGLang. Logs every call.
- Hard fail-closed on missing sandbox backend for Ornith-emitted code (new policy).
- Metrics: success rate vs. baseline sonnet path on a held-out 50-task set.
- **Exit gate:** success rate ≥ baseline AND zero sandbox-bypass incidents.

### Phase 2 — Peer agent + scaffold capture (4 weeks)
- Capture successful scaffolds into `.gludd/collections/general_ludd/agent/roles/ornith_<hash>/`
  (project-tier shadowing per AGENTS.md "Project-Collection Precedence").
- Promote scaffolds through a review queue (a `HumanTodo` per promotion) — no auto-promotion yet.
- Add the MCP server (§7) so opencode plugins can call `ornith_solve`.
- **Exit gate:** ≥10 scaffolds promoted, regression rate <5%, audit log shows zero permission
  violations across all calls.

### Phase 3 — Self-improvement substrate (continuous)
- Ship `(scaffold, outcome)` pairs to an out-of-band RL training job (operator-run, not gludd).
- Gludd consumes new model checkpoints as they're published to Hugging Face — versioned in
  `config/ornith.yml` with a pinned SHA.
- Regression ceiling (5%) gates auto-roll-forward of new checkpoints.
- Gludd's own prompts/playbooks become candidates for Ornith-driven optimization: every gludd
  playbook is treated as a scaffold, evaluated against the gate, and improved versions land as
  project-tier overrides.
- **Exit gate:** measurable improvement in gludd's own `make gate` runtime and SWE-bench-style
  held-out pass rate over a 90-day window, with regression rate held under ceiling.

---

## 9. File touches (rough list, no implementation)

New files:
- `src/general_ludd/integrations/ornith/__init__.py`
- `src/general_ludd/integrations/ornith/adapter.py` — `OrnithAdapter` (OpenAI-compatible client,
  tool-call budget, scaffold capture)
- `src/general_ludd/integrations/ornith/feedback.py` — feedback-store writer
- `src/general_ludd/integrations/ornith/mcp_server.py` — MCP server (§7)
- `src/general_ludd/integrations/ornith/policy.py` — fail-closed policy hook for loop.py
- `config/ornith.yml` — endpoint URL, model SHAs, budgets, intersection defaults
- `config/permissions/agent-ornith.yml` — `PermissionSpec` template for `agent:ornith`
- `alembic/versions/<new>_ornith_audit_fields.py` — adds `scaffold_sha256`, `model`, `tokens_*`
- `tests/unit/test_ornith_adapter.py`, `test_ornith_permission_intersection.py`,
  `test_ornith_fail_closed.py`, `test_ornith_feedback_store.py`
- `tests/integration/test_ornith_dispatch_under_sandbox.py`
- `tests/e2e/test_ornith_solve_mcp.py`
- This design doc.

Modified files:
- `src/general_ludd/event_loop/loop.py:1285` — branch on `REQUIRE_SANDBOX_FOR`
- `src/general_ludd/security/sandboxes/detect.py` — expose `backend_can_enforce(spec)` predicate
- `daemon.py` lifespan — register MCP server, load `config/ornith.yml`
- `src/general_ludd/ansible/paths.py` — include Ornith-promoted role paths in precedence resolution
- `opencode.json` — register `ornith` MCP server
- `AGENTS.md` — new section "Ornith Integration Policy" (fail-closed, intersection, scaffold capture)
- `.opencode/plugin/enforce-ornith-sandbox.ts` — new 3-layer guardrail (mirrors existing plugins)
- `Makefile` — `make ornith-serve`, `make ornith-eval`, `make ornith-promote-scaffold`

New ansible roles (Phase 2+):
- `collections/ansible_collections/general_ludd/agent/roles/ornith_serve/` — local inference bootstrap
- One role per promoted scaffold (under `.gludd/collections/`)

Docs:
- `docs/design/SYMBIOTIC_AGENT_INTEGRATION.md` (this file)
- `docs/integrations/ornith.md` (operator-facing runbook — Phase 1)

---

## 10. Open questions

1. **`exec:` capability scheme.** The current `PermissionSpec` covers `file:` and `net:` but not
   "which binaries may be executed." Ornith-emitted code wants to run `python3`, `git`, `make` — but
   not `curl`, `nc`, or arbitrary binaries. Do we extend `PermissionSpec` with an `exec:` scheme
   (and update all 7 sandbox backends), or rely on Landlock's `EXECUTE` flag + bubblewrap's read-only
   `/usr/bin` bind? **Recommendation:** extend the scheme — `EXECUTE` alone is per-file, not
   per-binary-name, and bubblewrap's bind-mount trick doesn't generalize to macOS/Windows.

2. **Fail-closed default vs. fail-open.** The daemon's existing contract is fail-open ("UNSANDBOXED
   with a warning"). Hard-denying for Ornith creates a precedent. Should we generalize to a
   per-principal `fail_closed: bool` on `PermissionSpec`? Likely yes, but it's a contract change
   that needs its own design pass.

3. **Training-data export licensing.** Gludd's `(scaffold, outcome)` pairs may include proprietary
   customer code. Shipping them to an offline RL trainer (even operator-run) needs a redaction layer
   and a license review. Out of scope for this doc but blocks Phase 3.

4. **Model versioning & reproducibility.** When Ornith publishes a new checkpoint, the success-rate
   baseline shifts. How do we pin a baseline per model SHA for the regression ceiling? Probably a
   per-SHA eval pass on the held-out set on each checkpoint bump.

5. **Interaction with the 10-agent floor.** Ornith inference is GPU-bound and single-tenant per
   server; dispatching 10 parallel Ornith subagents would queue. Does the floor policy need a
   per-provider concurrency cap, or do we route excess demand to the sonnet fallback?

6. **Ornith 397B-MoE hosting.** The 397B model needs 8×H100. For most gludd deployments the 9B is
   the right default; the 35B-MoE for hot-path code-gen. Should the adapter auto-fallback across
   sizes based on queue depth?

7. **CodeAct vs. Ornith tool-call format.** Ornith emits OpenAI-style `tool_calls`; CodeAct emits
   raw Python. If we ever wire CodeAct as a secondary code-exec engine inside a scaffold Ornith
   generates, we need a format translator. Defer.

---

## References

- Ornith-1.0-9B model card — https://huggingface.co/deepreinforce-ai/Ornith-1.0-9B
- Ornith-1.0-35B — https://huggingface.co/deepreinforce-ai/Ornith-1.0-35B
- Ornith-1.0-397B — https://huggingface.co/deepreinforce-ai/Ornith-1.0-397B
- Ornith blog — https://deep-reinforce.com/ornith.html
- SWE-agent — https://github.com/SWE-agent/SWE-agent (arXiv:2405.15793)
- OpenHands — https://github.com/OpenHands/OpenHands
- Aider — https://github.com/Aider-AI/aider
- Continue.dev (archived) — https://github.com/continuedev/continue
- Devin (proprietary) — https://devin.ai/
- Sweep (proprietary) — https://www.sweep.dev/
- AutoCodeRover (arXiv:2404.05427) — https://github.com/AutoCodeRoverSG/auto-code-rover
- Agentless (arXiv:2407.01489) — https://github.com/OpenAutoCoder/Agentless
- CodeAct (arXiv:2402.01030) — https://github.com/xingyaoww/code-act
- Gludd PermissionSpec — `src/general_ludd/security/permissions.py:81`
- Gludd sandbox backends — `src/general_ludd/security/sandboxes/`
- Gludd project-collection precedence — AGENTS.md "Project-Collection Precedence Contract"
