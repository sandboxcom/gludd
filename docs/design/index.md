# Design Documents

Architecture decision records and design specifications.

## Contents

| Document | Description |
|----------|-------------|
| [Symbiotic Agent Integration](SYMBIOTIC_AGENT_INTEGRATION.md) | Ornith self-improving agent integration |
| [Collection Structure](COLLECTION_STRUCTURE.md) | Terraform/OPA collection layout and precedence |
| [Queue Lease Audit](../audit/QUEUE_LEASE_CLAIM_CONCURRENCY_AUDIT_2026-06-25.md) | Queue lease claim concurrency audit |
| [Permission System](PERMISSION_SYSTEM.md) | Capability-based permission system |
| [Sandbox Backends](SANDBOX_BACKENDS.md) | OS-level sandbox backends (Landlock, bubblewrap, etc.) |
| [Human Todos](HUMAN_TODOS.md) | Bot→Human request system |
| [Project Collections](PROJECT_COLLECTIONS.md) | 3-tier collection precedence contract |
| [Remediation System](REMEDIATION_SYSTEM.md) | Blocker detection and remediation |
| [OpenBao Break-Glass Backup](OPENBAO_BREAK_GLASS_BACKUP.md) | Encrypted OpenBao raft store backup |
| [Daemon Integration Plan](daemon_integration_plan.md) | Wiring built-but-unwired modules |
| [Model Serving Deployment](MODEL_SERVING_DEPLOYMENT.md) | Slurm, bare-metal, cloud model serving paths |
| [Terraform Infra Structure](TERRAFORM_INFRA_STRUCTURE.md) | Hybrid static modules + dynamic composition |
| [Pipeline Controller](pipeline_controller.md) | Async lanes, pipeline controller |
| [Self-Update Router](self_update_router.md) | Self-update mechanism |
| [Connector Wiring Plan](connector_wiring_plan.md) | Observability connector wiring |
| [Feature Package Wiring](feature_package_wiring.md) | Feature package wiring |
| [Observability Receiver](observability_receiver.md) | OTLP/webhook/GELF receiver |
| [MCP Catalog OOM Fix](MCP_CATALOG_OOM_FIX_SPEC.md) | MCP catalog OOM fix specification |
| [Tool Calls via Ansible](tool_calls_via_ansible.md) | Tool call execution via Ansible |
| [Issue Sources](issue_sources.md) | Issue source connectors design |
| [Git Execution Architecture](git_execution_architecture.md) | Git execution architecture |
| [Contextual Logging](CONTEXTUAL_LOGGING_AND_ERROR_REPRO.md) | Logging and error reproduction |
| [Connector Join Key Normalization](connector_join_key_normalization.md) | Connector join key normalization |
| [Feature Gap Backlog](feature_gap_backlog.md) | Feature gap backlog |
| [Batch 5 Roadmap](BATCH5_ROADMAP.md) | Batch 5 roadmap |
| [Unified Data Source Relevance](unified_data_source_relevance.md) | Unified data source relevance |
| [Playbook Web Renderer](PLAYBOOK_WEB_RENDERER.md) | Playbook web renderer design |
| [Per-Model Prompt Adapter](per_model_prompt_adapter.md) | Per-model prompt adapter |
| [Backlog Audit System](backlog_audit_system.md) | Backlog audit system |
| [ML/AI Expert and Safe Self-Improvement](specs/SPEC_ML_AI_EXPERT_AND_SAFE_SELF_IMPROVEMENT.md) | Versioned expert collection and governed continual-research contract |

## Related Sections

- [Architecture](../architecture/) — System architecture
- [API Reference](../api/) — API design
- [Development](../development/) — Development guides

---

[Back to Documentation Index](../index.md)
