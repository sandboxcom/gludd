# observe_security_signal

Surface a cross-source security signal. Gathers audit events (e.g. Okta system
log), security-relevant logs, and incidents in a window, correlates them by
`host` (lateral-movement clustering; switch to `service`/`trace_id` for
request-scoped signals), and flags hosts/actors with clustered high-severity
activity at/above `signal_min_group_size`.

Built on `GluddObserve.query_sources` + `connectors.normalize.correlate`.

## KINDs

`events`, `logs`, `incidents`.

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `observe_kinds` | `[events, logs, incidents]` | Source KINDs to gather |
| `correlate_by` | `host` | Join key to cluster the signal on |
| `start` / `end` | `~` | Epoch-second window bounds (optional) |
| `min_severity` | `warn` | Minimum canonical severity counted toward the signal |
| `signal_min_group_size` | `3` | Cluster size at/above which a host/actor is suspicious |
| `query_spec` | `{}` | Backend query spec forwarded to each source |
| `artifact_dir` | `/tmp/gludd-observe-security-signal` | Output directory |
| `daemon_url` / `psk` | `http://localhost:8000` / `""` | Daemon connectivity |
| `capability_role` | `observe_security_signal` | Least-privilege capability identity |

## Artifact

`observe_security_signal.json` — `suspicious_clusters` (host/actor keys over the
threshold), ranked `clusters`, `cluster_count`, and any `source_errors`.

## Deferred wiring

Calls `general_ludd.agent.gludd_observe` (`op=query_sources` + correlate); that
module + daemon registration are the deferred #73 wiring step. Facade unit tests:
`tests/unit/test_observe_facade.py`.
