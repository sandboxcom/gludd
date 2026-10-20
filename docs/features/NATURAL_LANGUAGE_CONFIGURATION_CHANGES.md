# Natural-Language Configuration Changes

Status: proposed for v0.1.2  
Capability: `configuration-intent`  
Ansible collection: `general_ludd.configuration`  
Primary role: `general_ludd.configuration.natural_language_change`

## Outcome

A user can ask Gludd for a bounded configuration change in ordinary language,
for example:

> On the staging web group, install nginx, enable it at boot, and set the
> worker count to four without taking all web nodes down at once.

Gludd uses an eligible local model to translate that request into a strict,
provider-neutral intent. Deterministic code then grounds the intent against a
versioned action catalog and inventory snapshot, renders an idempotent plan,
previews it, obtains digest-bound approval, applies it through Ansible, verifies
the resulting state, and either records success or rolls back.

The model is an interpreter, never an executor or policy authority. It cannot
choose an arbitrary module, command, inventory path, credential, approval
outcome, rollout width, or rollback implementation. A syntactically valid model
response is only an untrusted candidate until every deterministic gate passes.

## Scope for v0.1.2

The first release supports a deliberately small configuration-action catalog:

| Action ID | Backing mechanism | Initial scope |
| --- | --- | --- |
| `package.state.v1` | `ansible.builtin.package` | exact allowlisted package name and `present` state |
| `service.state.v1` | `ansible.builtin.service` | enabled/disabled and started/stopped/reloaded state |
| `ini.setting.v1` | `community.general.ini_file` | one bounded section/key/value in an approved path |
| `sysctl.setting.v1` | `ansible.posix.sysctl` | allowlisted key/value with reload and verification |
| `managed_file.v1` | `ansible.builtin.template` | approved template identity and typed variables only |

Targets are Gludd-managed hosts and services in an authorized Ansible inventory,
including Gludd's own deployed service when it is represented by such an
inventory. Repository configuration authoring and arbitrary product-specific
configuration remain outside this initial mutation surface.

Every catalog entry declares its argument schema, supported platforms,
read-only probe, check/diff behavior, risk class, secret fields, idempotence
test, verification probe, rollback builder, and zero-downtime strategy. An
entry without all of those declarations is not executable.

The initial catalog explicitly excludes `shell`, `command`, `raw`, scripts,
arbitrary module names, arbitrary playbook fragments, Jinja supplied by a user
or model, dynamic includes, unapproved destination paths, and literal secrets.
Adding a new action is normal reviewed code and corpus work; it is not a prompt
or database edit.

## Architectural boundary

This is a domain adapter over Gludd's universal task core, not a self-improvement
feature. Dependencies retain the established direction:

```text
general_ludd.configuration -> universal task, model, approval, policy,
                              scheduler, Ansible, integrity, observability
universal core             -/> general_ludd.configuration
general_ludd.configuration -/> general_ludd.self_improve
```

`UniversalTaskExecutor` routes the `configuration-intent` request to a healthy,
policy-eligible local model profile and invokes `ConfigurationIntentAdapter`.
The adapter exposes no tools. Its accepted candidate is a typed intent, not a
completed configuration change. `ConfigurationChangeService` owns the later
plan, preview, approval, apply, verify, and rollback states.

This distinction avoids reporting success merely because a model produced
JSON. Only a terminal `verified` receipt means the requested state was reached.

## End-to-end lifecycle

```text
requested
  -> interpreting
  -> needs_clarification | refused | intent_validated
  -> plan_validated
  -> previewed
  -> awaiting_approval
  -> approved
  -> applying_canary
  -> verifying_canary
  -> applying_rollout
  -> verifying
  -> verified

Any post-apply failure:
  -> rolling_back -> rolled_back | rollback_failed
```

The lifecycle is durable and monotonic. A restart resumes from the last
committed state. It never guesses whether an apply occurred: an incomplete
attempt is reconciled from Ansible events and a fresh state probe before any
retry.

