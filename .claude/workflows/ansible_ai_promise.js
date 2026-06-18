export const meta = {
  name: 'ansible-ai-promise-feature',
  description: 'Build an Ansible scatter-gather "promise" role: fan out parallel AI-model calls, barrier-join until all (or a required subset) return',
  whenToUse: 'When building or revising the gludd Ansible parallel-AI-call promise/barrier role (ai_parallel_dispatch).',
  phases: [
    { title: 'Design', detail: '3 independent role-API designs, judged' },
    { title: 'Build', detail: 'implement the chosen design in a worktree, commit via commit-bootstrap' },
    { title: 'Verify', detail: 'adversarial review of barrier/required-subset/timeout/budget semantics' },
    { title: 'Fix', detail: 'patch any blocking issue on the same branch' },
  ],
}

// --- Discovery facts (already mapped 2026-06-17; baked in so this workflow is self-contained on restart) ---
const FACTS = [
  'EXEC ENGINE: gludd runs ansible-core PlaybookExecutor in a forked child (CoreAnsibleRunner._execute_with_core, src/general_ludd/ansible/core_runner.py ~L431-542) — NOT ansible-runner/subprocess. Native async:/poll:/async_status AND meta: flush_handlers WORK (same engine as ansible-playbook). connection: local; async status files at ~/.ansible_async on the worker host. Total wall-clock is bounded by GLUDD_PLAYBOOK_TIMEOUT (default 300s; SIGKILL on the fork-child group) — every async: timeout PLUS the barrier retries*delay MUST fit inside it.',
  'MODEL MODULE: general_ludd.agent.gludd_model_call ALREADY EXISTS as a normal MODULE (forks -> async-compatible). args: prompt(required), model_profile OR route_task_type (mutually exclusive), max_tokens(2048), daemon_url(http://localhost:8000), psk(no_log), timeout(120). returns: text, model_profile_id, usage{prompt_tokens,completion_tokens,total_tokens}; changed=true. It POSTs /admin/models/call on the daemon. There is also gludd_agent_run (tool-loop). NO action plugins exist. Use gludd_model_call as the per-call task for the fan-out.',
  'GATEWAY BUDGET: call/response mechanics are thread-safe, BUT the budget cap is TOCTOU under concurrency (check_budget then record_spend not atomic) and the generation path passes no estimated_cost/budget_remaining, so the pre-call gate is bypassed. THEREFORE the role MUST cap in-flight concurrency (batch the fan-out via loop + max_in_flight), never fire unbounded.',
  'PRIOR ART: none. No async:/poll:/async_status/meta: flush_handlers/strategy: free anywhere in the collection. Build fresh.',
  'ROLE TEMPLATE: mirror collections/ansible_collections/general_ludd/agent/roles/implement_change/ . Layout: tasks/main.yml, defaults/main.yml (all vars commented), meta/main.yml (galaxy_info, min_ansible_version "2.14", license MIT, dependencies []). No handlers/ dir exists yet (this role will add one for the flush_handlers variant). register/until/retries/delay are already used in molecule prepare.yml (uri /healthz until status==200 retries 20 delay 0.5).',
  'MOLECULE: molecule/playbooks/role_<name>/ with molecule.yml + default/{prepare.yml,converge.yml,verify.yml}. converge = one play on localhost, connection: local, gather_facts: false, single include_role task. provisioner env ANSIBLE_COLLECTIONS_PATH=${MOLECULE_PROJECT_DIRECTORY}/collections + a unique GLUDD_MOCK_PORT. verify slurps a JSON artifact and asserts fields. A mock daemon lives at molecule/mock_daemon/server.py.',
  'COMMIT: make-only Bash repo. Commit the feature branch with the command  make commit-bootstrap MSG=...  (the sanctioned NO-GATE commit: fast pre-commit hooks only — ruff/secrets/conflict/collection). NEVER run make ship / make gate / full make test (gate stampede). Use Edit/Write for files. Do NOT spawn sub-agents.',
].join('\n\n')

const COLL = '/Users/shawnwilson/gludd/collections/ansible_collections/general_ludd/agent'

