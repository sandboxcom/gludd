# Gludd Smoke Tests

`gludd smoke <provider> <test>` runs a low-cost service smoke test and prints a
third-party friendly evidence bundle: logs, metrics, events, timestamps, status,
cost estimate, selected provider, selected test, and redacted configuration facts.

Examples:

```bash
AWS_KEY=... AWS_SECRET=... gludd smoke aws ec2-a100 --json
OPENROUTER_API_KEY=... gludd smoke openrouter metadata --live --json
VLLM_BASE_URL=http://127.0.0.1:8000/v1 gludd smoke vllm metadata --live --json
OPENROUTER_API_KEY=... GROQ_API_KEY=... gludd smoke multi-provider model-juggle --live --json
VLLM_BASE_URL=http://127.0.0.1:8000/v1 LLAMACPP_BASE_URL=http://127.0.0.1:8080/v1 \
  GLUDD_SMOKE_LOCAL_MODEL=tinyllama gludd smoke multi-platform model-juggle --live --json
SLURM_REST_URL=https://slurm.example.edu SLURM_REST_TOKEN=... gludd smoke slurm metadata --json
```

By default, smoke tests are dry-run preflights. They validate credentials,
registered provider support, cost ceilings, and expected endpoints without
provisioning infrastructure or spending tokens. Add `--live` to allow cheap
metadata requests such as model-list probes. Provisioning and token-generating
prompt calls should remain separate, explicit follow-up tests with a real spend
ceiling.

## Live Credential Policy

Do not treat fabricated API keys as live provider evidence. OpenAI and Anthropic document API keys created in their consoles; their public docs do not define a universal test key that returns a successful diagnostic response. OpenRouter documents a real-key /api/v1/key inspection endpoint and management keys for admin operations, but those still require real credentials. When no documented sandbox or test credential exists, a bad-key response only proves the request reached an auth gate. Record that as auth_rejected, not as a provider-health pass.

## Commands

List every registered smoke test:

```bash
gludd smoke list
gludd smoke list --json
gludd smoke aws --list
```

Run a preflight:

```bash
AWS_KEY=... AWS_SECRET=... gludd smoke aws ec2-a100 --json
```

Run a live metadata probe:

```bash
OPENROUTER_API_KEY=... gludd smoke openrouter metadata --live --json
```

Override a provider endpoint when a provider uses account-scoped URLs:

```bash
CLOUDFLARE_API_TOKEN=... gludd smoke cloudflare metadata \
  --base-url https://api.cloudflare.com/client/v4/accounts/<account>/ai/v1/models \
  --live --json
```

Limit acceptable spend. The default ceiling for smoke tests is USD 10.00; lower it
for metadata-only checks or leave the default in place for bounded manual
provisioned runs:

```bash
OPENAI_API_KEY=... gludd smoke openai model-ping --max-cost-usd 10.00 --json
```

Dry-run `model-ping` reports `estimated_cost_usd: 0.0`; live model-ping and
provisioned compute smokes fail before any live action if their estimate exceeds
`--max-cost-usd`.

## Evidence Output

Use `--json` when sharing output with a provider, support team, or a third party.
The report includes:

- `run_id`, `provider`, `test`, `mode`, `started_at`, `completed_at`, `status`
- `estimated_cost_usd`
- `metrics`: checks, failures, HTTP request count, model count, duration
- `events`: structured start, credential, HTTP, skip, and completion events
- `logs`: redacted diagnostic messages and fields
- `trace`: ordered log/event entries with `sequence` and `trace_id`
- `coverage_depth` and `functional_scope`: what the smoke actually exercised
- `analysis_prompt`: the prompt to use when handing the JSON bundle back for repair
- `model_juggle`: ordered multi-provider/platform plan and per-leg results for model juggling smokes

For manual provider debugging, write a durable bundle and share the file path:

```bash
OPENROUTER_API_KEY=... gludd smoke openrouter model-ping --live --json --output /tmp/gludd-openrouter-smoke.json
```

When a run fails, send the saved JSON path and ask for analysis using the bundled
`analysis_prompt`. Do not paste API keys or provider secrets. A useful repair
request is:

```text
Please analyze the Gludd smoke report at /tmp/gludd-openrouter-smoke.json. Use
the report analysis_prompt, trace_id, ordered trace sequence, logs, events, and
metrics to identify the failing provider/service path and propose the focused
code/tests/docs changes needed to fix it.
```

Secret-looking values are redacted before they enter logs or events. The output
names which variables were present or missing, but never includes API keys,
tokens, or passwords.

## Output Templates

Smoke list/report/trace formatting is rendered through compiled Jinja2 templates in
`templates/log_output/`. The templates use Ansible-style Jinja2 syntax but run in
a restricted sandbox as data formatters only: they receive already-built report,
metric, log, event, and trace objects and cannot call Python or Ansible code.

Operators can add/remove fields, reorder output, and add conditional formatting
without changing smoke-test code:

```bash
GLUDD_OUTPUT_TEMPLATES_DIR=/path/to/templates \
  OPENROUTER_API_KEY=... gludd smoke openrouter model-ping --live \
  --output-template smoke.report.text.j2 \
  --output /tmp/gludd-openrouter-smoke.txt
```

`GLUDD_OUTPUT_TEMPLATES_DIR` prepends an operator-owned template directory ahead
of the built-in templates. `--output-template` selects a compiled template by
name, and `--output` writes the rendered evidence bundle to a durable file. The
daemon compiles the default template registry at startup so repeated smoke output
renders do not re-scan template files on every request.

## Coverage Map

Model/API providers come from `general_ludd.models.provider_presets`:

- `openrouter`, `openai`, `anthropic`, `zai`, `groq`, `deepseek`, `baseten`
- `lambdalabs`, `together`, `fireworks`, `replicate`, `runpod`, `modal`
- `coreweave`, `mistral`, `cohere`, `nvidia`, `perplexity`, `huggingface`
- `ai21`, `google`, `cloudflare`, `databricks`, `azure-ai-foundry`

Each model/API provider gets:

- `credential-check`: no network, validates configured env variables
- `metadata`: optional live metadata GET, usually `/models`
- `model-ping`: low-cost prompt-call preflight; dry-run by default

Multi-model orchestration smokes:

- `multi-provider model-juggle`: plans OpenRouter, OpenAI, Groq, DeepSeek,
  Together, Fireworks, Mistral, and Cohere model calls. With `--live`, it runs
  every configured provider key and fails unless at least two provider legs are
  configured and every configured leg returns completion content.
- `multi-platform model-juggle`: plans model API providers plus `vllm`,
  `llamacpp`, and `ollama` serving platforms. With `--live`, it runs every
  configured API key or local base URL and records per-leg endpoint, model,
  status code, completion status, and elapsed time under `model_juggle.results`.

Compute providers come from `general_ludd.infra.providers`:

- `aws`, `azure`, `gcp`, `runpod`, `vast-ai`, `lambda-labs`, `modal`
- `coreweave`, `digital-ocean`, `oracle`, `vmware`, `kubernetes`
- `together-ai`, `fireworks-ai`, `huggingface`, `replicate`

Each compute provider gets `credential-check` and one `gpu-<type>` smoke per GPU
price entry in the compute registry. Without `--provisioned`, these run as
dry-run preflights. With `--provisioned`, gludd uses the existing
`DeploymentManager` lifecycle to provision the resource, run a one-token
OpenAI-compatible model task against the returned endpoint, probe `/health`,
`/v1/models`, and `/metrics`, and call destroy in a `finally` cleanup path. The
JSON bundle includes `endpoint_diagnostics.expected` with the tunables gludd sent
into deployment, including provider, GPU type/count, engine (`vllm` or
`llamacpp`), model, region, cost ceiling, network CIDR, decoding options, and
workload/deployment profile. The smoke fails if the provisioned endpoint does
not return the expected model id or Prometheus process/engine metrics, so a saved
bundle can prove whether vLLM/llama.cpp process, model, and serving metrics made
it back to gludd. AWS also exposes `ec2-a100` as a user-friendly alias for the
A100 EC2 smoke.