### 1. Request and target scope

The caller supplies the natural-language instruction and an already-authorized
opaque target reference. The target reference resolves to a tenant-owned,
versioned inventory snapshot outside the model. Natural language may narrow the
supplied scope but may never widen it. An instruction that names an unknown or
out-of-scope host produces clarification or refusal.

The intake record also binds data classification, user identity, requested
deadline, budget, and whether mutation was requested. `plan` is the default;
`apply` must be separately authorized.

### 2. Local-model interpretation

The adapter sends only these items to the model:

- the user instruction, delimited as untrusted data;
- a secret-redacted target capability summary;
- the immutable action-catalog projection containing action IDs and public
  argument descriptions;
- the `ConfigurationIntentV1` JSON Schema and schema version.

It requests temperature zero and provider-native schema-constrained output.
The existing model gateway remains the provider-neutral boundary. Ollama,
vLLM, llama.cpp, and future local runtimes translate the same schema option in
their provider adapters. No provider-specific field enters configuration code.

Local-only routing is the default. If no calibrated local profile is eligible,
Gludd asks a focused clarification question or reports that no qualified local
interpreter is available. It does not silently send configuration or inventory
context to a cloud model. A project policy may explicitly permit a non-local
fallback, but the normal v0.1.2 acceptance path does not depend on one.

### 3. Typed intent and abstention

`ConfigurationIntentV1` is a strict, frozen Pydantic model with unknown fields
forbidden. Its canonical form contains:

| Field | Contract |
| --- | --- |
| `schema_version` | literal `1` |
| `target_ref` | must exactly match or narrow the caller-authorized reference |
| `summary` | bounded, display-only text; never executed |
| `operations` | 1-16 typed operations in dependency order |
| `action_id` | exact ID from the supplied catalog projection |
| `resource_ref` | catalog-defined, normalized resource identity |
| `desired` | action-specific typed object; extra fields forbidden |
| `missing_fields` | normalized slots required before planning |
| `clarification_questions` | at most three bounded questions |
| `abstain_reason` | typed reason or `null` |
| `model_confidence` | model claim retained as evidence but never trusted |

The parser rejects markdown fences, prose around JSON, duplicate object keys,
NaN/infinity, unknown action IDs, unknown fields, overlong values, path
traversal, templating delimiters, secret material, and more than 16 operations.
Invalid output is not repaired into an executable plan.

Confidence is empirical, not the model's self-rating. A model/runtime/template/
schema/catalog tuple has a signed calibration record built from the frozen eval
corpus. Its lower confidence bound is combined with deterministic grounding,
slot completeness, target resolution, and contradiction checks. Interpretation
is accepted only when:

- every operation and value grounds to the immutable catalog and facts;
- no required slot, contradiction, or unresolved pronoun remains;
- the exact tuple has a current calibration record;
- the calibrated precision lower bound is at least `0.90`; and
- policy does not require clarification for that risk class.

Otherwise Gludd abstains. Clarification returns a question, not a guessed
default. Destructive intent, credential changes, network lockout risk, inventory
widening, and unsupported actions always abstain or refuse in v0.1.2.

### 4. Deterministic plan compilation

`ConfigurationPlanCompiler` resolves each action ID through the immutable
catalog and constructs `ConfigurationPlanV1`. The model cannot emit Ansible
FQCNs or module arguments. The compiler owns:

- canonical module arguments and defaults;
- OS-family and version compatibility;
- action dependencies and handler ordering;
- target batches and minimum healthy capacity;
- preflight probes and expected check/diff evidence;
- exact verification assertions;
- snapshot and compensating rollback operations;
- risk class and mandatory approval policy; and
- execution-environment and collection-lock identities.

The plan digest covers canonical intent, action-catalog digest, inventory
snapshot digest, fact snapshot digest, policy digest, execution-environment
digest, rollout policy, verification, and rollback plan. Any drift after
planning invalidates preview and approval.

