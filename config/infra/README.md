# Infrastructure Configuration

## `providers.yml` — cloud GPU provider catalog

Declares every cloud GPU provider gludd can deploy to, along with the
metadata the orchestrator needs to pick one (price, GPU range, spot
support, billing granularity, terraform provider source).

### Schema

Each entry under `providers:` has:

| Field | Type | Purpose |
|---|---|---|
| `provider` | str | Internal id used elsewhere in gludd config (`aws`, `gcp`, `runpod`, etc.) |
| `display_name` | str | Human-friendly name shown in CLI / TUI |
| `terraform_provider` | str | HCL provider source. `"none (API-only)"` for providers with no terraform integration (Together.ai, Fireworks.ai, HuggingFace, Replicate). |
| `supports_spot` | bool | Whether the provider offers spot/preemptible instances |
| `sub_hour_billing` | bool | Whether billing is per-second (true) or rounded up to the hour (false) |
| `min_gpu` / `max_gpu` | str | Cheapest / most expensive GPU SKU offered |
| `pricing` | dict[str, float] | Per-GPU hourly USD price |

### Providers shipped

| Provider | Spot | Sub-hour | Min GPU | Max GPU |
|---|---|---|---|---|
| `aws` | yes | no | t4 | a100_80 |
| `gcp` | yes | yes | l4 | a100_80 |
| `azure` | yes | no | t4 | a100_80 |
| `runpod` | yes | yes | l4 | a100_80 |
| `vast_ai` | yes | yes | rtx_4090 | a100_80 |
| `lambda_labs` | no | yes | a100_80 | a100_80 |
| `modal` | no | yes | t4 | a100_80 |
| `coreweave` | yes | yes | l40s | a100_80 |
| `digital_ocean` | no | yes | rtx_6000_ada | h100 |
| `oracle` | yes | no | a10 | a100_80 |
| `together_ai` | no (API-only) | yes | a100_40 | h100 |
| `fireworks_ai` | no (API-only) | yes | a100_40 | h100 |
| `huggingface` | no (API-only) | yes | t4 | a100_80 |
| `replicate` | no (API-only) | yes | t4 | a100_80 |

### vLLM defaults

The `vllm_defaults:` block sets defaults applied to any vLLM-serving
deployment regardless of provider:

- `guided_decoding_backend: outlines` — default structured-output backend
- `enable_structured_outputs: true` — turn on JSON/schema-constrained decoding globally
- `available_backends: [outlines, xgrammar, lm-format-enforcer]` — the backends gludd knows how to configure

### Adding a new provider

Append a new entry to `providers:` with all fields populated. If the
provider has a terraform integration, also add the corresponding
`terraform_provider` source string. For API-only providers (no terraform),
use the literal string `"none (API-only)"`. The pricing dict is consumed
by the cost-estimation surface in the daemon.
