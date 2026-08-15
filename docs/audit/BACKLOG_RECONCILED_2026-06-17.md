# Reconciled Security/Correctness Backlog — 2026-06-17

State after the post-batch-2 + post-batch-3a sweep (entire codebase audited; every
finding adversarially verified true/false). Supersedes SECURITY_AUDIT_BACKLOG_2026-06-17.md
for status. Apply order is one-cycle-per-gate (never two gates concurrently).

## SHIPPED in batch 3a (commit pending gate `bmxw2l2uj`)
- accounting tokens_used real counts; capabilities docstring honesty; pid div-by-zero
  guard; budget_manager record_spend lock; secrets/migration log redaction;
  worker_broadcast PSK auth header; skills/fetcher URL-encode. (7 fixes + 7 tests.)

## SHIPPABLE NEXT (prepped, anchors verified, tests drafted)
| Cycle | Finding | File | Fix |
|---|---|---|---|
| 3b | dispatch key collision | dispatch/variable_store.py:102 | `.replace(".","_DOT_").replace("-","_DASH_")` + flip 2 pinned tests |
| 3b | SSTI in render() | dispatch/variable_store.py:76 | `SandboxedEnvironment` (import from jinja2.sandbox); disjoint from 3b-2A |
| 3b | `--&gt;` escape + dedup | issue_sources/markdown_todo.py:176 | escape `comment.replace("--&gt;","--&gt;")`, dedup full marker; external_id = md-+sha1(rel::text)[:12] |
| A  | gated_commit/gated_merge port | git_automation/{types,repo,__init__}.py | + GatedCommitResult; gate via subprocess(shell=False), commit only if rc==0; also add CloneResult to __all__ |
| A  | REAL non-stub ansible op | collections/.../gludd_git_automation.py + roles | mandatory caller gate_cmd, module.fail_json if rc!=0 |
| 3  | is_path_within→is_join_within | security/{auth,__init__}.py + skills/fetcher.py | rename + back-comp alias (covers all importers) |
| 4  | cassandra/clickhouse SSRF | connectors/{cassandra_stats,clickhouse_stats}.py | `is_safe_endpoint(url)` in _build_default_executor; no existing tests break |

## CONFIRMED-REAL, fix+test being drafted (future batches)
| Finding | File | Severity | Note |
|---|---|---|---|
| per-job budget bypass | models/job_invocation.py:68 | Medium | call_model omits budget_remaining; thread through + 2 call sites |
| dispatch_many no global ceiling | agents/dispatcher.py | Med (theoretical) | add global asyncio.Semaphore; no large-N callers today |
| prometheus normalize DoS | connectors/prometheus.py | Med | NaN/Inf not validated; no result-size cap; label cap; non-str labels |
| redirect guard | connectors/pagerduty.py:110, opsgenie.py:126 | Low | add follow_redirects=False (others use urllib/injected — safe) |
| base_url mutable | issue_sources/base.py:~593 | Low | convert to @property with guarded setter |

## DEFERRED (preconditions / blast-radius)
- secrets/manager.resolve() raise: BLOCKED — slurm/deployment/mcp callers depend on warn-and-None.
- release.py manifest self-attestation (no signature chain): Medium; needs signing infra.
- git_automation Fix 5 (_run_git routing of 9 methods + realpath jail): safe (no subprocess.run stubs in tests); large surface — own cycle.
- release Fix 1B (LICENSE-in-manifest assert): fixture audit pending.
- tool_loop.py:118 server_id: re-audited SOUND (registry carries server_id) — drop.

## FALSE POSITIVES (verified, do NOT fix)
- worker YAML-injection (runner.py): PyYAML never emits !!python for strings; vars in-memory to VariableManager.
- worker Jinja extravars (app.py): mitigated by wrap_extravars→AnsibleUnsafe.
- spend/accounting per-project auth: NOT a gap — gludd single-tenant (one PSK = operator owns all projects).

## SOUND (audited, no action)
events, gateway/resilience (retry+breaker, 529 retryable, half-open), daemon auth
(all mutating/admin endpoints PSK-gated, fail-closed), web routers, pipeline merge
gate (fail-closed), scheduler, projects/workspace jail, mcp, model-registry, db,
config, filestore, metrics/pricing, self_improve (default-DENY gate), feature_verifier
(strict fail-closed), code_intelligence, cli/hooks, normalize.py core.