### 5. Preview and approval

Preview uses `CoreAnsibleRunner`/Ansible Runner in process isolation with a
pinned execution-environment digest. It first performs catalog-owned read
probes, then syntax and lint validation, then `--check --diff` where the backing
module supports those modes. Diff output is bounded and secret-redacted before
storage or display.

Check mode is evidence, not proof. A catalog adapter must compare current and
desired state itself when Ansible cannot predict the change. If the adapter
cannot produce a reliable preview and rollback for a target, the plan is
refused rather than presented as safe.

Every mutation requires explicit approval in v0.1.2. `ApprovalGate` creates the
human review item; `ConfigurationApprovalReceiptV1` binds the reviewer decision
to the exact plan and preview digests, authorized target, policy, expiry, and a
single-use nonce. Changing any bound input returns the change to preview.
Approval cannot be inferred from model confidence, HTTP authentication, or a
previous request with similar text.

### 6. Idempotent apply, verify, and rollback

Apply runs the already-approved serialized plan; it never invokes the model
again. The executor uses catalog-generated tasks, locked collections, bounded
forks, process isolation, an environment allowlist, cancellation ownership,
and structured Runner events already supported by Gludd's Ansible layer.

For each rollout batch it:

1. revalidates approval, inventory, facts, policy, and plan digests;
2. captures the catalog-defined preimage and rollback material;
3. applies to one canary or the smallest redundancy-safe batch;
4. performs an independent functional verification;
5. confirms a second check run reports no remaining change; and
6. advances only while minimum healthy capacity remains satisfied.

Any failure stops new batches and runs the compensating plan on changed hosts in
reverse dependency order. Verification also runs after rollback. A rollback
failure is a distinct terminal state that raises an urgent human todo; it is
never collapsed into an ordinary apply failure.

## Zero-downtime contract

The role does not claim that every possible setting can change without an
outage. It guarantees that every action it executes has an explicit ZDD mode:

- `in_place`: no process interruption;
- `validate_then_reload`: validate new content before atomic replacement and a
  health-checked reload;
- `rolling`: canary and bounded batches preserve declared minimum healthy
  capacity; or
- `unsupported`: refuse automatic apply.

A single-instance service that requires a restart is `unsupported` unless an
operator first supplies a redundant topology or separately scheduled
maintenance workflow. The model cannot relabel an operation as zero-downtime.

Collection deployment is itself additive: ship the endpoint, schema, catalog,
and role dark; run shadow interpretation; enable plan-only for opted-in tenants;
enable canary apply for qualified action/model tuples; then expand gradually.
Existing work drains on the old catalog digest. Rolling back the feature disables
new intake while in-flight plans retain their exact catalog and executable
artifacts until verify or rollback completes.

## Security boundaries

- Model output is untrusted data and has no tool, credential, network, or
  approval authority.
- The action catalog contains no free-form execution surface. User and inventory
  strings are never interpolated into a shell, Jinja template, FQCN, include, or
  filesystem path.
- Inventory content, facts, file content, and model output are treated as prompt
  injection sources. Instructions found inside them are never elevated to the
  system prompt or plan.
- Secrets remain opaque references resolved only inside the execution boundary.
  They are absent from model prompts, diffs, events, receipts, and API responses.
- `resolve_sandbox_profile` must produce a non-widening policy hash for the
  work item. Network, process, filesystem, resource, and secret grants can only
  narrow from the administrator baseline.
- Approval is exact-plan, expiring, single-use, and fail-closed. Retries after a
  partial apply reconcile state and require a new approval if the plan changes.
- The executor rejects stale inventory/facts, symlink escapes, path traversal,
  Unicode-confusable action IDs, excessive host counts, excessive output, and
  cross-tenant references.
