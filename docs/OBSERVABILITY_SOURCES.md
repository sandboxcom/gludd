# Observability & Pipeline Connector Roadmap

gludd's job across heterogeneous sources is **correlation**: take an anchor (commit,
time window, service, alert) and fan out to many backends, then join on shared keys.

## Universal join keys (the product)
Every connector emits a normalized record carrying as many of these as it has —
correlation quality is bounded by them:
`commit SHA · release/version · service / k8s label set · trace_id · timestamp window`.

## Connector layer
- Contract: `src/general_ludd/connectors/base.py` — `Source` Protocol (+ `PipelineSource`/
  `LogSource`/`MetricSource`/`TraceSource`), `SourceRegistry`, `Observability.find()/associate()`,
  `is_safe_endpoint()` (literal-host SSRF guard, no DNS).
- Normalized record keys: `ts, source, kind, level_or_status, message, value, labels, raw`.

### Built (this session)
github_actions, jenkins, aws_pipeline(+cloudwatch), prometheus, elasticsearch, splunk,
graylog, signoz, grafana_loki, jsonl + syslog (local_files). In-flight: azure_monitor +
azure_resource_graph, gcp_observability, datadog, sentry, jaeger + tempo_zipkin, kubernetes,
gitlab_ci + azure_devops + circleci.

### Backlog (prioritized; bundle by AUTH family, not by KIND)
- **Wave 1 core (correlation pillars):** Prometheus✓, Loki✓, OTLP/Tempo/Jaeger✓, Sentry, k8s API.
- **Wave 2 hosted single-pane:** Datadog✓, Grafana datasource proxy, GitLab✓, Elasticsearch✓, PagerDuty.
- **Wave 3 cloud suites (shared cred-chain → one connector family each):**
  - AWS (SigV4): CloudWatch Logs+Metrics, X-Ray, CodePipeline/CodeBuild, **CloudTrail** (who changed what).
  - Azure (Entra): Monitor/Log Analytics (KQL)✓, DevOps Pipelines✓, Resource Graph✓, Activity Log.
  - GCP (ADC): Cloud Logging/Monitoring✓, Asset Inventory.
- **Wave 4 long tail:** New Relic (NRQL), Honeycomb, Buildkite/Travis, Opsgenie, Rollbar/Bugsnag,
  InfluxDB/Graphite, Argo/Tekton, Terraform/OpenTofu state, journald, Datadog APM, Elastic APM.

## Debugging-role workflows (each fans across sources, correlates on join keys)
Built as Ansible roles calling a daemon `gludd_observe` endpoint over the façade:
1. **CI Failure Triage** — failed run+commit → PIPE step logs → Sentry issues for that release →
   traces for the failing service → build logs.
2. **Latency Spike** — service+window → PromQL p95/p99 → slow traces (TraceQL/Jaeger) → recent
   deploy/Argo sync + k8s rollout events → error logs.
3. **Incident Triage** — PagerDuty/Opsgenie alert → metric+neighbors → logs in window → k8s pod
   state + CloudTrail/Activity "who changed what" → last deploy.
4. **Deploy/Release Regression** — release/deploy event → Sentry "new in release" → before/after
   metric compare → trace diff old vs new → IaC drift → rollback candidates.
5. **Is-It-Even-Running / CrashLoop** — service/pod → k8s phase+restarts+Events (OOM/ImagePull) →
   last container logs → resource saturation → recent deploy.
6. **Cross-Stack Error Correlation** — Sentry issue → trace_id waterfall → logs by trace_id →
   error-rate trend → GitHub blame of the introducing commit.

## Implementation notes
- Bundle by auth family collapses ~15 sources into ~4 auth efforts.
- OTel as the internal trace/metric/log model lets Jaeger/Tempo/SigNoz/Datadog/X-Ray feed one schema.
- Start where gludd lives (local JSONL/journald + GitHub Actions) for a working
  "CI failure → logs → error tracker" loop with least auth friction.
