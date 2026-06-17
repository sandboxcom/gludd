# observe_saturation_capacity

Fleet capacity / saturation check. Pulls saturation metrics (cpu / mem / queue
depth) over a window, builds the service↔host topology, and flags samples whose
value meets/exceeds `saturation_threshold` — a host with `saturated_sample_min`
or more breaches is `at_capacity`.

Built on `GluddObserve.query_sources` + `GluddObserve.topology`.

## KINDs

`metrics`.

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `observe_kinds` | `[metrics]` | Source KINDs to gather |
| `start` / `end` | `~` | Epoch-second window bounds (optional) |
| `saturation_threshold` | `0.85` | Value at/above which a sample is saturated |
| `saturated_sample_min` | `3` | Breaches on a host that escalate it to `at_capacity` |
| `query_spec` | `{promql: ""}` | Backend query spec (e.g. PromQL for the saturation metric) |
| `artifact_dir` | `/tmp/gludd-observe-saturation-capacity` | Output directory |
| `daemon_url` / `psk` | `http://localhost:8000` / `""` | Daemon connectivity |
| `capability_role` | `observe_saturation_capacity` | Least-privilege capability identity |

## Artifact

`observe_saturation_capacity.json` — `saturated_samples`, the `topology`
(`{services, hosts}` adjacency), `saturated_sample_count`, and any `source_errors`.

## Deferred wiring

Calls `general_ludd.agent.gludd_observe` (`op=query_sources`, `op=topology`); that
module + daemon registration are the deferred #73 wiring step. Facade unit tests:
`tests/unit/test_observe_facade.py`.