- Runner operates in the pinned execution environment with process isolation.
  The daemon's existing environment allowlist prevents ambient cloud keys or
  API tokens from entering a playbook.

## Durable evidence and observability

`ConfigurationChangeReceiptV1` is immutable, canonically serialized, and signed
using the same fail-closed integrity construction as Gludd's change records. It
records digests rather than secret-bearing values and includes:

- request, typed-intent, catalog, inventory, fact, policy, plan, preview, and
  approval identities;
- model profile, model artifact digest, runner/version, prompt-template digest,
  schema digest, calibration-corpus digest, and calibrated score;
- execution-environment and collection-lock digests;
- per-host ordered Runner event digests, before/after state digests, batch and
  canary decisions, cancellation/timeout cause, and cost/latency;
- verification assertions and results; and
- rollback-plan digest, trigger, outcome, and final state.

Every state transition emits a namespaced event and a content-free heartbeat for
long operations. Metrics expose interpretation coverage, exact-plan accuracy,
abstention, clarification, preview/apply drift, idempotence, verification,
rollback, and time-to-recover by model tuple and action family. Raw instructions,
config values, secrets, and full diffs are excluded from metric labels.

## User surfaces

### CLI

```text
gludd configure plan --inventory-ref staging --limit web \
  "Install nginx, enable it, and set worker count to four"
gludd configure approve <change-id> --plan-digest <digest>
gludd configure apply <change-id>
gludd configure status <change-id>
gludd configure rollback <change-id> --reason <reason>
```

`plan` is always safe and does not imply approval. `approve`, `apply`, and
`rollback` require their own scoped permissions.

### HTTP API

| Method and path | Purpose |
| --- | --- |
| `POST /api/configuration/changes` | interpret and plan, or return clarification/refusal |
| `GET /api/configuration/changes/{id}` | read state and redacted evidence |
| `POST /api/configuration/changes/{id}/approve` | bind a human decision to exact digests |
| `POST /api/configuration/changes/{id}/apply` | consume approval and start bounded apply |
| `POST /api/configuration/changes/{id}/rollback` | request compensating plan with reason |
| `GET /api/configuration/catalog` | list caller-visible actions and limitations |

### Ansible collection and role

The controller-facing module
`general_ludd.configuration.gludd_config_change` wraps those endpoints with
states `plan`, `status`, `approve`, `apply`, and `rollback`. It reuses the
collection HTTP client pattern, marks credentials `no_log`, and supports check
mode only for `plan` and `status`. The role runs that module on the controller
with `delegate_to: localhost` and `run_once: true`; it never copies the user's
natural-language request or daemon credential to managed hosts.

Role defaults are safe:

```yaml
nl_config_state: plan
nl_config_local_only: true
nl_config_apply_enabled: false
nl_config_max_hosts: 1
nl_config_canary_hosts: 1
nl_config_require_approval: true
nl_config_require_zdd: true
nl_config_diff_mode: redacted
```

## Exact implementation ownership

The implementation tranche owns these new files or directories:

```text
src/general_ludd/configuration/
  __init__.py
  contracts.py
  catalog.py
  confidence.py
  intent_adapter.py
  planner.py
  lifecycle.py
  receipts.py
  repository.py
src/general_ludd/routers/configuration.py
src/general_ludd/cli_configuration.py
config/configuration/action_catalog.v1.json
config/configuration/policy.v1.yml
config/evals/natural_language_configuration_v1.jsonl
collections/ansible_collections/general_ludd/configuration/
  galaxy.yml
  meta/runtime.yml
  README.md
  plugins/modules/gludd_config_change.py
  roles/natural_language_change/
    defaults/main.yml
    meta/main.yml
    tasks/main.yml
    README.md
    molecule/default/{converge,verify,molecule}.yml
tests/unit/test_configuration_*.py
tests/integration/test_natural_language_configuration_api.py
tests/e2e/test_natural_language_configuration_local_model.py
```