const DESIGN = {
  type: 'object', additionalProperties: false,
  required: ['approach','tasks_main_yaml','handler_variant_yaml','required_subset_mechanism','concurrency_cap_mechanism','timeout_notes','risks'],
  properties: {
    approach: { type: 'string' },
    tasks_main_yaml: { type: 'string', description: 'Concrete tasks/main.yml: fan out N gludd_model_call tasks with async + poll 0 (register job ids), then a barrier looping async_status with until/retries/delay until ALL finish; gather text results into a fact keyed by call name.' },
    handler_variant_yaml: { type: 'string', description: 'The handler-based variant (handlers/main.yml + a tasks file): notify a handler per dispatch + meta flush_handlers as the join, plus an honest note on its result-collection limits vs async.' },
    required_subset_mechanism: { type: 'string', description: 'How a caller proceeds once a REQUIRED subset returns (e.g. required_keys var; the until condition only checks the required job ids).' },
    concurrency_cap_mechanism: { type: 'string', description: 'How in-flight parallelism is capped (batch the fan-out via loop + max_in_flight) to avoid the gateway budget TOCTOU.' },
    timeout_notes: { type: 'string', description: 'How async timeout + retries*delay stay under GLUDD_PLAYBOOK_TIMEOUT; partial-timeout behavior.' },
    risks: { type: 'array', items: { type: 'string' } },
  },
}

phase('Design')
const angles = [
  'MVP-first: smallest correct native-async fan-out plus barrier',
  'robustness-first: partial-failure, required-subset, timeout, concurrency cap',
  'ergonomics-first: clean role API plus the handler/flush_handlers variant the user named',
]
const designs = (await parallel(angles.map(function (angle, i) {
  return function () {
    return agent(
      'gludd repo. Design an Ansible role (ai_parallel_dispatch) that fans out MULTIPLE AI-model calls in PARALLEL and barrier-joins until all (or a required subset) return — a promise system. Design lens: ' + angle + '\n\nGROUND-TRUTH FACTS (obey them):\n' + FACTS + '\n\nHard requirements: (1) PRIMARY mechanism = Ansible NATIVE async: each call is a gludd_model_call task with async set and poll 0 (returns an ajob id), then async_status with until/retries/delay awaits them — this works in this engine. (2) ALSO deliver the handler + meta flush_handlers variant the user explicitly asked about, with an honest note on where it fits vs async. (3) support a REQUIRED-subset join. (4) CAP in-flight concurrency (gateway budget is TOCTOU under unbounded fan-out). (5) keep async timeout + barrier polling under GLUDD_PLAYBOOK_TIMEOUT. Produce concrete, paste-ready YAML in the schema fields.',
      { label: 'design:' + i, phase: 'Design', schema: DESIGN, effort: 'high' }
    )
  }
}))).filter(Boolean)

const chosen = await agent(
  'You are the design judge for an Ansible parallel-AI-call promise role. Pick the STRONGEST design and graft the best ideas from the others into one final spec. Favor: correct native-async barrier semantics, a working required-subset join, a real in-flight concurrency cap, and timeout safety under GLUDD_PLAYBOOK_TIMEOUT — while still delivering the handler/flush_handlers variant.\n\nFACTS:\n' + FACTS + '\n\nCANDIDATE DESIGNS:\n' + designs.map(function (d, i) { return '=== design ' + i + ' ===\n' + JSON.stringify(d, null, 2) }).join('\n\n') + '\n\nReturn the FINAL chosen design (same schema fields), paste-ready.',
  { label: 'design:judge', phase: 'Design', schema: DESIGN, effort: 'high' }
)
log('Design chosen: ' + (chosen && chosen.approach ? chosen.approach.slice(0, 80) : 'n/a'))

