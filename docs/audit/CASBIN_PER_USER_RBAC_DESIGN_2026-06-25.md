# Casbin RBAC + Per-User PSK + Per-User Routing + Manager Capabilities — Design

**Date:** 2026-06-25  **Branch:** feature/alpha4-green-the-gate
**Directive (user, 2026-06-25):** replace the global `GLUDD_PSK` with Casbin roles + per-user PSK;
key per-task routing (which skills/models/prompts work best) to the **user** (borrow other users'
weights only when the user lacks their own data); add a **manager** role that grants users
read | read-write access scoped to specific projects / secrets / MCP / skills / models, plus
per-user-per-model and per-user-per-project spend limits — all exposed via `gludd` CLI options.

This doc is grounded in 4 read-only investigations (auth, routing, budget+registries, CLI) — every
seam below cites a verified file:line. Companion: the Casbin policy-model proposal (model.conf +
casbin_rule schema) lands as an appendix when its agent reports.

---

## 0. Current state (verified)

- **Auth = single shared secret, NO principal.** Middleware `daemon.py:1568-1623` +
  `security/auth.py:79-101` (`check_bearer_token` → `verify_psk` via `hmac.compare_digest`).
  Fail-closed 503 when no PSK + `require_auth` (`daemon.py:1577-1585`). Public paths are GET-only
  on an allow-list (`daemon.py:1543-1566`). **No UserModel** anywhere (`db/models.py`); `AuditEventModel.actor`
  is a free string default `"agent"`.
- **Existing authz primitive:** `AgentRegistry.can_invoke(invoker, target)` (`agents/registry.py:42-54`)
  — agent→agent only, enforced at `agents/dispatcher.py:77-90`. Returns bool (not raise).
- **Casbin NOT a dependency** (`pyproject.toml`). Must be added (+ a policy-store adapter).
- **Routing:** `AdaptiveRouter` (`scoring/router.py`) already does cross-PROJECT borrowing —
  sufficiency gate `own_sample_total < self._min_samples` (`router.py:248-250`), borrow block
  `_composite_similarity_weight` (`router.py:337-374`). Scores from `get_aggregate_scores`
  (`repository.py:808-873`, groups by prompt/model/task_type/project_id; **no user_id**). Benchmarks
  written via `recorder.record_from_trace` (`observability/recorder.py:50-112`) from an `ExecutionTrace`
  (`observability/tracer.py`) created at `loop.py:1453-1476` — **no user_id captured**.
- **Budget:** chokepoint `ModelGateway.check_budget` (`gateway.py:324-356`) raises `BudgetExceededError`;
  billed at `gateway.py:620-621`. Limits per-todo/daily/monthly (`budget_manager.py:38-40`) + per-project
  quota (`accounting/ledger.py:34-47`). **No per-user spend tracking** — usage records carry no principal.
- **Resource registries (grant targets), with stable IDs:** projects `proj-xxxxxxxx`
  (`projects/manager.py:143`); models `model_profile_id` e.g. `glm-4` (`gateway.py:253`); secrets alias
  e.g. `openai_api_key` / mount:path (`secrets/manager.py:79-97`); MCP `(server_id, tool_name)`
  (`mcp/registry.py:36-85`); skills `skill.name` global or per-project (`skills/registry.py`).
- **CLI:** argparse, hierarchical subparsers (`cli.py:241-728`), API-backed → `/admin/*` over httpx with
  PSK bearer. Add commands after `agents_parser` (~`cli.py:568-574`) + `subcommand_map` (~694-712);
  new router `routers/users.py` registered at `daemon.py:1757-1832`.

---

## 1. Target architecture