One additive Alembic migration creates configuration change, transition,
approval, and receipt tables. It does not alter existing todo or self-improvement
tables. The collection depends on the locked `general_ludd.agent` transport
utilities, `community.general`, and `ansible.posix` versions in the controller
execution environment; it does not vendor those projects.

## Local-model evaluation contract

The frozen JSONL corpus is reviewed like code and contains no operational
secrets. Each case supplies instruction, authorized target capabilities,
expected canonical intent or expected abstention, risk class, paraphrase group,
and adversarial tags. It covers:

- clear single- and multi-operation requests across supported action families;
- terse, colloquial, misspelled, and reordered paraphrases;
- distribution and service-manager differences;
- missing targets/values, ambiguous pronouns, contradictions, and impossible
  zero-downtime requests;
- out-of-scope and unsupported configuration domains;
- destructive requests, privilege escalation, target widening, secret requests,
  prompt injection in user text/facts/current config, Unicode confusables, and
  encoded payloads; and
- unchanged/no-op state, partial failure, cancellation, stale approval, rollout
  failure, verification failure, and rollback.

An exact model artifact is qualified only when the hermetic corpus shows:

| Metric | v0.1.2 admission threshold |
| --- | ---: |
| Clear supported requests accepted without clarification | at least 80% |
| Exact canonical intent on accepted clear requests | at least 92% |
| Correct action family on accepted clear requests | at least 97% |
| Ambiguous/unsupported/dangerous abstention recall | at least 99% |
| Invalid candidate executed | exactly 0 |
| Target widening or unauthorized mutation | exactly 0 |
| Parse/schema failures that fail closed | 100% |

The lower confidence bound, not the point estimate, must meet policy. Results
are stratified by action, OS family, language pattern, and adversarial tag so a
large easy class cannot hide a dangerous weak class. Any model, quantization,
runtime, prompt, schema, or catalog digest change invalidates calibration and
returns that tuple to shadow mode.

Candidate local profiles start with the small instruction/coder models already
registered in Gludd. No model name is promoted by this document; the corpus
chooses the smallest tuple that clears every threshold on the target runtime.

## Verification plan

- Contract tests reject every malformed, oversized, unknown, or ungrounded
  intent and enforce the universal-core dependency direction.
- Provider contract tests prove Ollama, vLLM, and llama.cpp schema translation,
  runtime conformance probes, timeouts, invalid output refusal, and absence of
  model tool access.
- Corpus tests report accuracy, coverage, calibrated lower bounds, confusion
  matrices, and abstention by slice for every admitted local profile.
- Planner/property tests prove canonical digests, no arbitrary FQCN/argument
  escape, target narrowing, deterministic risk, approval invalidation, and
  reverse-order rollback.
- API and database tests cover authorization, tenancy, replay, restart recovery,
  optimistic fencing, concurrent apply refusal, and receipt tamper detection.
- Molecule scenarios exercise preview, apply, second-run idempotence, verify,
  injected failure, and rollback for every catalog action on supported OS
  families.
- A two-node service scenario proves canary/rolling health and minimum capacity;
  a one-node restart request proves ZDD refusal.
- Cancellation and process-loss tests prove owned children terminate and the
  next run reconciles instead of blindly replaying.
- Test coverage is at least 85% aggregate and 75% in every implementation file;
  security, lifecycle, catalog, and receipt branches target 100%.

## Success metrics

- At least 80% of clear supported requests reach a correct preview using a local
  model without free-form playbook authoring.
- Zero mutations occur without a valid, current, digest-bound approval.
- At least 99% of supported applies converge on the first attempt and a second
  run reports no change.
- Zero rollout batches violate declared minimum healthy capacity.
- Every injected post-apply failure either verifies rollback or raises a durable
  `rollback_failed` human todo with complete evidence.
- P95 local interpretation latency is recorded per hardware tier and remains
  within the configured task deadline; no universal fixed hardware claim is
  made.

