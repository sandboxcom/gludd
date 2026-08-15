# Architecture

System architecture documentation for the General Ludd daemon.

## Contents

This directory does not yet hold split-out architecture pages. The full
system architecture (daemon lifecycle, event loop, worker, Ansible
integration) is documented in one place: [docs/architecture.md](../architecture.md).

## Quick Reference

```text
User ──CLI/TUI──▶ Daemon (FastAPI + Gunicorn, :8000)
                      │
        ┌─────────────┼─────────────┐
        ▼             ▼             ▼
   Event Loop    Admin Router    Todo Router
        │
   ┌────┼────┬──────────┬──────────┐
   ▼    ▼    ▼          ▼          ▼
Claim Dispatch Review  Reconcile Self-Improve
        │
   ┌────▼────┐
   │ Ansible │  (general_ludd.agent collection)
   │ Runner  │
   └────┬────┘
        │
┌───────┼────────────────────┐
▼       ▼                    ▼
gludd_* Roles (~34)      Model Gateway
modules                    │
        │                 ▼
        ▼        ┌─────────────────────────┐
      ┌─────────────────────────────────┐  │
      │     SQLite (single-worker)      │  │
      │  todos · returns · benchmarks   │  │
      │  messages · metrics · traces    │  │
      └─────────────────────────────────┘  │
```

## Key Components

| Component | Location | Purpose |
|-----------|----------|---------|
| Daemon | `src/general_ludd/daemon.py` | FastAPI app, lifespan, routers |
| Event Loop | `src/general_ludd/event_loop/loop.py` | Tick cycle, phases |
| Worker | `src/general_ludd/worker/app.py` | Subprocess execution |
| Ansible Runner | `src/general_ludd/ansible/runner.py` | Playbook execution |
| Model Gateway | `src/general_ludd/models/gateway.py` | Multi-model routing |
| Database | `src/general_ludd/db/` | SQLite + SQLAlchemy + Alembic |
| Permissions | `src/general_ludd/security/permissions.py` | Capability-based auth |
| Sandboxes | `src/general_ludd/security/sandboxes/` | Landlock, bubblewrap, etc. |

## Related Design Docs

- [Daemon Integration Plan](../design/daemon_integration_plan.md) — Wiring built-but-unwired modules
- [Model Serving Deployment](../design/MODEL_SERVING_DEPLOYMENT.md) — Slurm, bare-metal, cloud paths
- [Terraform Infra Structure](../design/TERRAFORM_INFRA_STRUCTURE.md) — Hybrid static modules + dynamic composition
- [Pipeline Controller](../design/pipeline_controller.md) — Async lanes, pipeline controller
- [Self-Update Router](../design/self_update_router.md) — Self-update mechanism
- [Connector Wiring Plan](../design/connector_wiring_plan.md) — Observability connector wiring
- [Feature Package Wiring](../design/feature_package_wiring.md) — Feature package wiring
- [Observability Receiver](../design/observability_receiver.md) — OTLP/webhook/GELF receiver
- [Remediation System](../design/REMEDIATION_SYSTEM.md) — Blocker detection and remediation
- [OpenBao Break-Glass Backup](../design/OPENBAO_BREAK_GLASS_BACKUP.md) — Encrypted backup of OpenBao raft store

---

[Back to Documentation Index](../index.md)
