# CLI Reference

Command-line interface for the General Ludd agent.

## Contents

This directory does not yet hold split-out CLI pages; all commands are
covered in the Quick Reference below. For configuration file details, see
[docs/CONFIG_REFERENCE.md](../CONFIG_REFERENCE.md).

## Quick Reference

```bash
gludd --help                    # Global help
gludd <command> --help          # Command-specific help
gludd daemon --port 8000        # Start the daemon
gludd todo add "Fix bug"        # Add a todo
gludd todo list --status queued # List queued todos
gludd status                    # Daemon status
gludd health                    # Health check
gludd version                   # Version info
gludd project init --namespace acme --collection project  # Scaffold project collection
gludd project paths             # Show collection precedence
gludd human-todo list           # List human todos
gludd human-todo done HTODO-xxx --resolution "Fixed"      # Resolve human todo
gludd remediation scan          # Run blocker detection
gludd remediation chronic-blockers  # Show chronic blockers
```

## Global Options

| Option | Description |
|--------|-------------|
| `--config-dir PATH` | Config directory (default: `~/.config/general-ludd`) |
| `--daemon-url URL` | Daemon URL (default: `http://localhost:8000`) |
| `--json` | Output JSON instead of table |
| `-v, --verbose` | Verbose output |

## Authentication

Write operations require the daemon PSK. Set `GLUDD_AUTH_PSK` environment variable or pass `--psk` (if supported by subcommand).

```bash
export GLUDD_AUTH_PSK="your-psk-here"
gludd todo add "New feature"
```

## Configuration Files

| File | Purpose |
|------|---------|
| `~/.config/general-ludd/general-ludd.yml` | Main configuration |
| `~/.config/general-ludd/model_routing.yml` | Model routing with fallback chains |
| `~/.config/general-ludd/model_profiles/*.yml` | Model profiles (API keys, endpoints) |
| `~/.config/general-ludd/openbao/default.yml` | OpenBao secrets backend |
| `~/.config/general-ludd/.gludd/config.yml` | Project-local collection config |

See [docs/CONFIG_REFERENCE.md](../CONFIG_REFERENCE.md) for details.

---

[Back to Documentation Index](../index.md)