Local/cluster model backends:

- `ollama metadata`
- `vllm metadata`
- `llamacpp metadata`
- `slurm metadata`

Connector/service preflights:

- `github-actions`, `notion`, `kubernetes`, `nomad`, `searx`, `zendesk`
- `clickhouse`, `statsd`, `windows-wmi`, `windows-defender`

These match the current connector modules closely enough for a first diagnostic
surface. Deeper connector-specific probes should be added when each connector
has a stable low-cost health endpoint and a clear no-secret evidence schema.

## Known Long-Lived Issues To Capture

The smoke output is designed to capture enough facts for the recurring provider
failure classes below without leaking credentials.

- AWS EC2 GPU capacity: AWS re:Post documents `InsufficientInstanceCapacity` as
  an on-demand capacity problem, and recommends retrying later, launching fewer
  instances, avoiding a pinned Availability Zone, or trying another instance
  type or subnet/AZ.
- Azure GPU capacity and quota: Microsoft Q&A threads describe cases where GPU
  quota exists but regional SKU capacity is unavailable, plus NC-series quotas
  that must be requested per family and region. Smoke reports should include
  region, requested family/test, and whether credentials/subscription variables
  were present.
- GCP A100 quota: Google developer forum posts show approved-looking quota
  increases can still leave regional A100 quota unusable or confusing. Include
  region/zone, GPU type, and quota-related command output from follow-up checks.
- RunPod availability: RunPod docs and user reports describe GPU availability,
  queueing, and restart-with-zero-GPU cases tied to physical GPU allocation and
  volume placement. Prefer metadata/availability checks before scheduling work.
- OpenAI-compatible providers: rate limits and overloaded responses are normal
  operational failures. OpenAI guidance recommends exponential backoff for 429s;
  Anthropic user reports show persistent 529 overloaded responses. Preserve
  status code, provider, model, retry count, and timing in shared smoke evidence.
- Slurm REST: SchedMD documents JWT headers, terse authentication failures, and
  common slurmrestd issues such as invalid tokens, wrong API version, connection
  refusal, and socket binding problems. Smoke output should include endpoint,
  auth mode, HTTP status, and daemon-log pointers, not token values.
- Multi-provider routing: OpenRouter documents free-tier 429 rate limits and
  recommends reading rate-limit headers, exponential backoff, and circuit
  breakers. Its failover guidance separates provider outages/rate limits from
  model fallback problems. Multi-provider smokes preserve per-leg provider,
  model, status code, elapsed time, and completion status so routing failures can
  be isolated. Sources: https://openrouter.zendesk.com/hc/en-us/articles/39501163636379-OpenRouter-Rate-Limits-What-You-Need-to-Know and https://openrouter.ai/blog/insights/reliability-failover/.
- vLLM: upstream docs and forum answers describe the OpenAI-compatible server as
  exposing Prometheus metrics at `/metrics`, including `vllm:` queue/cache/token
  metrics. Provisioned smoke tests require those names when engine is `vllm`.
  Sources: https://docs.vllm.ai/en/v0.18.0/design/metrics/ and https://discuss.vllm.ai/t/vllm-engine-metrics/810/15.
- llama.cpp: `llama-server` exposes `/health`, `/models`, and `/metrics`, but
  the Prometheus endpoint requires the server to be started with `--metrics` and
  reports `llamacpp:` metric names. Provisioned smoke tests surface missing
  metrics as endpoint diagnostics rather than hiding the issue. Source:
  https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md.

## Operational Pattern

Use dry-run smoke tests first. They are safe in CI and support tickets:

```bash
gludd smoke aws ec2-a100 --json
gludd smoke openai credential-check --json
gludd smoke slurm metadata --json
```

Then run the cheapest live metadata probe:

```bash
OPENAI_API_KEY=... gludd smoke openai metadata --live --json
```

Only after metadata passes should you run a model prompt smoke or a provisioned
compute test. Keep those follow-ups behind an explicit spend ceiling and attach
the dry-run plus metadata JSON reports to the ticket or incident.
