# Design Documents

Architecture decision records and design specifications.

## Contents

| Document | Description |
|----------|-------------|
| [Symbiotic Agent Integration](symbiotic-agent.md) | Ornith self-improving agent integration |
| [Collection Structure](collection-structure.md) | Terraform/OPA collection layout and precedence |
| [Queue Lease Audit](queue-lease-audit.md) | Queue lease claim concurrency audit |
| [Permission System](permission-system.md) | Capability-based permission system |
| [Sandbox Backends](sandbox-backends.md) | OS-level sandbox backends (Landlock, bubblewrap, etc.) |
| [Human Todos](human-todos.md) | Bot→Human request system |
| [Project Collections](project-collections.md) | 3-tier collection precedence contract |
| [Remediation System](remediation-system.md) | Blocker detection and remediation |
| [OpenBao Break-Glass Backup](openbao-break-glass-backup.md) | Encrypted OpenBao raft store backup |
| [Daemon Integration Plan](daemon_integration_plan.md) | Wiring built-but-unwired modules |
| [Model Serving Deployment](model-serving-deployment.md) | Slurm, bare-metal, cloud model serving paths |
| [Terraform Infra Structure](terraform-infra-structure.md) | Hybrid static modules + dynamic composition |
| [Pipeline Controller](pipeline-controller.md) | Async lanes, pipeline controller |
| [Self-Update Router](self-update-router.md) | Self-update mechanism |
| [Connector Wiring Plan](connector-wiring-plan.md) | Observability connector wiring |
| [Feature Package Wiring](feature-package-wiring.md) | Feature package wiring |
| [Observability Receiver](observability-receiver.md) | OTLP/webhook/GELF receiver |
| [MCP Catalog OOM Fix](mcp-catalog-oom-fix.md) | MCP catalog OOM fix specification |
| [Tool Calls via Ansible](tool-calls-via-ansible.md) | Tool call execution via Ansible |
| [Issue Sources](issue-sources.md) | Issue source connectors design |
| [Git Execution Architecture](git-execution-architecture.md) | Git execution architecture |
| [Contextual Logging](contextual-logging-and-error-repro.md) | Logging and error reproduction |
| [Connector Join Key Normalization](connector-join-key-normalization.md) | Connector join key normalization |
| [Feature Gap Backlog](feature-gap-backlog.md) | Feature gap backlog |
| [Batch 5 Roadmap](batch5-roadmap.md) | Batch 5 roadmap |
| [Unified Data Source Relevance](unified-data-source-relevance.md) | Unified data source relevance |
| [Playbook Web Renderer](playbook-web-renderer.md) | Playbook web renderer design |
| [Per-Model Prompt Adapter](per-model-prompt-adapter.md) | Per-model prompt adapter |
| [Backlog Audit System](backlog-audit-system.md) | Backlog audit system |

## Related Sections

- [Architecture](../architecture/) — System architecture
- [API Reference](../api/) — API design
- [Development](../development/) — Development guides

---

[Back to Documentation Index](../index.md)