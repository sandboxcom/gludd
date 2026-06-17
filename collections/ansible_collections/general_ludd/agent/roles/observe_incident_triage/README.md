# observe_incident_triage

Triage a live incident by correlating telemetry across every configured
observability source. Given an incident *seed* (a normalized incident record
carrying a `trace_id`), it pulls the logs / traces / metrics / events in a time
window around the incident and groups them on the shared `trace_id` — the
incident's blast radius across backends.

Built on the `GluddObserve` facade
(`general_ludd.observe.facade.GluddObserve.correlate_incident`).

## KINDs

`logs`, `traces`, `metrics`, `events` (configurable via `observe_kinds`).

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `incident_seed` | `{}` (required) | Incident record: `{ts, labels: {trace_id, service}}` |
| `observe_kinds` | `[logs, traces, metrics, events]` | Source KINDs to gather |
| `correlate_by` | `trace_id` | Canonical join key to group on |
| `window_s` | `300` | Half-window (s) of telemetry each side of the seed ts |
| `query_spec` | `{}` | Backend query spec forwarded to each source |
| `artifact_dir` | `/tmp/gludd-observe-incident-triage` | Output directory |
| `daemon_url` | `http://localhost:8000` | Daemon URL |
| `psk` | `""` | Pre-shared key |
| `capability_role` | `observe_incident_triage` | Least-privilege capability identity |

## Artifact

`observe_incident_triage.json`:

```json
{
  "role": "observe_incident_triage",
  "seed_trace_id": "T1",
  "correlate_by": "trace_id",
  "group_count": 1,
  "groups": {"T1": [ /* normalized records */ ]},
  "source_errors": []
}
```

## Deferred wiring

The role calls a `general_ludd.agent.gludd_observe` module (`op=correlate_incident`)
that constructs `GluddObserve` over the daemon's `ConnectorRegistry`. That module
+ its daemon registration are the **deferred wiring step** for #73 — the facade
and its unit tests (`tests/unit/test_observe_facade.py`) land first.
