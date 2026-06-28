# Feature: langchain/langgraph Ansible libraries + model-backed role workflows

Requested 2026-06-25: "create Ansible libraries to use langchain and langgraph, and role
workflows that utilize these + the dynamic variables we expose, to make decisions and do
jobs with models." (This SUPERSEDES the cancelled langchain/langgraph removal plan — they
are now KEEP-and-extend.)

## Build status (2026-06-25)

**BUILT (new files, gate-clean):**
- **Modules**: `gludd_langchain_generate.py`, `gludd_langgraph_workflow.py`,
  `gludd_langgraph_decision.py` (collection `plugins/modules/`) + `module_utils/gludd.py`
  helpers `strip_code_fences`/`parse_structured`.
- **Role**: `roles/langgraph_decision/` (tasks/defaults/meta); **Playbooks**:
  `playbooks/langgraph_decide.yml`, `playbooks/langchain_generate.yml`.
- **Daemon endpoint**: `POST /admin/models/workflow` in `routers/models.py`
  (+ `tests/unit/test_models_workflow_endpoint.py`).
- **Tests**: `tests/integration/test_langgraph_decision_feature.py` (36 pass),
  `test_playbook_registry.py` updated to register the 3 modules (168 pass),
  provider-registry factory `ProviderRegistry.from_profiles()` + test.
- **Molecule** scenarios for the workflow module + role (test-only).
- **3 adversarial-review defects FIXED**: decision task no longer passes unsupported
  `work_type`/`quality_threshold`; workflow module reads `resp.get('prompt')`; role sources
  `quality_score` from the workflow result.

**POST-COMMIT WIRING (deferred — these files are in the pending alpha.4 commit, or need the
live-call fix):**
1. **CRITICAL** — wire `ProviderRegistry.from_profiles(profiles)` into `daemon.py:899`,
   `worker/app.py:70`, `models.py:124/431/571` (fixes "No provider registry configured" —
   without it NO live model call works, existing or new). See
   `docs/audit/POST_ALPHA4_SECURITY_FINDINGS_2026-06-25.md` CI-1.
2. Route a `work_type` to the new playbook in `_WORK_TYPE_PLAYBOOK_MAP`
   (`event_loop/loop.py:142`) + consider `_GENERATION_WORK_TYPES` exclusion
   (`job_invocation.py:30`) to avoid double model calls. Confirm default-routing change with
   the operator first.
3. Add new playbooks to the relevant queue `allowed_playbooks` (`schemas/queue.py`).
4. `/admin/models/call` now (being) extended to accept `system` (was dropped server-side).

## Architecture decision (from verified recon)

**Server-side engine + thin stdlib Ansible modules, with a local-first fallback.**

Why not import langchain/langgraph directly in the Ansible module:
- The runner env-scrub (`ansible/core_runner.py:435-484`) strips `ZAI_API_KEY`/`OPENAI_*`/
  `AWS_*` before module execution → an in-module `ChatOpenAI()` would have no credentials.
- Modules may run on remote/SSH nodes without the deps; every existing `gludd_*` module is
  deliberately stdlib-only (`urllib` via `GluddClient`) and brokers heavy work to the daemon.
- The daemon already holds creds + `ModelGateway` (routing, retry, circuit-breaker, usage
  accounting, SSRF guard). Reusing it inherits all of that.

So:
1. **Daemon-side**: a real `langgraph.StateGraph` engine (new `models/langgraph_workflow.py`,
   distinct from the hand-rolled `langgraph_gateway.py`) + a langchain `ChatOpenAI` call path
   reusing `ModelGateway`. Exposed via a NEW endpoint, e.g. `POST /admin/agents/graph-run`
   (mirrors the existing `POST /admin/models/call` that `gludd_model_call` uses).
   NOTE: the gateway has a known wiring gotcha — production factories pass
   `provider_registry=None`; the new endpoint/engine MUST build a `ProviderRegistry` and
   `register_provider("openai","langchain_openai","ChatOpenAI")` before constructing the
   gateway (import name `langchain_openai`, underscore).
2. **Ansible modules** (`collections/ansible_collections/general_ludd/agent/plugins/modules/`):
   - `gludd_langchain_generate.py` — structured langchain generation by `model_profile`.
   - `gludd_langgraph_run.py` — runs the daemon StateGraph workflow; returns the decision dict.
   Both stdlib-only (`GluddClient`), `local-first-then-HTTP` like `gludd_agent_run` (try the
   in-process gateway/graph when importable + creds present, else POST to the daemon). No
   registration step — just the files. `ok_result`/`error_result` from `module_utils/gludd.py`.
3. **Role** (`collections/.../roles/langgraph_decision/`): validate vars → artifact dir →
   block/rescue/always; consume `prompt_text`/`model_profile`/`work_type`/`todo_id` (each
   `default()`-guarded — only `model_response` is guaranteed as an extravar today); call the
   modules; branch on the returned decision; surface the result via a task that succeeds
   (rides the `runner_on_ok` event `result` — on-disk artifacts are deleted, not returned).
4. **Playbook** (`playbooks/model_decision.yml`): includes the role; wired into
   `_WORK_TYPE_PLAYBOOK_MAP` (`event_loop/loop.py:142`) for a work_type (candidate: `code`/
   `bug_fix`/`refactor`/`review`, or a new `model_decision`); add to the queue's
   `allowed_playbooks` (`schemas/queue.py`); consider excluding that work_type from
   `_GENERATION_WORK_TYPES` (`job_invocation.py:30`) to avoid a double model call.
5. **Tests** (project-root `tests/`): unit tests for the modules (mock `GluddClient`/gateway),
   extend `tests/integration/test_playbook_registry.py` for the new playbook+modules, and a
   molecule scenario if feasible. `make ansible-syntax` + targeted unit tests to validate.

## Dynamic-var contract (for the role)
Reference top-level, NOT nested: `{{ model_response }}` (guaranteed for generation work_types),
`{{ prompt_text | default('') }}`, `{{ model_profile | default('default') }}`,
`{{ work_type | default('code') }}`, `{{ todo_id | default('none') }}`. Budget keys are spread
flat from `budget_context` and must be `default()`-guarded.

## Status
Design specs (full file contracts + langgraph State schema) in flight; implementation follows
directly against this plan once they land.
