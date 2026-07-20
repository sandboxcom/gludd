# Gludd Smoke Tests

`gludd smoke <provider> <test>` runs a low-cost service smoke test and prints a
third-party friendly evidence bundle: logs, metrics, events, timestamps, status,
cost estimate, selected provider, selected test, and redacted configuration facts.

Examples:

```bash
AWS_KEY=... AWS_SECRET=... gludd smoke aws ec2-a100 --json
OPENROUTER_API_KEY=... gludd smoke openrouter metadata --live --json
VLLM_BASE_URL=http://127.0.0.1:8000/v1 gludd smoke vllm metadata --live --json
SLURM_REST_URL=https://slurm.example.edu SLURM_REST_TOKEN=... gludd smoke slurm metadata --json
```

By default, smoke tests are dry-run preflights. They validate credentials,
registered provider support, cost ceilings, and expected endpoints without
provisioning infrastructure or spending tokens. Add `--live` to allow cheap
metadata requests such as model-list probes. Provisioning and token-generating
prompt calls should remain separate, explicit follow-up tests with a real spend
ceiling.

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

Limit acceptable spend:

```bash
OPENAI_API_KEY=... gludd smoke openai model-ping --max-cost-usd 0.001 --json
```

Dry-run `model-ping` reports `estimated_cost_usd: 0.0`; live model-ping preflight
uses the registered tiny-call estimate and fails before any live action if it
exceeds `--max-cost-usd`.

## Evidence Output

Use `--json` when sharing output with a provider, support team, or a third party.
The report includes:

- `run_id`, `provider`, `test`, `mode`, `started_at`, `completed_at`, `status`
- `estimated_cost_usd`
- `metrics`: checks, failures, HTTP request count, model count, duration
- `events`: structured start, credential, HTTP, skip, and completion events
- `logs`: redacted diagnostic messages and fields

Secret-looking values are redacted before they enter logs or events. The output
names which variables were present or missing, but never includes API keys,
tokens, or passwords.

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

Compute providers come from `general_ludd.infra.providers`:

- `aws`, `azure`, `gcp`, `runpod`, `vast-ai`, `lambda-labs`, `modal`
- `coreweave`, `digital-ocean`, `oracle`, `vmware`, `kubernetes`
- `together-ai`, `fireworks-ai`, `huggingface`, `replicate`

Each compute provider gets `credential-check` and one `gpu-<type>` preflight per
GPU price entry in the compute registry. AWS also exposes `ec2-a100` as a
user-friendly alias for the A100 EC2 preflight.

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
- vLLM: upstream maintainers recommend the OpenAI-compatible server for
  production use; smoke tests should target `/v1/models` and OpenAI-compatible
  chat paths rather than the demonstration API server.

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
