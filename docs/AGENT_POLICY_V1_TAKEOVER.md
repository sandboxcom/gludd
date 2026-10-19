# Agent policy v1 takeover and release work summary

This is a versioned handoff document, not generated current observation JSON or a release seal. Snapshot authoring date: 2026-10-19. Branch agent-agents-compression-20261019 starts at development C3583000e3b315f67acf0292dec8ba38642317a1c. Root AGENTS remains the binding instruction entry; [preservation map](AGENT_POLICY_V1_PRESERVATION.md) and [original source](AGENT_POLICY_V1_SOURCE_3583000.md) explain this branch's documentation change.

## Native execution and ownership

Use functions.exec only to transport a named Make command/result or literal editing data. Project analysis/workflows belong to native Make owners with documented variables, argv/status and durable artifacts; no project verdict comes from ad hoc JS/Python in a tool wrapper or hidden function-store state.

Start with make help and explicit target variables from config/make_target_contract.json. Use make work-item for work identity; make active-work-status and native workstream registry for logical ownership/PIDs/tasks. Model-agent names are not OS PIDs. Use existing make pipeline-status / make ci-await producers for generated current observations; never manually rewrite their JSON or build another observer/cache.

Before transferring work record branch/ref/task, source identity, implementation/focused/source-review/admission/fullgate/exactCI/completion states, one actual failing owner/input, retained evidence and exact next native command. Preserve unknown/censored/failed states. Native producer output supersedes this snapshot for new facts.

## Unmerged release repair chain

At the recorded snapshot, development is C3583000e3b315f67acf0292dec8ba38642317a1c. The native range C3583000..7207e97 contains14 commits, unmerged into development. Its source-only cumulative approval is /tmp/gludd-v012-review-7207e97.md and75-whole-module cohort is /tmp/gludd-v012-final-whole-module-cohort-7207e97.txt. No new complete gate/hosted proof follows from that source approval.

| Deliverable group | Original source commits |
|---|---|
| Registered runtime inspection and refusal repair |77b80ea,0fccc95|
| Workflow-pinned UV acquisition |253a6a2|
| Selected lock/jsonpointer composition |f634b76|
| Native startup diagnostic and bounded reader |94055fe,6ade745|
| Descendant-symlink cleanup and root-FD race repair |913ff57,1329a87|
| Node harness probe deferred to fixture runtime |3dd6d1d|
| Captured RECORD/Python3.12 validation |368dd05|
| Selected Make/UV fixture portability |f907a4f|
| Fresh metadata binding once per record |dd806d3|
| Receipt mode-input diagnostics |d6a634a|
| Exact setuid-input admission routing |7207e97|

The first/latest native committer Unix values were1792333495/1792373867. Preserve the original changes-requested reviews for77b80ea,94055fe and913ff57 and the later repair approvals; do not turn their ancestors into independent approvals.

Historical focused confirmations are separate. /tmp/gludd-v012-item-confirmation-reconciliation.md reconciles19 S97/S98 items, including the older nine focused confirmations. Those confirmations predate the work-day range and are not19 completed release tasks: documentary focused evidence exists for all19 at varying scope; current release closure remains0/19. Individual historic fullgate/exactCI counts are unknown where unauthenticated. Never replace that distinction with zero history or current completion.

## Current blocker and next actions

Active release task S98.4 is on branch agent-v012-native-312-startup-diagnostic, worktree /tmp/gludd-worktrees/agent-v012-native-312-startup-diagnostic. Initial M25 source f277be0ec172741524d97410461db24e8e2607cb had source-review R1 CHANGES REQUESTED because profile trace writes reopened a pathname. Its immutable child7d079b502a1d1bf5e8a029d47543cf9f69d00349 (pin refs/heads/reviewed-v012-source-7d079b5; parentf277) is now source-only APPROVED in /tmp/gludd-v012-review-7d079b5.md. Preserve the negative parent and actual failure history.
The measured Git metadata3→1 repair is now frozen at c1d926effb11d764c27799cc030cf2e4961c839f (refs/heads/reviewed-v012-source-c1d926e), source-only approved in /tmp/gludd-v012-review-c1d926e.md. Its normal commit/collection/scans are terminal; TASKS/SESSION writer lock was explicitly released. No new exact-source90/60 or hosted/full-gate proof is implied. Root owns genuine integration of the approved runtime/policy branches, then actual native admission and the normal measured CI repair push; the source author need not wait for policy metadata.