```text
 request ─► auth middleware (daemon.py:1568)
              │  resolve per-user PSK → user_id  (NEW: PSK store, hashed, constant-time)
              │  attach request.state.user_id
              ▼
            Casbin enforce(user_id, domain=project, obj=<typed>, act=read|write)  (NEW)
              │  deny → 403
              ▼
   handler ─► thread user_id into Job/Task/Trace
              ├─► ModelGateway.call_model(..., user_id)  ─► per-user spend check (NEW) + per-user routing
              ├─► secret fetch / mcp dispatch / skill invoke  ─► Casbin enforce per resource (NEW)
              └─► benchmark recorder persists user_id  ─► AdaptiveRouter prefers this user's history
```

Two orthogonal axes, deliberately **separated**:
- **Casbin = access (yes/no)** over typed resources, scoped by project-domain.
- **budget_manager = spend accounting** (per-user limits), composed at the model chokepoint.
`can_invoke` (agent→agent) **coexists** — Casbin governs the HTTP/user edge; can_invoke stays for
agent dispatch. Do not rip out a working primitive in phase 1.

---

## 2. Phase plan (dependency-ordered; each phase = one commit, CI-gated)

**P1 — Identity + per-user PSK + auth chokepoint (DEPENDENCY ROOT).**
- New `UserModel` (`db/models.py`): `user_id` (PK, `usr-xxxxxxxx`), `username`, `email`, `role`,
  `psk_hash` (argon2/bcrypt or hmac-sha256 of a high-entropy token — **never store plaintext**),
  `disabled`, `created_at`. Migration (next free rev) — additive, SQLite-safe inline (008 pattern).
- New `security/principals.py`: `resolve_user(token) -> user_id | None` (constant-time over the PSK
  store; cache by token-hash). Keep the global `GLUDD_PSK` as a **bootstrap-admin** user so the
  system is never locked out during migration (seed a `usr-bootstrap` admin from `GLUDD_PSK` if set).
- Middleware (`daemon.py:1568-1623`): after bearer extraction, resolve token→user_id; on success set
  `request.state.user_id`; on unknown token → 401. Posture stays fail-closed; default-deny.
- Tests: per-user PSK accept/reject, bootstrap-admin path, constant-time, no-PSK-in-logs (the
  EnvironmentBrief allow-list already redacts — extend the redaction set).

**P2 — Casbin enforcement over the 5 resource types.**
- Add `casbin` (+ `casbin-sqlalchemy-adapter`) to deps; `casbin_rule` table (DB-backed so managers
  mutate policy at runtime). Model = **RBAC with domains** (domain = project_id; obj = typed string
  `project:<id>` / `model:<id>` / `secret:<alias>` / `mcp:<server>:<tool>` / `skill:<name>`; act =
  `read` | `write`). `g` for user→role, `g2` optional for resource grouping. (Exact `model.conf` +
  matcher from the policy-model proposer appendix.)
- Enforce points: HTTP edge in middleware (path/method → obj/act); plus the resource chokepoints:
  model `gateway.call_model:324-356`, secret fetch (`secrets/manager.py`), mcp dispatch
  (`mcp` client call), skill invoke (`skills/registry.py`). Each: `enforce(user_id, project, obj, act)`
  → deny raises a typed `AccessDenied` (403 at the edge; logged, never silent).
- `can_invoke` coexists unchanged (documented boundary).

**P3 — Per-user routing (mirror cross-project borrowing).**
- Add nullable `user_id` (String(128), indexed) to `BenchmarkResultModel` (`db/models.py:590-635`) +
  composite indices `(user_id, task_type)` and `(user_id, project_id, task_type)`; migration patterned
  on `009_add_benchmark_project_id.py` (no FK — principal string).
- Thread user_id request→job→trace: add `user_id` to `ExecutionTrace` (`tracer.py`) captured at
  `loop.py:1453-1476`, persisted in `recorder.record_from_trace` data dict (`recorder.py:86`).
- `get_aggregate_scores` (`repository.py:808-873`): add `user_id` kwarg → group_by + filter + result
  key (backward-compat: omit when None).
