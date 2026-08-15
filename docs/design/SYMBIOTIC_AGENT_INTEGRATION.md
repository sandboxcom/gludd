# Symbiotic Integration of Self-Improving Coding Agents with Gludd

**Status:** Phase 1 — Implementation in progress
**Last updated:** 2026-06-29
**Author:** Agent research session; refined against the in-flight implementation

---

## Table of contents

1. [Executive summary](#1-executive-summary)
2. [Candidate matrix](#2-candidate-matrix)
3. [Ornith — detailed profile (the chosen candidate)](#3-ornith--detailed-profile-the-chosen-candidate)
4. [Symbiotic coexistence analysis](#4-symbiotic-coexistence-analysis)
5. [Phase 1 — Implementation map](#5-phase-1--implementation-map)
6. [Phase 2 — Promotion path (PR → bundled)](#6-phase-2--promotion-path-pr--bundled)
7. [Phase 3 — Closed-loop RL](#7-phase-3--closed-loop-rl)
8. [Security model](#8-security-model)
9. [Worked example — end-to-end Ornith task](#9-worked-example--end-to-end-ornith-task)
10. [Failure-mode matrix](#10-failure-mode-matrix)
11. [Self-improvement loop — how gludd measures Ornith is actually helping](#11-self-improvement-loop--how-gludd-measures-ornith-is-actually-helping)
12. [Permission / sandbox / audit surface (consolidated)](#12-permission--sandbox--audit-surface-consolidated)
13. [MCP server surface](#13-mcp-server-surface)
14. [Open questions](#14-open-questions)
15. [References](#15-references)

---

## 1. Executive summary

**Recommendation: integrate DeepReinforce's Ornith-1.0 (MIT-licensed, self-improving agentic-coding
LLM family: 9B / 35B / 397B) into gludd as a peer agent + self-improvement substrate. Implementation
landed in Phase 1 and is wiring through the daemon now.**

Ornith's defining feature — a closed RL loop that **jointly optimizes the agent's own scaffold (the
tool-orchestration / prompt code) and the resulting solution rollouts** — is exactly the capability
gludd cannot grow on its own: gludd's playbooks, ansible roles, and agent prompts are currently
hand-authored. Conversely, gludd possesses exactly what Ornith lacks: an OS-level permission system
(`PermissionSpec` + `path_prefix` / `allowed_hosts`), six sandbox backends (Landlock, bubblewrap,
AppArmor, SELinux, macOS Seatbelt, FreeBSD Jail, Windows AppContainer), an STS + audit-log capability
system, and a daemon-managed worktree dispatcher. The two systems are complementary at the architectural
seam: **Ornith emits scaffolds + solutions; gludd executes them inside a permission-scoped sandbox and
feeds outcomes back into Ornith's training loop.** This doc specifies that seam, the failure modes, and
a three-phase rollout — and now reflects the actual files shipping under `src/general_ludd/ornith/`,
`src/general_ludd/routers/ornith.py`, and the `ornith_self_improve` ansible role.

Related design docs:
- [Permission system](PERMISSION_SYSTEM.md) — the `agent:ornith` capability
- [Sandbox backends](SANDBOX_BACKENDS.md) — how the Ornith subprocess is contained
- [Project collections](PROJECT_COLLECTIONS.md) — how promoted scaffolds become bundled
- [Human todos](HUMAN_TODOS.md) — the review gate

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
  `PermissionSpec` is *required* — implemented via the `agent:ornith` capability (see §5, §8).
- **No persistent memory across sessions.** Scaffolds are per-rollout. Gludd's daemon persists
  successful scaffolds as canonical ansible roles (the bidirectional loop; see §6).
- **No audit trail.** Every Ornith call is wrapped by `OrnithMCPClient` / `OutcomeObserver` so the
  audit row + `OrnithTrainingPairModel` capture `{scaffold, target_files, tokens, model_sha, outcome}`.

---

## 4. Symbiotic coexistence analysis

For the candidates that passed the license + stable-API filter, the coexistence modes:

### 4.1 Ornith × gludd (the recommended pair) — all five modes apply

| Mode | Ornith's role | Gludd's role |
|---|---|---|
| **(a) Tool gludd dispatches** | Solves a single sub-task (write a function, fix a bug) when gludd's ansible/worker stack would be heavier | Dispatches Ornith via `gludd ornith solve`, scoped under `path_prefix=/worktree/<task>/`, `allowed_hosts={localhost:8000}` |
| **(b) Higher-level orchestrator** | Owns end-to-end coding tasks (issue → PR) | Provides sandboxing, secrets, `HumanTodo` escalation, permission intersection |
| **(c) Peer agent** | Code-gen & refactoring | Ops, ansible, security review, CI gating |
| **(d) Self-improvement substrate** | Optimizes gludd's prompts/playbooks based on observed outcomes | Provides the eval harness (gate green/red, SWE-bench-style held-out set) and the safety boundary |
| **(e) Bidirectional** | Generates new ansible roles / OPA policies / opencode plugins as "discovered scaffolds" | Wraps execution in `bubblewrap`/`Landlock`, audits every call, persists validated scaffolds into `.gludd/collections/` (the project-collection precedence system) |

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

## 5. Phase 1 — Implementation map

Phase 1 lands the data-collection half of the loop: the daemon speaks MCP to Ornith-aware clients,
captures every `(scaffold, outcome)` pair into Postgres, and the operator's CLI surfaces it. The
files below are the canonical paths the parallel tasks are landing against.

| Component | File | One-line summary |
|---|---|---|
| **MCP server** | `src/general_ludd/ornith/mcp_server.py` | Exposes the Ornith surface (solve/improve/status) to MCP clients (opencode plugins, external agents). Implements the `server: gludd.ornith` schema in §13. |
| **MCP client** | `src/general_ludd/ornith/client.py` (`OrnithMCPClient`) | Daemon-side client that dispatches a sub-task to the local vLLM/SGLang endpoint, enforces `max_iterations` / `max_tokens_per_call` / wall-clock timeout, captures the scaffold, writes one `OrnithTrainingPairModel` row per call. |
| **Training repo** | `src/general_ludd/ornith/training_repo.py` (`OrnithTrainingRepo`) | CRUD over the `ornith_training_pairs` table. `export_dataset() -> path` writes a JSONL stream of `{task_description, target_files, scaffold_content, scaffold_hash, model_sha, outcome_status, outcome_details, reward}` for the offline RL trainer. |
| **Outcome observer** | `src/general_ludd/ornith/outcome_observer.py` (`OutcomeObserver`) | Resolves `pending` pairs: inspects the eventual fate of each scaffold (gate green/red, PR merged/reverted/closed, review verdict) and writes `outcome_status` + `outcome_details`. Scheduled by the daemon hourly (mirrors `BlockerDetector.scan()`). |
| **Daemon router** | `src/general_ludd/routers/ornith.py` | FastAPI router exposing `POST /api/ornith/solve`, `GET /api/ornith/pairs`, `POST /api/ornith/export`, `GET /api/ornith/status`. Wraps `OrnithMCPClient` / `OrnithTrainingRepo`. |
| **Ansible module** | `collections/ansible_collections/general_ludd/agent/plugins/modules/gludd_ornith.py` | Ansible-callable wrapper around `gludd ornith solve` — lets a playbook dispatch Ornith with the same args as the CLI. Returns `{patch_path, scaffold_hash, outcome, audit_row_id}`. |
| **Permission spec** | `config/permissions/build.yml` (and `agent-ornith.yml`) | Grants the `agent:ornith` capability: `file:repo` scoped to `/worktree/<task>/`, `net:egress:llm_api` to the local inference host only. Subagents default-deny; intersection with the human spec narrows further. |
| **Self-improve role** | `collections/ansible_collections/general_ludd/agent/roles/ornith_self_improve/` | Operator-run role that pulls the latest JSONL export, calls the offline RL trainer, and writes the resulting model SHA back into `ornith_model_sha` in gludd's config. Idempotent; CI-gated. |
| **CLI** | `src/general_ludd/cli_ornith.py` | `gludd ornith {solve\|improve\|pairs\|export\|status\|self-improve}` — thin wrapper over the router; operator + agent entry point. |
| **Training-pair table** | `alembic/versions/014_add_ornith_training_pairs.py` + `src/general_ludd/db/models.py:915` (`OrnithTrainingPairModel`) | Persistence: `invoked_at`, `target_files`, `scaffold_kind`, `scaffold_content`, `scaffold_hash`, `iterations_used`, `tokens_consumed`, `model_sha`, `outcome_status`, `outcome_details`. |
| **Package init** | `src/general_ludd/ornith/__init__.py` | Re-exports `OrnithMCPClient`, `OrnithTrainingRepo`, `OutcomeObserver` so the daemon lifespan can wire them. |

The package is intentionally daemon-importable from boot (per the No-Manual-Default policy): even
before the MCP server is registered, the daemon constructs an `OrnithTrainingRepo` and
`OutcomeObserver` so the data-collection side of the loop is live on every install.

---

## 6. Phase 2 — Promotion path (PR → bundled)

An Ornith-generated scaffold graduates from a one-off rollout artifact to a bundled gludd
collection entry through the project-collection precedence system. This is the
bidirectional loop — **the property that makes Ornith symbiotic rather than just "a model we
call."**

```text
 Ornith solve  →  PR on target repo  →  CI gate  →  HumanTodo review
                                                              │
                                            ┌─────────────────┴──────────────┐
                                            ▼                                  ▼
                              rejected (outcome=failure)            approved (outcome=success)
                                            │                                  │
                                  negative RL example                        merged into master
                                                                                   │
                                                                scaffold promoted to
                                                                .gludd/collections/general_ludd/
                                                                  agent/roles/ornith_<hash>/
                                                                (PROJECT tier)
                                                                                   │
                                                                operator review every N promotions;
                                                                the strongest one is copied to
                                                                collections/ansible_collections/...
                                                                  (BUNDLED tier — shipped release)
                                                                                   │
                                                                precedence: PROJECT > USER > BUNDLED
```

### 6.1 Steps

1. **Ornith emits a scaffold** via `OrnithMCPClient.solve()`. The scaffold (a playbook, role, module,
   plugin, OPA policy, or patch) is recorded in `OrnithTrainingPairModel` with `outcome_status=pending`.
2. **The patch becomes a PR** against the target repo. The agent (or operator) opens it from the
   `gludd ornith solve` output; the PR description carries the `scaffold_hash` and a link back to the
   pair row.
3. **CI runs the gate.** `OutcomeObserver` polls the gate / PR state. On gate failure, it writes
   `outcome_status=failure` with the gate tail in `outcome_details` and the pair becomes a negative
   RL example.
4. **Human review gate.** On gate green, the daemon files a `HumanTodo(category=review)` per
   [HUMAN_TODOS.md](HUMAN_TODOS.md) referencing the PR. **No scaffold is promoted without a human
   `done`** on that todo (the review gate).
5. **Promotion to PROJECT tier.** On human approval + PR merge, the scaffold is materialized under
   `<project_root>/.gludd/collections/ansible_collections/general_ludd/agent/roles/ornith_<scaffold_hash>/`
   by the self-improve role. `OutcomeObserver` sets `outcome_status=success`.
6. **Promotion to BUNDLED tier (operator action).** Periodically the operator reviews the
   PROJECT-tier scaffolds; the strongest, regression-free ones are copied into
   `collections/ansible_collections/general_ludd/agent/roles/` so they ship with the next release.
   Per [PROJECT_COLLECTIONS.md](PROJECT_COLLECTIONS.md), PROJECT shadows BUNDLED until that copy —
   so day-to-day work uses the freshest scaffold without waiting for a release.

### 6.2 Precedence at runtime

The daemon resolves collection paths via `src/general_ludd/ansible/paths.py` at startup and on
project switch (see AGENTS.md "Project-Collection Precedence Contract"). The lookup order is
`PROJECT > USER > BUNDLED`, so a freshly promoted scaffold shadows the older bundled version
immediately — no daemon restart, no release cut.

### 6.3 Regression ceiling

If a promoted scaffold causes a previously-green test to fail, the regression counts against the
ceiling in §11. Above 5% the operator freezes promotions for a manual review pass (the
`ornith_self_improve` role exposes `gludd ornith self-improve --freeze-promotions`).

---

## 7. Phase 3 — Closed-loop RL

Phase 3 closes the loop: gludd emits labeled training data; the offline RL trainer consumes it; a
new model checkpoint rolls back into gludd's config; the next wave of solves uses it. Gludd never
runs the trainer — that's operator-side — but gludd owns every other leg of the loop.

```text
 ┌─────────────────────────┐   OrnithTrainingRepo.export_dataset()   ┌──────────────────────┐
 │  ornith_training_pairs  │ ──────────────────────────────────────► │  JSONL on disk       │
 │  (Postgres)             │   {task, scaffold, outcome, reward}     │  /var/lib/gludd/     │
 └─────────────────────────┘                                          │    ornith/<sha>.jsonl│
       ▲                                                               └──────────┬───────────┘
       │ OutcomeObserver resolves outcome → reward                              │ operator copies
       │                                                                           │ to trainer host
       │                                                                           ▼
       │                                                              ┌────────────────────────┐
       │                                                              │ Ornith offline trainer │
       │                                                              │ (DeepReinforce side)   │
       │                                                              └────────────┬───────────┘
       │                                                                           │ new checkpoint
       │                                                                           │ SHA published
       │                                                                           ▼
       │                                            ┌──────────────────────────────────────────┐
       │                                            │ ornith_self_improve ansible role writes  │
       │                                            │   ornith_model_sha: <new-sha>            │
       │                                            │ into config/ornith.yml                   │
       │                                            └────────────────────┬─────────────────────┘
       │                                                                 │ daemon reloads
       │                                                                 ▼
       │     next OrnithMCPClient.solve() dispatches against the new SHA ─┘
       │                                                                  (recorded in model_sha)
       └──────────────────────────────────────────────────────────────────┘
```

### 7.1 The export

`OrnithTrainingRepo.export_dataset(since=None, only_status=("success", "failure")) -> Path`
streams a JSONL file. Each line:

```json
{
  "pair_id": "ORN-...",
  "task_description": "refactor foo.py to use tenacity",
  "target_files": ["src/foo.py"],
  "scaffold_kind": "patch",
  "scaffold_content": "...",
  "scaffold_hash": "sha256:...",
  "model_sha": "deepreinforce-ai/Ornith-1.0-9B@abc123",
  "outcome_status": "success",
  "outcome_details": {"gate": "PASS", "review": "approved", "merged_sha": "..."},
  "reward": 1.0
}
```

The `reward` field is computed by `OutcomeObserver` from the outcome tuple: `success → 1.0`,
`failure → 0.0`, `regression → -1.0`, `timeout/blocked → 0.0` (counts as neither win nor loss for
RL purposes but is exported for completeness). Operators can override the reward function via
`config/ornith.yml` without touching the observer.

### 7.2 Rolling the model SHA back in

The `ornith_self_improve` role (§5) is the only sanctioned path:

1. Pull the latest JSONL export (`gludd ornith export --out /tmp/ornith.jsonl`).
2. Hand it to the operator-run RL trainer (out of band).
3. On trainer completion, the new model SHA is written to `config/ornith.yml`'s `ornith_model_sha`.
4. The daemon reloads `OrnithMCPClient` on next dispatch — every subsequent `solve()` records the new
   `model_sha` in its `OrnithTrainingPairModel` row, so success rates are attributable per SHA.
5. Held-out eval pass (gate + SWE-bench sample) gates the roll-forward; if the new SHA's regression
   rate crosses the §11 ceiling, `ornith_self_improve` reverts the SHA and files a `HumanTodo`.

### 7.3 Evaluation

The loop is evaluated by three rolling-30-day metrics (defined in §11): **task success rate** per
`(task_type, model_sha)`, **token efficiency** vs. the sonnet baseline, **regression rate** per
promoted scaffold. The dashboard is `gludd ornith status` (CLI) and `GET /api/ornith/status`
(router). A "healthy" loop shows success rate monotonically improving across checkpoints with
regression rate held under ceiling; a flat or declining success rate across two consecutive
checkpoints triggers a `HumanTodo` so the operator can intervene before another checkpoint rolls.

---

## 8. Security model

### 8.1 Permission — `agent:ornith` capability

Ornith runs under the `agent:ornith` principal. Its `PermissionSpec` lives at
`config/permissions/agent-ornith.yml` and is intersected with the human spec and the agent spec
per AGENTS.md "Human Permission Subjects + Intersection Policy":

```text
effective_spec = intersection(human_spec, agent_spec, ornith_requested_spec)
```

Default `ornith_requested_spec` for a code-gen sub-task (see [PERMISSION_SYSTEM.md](PERMISSION_SYSTEM.md)):

```yaml
capabilities:
  - resource: file:repo
    actions: ["read", "write"]
    constraints:
      path_prefix: "/worktree/<task_id>/"
  - resource: net:egress:llm_api
    actions: ["connect"]
    constraints:
      allowed_hosts: ["localhost", "127.0.0.1"]
      allowed_ports: [8000]
  - resource: exec:binaries                  # see open question §14.1
    actions: ["execute"]
    constraints:
      binaries: ["python3", "git", "make"]
```

**Subagents default-deny.** Any subagent that wants to dispatch Ornith must inherit (or escalate
to) the `agent:ornith` capability; without it the request is rejected at the router. Anything
Ornith requests *outside* the intersection goes through the standard escalation flow
(`POST /admin/perm/escalation-request`, requires ≥3 `alternatives_tried`).

### 8.2 Sandbox — Landlock / bubblewrap / AppContainer

The Ornith subprocess never runs unsandboxed. The daemon selects a backend via
`src/general_ludd/security/sandboxes/detect.py` (see [SANDBOX_BACKENDS.md](SANDBOX_BACKENDS.md)):

| Host platform | Backend wrapping Ornith's code emission | Why |
|---|---|---|
| Linux ≥ 6.7, pylandlock importable | **Landlock** (preferred) | In-process, finest filesystem ACL |
| Linux, `bwrap` present | **bubblewrap** + seccomp advisory | Namespace isolation; pairs with nftables for hostname ACL |
| Linux w/ AppArmor | **AppArmor** profile `gludd-ornith` | Path + net restrictions |
| macOS | **Seatbelt** (`sandbox-exec`) | Already supported in `macos_seatbelt.py` |
| FreeBSD | **jail** | `freebsd_jail.py` |
| Windows | **AppContainer** | `windows_appcontainer.py` |

The sandbox grants Ornith read access to `target_files` only (the worktree under
`path_prefix`); write access is restricted to a scratch dir; the output patch is emitted to a
well-known path and goes through an audit gate **before** it is applied to the real worktree.

**Fail-closed policy for Ornith-emitted code:** if no enforcing backend is available, the dispatch
is hard-denied (not warned). This is a contract change from the daemon's historical fail-open —
see §14.2 and §12.

### 8.3 Audit — every solve/improve is recorded

Every invocation lands one row in `ornith_training_pairs` (the `OrnithTrainingPairModel` at
`src/general_ludd/db/models.py:915`) carrying `{invoked_at, task_description, target_files,
scaffold_kind, scaffold_content, scaffold_hash, iterations_used, tokens_consumed, model_sha,
outcome_status, outcome_details, project_id, agent_id}`. The migration is
`alembic/versions/014_add_ornith_training_pairs.py`.

Outcomes are resolved later by `OutcomeObserver` (`src/general_ludd/ornith/outcome_observer.py`)
from the gate / review / git history. The JSONL export (`OrnithTrainingRepo.export_dataset`)
includes the reward signal derived from the resolved outcome, so the trainer sees labeled data,
not raw scaffolds.

### 8.4 Bounded scope

`OrnithMCPClient` (`src/general_ludd/ornith/client.py`) enforces three independent ceilings on
every dispatch:

| Bound | Default | Source |
|---|---|---|
| `max_iterations` | 50 tool calls per task | `config/ornith.yml` |
| `max_tokens_per_call` | per-call output cap (truncates `<think>` bloat) | `config/ornith.yml` |
| `timeout` | wall-clock per dispatch (default 600 s) | `config/ornith.yml` |

A ceiling breach aborts the dispatch, reverts the worktree, and the pair row is recorded with
`outcome_status=timeout|blocked`. The breach also counts against the §10 infinite-loop failure
mode.

---

## 9. Worked example — end-to-end Ornith task

A gludd task needs to refactor `src/foo.py` to use `tenacity` for retries.

1. **Dispatch.** The owning gludd agent runs:
   ```text
   gludd ornith solve \
       --task "refactor foo.py to use tenacity for retries" \
       --target-files src/foo.py
   ```
   The CLI (`src/general_ludd/cli_ornith.py`) hits `POST /api/ornith/solve` on the router
   (`src/general_ludd/routers/ornith.py`).

2. **Permission + sandbox.** The router intersects
   `(human_spec ∩ agent:ornith_spec ∩ requested_spec)`, narrows `path_prefix` to the task
   worktree, picks a sandbox backend via `detect.py`, and hands the call to
   `OrnithMCPClient.solve()`.

3. **Inference.** `OrnithMCPClient` dispatches the prompt to the local vLLM/SGLang endpoint
   serving `deepreinforce-ai/Ornith-1.0-9B`. Ornith emits `<think>`, then a `tool_calls` block
   that produces a patch. The client enforces `max_iterations=50`, `max_tokens_per_call`, and
   the wall-clock timeout.

4. **Capture.** The patch + scaffold are written to a scratch path. A new
   `OrnithTrainingPairModel` row is inserted with `outcome_status=pending`, `scaffold_hash`,
   `tokens_consumed`, `model_sha`, `target_files=["src/foo.py"]`.

5. **PR.** The patch becomes a PR on the target repo. The PR body references the pair id and
   `scaffold_hash`. CI runs the gate.

6. **Gate result.** `OutcomeObserver` resolves the pair. If the gate is **red**,
   `outcome_status=failure`, `outcome_details={gate_tail: "..."}`. If the gate is **green**, the
   daemon files a `HumanTodo(category=review)` ([HUMAN_TODOS.md](HUMAN_TODOS.md)) referencing the
   PR — the review gate.

7. **Human approval.** The human reviews the PR via `gludd human-todo done <id>` with a
   `human_resolution`. The PR is merged.

8. **Outcome finalized.** `OutcomeObserver` sets `outcome_status=success`,
   `outcome_details={review: "approved", merged_sha: "..."}`. The reward for this pair is `1.0`.

9. **Promotion (Phase 2).** If the scaffold generalizes, the `ornith_self_improve` role
   promotes it to `.gludd/collections/.../ornith_<scaffold_hash>/` (PROJECT tier), shadowing the
   bundled version per [PROJECT_COLLECTIONS.md](PROJECT_COLLECTIONS.md).

10. **Loop closes (Phase 3).** On the next `gludd ornith export`, this pair ships in the JSONL
    with `reward=1.0`. The operator's offline RL trainer consumes it. A new checkpoint lands;
    its SHA is written to `ornith_model_sha` by the `ornith_self_improve` role. The next
    `solve()` uses the new SHA — and its result is recorded against that SHA, so the success-rate
    delta is attributable.

---

## 10. Failure-mode matrix

Concrete mitigations cite the file:line where the guard lives.

| Failure | Detection | Mitigation |
|---|---|---|
| Ornith server (vLLM/SGLang) crashes | healthcheck probe every 5 s; 3 fails → degrade | Re-route to fallback provider (Anthropic/OpenAI via existing worker); file `HumanTodo` if persistent. Router: `src/general_ludd/routers/ornith.py`. |
| Infinite tool-call loop | per-task budget (default 50) | Hard kill at budget + revert worktree; record `outcome_status=blocked`. `OrnithMCPClient` enforces `max_iterations` (`src/general_ludd/ornith/client.py`). |
| Malformed `<tool_call>` JSON | vLLM parser error or schema validator | Retry with repair prompt (1×); if still bad, capture scaffold as RL negative example (`OrnithTrainingPairModel.outcome_status=failure`). |
| Permission violation (Ornith requests out-of-intersection capability) | existing escalation gate | Auto-deny, log; surface `HumanTodo(category=permission_escalation)`. Spec: `config/permissions/agent-ornith.yml`. |
| Sandbox fail-open on a host without Landlock/bwrap | `sandbox_applied=false` | **For Ornith-emitted code: hard deny** (new policy — see §12). Detection in `src/general_ludd/security/sandboxes/detect.py`. |
| Scaffold attempts destructive op (`rm -rf`, force-push) | substring + AST pattern match in client pre-exec | Block + record as negative RL example. Guard in `OrnithMCPClient` (`src/general_ludd/ornith/client.py`). |
| Hallucinated file paths outside `path_prefix` | sandbox backend enforces; post-hoc `tool_calls` scan | Block at sandbox; treat as negative example. |
| Token-budget runaway (`<think>` grows unbounded) | per-call `max_tokens_per_call` + per-task sum | Truncate, log, file `HumanTodo` if recurring. Bound in `OrnithMCPClient` (§8.4). |
| Network exfiltration attempt | `allowed_hosts` enforcement + audit scan | Block at sandbox, escalate. Spec: `config/permissions/agent-ornith.yml`. |
| Training-set poisoning (curated scaffold is wrong) | held-out eval set (gate + SWE-bench sample) before promotion | Revert promoted role, drop from RL positive set. Promotion gate: §6.1 step 4 (human review). |
| Regression from a promoted scaffold | previously-green test now fails | Counts against the 5% ceiling (§11); above ceiling freezes promotions. Operator action: `gludd ornith self-improve --freeze-promotions`. |
| Wall-clock timeout | `timeout` in `OrnithMCPClient` | Abort, revert worktree, `outcome_status=timeout`. §8.4. |

---

## 11. Self-improvement loop — how gludd measures Ornith is actually helping

Three orthogonal signals, all derived from the `ornith_training_pairs` table:

1. **Task-level outcome** — for each dispatched Ornith task: `success|failure|timeout|blocked`.
   Success = the patch passed `make gate` on the target worktree AND a human (or higher-tier
   review agent) marked it `done`. Tracked as a rolling 30-day success rate per
   `(task_type, model_sha)`.
2. **Token efficiency** — `tokens_consumed` vs. the gludd-baseline (the existing sonnet dispatch
   path on the same task class). Ornith must beat baseline by ≥X% to justify GPU cost.
3. **Regression rate** — when a promoted Ornith-generated scaffold (a new ansible role in
   `.gludd/collections/`) causes a previously-green test to fail, that's a regression. Rate is
   `regressions / promoted_scaffolds`. Hard ceiling: if >5%, freeze promotions for a human review.

These three metrics ARE the eval harness Ornith's RL trainer consumes offline. Gludd never runs
the trainer; it ships labeled `(scaffold, outcome, reward)` pairs (via
`OrnithTrainingRepo.export_dataset`) to a training job the operator runs separately out-of-band —
the bidirectional seam.

---

## 12. Permission / sandbox / audit surface (consolidated)

| Surface | Existing gludd component | Change required for Ornith |
|---|---|---|
| Capability intersection | `src/general_ludd/security/permissions.py:PermissionSpec.intersect` | None — already correct. Spec template ships at `config/permissions/agent-ornith.yml`. |
| Sandbox fail-closed policy | `src/general_ludd/event_loop/loop.py` (UNSANDBOXED warning) | Add `REQUIRE_SANDBOX_FOR="ornith"`; for Ornith-emitted code, hard-deny instead of warn. See §14.2. |
| Audit log | `ornith_training_pairs` table (`src/general_ludd/db/models.py:915`) | Already adds `scaffold_hash`, `model_sha`, `tokens_consumed`. Migration: `alembic/versions/014_add_ornith_training_pairs.py`. |
| STS | existing STS mint | Scope new tokens to `agent:ornith` principal. |
| MCP server registry | opencode plugin config | New `OrnithMCPProvider` (`src/general_ludd/ornith/mcp_server.py`). |
| HumanTodo escalation | `HumanTodo` model + `POST /api/human-todos` | Reuse unchanged — the review gate (§6.1 step 4). |
| Router | `src/general_ludd/routers/ornith.py` | New; exposes solve/pairs/export/status. |
| CLI | `src/general_ludd/cli_ornith.py` | New; `gludd ornith {solve\|improve\|pairs\|export\|status\|self-improve}`. |
| Ansible module | `collections/.../modules/gludd_ornith.py` | New; playbook-callable wrapper around `solve`. |
| Self-improve role | `collections/.../roles/ornith_self_improve/` | New; rolls new model SHAs back into `ornith_model_sha`. |

---

## 13. MCP server surface

The MCP server (`src/general_ludd/ornith/mcp_server.py`) exposes the Ornith tools gludd speaks
both as a server (for opencode plugins / external agents) and as a client (the daemon dispatching
Ornith via `OrnithMCPClient`). The schema:

```yaml
server: gludd.ornith
tools:
  - name: ornith_solve
    description: >
      Dispatch a code-gen sub-task to a local Ornith endpoint under a per-task
      PermissionSpec. Returns a patch + scaffold_hash + outcome.
    inputSchema:
      type: object
      required: [task_description, target_files]
      properties:
        task_description: { type: string }
        target_files: { type: array, items: { type: string } }    # MUST be inside an existing worktree
        max_iterations: { type: integer, default: 50 }
        max_tokens_per_call: { type: integer }
        timeout_seconds: { type: integer, default: 600 }
        requested_permissions:                                     # what the scaffold thinks it needs
          type: object
          properties:
            file_prefix: { type: string }
            net_hosts: { type: array, items: { type: string } }
        model: { type: string, default: "deepreinforce-ai/Ornith-1.0-9B" }
    outputSchema:
      type: object
      properties:
        patch_path: { type: string }
        scaffold_hash: { type: string }
        outcome: { type: string, enum: [success, failure, timeout, blocked] }
        effective_permission_spec: { type: object }
        pair_id: { type: string }     # FK into ornith_training_pairs

  - name: ornith_improve
    description: >
      Submit a curated (scaffold, outcome) pair to the feedback store for the
      offline RL trainer. Does NOT mutate weights.
    inputSchema:
      type: object
      required: [pair_id, feedback]
      properties:
        pair_id: { type: string }                                   # the scaffold being evaluated
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
    description: Current Ornith endpoint health, last-30d success rate per model_sha, queue depth.
    mimeType: application/json

prompts:
  - name: ornith_meta
    description: >
      System-prompt fragment injected when a gludd agent dispatches to Ornith —
      restates the permission boundary, the failure budget, and the audit contract.
```

`ornith_solve` is the only entry point that lets arbitrary agent code invoke Ornith. Every call
goes through: (1) capability intersection, (2) sandbox detection, (3) `OrnithTrainingPairModel`
row insert, (4) inference under the bounds in §8.4, (5) outcome feedback via `OutcomeObserver` —
in that order.

---

## 14. Open questions

1. **`exec:` capability scheme.** The current `PermissionSpec` covers `file:` and `net:` but not
   "which binaries may be executed." Ornith-emitted code wants to run `python3`, `git`, `make` —
   but not `curl`, `nc`, or arbitrary binaries. Do we extend `PermissionSpec` with an `exec:`
   scheme (and update all 7 sandbox backends), or rely on Landlock's `EXECUTE` flag + bubblewrap's
   read-only `/usr/bin` bind? **Recommendation:** extend the scheme — `EXECUTE` alone is per-file,
   not per-binary-name, and bubblewrap's bind-mount trick doesn't generalize to macOS/Windows.

2. **Fail-closed default vs. fail-open.** The daemon's existing contract is fail-open
   ("UNSANDBOXED with a warning"). Hard-denying for Ornith creates a precedent. Should we
   generalize to a per-principal `fail_closed: bool` on `PermissionSpec`? Likely yes, but it's a
   contract change that needs its own design pass — see [SANDBOX_BACKENDS.md](SANDBOX_BACKENDS.md).

3. **Training-data export licensing.** Gludd's `(scaffold, outcome)` pairs may include proprietary
   customer code. Shipping them to an offline RL trainer (even operator-run) needs a redaction
   layer and a license review. Out of scope for this doc but blocks Phase 3.

4. **Model versioning & reproducibility.** When Ornith publishes a new checkpoint, the success-rate
   baseline shifts. How do we pin a baseline per model SHA for the regression ceiling? Probably a
   per-SHA eval pass on the held-out set on each checkpoint bump (tracked in
   `OrnithTrainingPairModel.model_sha`).

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

## 15. References

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
- Gludd PermissionSpec — `src/general_ludd/security/permissions.py`
- Gludd sandbox backends — `src/general_ludd/security/sandboxes/`
- Gludd project-collection precedence — AGENTS.md "Project-Collection Precedence Contract"
- Cross-linked design docs:
  - [PERMISSION_SYSTEM.md](PERMISSION_SYSTEM.md) — the `agent:ornith` capability
  - [SANDBOX_BACKENDS.md](SANDBOX_BACKENDS.md) — how the Ornith subprocess is sandboxed
  - [PROJECT_COLLECTIONS.md](PROJECT_COLLECTIONS.md) — how promoted scaffolds become bundled
  - [HUMAN_TODOS.md](HUMAN_TODOS.md) — the review gate