phase('Build')
const built = await agent(
  'gludd repo, make-only Bash. Implement this Ansible parallel-AI-call promise role end-to-end and COMMIT it. Use Edit/Write for files; commit with the command  make commit-bootstrap MSG=...  ONLY (NEVER make ship/gate/full test). Do NOT spawn sub-agents.\n\nFACTS:\n' + FACTS + '\n\nFINAL DESIGN TO IMPLEMENT:\n' + JSON.stringify(chosen, null, 2) + '\n\nDeliver under ' + COLL + '/roles/ai_parallel_dispatch/ (mirror the implement_change role layout): tasks/main.yml (native-async fan-out over a list of model-call specs + barrier join via async_status until/retries/delay + required-subset gate + in-flight concurrency cap via batching), handlers/main.yml plus tasks/handler_barrier.yml for the meta flush_handlers variant, defaults/main.yml (calls=[], max_in_flight, async_timeout, poll_delay, poll_retries, required_keys=[], fail_on_partial), meta/main.yml, and README.md documenting the promise semantics + the GLUDD_PLAYBOOK_TIMEOUT constraint + the budget-concurrency rationale. Each model call uses general_ludd.agent.gludd_model_call. ALSO add a lightweight pytest under tests/unit/ that loads the role tasks/main.yml as YAML and asserts the barrier structure is present (async on the dispatch task, async_status + until in the join, required_keys honored in the until/when, max_in_flight batching). Then git-add the new files and run  make commit-bootstrap MSG=feat: ai_parallel_dispatch role - Ansible native-async promise/barrier for parallel AI-model calls (+ handler/flush_handlers variant). Return the branch name, commit SHA, the file list, AND the full text of tasks/main.yml + the handler-variant file (for review).',
  { label: 'build:role', phase: 'Build', isolation: 'worktree', effort: 'high' }
)

phase('Verify')
const VERDICT = {
  type: 'object', additionalProperties: false,
  required: ['dimension','sound','issues','severity'],
  properties: {
    dimension: { type: 'string' },
    sound: { type: 'boolean' },
    issues: { type: 'array', items: { type: 'string' } },
    severity: { type: 'string', enum: ['none','low','medium','high','blocking'] },
  },
}
const lenses = [
  'BARRIER CORRECTNESS: does the play actually BLOCK until the awaited jobs finish? Verify async_status loops with until finished and sufficient retries*delay, and that results are collected. Could it proceed before completion (false join)?',
  'REQUIRED-SUBSET and PARTIAL FAILURE: does proceeding once the REQUIRED subset returns actually work, and are optional/failed/timed-out jobs handled, not silently treated as success? Does fail_on_partial behave as documented?',
  'TIMEOUT and BUDGET CONCURRENCY: do async timeout + retries*delay stay under GLUDD_PLAYBOOK_TIMEOUT (else SIGKILL mid-poll)? Does max_in_flight actually cap concurrent gludd_model_call invocations (the budget TOCTOU)? Any unbounded fan-out left?',
]
const builtText = (typeof built === 'string') ? built : JSON.stringify(built)
const verdicts = (await parallel(lenses.map(function (lens, i) {
  return function () {
    return agent(
      'Adversarially review this just-built Ansible promise role. Lens: ' + lens + '\n\nFACTS:\n' + FACTS + '\n\nBUILD RESULT (branch + key file contents):\n' + builtText + '\n\nBe skeptical — default to sound=false if the evidence does not clearly show the barrier/subset/timeout/cap works. List concrete issues with the fix needed. severity blocking = the promise/barrier is incorrect or can overshoot budget/timeout.',
      { label: 'verify:' + i, phase: 'Verify', schema: VERDICT, effort: 'high' }
    )
  }
}))).filter(Boolean)

const blocking = verdicts.filter(function (v) { return v && (v.severity === 'blocking' || v.severity === 'high') })
let fixResult = null
if (blocking.length) {
  phase('Fix')
  fixResult = await agent(
    'gludd repo, make-only Bash. The ai_parallel_dispatch role has blocking issues from adversarial review. Check out the existing feature branch (info below), apply the fixes, and re-commit with the command  make commit-bootstrap MSG=fix: ai_parallel_dispatch - address barrier/subset/timeout/budget review. Use Edit/Write; NO make ship/gate; NO sub-agents.\n\nBUILD:\n' + builtText + '\n\nBLOCKING ISSUES:\n' + blocking.map(function (b) { return '- [' + b.dimension + '] ' + (b.issues || []).join('; ') }).join('\n') + '\n\nReturn the updated commit SHA + what changed.',
    { label: 'fix:role', phase: 'Fix', isolation: 'worktree', effort: 'high' }
  )
}

return {
  chosen_approach: chosen ? chosen.approach : null,
  build: built,
  verdicts: verdicts,
  blocking_count: blocking.length,
  fix: fixResult,
}
