# Tracked-file line-limit remediation plan

This is the execution contract for the 31 tracked text files that violated the
strict fewer-than-2,500-physical-lines rule immediately after the root Makefile
was split. No tracked text-file exception is permitted. Every destination file
must target at most 2,000 lines so ordinary maintenance has headroom before the
2,500-line hard gate.

The work lands one oversized file at a time on a feature branch. Each slice
starts with a failing regression, preserves its public entry point, proves the
new shape, and is merged before the next owner edits the same artifact family.
Generated artifacts are regenerated, never patched by hand. Source and tests are
split on behavior boundaries, not arbitrary line ranges.

## Format decisions and evidence

### detect-secrets baseline: repair, then canonical compaction

The current baseline contains findings for `.secrets.baseline` itself and omits
the upstream `is_baseline_file` filter. That recursive inventory accounts for
most of its 208,277 lines. The first implementation step is therefore to restore
the official baseline-file filter and regenerate against the exact tracked-file
inventory. The maintained update command removes findings that no longer exist
while preserving labels, as documented in the
[detect-secrets README](https://github.com/Yelp/detect-secrets/blob/master/README.md).
The upstream design treats a baseline as one JSON document containing settings
and a `SecretsCollection`, so directory fragmentation would break the supported
CLI boundary; see the
[baseline design](https://github.com/Yelp/detect-secrets/blob/master/docs/design.md).

After de-recursion, an atomic JSON serializer uses sorted keys and compact
separators. Whitespace is not part of the JSON data model, so `scan --baseline`,
the pre-commit hook, and `audit --stats` continue to consume the same non-slim
baseline. Gludd must not use upstream `--slim`: the official help says slim
baselines cannot be audited. Normalized JSON equality before and after
compaction, exact finding identity, hook behavior, audit behavior, and a second
regeneration with zero diff are mandatory. A long-lived practitioner question,
[detect-secrets issue 246](https://github.com/Yelp/detect-secrets/issues/246),
shows why the update and hook roles must remain distinct; the project must not
replace them with an ad-hoc scanner.

### uv lock: supported profile fragmentation, never hand-minification

`uv.lock` is managed by uv, has an evolving versioned schema, and should not be
edited manually according to the official
[project layout documentation](https://docs.astral.sh/uv/concepts/projects/layout/).
Although TOML permits equivalent inline tables, custom whitespace compaction is
rejected because uv has a preview canonical-format check and can change its
serializer. A workspace is also not the answer: uv explicitly gives every
workspace one shared lock in its
[workspace documentation](https://docs.astral.sh/uv/concepts/projects/workspaces/).

The supported remediation is to reduce the root project to its deployable core
closure and move optional dependency universes into independent, non-workspace
profile projects under `requirements/profiles/`. Each profile owns its own
`pyproject.toml` and uv-generated `uv.lock`; no profile may exceed 2,000 lines.
Provider, inference, game E2E, Ansible-controller, observability, benchmark, and
developer-tool profiles are selected explicitly by Make targets. If the reduced
core lock still exceeds 2,000 lines, optional runtime adapters move to their own
installable profile before merge; the line gate may not be waived.

Every existing sync, build, container, SBOM, audit, and CI consumer must declare
its profile set and use `uv lock --check` plus `uv sync --locked`. The official
[locking documentation](https://docs.astral.sh/uv/concepts/projects/sync/)
defines those checks. This addresses the real scale failure reported in
[uv issue 9735](https://github.com/astral-sh/uv/issues/9735), while avoiding the
manual pin-and-regenerate workaround discussed there. The continued inclusion
of irrelevant wheels despite environment restrictions was reported in
[uv issue 6512](https://github.com/astral-sh/uv/issues/6512); therefore merely
adding platform markers is not accepted as remediation.

### Markdown: linked indexes and domain pages, not invented includes

CommonMark has no portable include directive. The request remains open in
[CommonMark issue 630](https://github.com/commonmark/commonmark-spec/issues/630),
and users continue to ask for it in the
[CommonMark forum](https://talk.commonmark.org/t/is-it-possible-to-include-files-in-commonmark/4128).
Each large prose document therefore becomes a concise, rendered index linking to
domain pages in a same-named directory. This follows MkDocs' maintained
[multi-page layout](https://github.com/mkdocs/mkdocs/blob/master/docs/user-guide/writing-your-docs.md)
and avoids the preprocessor requested in the long-lived
[MkDocs issue 777](https://github.com/mkdocs/mkdocs/issues/777).

Machine consumers of behavioral specifications use one shared ordered-manifest
loader rather than concatenating Markdown opportunistically. It returns the
same ordered record model to auditors, generators, plugins, and tests. The
top-level page remains a human index; no tool pretends links are textual
includes. Semantic parity is the ordered tuple of spec ID, title, category,
behavior, enforcement, and test fields, not byte-for-byte Markdown.

`AGENTS.md` remains the complete concise normative contract agents receive
automatically. Historical incidents, rationale, and detailed procedures move to
`docs/agent-rules/`, while every mandatory prohibition, safety condition, and
pointer stays in the root file. Structural tests pin those required statements
and reject contradictory copies.

### YAML: explicit manifest and safe composition

YAML 1.2 defines streams of documents but no portable file-include directive.
The practitioner request in
[PyYAML issue 632](https://github.com/yaml/pyyaml/issues/632) demonstrates that
`!include` requires a custom constructor. Gludd will not add a magic tag.
`docs/MCP_TOOLS_TOPICS.yml` becomes a small versioned manifest with an explicit
ordered `parts` list. The generator writes bounded topic shards, and one shared
safe loader validates paths, hashes, schema version, unique topic/tool IDs, and
deterministic order before composing the in-memory model. External consumers
that need a monolith receive an untracked generated artifact, never a second
source of truth.

### Skills, source, and tests

Each expert `SKILL.md` becomes a routing document containing triggers, safety
rules, and a complete reference map. Detailed language guidance moves under its
`references/` directory, matching the skill loader's progressive-disclosure
model. A smoke test follows every reference and proves core prohibitions remain
in the root skill.

Production modules retain their existing import path as a facade and move
cohesive behavior to sibling packages. Imports, signatures, CLI/API behavior,
state ownership, and content-free observability are characterized before the
split. Test modules split by feature or endpoint and share fixtures only through
the narrowest `conftest.py`; CI shard manifests and any stored node IDs migrate
in the same commit. Collection count and behavior, not old test-file paths, are
the compatibility contract.

## Exact 31-file execution ledger

| Path | Owner | Mode | Destination | Required consumer migrations | Validation | Rollback |
| --- | --- | --- | --- | --- | --- | --- |
| `.secrets.baseline` | Security tooling | canonical compaction | Same root path after de-recursion and compact JSON serialization | Baseline generator, disposable pre-push wrapper, pre-commit hook, audit tests, freshness checks | `make secrets-baseline`, `make secrets-scan`, focused security tests, and `make check-file-line-limits` | Revert serializer and restore the reviewed pre-migration baseline atomically |
| `uv.lock` | Dependency and release tooling | profile fragmentation | Root core lock plus independent `requirements/profiles/*/uv.lock` files | Sync, build, container, SBOM, audit, optional-runtime, and CI targets select explicit locked profiles | `make deps-audit`, locked sync behavioral examples, profile parity tests, and `make check-file-line-limits` | Revert profile commits and restore the prior root project and lock together |
| `docs/specs/BEHAVIORAL_SPECS.md` | Enforcement specification | index fragmentation | `docs/specs/behavioral/` ordered shards plus concise index | Shared spec loader, all spec auditors and generators, enforcement plugin, feature inventory, tests | `make lint-specs`, spec claim tests, ordered semantic digest parity, and `make check-file-line-limits` | Revert loader migration and restore the pre-split monolith from the parity fixture |
| `docs/MCP_TOOLS_TOPICS.yml` | MCP documentation generator | index fragmentation | Root YAML manifest plus `docs/mcp-tool-topics/*.yml` shards | Generator, hygiene checker, feature inventory, Ansible sync role, MCP tests | `make mcp-docs-check`, generated-artifact hygiene, schema parity, and `make check-file-line-limits` | Revert manifest consumers and restore the generator-produced monolith |
| `AGENTS.md` | Agent governance | index fragmentation | Concise root contract plus `docs/agent-rules/*.md` rationale pages | Governance tests, instruction mirrors, agent onboarding links | `make test-specific TESTFILE=tests/unit/test_agent_concurrency_contract.py`, documentation integrity tests, and `make check-file-line-limits` | Revert the rule extraction and restore the root contract from its reviewed predecessor |
| `.opencode/skills/go-expert/SKILL.md` | Go skill maintainer | skill routing split | Root routing file plus `.opencode/skills/go-expert/references/*.md` | Skill reference resolver, skill smoke tests, documentation links | `make test-opencode-e2e`, skill reference tests, `make lint-markdown`, and `make check-file-line-limits` | Revert the skill split and restore the prior single SKILL document |
| `.opencode/skills/java-expert/SKILL.md` | Java skill maintainer | skill routing split | Root routing file plus `.opencode/skills/java-expert/references/*.md` | Skill reference resolver, skill smoke tests, documentation links | `make test-opencode-e2e`, skill reference tests, `make lint-markdown`, and `make check-file-line-limits` | Revert the skill split and restore the prior single SKILL document |
| `docs/internal/sprint0.md` | Project history documentation | index fragmentation | `docs/internal/sprint0/` chronological pages plus index | Documentation indexes, inbound anchors, integrity tests | `make lint-markdown`, documentation link tests, anchor redirect checks, and `make check-file-line-limits` | Revert page split and restore the chronological monolith |
| `docs/specs/FEATURE_EXPERT_SYSTEM_INTEROPERABILITY.md` | Expert-system specification | index fragmentation | `docs/specs/expert-system-interoperability/` domain pages plus index | Feature inventory, specification links, claim tests | `make lint-specs`, feature inventory tests, semantic heading parity, and `make check-file-line-limits` | Revert page split and restore the specification monolith |
| `docs/features/BETA4_DUAL_TRACK_CI.md` | CI feature documentation | index fragmentation | `docs/features/beta4-dual-track-ci/` workflow and incident pages plus index | CI docs links, claim freshness, workflow tests | `make lint-markdown`, CI documentation tests, link checks, and `make check-file-line-limits` | Revert page split and restore the feature monolith |
| `docs/design/specs/SPEC_ML_AI_EXPERT_AND_SAFE_SELF_IMPROVEMENT.md` | Self-improvement design | index fragmentation | `docs/design/specs/ml-ai-safe-self-improvement/` bounded design pages plus index | Design inventory, feature links, safety claim tests | `make lint-specs`, self-improvement documentation tests, and `make check-file-line-limits` | Revert page split and restore the reviewed design monolith |
| `src/general_ludd/event_loop/loop.py` | Event-loop runtime | facade split | `src/general_ludd/event_loop/` lifecycle, review, dispatch, and reconciliation modules | Internal imports, monkeypatch targets, coverage config, event-loop tests | `make test-files TESTFILES='tests/unit/test_event_loop.py tests/integration/test_decision_codification_live_review.py'`, strict typing, and `make check-file-line-limits` | Revert the module split while the facade preserves the old import surface |
| `src/general_ludd/cli.py` | CLI runtime | facade split | `src/general_ludd/cli_commands/` command groups plus thin parser facade | Console entry point, parser helpers, CLI tests, completion docs | `make test-files TESTFILES='tests/unit/test_cli.py tests/unit/test_cli_decision_codification.py'`, strict typing, and `make check-file-line-limits` | Revert command modules and restore the facade implementation |
| `src/general_ludd/models/gateway.py` | Model gateway | facade split | `src/general_ludd/models/gateway/` routing, clients, policies, and telemetry modules | Imports, provider fakes, gateway tests, coverage config | `make test-files TESTFILES='tests/unit/test_models_gateway.py'`, strict typing, and `make check-file-line-limits` | Revert package extraction and restore the gateway module |
| `src/general_ludd/daemon.py` | Daemon runtime | facade split | `src/general_ludd/daemon_components/` startup, routes, resources, and shutdown modules | Entrypoint, app factory imports, router tests, resource inventory | `make test-files TESTFILES='tests/unit/test_daemon.py tests/integration/test_full_pipeline_e2e.py'`, resource checks, and `make check-file-line-limits` | Revert component extraction and restore the daemon module |
| `scripts/test_hook_runtime.py` | Hook-runtime tooling | facade split | `scripts/hook_runtime/` fixtures, runner, assertions, and cases plus entry facade | Make hook target, Node fixtures, runtime test imports | `make test-hook-runtime`, scoped lint, and `make check-file-line-limits` | Revert helper extraction and restore the script entry implementation |
| `scripts/agent_watchdog.py` | Watchdog tooling | facade split | `scripts/agent_watchdog/` state, process, policy, and CLI modules plus entry facade | Watchdog Make targets, subprocess tests, resource ownership inventory | `make test-files TESTFILES='tests/unit/test_task_watchdog.py tests/unit/test_agent_watchdog.py'`, scoped lint, and `make check-file-line-limits` | Revert package extraction and restore the script implementation |
| `src/general_ludd/self_improve/codex_comparison.py` | Self-improvement runtime | facade split | `src/general_ludd/self_improve/codex_comparison/` protocol, scoring, execution, and receipts | Imports, proposal targets, comparison tests, coverage config | `make test-specific TESTFILE=tests/unit/test_self_improve_codex_comparison.py`, strict typing, and `make check-file-line-limits` | Revert package extraction and restore the comparison module |
| `src/general_ludd/db/repository.py` | Persistence layer | facade split | `src/general_ludd/db/repositories/` aggregate-specific repositories plus facade | Service imports, transaction fakes, migrations, repository tests | `make test-files TESTFILES='tests/unit/test_repository.py tests/integration/test_database.py'`, strict typing, and `make check-file-line-limits` | Revert repository modules and restore the original facade body |
| `src/general_ludd/self_improve/managed_runner.py` | Self-improvement runtime | facade split | `src/general_ludd/self_improve/managed/` admission, execution, evaluation, and publication modules | Imports, runner factories, managed-runner tests, coverage config | `make test-specific TESTFILE=tests/unit/test_self_improve_managed_runner.py`, strict typing, and `make check-file-line-limits` | Revert managed package extraction and restore the runner module |
| `src/general_ludd/self_improve/runtime.py` | Self-improvement runtime | facade split | `src/general_ludd/self_improve/runtime_components/` discovery, routing, lifecycle, and traces | Imports, runtime assembly, provider tests, coverage config | `make test-files TESTFILES='tests/unit/test_self_improve_runtime.py tests/integration/test_self_improve_runtime.py'`, strict typing, and `make check-file-line-limits` | Revert component extraction and restore the runtime module |
| `src/general_ludd/pricing_intel/sources.py` | Pricing intelligence | facade split | `src/general_ludd/pricing_intel/sources/` provider-specific source modules plus registry facade | Source registry imports, provider fakes, pricing tests | `make test-files TESTFILES='tests/unit/test_pricing_intel.py'`, strict typing, and `make check-file-line-limits` | Revert source package extraction and restore the registry module |
| `tests/e2e/test_game_building_deepseek.py` | Game E2E tests | test split | `tests/e2e/game_building_deepseek/` scenario modules with shared fixtures | CI shard inventory, markers, stored node IDs, coverage config | `make test-files TESTFILES='tests/e2e/game_building_deepseek'`, collection parity, and `make check-file-line-limits` | Revert test split and restore the original test module |
| `tests/unit/test_self_improve_codex_comparison.py` | Self-improvement tests | test split | `tests/unit/self_improve_codex_comparison/` protocol, failure, scoring, and receipt tests | CI shard inventory, node IDs, shared fixtures, coverage target | `make test-files TESTFILES='tests/unit/self_improve_codex_comparison'`, collection parity, and `make check-file-line-limits` | Revert test split and restore the original test module |
| `tests/unit/test_ci_named_shard_files.py` | CI infrastructure tests | test split | `tests/unit/ci_named_shard_files/` schema, selection, balance, and failure tests | Named-shard config, cached node IDs, test selectors | `make test-files TESTFILES='tests/unit/ci_named_shard_files'`, shard parity, and `make check-file-line-limits` | Revert test split and restore the original test module |
| `tests/unit/test_automatic_disk_cleanup.py` | Resource cleanup tests | test split | `tests/unit/automatic_disk_cleanup/` policy, lease, pressure, and recovery tests | CI shard inventory, fixtures, cleanup target references | `make test-files TESTFILES='tests/unit/automatic_disk_cleanup'`, resource parity, and `make check-file-line-limits` | Revert test split and restore the original test module |
| `tests/unit/test_behavioral_enforcement.py` | Enforcement tests | test split | `tests/unit/behavioral_enforcement/` policy-family modules | Spec-to-test mappings, CI shard inventory, fixtures, node IDs | `make test-files TESTFILES='tests/unit/behavioral_enforcement'`, spec mapping parity, and `make check-file-line-limits` | Revert test split and restore the enforcement test module |
| `tests/unit/test_self_improve_codex_runner.py` | Self-improvement tests | test split | `tests/unit/self_improve_codex_runner/` lifecycle, failures, budgets, and receipts | CI shard inventory, fixtures, coverage target | `make test-files TESTFILES='tests/unit/self_improve_codex_runner'`, collection parity, and `make check-file-line-limits` | Revert test split and restore the original runner test module |
| `tests/unit/test_probabilistic_deep.py` | Probabilistic-system tests | test split | `tests/unit/probabilistic_deep/` primitive, composition, persistence, and edge tests | CI shard inventory, fixtures, coverage target | `make test-files TESTFILES='tests/unit/probabilistic_deep'`, collection parity, and `make check-file-line-limits` | Revert test split and restore the original probabilistic test module |
| `tests/e2e/test_connectors_batch5_workflows.py` | Connector E2E tests | test split | `tests/e2e/connectors_batch5/` connector-family workflow modules | CI shard inventory, E2E selectors, shared connector fixtures | `make test-files TESTFILES='tests/e2e/connectors_batch5'`, scenario parity, and `make check-file-line-limits` | Revert test split and restore the original connector test module |
| `tests/unit/test_routers_endpoints.py` | API router tests | test split | `tests/unit/router_endpoints/` endpoint-family modules | CI shard inventory, router fixtures, node IDs, coverage target | `make test-files TESTFILES='tests/unit/router_endpoints'`, route matrix parity, and `make check-file-line-limits` | Revert test split and restore the original router test module |

## Merge waves and hard gates

1. **Generated/config wave:** repair and compact `.secrets.baseline`, then split
   dependency profiles, then shard MCP topics. These are separate commits because
   each changes a release or security boundary. A failed security, lock, SBOM, or
   generated-artifact check rolls back that slice before the next starts.
2. **Governance/docs wave:** split behavioral specs first so its shared loader is
   available, then AGENTS, skills, and remaining prose one artifact at a time.
   Preserve inbound anchors or add explicit index redirects.
3. **Production wave:** characterize one public facade, split it, meet at least
   85% aggregate and 75% per-file branch-aware coverage, then merge before the
   next source owner begins.
4. **Test wave:** split one oversized test, update its shard registration and
   node-ID consumers, and prove collection/behavior parity before continuing.
5. **Convergence wave:** run `make check-file-line-limits
   FILE_LINE_LIMIT_POLICY=config/file_line_limits.json`, generated-artifact
   hygiene, duplicate-target and Make contract checks, then an exact-head
   `make gate`. Only the green integration head may update remote development.

The inventory is recalculated after every merge. Discovery of another oversized
tracked text file adds it to this ledger before work continues; it never creates
an allowlist entry. A rollback restores the complete pre-split artifact and all
of its consumers in one commit, so no half-migrated reader is left on the shared
development branch.
