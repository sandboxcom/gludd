# observe_latency_regression

Detect a latency regression for a service by building a time-ordered timeline of
metric samples (and the traces behind them) over a window, then flagging samples
whose latency value meets/exceeds a threshold. If the over-threshold fraction
exceeds `regression_fraction`, the verdict is `regressed`.

Built on `GluddObserve.timeline`.

## KINDs

`metrics`, `traces`.

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `service` | `""` (required) | Service under investigation (canonical `service` join key) |
| `observe_kinds` | `[metrics, traces]` | Source KINDs to gather |
| `start` / `end` | `~` | Epoch-second window bounds (optional) |
| `latency_threshold_s` | `1.0` | Sample latency (s) at/above which a sample is a breach |
| `regression_fraction` | `0.1` | Breach fraction that escalates to `regressed` |
| `query_spec` | `{promql: ""}` | Backend query spec (e.g. PromQL for the latency metric) |
| `artifact_dir` | `/tmp/gludd-observe-latency-regression` | Output directory |
| `daemon_url` / `psk` | `http://localhost:8000` / `""` | Daemon connectivity |
| `capability_role` | `observe_latency_regression` | Least-privilege capability identity |

## Artifact

`observe_latency_regression.json` — `verdict` (`regressed` / `within_budget`),
`sample_count`, `breach_count`, the offending `breaches`, and any `source_errors`.

## Deferred wiring

Calls `general_ludd.agent.gludd_observe` (`op=timeline`); that module + its daemon
registration are the deferred #73 wiring step. The facade ships with unit tests in
`tests/unit/test_observe_facade.py`.