The native first-two profile is a diagnostic result (2PASS/14.71s) pointing to hundreds of repeated Git/subprocess launches; it is not proof that metadata-snapshot cost was the runtime blocker, nor a formal90/60/gate/CI acceptance. The exact Git child now has its own source review; actual final runtime measurement remains Root-owned before a throughput claim. Earlier trace-source approval alone did not approve that child or the release.

For the policy branch the immediate next native actions are:
1. make check-make-target-contract
2. make audit-agent-context-size AGENT_CONTEXT_FILES='["AGENTS.md","docs/CLAUDE.md"]' AGENT_CONTEXT_JSON=1
3. The exact whole-owner conformance set is [AGENT_POLICY_V1_CONFORMANCE.txt](AGENT_POLICY_V1_CONFORMANCE.txt). Its explicit native invocation is recorded below; preserve failed and green receipts separately.
4. After ledger ownership releases and preservation review accepts, perform the actual task/session updates, required fresh normal collection and normal guarded commit with the exact FILES/MSG/PUSH command below. This runbook adds no push/merge authority or confirmation request: existing user/task authorization and native gates remain in force.

For release work, the proof owner continues the active runtime repair on its original branch. After all actual prerequisites clear, the exact next admission owner is make release-proof-admission RELEASE_PROOF_ADMISSION_VALIDATE_ONLY=0; only a real passing admission allows sealing/final replay, and it never replaces combined acceptance, the full canonical gate or exact-SHA hosted CI. The source author/root must select the exact frozen source and native bundle, not infer authorization from this stale snapshot. Do not delay an authorized normal measured-runtime CI repair push for this documentation branch.

## Exact policy verification and commit

The following is the actual27-whole-owner test scope, not a selected-node shortcut. Use canonical environment bindings shown in the appendix for Python-running targets. Native pass/fail receipts are named in [metrics](AGENT_POLICY_V1_METRICS.md); do not claim this command ran merely because it is printed here.

```text
make test-files TESTFILES='tests/unit/test_agent_context_metrics.py tests/unit/test_audit_agent_behavior.py tests/unit/test_priority_stacking_rule.py tests/unit/test_pipeline_priority.py tests/unit/test_release_candidate_discipline.py tests/unit/test_no_passive_ci_polling.py tests/unit/test_adaptive_dispatch_enforcement.py tests/unit/test_subagent_quality_integration.py tests/unit/test_task_tracking_guardrails.py tests/unit/test_spec_enforcement_m01_m18.py tests/unit/test_branch_discipline_checks.py tests/unit/test_agents_md_section.py tests/unit/test_noqa_guardrail_chain.py tests/unit/test_system_load_gate.py tests/unit/test_operational_discipline_sections.py tests/unit/test_ci_await_codification.py tests/unit/test_dc3_dc4_dc5_sections.py tests/unit/test_enforcement_agent_count_config.py tests/unit/test_agent_concurrency_contract.py tests/unit/test_no_prompt_progress_policy.py tests/unit/test_session_start_protocol.py tests/unit/test_anti_stop_behavior.py tests/unit/test_spec_enforcement_e02_e19.py tests/unit/test_spec_enforcement_p17_p29.py tests/unit/test_ci_interaction_discipline.py tests/unit/test_documentation_integrity.py tests/unit/test_make_target_contract.py' PYTEST_ARGS='-q -W error -n 0 --tb=short -o verbosity_test_cases=-2 --junitxml=/tmp/gludd-agents-compression-conformance-final.xml'
```

The intended whole-file commit command, only after independent review and ledger release, is:

```text
make ship-commit-files FILES='AGENTS.md docs/AGENT_POLICY_V1.md docs/AGENT_POLICY_V1_SOURCE_3583000.md docs/AGENT_POLICY_V1_PRESERVATION.md docs/AGENT_POLICY_V1_TAKEOVER.md docs/AGENT_POLICY_V1_METRICS.md docs/AGENT_POLICY_V1_RELEASE_HANDOFF.md docs/AGENT_POLICY_V1_CONFORMANCE.txt scripts/audit_agent_behavior.py tests/unit/test_agent_context_metrics.py tests/unit/test_audit_agent_behavior.py make/00-foundation.mk make/10-observability-and-tests.mk config/make_target_contract.json config/coverage_agent_context_metrics.ini TASKS.md SESSION.md' MSG='docs: compress agent policy with preserved rule context and native metrics' PUSH=0 DUPLICATE_CODE_ENGINE=/Users/shawnwilson/gludd/.opencode/node_modules/.bin/jscpd WORK_ITEM_ID=S98.4
```

