# observe_deploy_correlator

Answer "did this deploy cause a regression?". Given a deploy seed (a pipeline/CI
event carrying `ts` + `labels.commit`), it compares error/incident volume in the
window *before* the deploy against the window *after*, and flags the deploy as
`suspected_cause` when the after/before ratio exceeds `regression_ratio`.

Built on two windowed `GluddObserve.query_sources` calls.

## KINDs

`pipeline`, `metrics`, `logs`, `incidents`.

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `deploy_seed` | `{}` (required) | Pipeline record: `{ts, labels: {commit, service}}` |
| `observe_kinds` | `[pipeline, metrics, logs, incidents]` | Source KINDs to compare |
| `correlate_by` | `commit` | Join key associating telemetry with the deploy |
| `window_s` | `900` | Seconds before and after the deploy ts to compare |
| `regression_ratio` | `1.5` | after/before error ratio that flags suspected-cause |
| `query_spec` | `{}` | Backend query spec forwarded to each source |
| `artifact_dir` | `/tmp/gludd-observe-deploy-correlator` | Output directory |
| `daemon_url` / `psk` | `http://localhost:8000` / `""` | Daemon connectivity |
| `capability_role` | `observe_deploy_correlator` | Least-privilege capability identity |

## Artifact

`observe_deploy_correlator.json` — `errors_before` / `errors_after`, `verdict`
(`suspected_cause` / `new_errors_after_deploy` / `no_regression`), and any
`source_errors`.

## Deferred wiring

Calls `general_ludd.agent.gludd_observe` (`op=query_sources`, twice — before/after);
that module + daemon registration are the deferred #73 wiring step. Facade unit
tests: `tests/unit/test_observe_facade.py`.
