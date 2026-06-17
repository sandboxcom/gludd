# observe_error_spike_rca

Root-cause an error spike. Gathers error/critical logs + traces + events in a
window, correlates them by `trace_id` into groups, ranks the groups by error
volume, and surfaces the largest group (at/above `spike_min_group_size`) as the
likely root-cause candidate.

Built on `GluddObserve.query_sources` + `connectors.normalize.correlate`.

## KINDs

`logs`, `traces`, `events`.

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `service` | `""` | Optional service scope; empty => fleet-wide |
| `observe_kinds` | `[logs, traces, events]` | Source KINDs to gather |
| `correlate_by` | `trace_id` | Join key to correlate the spike on |
| `start` / `end` | `~` | Epoch-second window bounds (optional) |
| `min_severity` | `error` | Minimum canonical severity counted toward the spike |
| `spike_min_group_size` | `5` | Group size at/above which a group is a candidate |
| `query_spec` | `{}` | Backend query spec forwarded to each source |
| `artifact_dir` | `/tmp/gludd-observe-error-spike-rca` | Output directory |
| `daemon_url` / `psk` | `http://localhost:8000` / `""` | Daemon connectivity |
| `capability_role` | `observe_error_spike_rca` | Least-privilege capability identity |

## Artifact

`observe_error_spike_rca.json` — `root_cause_candidate` (top trace_id), ranked
`candidates`, `candidate_count`, and any `source_errors`.

## Deferred wiring

Calls `general_ludd.agent.gludd_observe` (`op=query_sources` + correlate); that
module + daemon registration are the deferred #73 wiring step. Facade unit tests:
`tests/unit/test_observe_facade.py`.