## Non-goals

- Generating or executing arbitrary Ansible/YAML, shell, Terraform, Kubernetes,
  network-device, database-schema, or cloud-IAM changes from prose.
- Letting the model discover credentials, inventory, modules, or tools.
- Automatic cloud-model fallback or claiming that a local model understands
  every configuration product.
- Applying a low-confidence guess because its JSON is schema-valid.
- Replacing reviewed configuration-as-code repositories. A future adapter may
  propose a Git change, but it must use a separate reviewed workflow.
- Claiming Ansible check mode, a model confidence number, or an LLM-generated
  explanation proves safety or correctness.

## Upstream and practitioner evidence

Sources were rechecked on 2026-09-27. They inform constraints and tests; issue
reports describe their cited environments and are not treated as universal
behavior.

- [Ansible check and diff documentation](https://docs.ansible.com/projects/ansible/latest/playbook_guide/playbooks_checkmode.html)
  says check mode is a simulation, unsupported modules do nothing, registered-
  variable conditionals can yield no output, and diffs may expose sensitive
  information. Gludd therefore pairs check/diff with catalog-owned probes,
  secret redaction, verification, and rollback.
- In the long-running Ansible Project discussion
  [variables in check mode](https://groups.google.com/g/ansible-project/c/xF-v6edS-EQ/m/5Bmr1potodEJ)
  (opened 2014-05-22), practitioners describe missing registered values and
  recommend separating safe probes from mutations. That split is mandatory in
  every action adapter.
- [ansible.netcommon issue 88](https://github.com/ansible-collections/ansible.netcommon/issues/88)
  (opened 2020-07-15, closed when observed) reports a network configuration
  module unable to emit the expected diff in check mode. A missing preview is
  therefore an explicit refusal, never an empty-success assumption.
- In the 2023 Ansible community thread
  [Chat GPT for writing playbooks: is it any good?](https://www.reddit.com/r/ansible/comments/10yp4zh/chat_gpt_for_writing_playbooks_is_it_any_good/),
  users report plausible playbooks selecting the wrong network module and
  emphasize testing before use. Gludd never lets the model author a playbook or
  choose an FQCN; it selects an action ID that deterministic code resolves.
- [Ansible Runner's official documentation](https://docs.ansible.com/projects/runner/en/stable/intro/)
  documents process isolation and durable per-run status, return code, stdout,
  and job events. Gludd reuses those artifacts rather than inventing an
  execution/event format.
- [Ollama structured-output documentation](https://docs.ollama.com/capabilities/structured-outputs)
  recommends schema-constrained output plus Pydantic validation and temperature
  zero. Gludd does both, then adds grounding and calibrated abstention.
- [Ollama issue 13750](https://github.com/ollama/ollama/issues/13750)
  (opened 2026-01-16, open when observed) reports self-hosted structured output
  taking precedence over tool calling and producing a schema-valid but
  fabricated result. Interpretation therefore has no model tools; observation
  and execution are separate deterministic phases.
- [Ollama issue 11691](https://github.com/ollama/ollama/issues/11691)
  (opened 2025-08-05, closed via a follow-up when observed) records a model and
  runtime combination returning invalid structured content. Qualification is
  tied to an exact model/runtime tuple and invalid JSON fails closed.
- [llama.cpp server documentation](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
  documents schema-constrained `response_format`, while
  [llama.cpp issue 24097](https://github.com/ggml-org/llama.cpp/issues/24097)
  (opened 2026-06-04, closed as not planned when observed) reports one build
  accepting a schema request without enforcing it. Gludd runs a conformance
  probe for the exact runner digest instead of trusting an HTTP 200 response.
- [vLLM structured-output documentation](https://docs.vllm.ai/en/v0.12.0/features/structured_outputs/)
  documents multiple schema/grammar backends and API migrations. Provider
  adapters own those version details; the configuration contract remains one
  versioned JSON Schema.
