# Operations Guides

Operational guides for running General Ludd in production.

## Contents

This directory does not yet hold split-out operations pages. Monitoring and
observability sources are covered in [docs/OBSERVABILITY_SOURCES.md](../OBSERVABILITY_SOURCES.md);
configuration (including budget settings) is in
[docs/CONFIG_REFERENCE.md](../CONFIG_REFERENCE.md). The quick reference below
covers day-to-day health/budget checks and troubleshooting entry points.

## Quick Reference

### Health Checks
```bash
# Daemon health
curl http://localhost:8000/healthz

# Full status with facts
curl -H "Authorization: Bearer $GLUDD_PSK" http://localhost:8000/api/facts

# Metrics export
curl -H "Authorization: Bearer $GLUDD_PSK" http://localhost:8000/admin/metrics/export
```

### Budget Monitoring
```bash
# Check current spend
curl -H "Authorization: Bearer $GLUDD_PSK" http://localhost:8000/api/metrics | jq '.budget'

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
