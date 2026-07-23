# gludd Operating Workflows

**Status:** current for v0.1.0-beta.1 documentation
**Last updated:** 2026-07-22

This guide maps the current project surfaces to the ways an operator or developer is expected to use gludd. It complements the README, smoke-test guide, collection design docs, and Terraform design docs.

## Documentation Map

- Project overview and architecture: [README.md](../README.md)
- Provider and platform smoke tests: [SMOKE_TESTS.md](SMOKE_TESTS.md)
- Configuration reference: [CONFIG_REFERENCE.md](CONFIG_REFERENCE.md)
- Project-local collections: [design/PROJECT_COLLECTIONS.md](design/PROJECT_COLLECTIONS.md)
- Collection layout contract: [design/COLLECTION_STRUCTURE.md](design/COLLECTION_STRUCTURE.md)
- Terraform infrastructure: [design/TERRAFORM_INFRA_STRUCTURE.md](design/TERRAFORM_INFRA_STRUCTURE.md)
- Model-serving deployment: [design/MODEL_SERVING_DEPLOYMENT.md](design/MODEL_SERVING_DEPLOYMENT.md)
- Presentation source and diagram policy: [presentation/DESIGN_revealjs_deck.md](presentation/DESIGN_revealjs_deck.md)

## Diagram and Presentation Policy

Use Mermaid fenced code blocks for flowcharts and sequence diagrams in Markdown. GitHub renders Mermaid natively in Markdown files, issues, pull requests, discussions, gists, and wikis, so the repo should not add a third-party GitHub diagram plugin for normal Markdown diagrams. GitHub also documents that third-party Mermaid plugins can cause rendering errors.

The reveal.js presentation keeps using the existing reveal.js Mermaid plugin because the deck is rendered outside GitHub Markdown. Keep the source diagram in Mermaid whenever possible, then let GitHub render the docs and reveal.js render the deck.

When replacing an ASCII diagram:

1. Convert the source to a Mermaid block.
2. Keep node labels short enough to render in GitHub and in the deck.
3. Prefer one current source diagram over a generated image artifact.
4. If GitHub rendering differs from the deck, simplify Mermaid syntax before adding a new renderer.

## Daily Use Workflow

1. Install dependencies and run the bootstrap checks from the README.
2. Start the daemon with a config directory that contains model profiles.
3. Submit todos through the CLI or API.
4. Inspect status, logs, traces, and metrics before trusting a completed return.
5. Run a provider smoke test before assigning real work to a new provider or model.
6. Keep smoke-test JSON output whenever a provider path fails. That file is the repair artifact.

For a checkout config, point the daemon at the repository config directory before startup. Without that config, the model gateway has no active profile and the dispatcher can fall back to a no-op executor.

## Provider Smoke Workflow

The smoke command shape is intentionally short for manual runs with real credentials:

```bash
AWS_KEY=foo AWS_SECRET=bar gludd smoke aws ec2-a100 --json --output /tmp/gludd-aws-ec2-a100.json
OPENROUTER_API_KEY=foo gludd smoke openrouter model-ping --live --json --output /tmp/gludd-openrouter.json
```

Use dry-run or metadata checks first when a provider supports them. Use live `model-ping` to prove a configured API key can make a minimal completion request. Use provisioned compute smoke tests only when you want gludd to stand up an endpoint, probe it, run a one-token task, capture metrics, and tear it down.

All smoke tests default to a USD 10.00 cost ceiling. Keep that default unless a test document explicitly says the scenario needs a lower ceiling. A smoke test must stop before spending money when the estimate exceeds the ceiling.

The saved JSON report is the handoff object. It includes run identity, provider, test name, mode, status, logs, metrics, events, ordered trace, endpoint diagnostics, redacted credential presence, functional scope, coverage depth, and an `analysis_prompt` field.

Manual repair handoff:

1. Run the smoke test with `--json --output` and keep the file path.
2. Confirm the output contains `analysis_prompt` and no raw secrets.
3. Give the file path to a separate agent or AI repair session.
4. Ask that agent to use the report `analysis_prompt`, ordered trace, logs, events, metrics, endpoint diagnostics, and provider/test fields.
5. Include only the saved path and any local reproduction notes. Do not paste API keys.

A useful request to a repair agent is:

```text
Please analyze the Gludd smoke report at /tmp/gludd-openrouter.json. Use the report analysis_prompt, trace_id, ordered trace, logs, events, metrics, endpoint_diagnostics, and functional_scope to identify the failing provider path and propose the focused code, tests, and docs changes needed to fix it.
```

## Smoke Depth Expectations

Every provider or platform that gludd has code to use should have a smoke path. API providers should support credential checks, metadata checks where available, and low-cost model-ping checks. Compute providers should support credential checks and GPU smoke aliases for the provider price catalog. Local or provisioned model-serving platforms should prove health, model listing, completion, and metrics collection.

Provisioned vLLM and llama.cpp smoke tests must:

- create the resource through the gludd deployment path
- pass tunables such as model, engine, GPU count, region, network CIDR, decoding options, and deployment profile
- probe health, model list, and metrics endpoints
- run a minimal inference task
- assert the model id and process or engine metrics return to gludd
- destroy the resource in cleanup even when the task fails
- write a report detailed enough for a third party to diagnose provider, deployment, model, or teardown failures

Multi-provider and multi-platform smokes validate that gludd can juggle more than one usable model path. Use them after individual providers pass, or when debugging routing and fallback behavior.

## Adding New Ideas or Features

Use the smallest surface that matches the idea:

- CLI, daemon, model routing, storage, or reporting behavior belongs in Python under `src/general_ludd/` with focused unit tests under `tests/`.
- Reusable task automation belongs in the bundled Ansible collection under `collections/ansible_collections/general_ludd/agent/`.
- Project-only behavior belongs in a project-local collection under `.gludd/collections/`.
- Provider infrastructure belongs in `infra/terraform/` stacks and modules, with validation tests.
- Presentation or operator guidance belongs in `docs/` and the reveal.js deck design notes.

Feature workflow:

1. Write or update the docs for the expected behavior and operator contract.
2. Add a failing test for the behavior.
3. Implement the narrowest code or collection change.
4. Run the focused tests for the changed surface.
5. Run the relevant gate target before committing.
6. Commit the docs, tests, and implementation as one complete logical change when they describe the same behavior.

For new smoke-test coverage, start with the report contract. The output must tell a repair agent what provider path was exercised, what was skipped, what credentials were present, what request was sent, what endpoint answered, what metrics were seen, what cleanup ran, and what failed.

## Internal Collections and Custom Business Logic

Use `gludd project init --namespace <namespace> --collection <collection>` to scaffold a project-local collection. That creates a collection under `.gludd/collections/ansible_collections/<namespace>/<collection>/` and records the chosen collection in `.gludd/config.yml`.

Use a custom namespace when adding project-only roles or modules. Use the same `general_ludd.agent` namespace and collection only when you intentionally want to shadow a bundled role or module by fully qualified name.

Common internal collection patterns:

- add a role that calls a company deployment wrapper
- add a module that reads an internal service catalog
- add a role that formats pull request evidence in a company template
- override `general_ludd.agent.project_init` to add local scaffold files
- add Terraform plugin material under `plugins/terraform/` for local infrastructure conventions
- add module utilities under `plugins/module_utils/` for shared business logic

Business logic should stay in the project collection when it is private, organization-specific, or not generally reusable. Move it into the bundled collection only when it is generic and covered by repo tests.

## Terraform and Model Serving Workflow

Use the implemented Terraform layout under `infra/terraform/` for reviewable GPU-serving infrastructure. Modules hold shared engine logic. Stacks compose provider-specific vLLM and llama.cpp deployments. Provider versions are pinned in `infra/terraform/versions.tf`, and the make targets warm and use the shared plugin cache.

Typical validation flow:

1. Choose a stack under `infra/terraform/stacks/`.
2. Warm the provider cache with the Terraform cache target.
3. Initialize the stack with the Terraform init target.
4. Validate the stack with the Terraform validate target.
5. Run a dry-run smoke test for the matching provider and GPU type.
6. Run a provisioned smoke test only when ready to spend bounded cloud cost.

Use Slurm batch deployment when the operator has a Slurm-managed GPU cluster. Use Terraform for cloud or private-cloud GPU capacity. Use local vLLM, llama.cpp, or Ollama paths for development and CI smoke surfaces.

## Mermaid Rendering Notes from GitHub and Community Reports

The repo uses GitHub-native Mermaid for Markdown diagrams rather than a third-party GitHub plugin.

References and operating decisions:

- GitHub documentation says Mermaid fenced code blocks render in GitHub Markdown surfaces, and standalone `.mmd` or `.mermaid` files can render in repositories.
- GitHub documentation warns that third-party Mermaid plugins can cause errors when GitHub renders Mermaid syntax.
- GitHub documentation lists known Mermaid rendering issues including extra padding below sequence diagrams, actor popover limitations, and incomplete accessibility coverage.
- GitHub Community discussion 106690 reports that HTML and links inside Mermaid nodes have changed behavior over time. Keep node labels plain and avoid clickable HTML or Markdown links inside diagram nodes.
- GitHub Community discussion 121855 reports inconsistent preview behavior for standalone `.mmd` editing compared with Markdown fenced Mermaid preview. Prefer fenced Mermaid blocks in Markdown docs for this repo.

Official docs:

- https://docs.github.com/en/get-started/writing-on-github/working-with-advanced-formatting/creating-diagrams
- https://docs.github.com/en/repositories/working-with-files/using-files/working-with-non-code-files#displaying-mermaid-files-on-github

Community reports:

- https://github.com/orgs/community/discussions/106690
- https://github.com/orgs/community/discussions/121855