TASKS/SESSION writer release is now recorded; this branch's supporting notes are prepared, with the guarded commit still pending. The native owner must validate actual allowed task/support scope and clean/registered inputs; the command is not proof of a commit, an admission bypass or authority to stage another owner's changes.

## Known current observation and failing family

The previously produced C358 snapshot .gludd/release-proof-status.json records Build39132753934 FAILURE and Molecule39132753933 PASS, release_ready=false/gate_evidence=false. Root's retained local gate is ABORTED; the projection field UNKNOWN is preserved, not rewritten. Family ce0164d707f6d521ab1269146bd7541dee47e0ad74355556f785c3f0ad9936e2 is gate(3.12), “Known runtime and handoff regression admission”. These are retained observations, not a new status query by this documentation task.

Native current state can be obtained by make active-work-status (no documented variables). Work selection uses make work-item WORK_ITEM_ACTION=status WORK_ITEM_ID='' WORK_ITEM_REASON='' WORK_ITEM_VALIDATE_ONLY=1; validate-only is a plan, not administrative advancement. Registry registration uses workstream-register with BRANCH, WORKTREE and ACTIVE_WORKSTREAM_REGISTRY; unregister uses only BRANCH and ACTIVE_WORKSTREAM_REGISTRY. Follow the existing contract; never manually author the registry.

A safe pipeline plan, matching the current contract, is:
`make pipeline-status PIPELINE_STATUS_REPO=sandboxcom/gludd PIPELINE_STATUS_BRANCH=development PIPELINE_STATUS_REMOTE=sandboxcom PIPELINE_STATUS_SHA=3583000e3b315f67acf0292dec8ba38642317a1c PIPELINE_STATUS_FAILURE_LEDGER=.gludd/ci-failure-ledger.json PIPELINE_STATUS_VALIDATE_ONLY=1 PIPELINE_STATUS_OUTPUT=/tmp/gludd-policy-takeover-pipeline-plan.json SSH_KEY=/tmp/gludd-contract-absent-key`.
It does not query hosted state or produce new success. For an actual native observation, the proof owner supplies its real existing SSH_KEY/input identity and VALIDATE_ONLY=0; pipeline-status or ci-await alone owns current JSON. A missing key/lookup/timeout is UNKNOWN/failure, never prior-green reuse.

Only after the clean measured descendant and exact evidence owner are selected, execute that family's FIRST evidence through make ci-failure-repair with ALL contract variables: CI_FAILURE_LEDGER=.gludd/ci-failure-ledger.json, CI_FAILURE_FAMILIES=ce0164d707f6d521ab1269146bd7541dee47e0ad74355556f785c3f0ad9936e2, CI_REPAIR_ALL_OPEN=0, the actual clean CI_REPAIR_SHA, the explicit existing CI_REPAIR_EVIDENCE_TARGET and CI_REPAIR_EVIDENCE_VARS, CI_FAILURE_VALIDATE_ONLY=0. No family-to-target mapping or positive formal90/60 proof is invented here. Preserve repaired versus hosted-resolved versus completed distinctions.

The required final completion-metadata protocol is now durable in [release reconciliation handoff](AGENT_POLICY_V1_RELEASE_HANDOFF.md). It retains the67 completed finite controls, genuine open S83.180 continuation/merge and SECOND exact-metadata-SHA fullgate/hosted pair. Read it when preparing that transition; it is not an always-read context dependency for unrelated work.

## Evidence and resource boundaries

Use docs/features/RELEASE_PROOF_THROUGHPUT.md and docs/RELEASE_RUNBOOK.md for established release procedures. Do not reseal/replay the same failing input, edit the candidate during proof, change90/60/three-child/21phase2850/85–75/ZDD/warning/cleanup/promotion obligations, or manually fabricate current observations.

Retain the original unexplained private433PASS/1FAIL receipt-mode evidence and later native input diagnostics as recorded; subset passes or a future admission do not retrospectively explain that failure. Current resource permission requires native ownership/capacity guard output, not an old percent or inferred idle process. No new capacity probe, gate, CI, push, promotion or publication was performed by this policy task.