- `AdaptiveRouter` (`router.py`): add `user_id` + `enable_cross_user_borrowing` ctor params; in BOTH
  scoring paths (`_get_best_from_history:213-283` and `_get_best_with_embeddings:425-514`) replicate the
  borrow gate — **prefer this user's own history; when `own_user_samples < min_samples`, fall back to
  global/other-users' aggregate with a flat `cross_user_weight` (≈0.7×)** and a `reason=
  "inherited_global_user_history"` tag. Flat weight (not decay) because users have no relationship graph.
  Default the flag ON (the directive wants per-user keying), but degrade gracefully to global when a
  user has no data yet — exactly the "unless better data isn't yet available" clause.

**P4 — Per-user spend limits (compose with Casbin, separate store).**
- `UserSpendPolicy` in config (`config/user_config.py:115` neighborhood) + a per-user runtime ledger in
  `budget_manager.py`: `per_model[model_id]`, `per_project[project_id]`, `total_daily`. Thread `user_id`
  into `gateway.call_model` (callers: `daemon.py:1194`, `routers/models.py:554`, `worker/app.py:134`)
  → `_check_user_budget(user_id, model_id, project_id, estimated_cost)` BEFORE `check_budget` (line 433).
  Record per-user spend at the billing point (`gateway.py:620-621`) + capture user_id in metrics
  (`metrics/collector.py`). Reporting endpoint `/admin/users/{user_id}/spend`.
- ⚠ Carry the budget-review findings (in flight) into this: under-estimate bypass, reservation leak on
  failure, TOCTOU over-spend — per-user limits must not reintroduce them.

**P5 — Manager CLI (`gludd user` / `gludd grant` / `gludd limit`).**
- New `routers/users.py` (`/admin/users`, `/admin/grants`, `/admin/limits/{user_id}`) registered at
  `daemon.py:1757-1832`; all manager-only (Casbin role `manager`/`admin`). CLI subcommands after
  `agents_parser` per the mapped pattern:
  - `gludd user create <username> --email --role [viewer|editor|manager|admin] --initial-psk`
  - `gludd user list | info <id> | disable <id>`
  - `gludd grant <user_id> --resource-type [project|secret|mcp|skill|model] --resource-id <id> --permission [read|read-write]`
  - `gludd grant list <user_id>` / `gludd grant revoke <user_id> <grant_id>`
  - `gludd limit set <user_id> --per-model glm-4=5.00 --per-project proj-abc=100.00 --total-daily 50.00`
  - `gludd limit show <user_id>`
  CLI authenticates as the calling manager's own PSK (bearer); grants → casbin_rule rows; limits →
  UserSpendPolicy rows. Output: tables/JSON per existing convention; exit 1 on 4xx.

---

## 3. Key decisions

1. **PSK at rest = hashed, never plaintext**; resolution constant-time; tokens redacted from logs +
   EnvironmentBrief. Bootstrap-admin from `GLUDD_PSK` prevents lockout during migration.
2. **Casbin model = RBAC-with-domains, DB-backed policy** (runtime-mutable by managers via CLI).
   Typed `obj` strings unify 5 resource types under one policy table.
3. **Access vs spend separated** — Casbin says yes/no; budget_manager meters. Composed only at the
   model chokepoint.
4. **can_invoke coexists** (agent→agent), not subsumed in v1.
5. **Per-user routing degrades to global** when the user is data-thin — satisfies "don't use other
   users' weights unless better data isn't yet available" without cold-starting every new user to zero.
6. **Default-deny** posture once per-user PSK is on; additive migrations only; CI is the gate.

## 4. Build sequencing under the concurrency governor

P1 is the root (everything needs user_id + identity). P3 (routing) and P4 (spend) both thread user_id
but touch DISJOINT files from P1's auth core (router/repo/recorder vs daemon-middleware/security) — they
can build in parallel AFTER P1's `user_id` request-context exists. P2 (Casbin) and P5 (CLI) gate on P1.
daemon.py is a shared hot file (P1, P2, P5 all touch it) — serialize daemon.py edits to avoid the
file contention flagged earlier; prefer landing P1's middleware edit, then P2/P5 router registrations.
Hold the public push for explicit user go.
