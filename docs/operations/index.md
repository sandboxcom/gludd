# Operations Guides

Operational guides for running General Ludd in production.

## Contents

Monitoring and observability sources are covered in
[docs/OBSERVABILITY_SOURCES.md](../OBSERVABILITY_SOURCES.md); configuration
(including budget settings) is in
[docs/CONFIG_REFERENCE.md](../CONFIG_REFERENCE.md). The quick reference below
covers day-to-day health/budget checks and troubleshooting entry points.

- [Podman 5.8 AppleHV startup recovery](PODMAN_APPLEHV_STARTUP_RECOVERY.md) —
  machine-scoped diagnosis and recovery for a macOS VM stuck at
  `Currently starting`, without resetting unrelated machines.

**The troubleshooting table lives in
[CONFIG_REFERENCE.md §4](../CONFIG_REFERENCE.md#4-troubleshooting)** — start there for
symptom → cause → fix.

## The first thing to check: are model profiles actually loaded?

Config discovery is `$GLUDD_CONFIG_DIR` → `~/.config/general-ludd` → `/etc/general-ludd`.
**The repo's own `config/` directory is NOT on that path.** A daemon started from a
checkout without `GLUDD_CONFIG_DIR` loads no model profiles, the model gateway stays
`None`, and the dispatcher silently falls back to a **no-op executor**: every dispatched
agent returns `status="completed"` with **empty output** and no warning, while `/healthz`
and `/readyz` still return 200/ready.

```bash
gludd models router-status   # MUST list an active profile — empty means the daemon can do no work
```

**`/healthz` returning ok does not mean the daemon can do any work.** Treat
`models router-status` as the real liveness check.

## Quick Reference

### Health Checks
```bash
# Daemon health (liveness only — does NOT prove model profiles loaded)
curl http://localhost:8000/healthz

# Full status with facts
curl -H "Authorization: Bearer $GLUDD_AUTH_PSK" http://localhost:8000/api/facts

# Metrics export
curl -H "Authorization: Bearer $GLUDD_AUTH_PSK" http://localhost:8000/admin/metrics/export
```

### Budget Monitoring
```bash
# Check current spend
curl -H "Authorization: Bearer $GLUDD_AUTH_PSK" http://localhost:8000/api/metrics | jq '.budget'

# Budget alerts are logged at warn/80% and error/100%
```

### Log Locations
| Component | Location |
|-----------|----------|
| Daemon | `journalctl -u gludd` or stdout |
| Gate runs | `.gate-logs/gate-<timestamp>.log` |
| Molecule | `molecule/default/molecule.log` |
| Ansible | `~/.ansible/ansible.log` |

### Common Operations

| Task | Command |
|------|---------|
| View gate status | `cat .gate-status` |
| Run gate in background | `make gate-background` |
| Check background gate | `make gate-status-check` |
| Tail gate log | `make gate-tail` |
| Kill background gate | `make gate-kill` |
| Verify remote push | `make verify-remote BRANCH=master SHA=<sha>` |
| Check CI verdict | `make ci-verdict BRANCH=master` |

---

[Back to Documentation Index](../index.md)
