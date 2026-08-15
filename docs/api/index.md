# API Reference

REST API endpoints and messaging interfaces for the General Ludd daemon.

## Contents

This directory does not yet hold split-out API pages; everything is
documented inline below: [Endpoint Overview](#endpoint-overview),
[Message Queue](#message-queue), [Model Gateway](#model-gateway).

## Base URL

```
http://localhost:8000
```

All endpoints require PSK authentication via `Authorization: Bearer <GLUDD_AUTH_PSK>` header, except:
- `GET /healthz` — public health check
- `GET /api/facts` — public (read-only daemon snapshot)
- `GET /api/human-todos` — public (human-visible queue)
- `GET /api/human-todos/feed` — public (incremental feed)

## Endpoint Overview

### Core API

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/healthz` | Health check (public) |
| `GET` | `/api/facts` | Live daemon snapshot (Ansible facts) |
| `GET` | `/api/metrics` | Agent metrics, model usage, costs, benchmarks |
| `GET` | `/api/traces` | Recent execution traces with aggregates |
| `GET` | `/api/todos` | Task queue management |
| `POST` | `/api/todos` | Create a new todo |
| `GET` | `/api/messages` | Message inbox |
| `POST` | `/api/messages` | Send a message |
| `POST` | `/api/messages/{id}/ack` | Acknowledge a message |

### Admin API (PSK Required)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/admin/metrics/export` | Metrics export |
| `POST` | `/admin/projects` | Register a project |
| `GET` | `/admin/projects` | List projects |
| `POST` | `/admin/models/call` | Raw model call with accounting |
| `GET` | `/admin/remediation/scan` | Run blocker detection |
| `POST` | `/admin/remediation/remediate` | Apply remediation |
| `GET` | `/admin/remediation/chronic-blockers` | Chronic blocker report |
| `GET` | `/admin/remediation/history` | Remediation audit trail |
| `POST` | `/admin/perm/escalation-request` | Request permission escalation |
| `GET` | `/admin/perm/escalation-request` | List escalation requests |

### Human Todo API

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `GET` | `/api/human-todos` | Public | List human todos |
| `GET` | `/api/human-todos/feed` | Public | Incremental feed |
| `GET` | `/api/human-todos/{id}` | Public | Fetch one |
| `POST` | `/api/human-todos` | PSK | File a request |
| `PATCH` | `/api/human-todos/{id}` | PSK | Resolve/advance |
| `DELETE` | `/api/human-todos/{id}` | PSK | Soft delete |
| `POST` | `/api/human-todos/{id}/tags` | PSK | Add tag/comment |

### Observability API

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/observe/sources` | Registered connector sources |
| `GET` | `/api/observe/health` | Connector health |
| `POST` | `/api/observe/query` | Query a connector |
| `POST` | `/api/observe/timeline` | Cross-source timeline (GluddObserve) |
| `POST` | `/api/observe/correlate` | Incident correlation (GluddObserve) |
| `POST` | `/v1/logs` | Ingest logs (ingest token) |
| `POST` | `/v1/metrics` | Ingest metrics (ingest token) |
| `POST` | `/v1/traces` | Ingest traces (ingest token) |
| `POST` | `/ingest/webhook` | Generic webhook ingest |
| `POST` | `/ingest/gelf` | GELF ingest |
| `POST` | `/ingest/fluent` | Fluent Bit ingest |
| `POST` | `/ingest/beats` | Beats ingest |

### Model Performance API

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/model-performance/summary` | Aggregate performance by model |
| `GET` | `/api/model-performance/by-role` | Performance by role |
| `GET` | `/api/model-performance/by-task` | Performance by task type |
| `GET` | `/api/model-performance/leaderboard` | Cost/quality leaderboard |

### Self-Update API

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/admin/self-update/plan` | Submit NL plan for self-update |
| `POST` | `/admin/self-update/enqueue` | Enqueue self-update as todo |

## Message Queue

The message queue (`/api/messages`) enables inter-agent coordination:

- **Broadcast**: Send to all agents by omitting `recipient_id`
- **Direct**: Target a specific `recipient_id`
- **Ack**: `POST /api/messages/{id}/ack` marks message read
- **Inbox**: `GET /api/messages?recipient_id=X` fetches unread

## Model Gateway

The model gateway (`src/general_ludd/models/gateway.py`) handles:

- **Provider Registry**: Dynamic LangChain provider loading
- **Model Profiles**: Per-model config (cost, quality, latency, fallbacks)
- **Routing**: Role-based, quality-based, latency-based selection
- **Billing**: Per-token cost accounting, budget guards
- **Health**: Circuit breakers, fallback chains
- **Secrets**: OpenBao / env var resolution

## Quick Examples

### Submit a Todo
```bash
curl -X POST http://localhost:8000/api/todos \
  -H "Authorization: Bearer $GLUDD_AUTH_PSK" \
  -H "Content-Type: application/json" \
  -d '{"title": "Fix login bug", "description": "OAuth callback fails", "queue": "core", "priority": "high", "work_type": "code"}'
```

### Get Daemon Facts (for Ansible)
```bash
curl http://localhost:8000/api/facts | jq '.gludd.work.todos[] | select(.status=="queued")'
```

### List Human Todos
```bash
curl http://localhost:8000/api/human-todos
```

---

[Back to Documentation Index](../index.md)
