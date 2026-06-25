# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in General Ludd, please
**do not** open a public issue. Instead, report it privately.

Email: security@general-ludd.dev (or file a private security advisory on GitHub)

We will respond within 72 hours and aim to release a fix within 14 days.

## Supported Versions

Only the latest release is supported for security fixes.
Prereleases (`v0.1.0-alpha-*`) receive best-effort support.

## Operator Security Requirements

### `GLUDD_PSK` — pre-shared key (required in production)

The daemon uses a PSK for Bearer-token auth on all non-public paths.

- **Unset (default):** daemon starts **fail-closed**. Every request to a
  non-public path returns `503 {"error":"auth_required"}`. A loud `SECURITY:`
  warning is emitted at startup.
- **Set:** callers must send `Authorization: Bearer <GLUDD_PSK>` on every
  non-public request. Comparison is constant-time (`hmac.compare_digest`).
- **`GLUDD_ALLOW_NO_AUTH=1`:** disables fail-closed for **development only** —
  the entire `/admin` surface becomes open to any caller. Never set in
  production. `GLUDD_REQUIRE_AUTH=1` overrides it (fail-closed always wins).

*Verified: `src/general_ludd/daemon.py` lines 1458–1541.*

### `GL_INTEGRITY_KEY` — integrity signing key (required for integrity features)

Used to HMAC-sign file-change records. If unset, `_get_integrity_key()` raises
`IntegrityKeyError` (fail-closed — no ephemeral fallback). The
`/admin/integrity/approve` endpoint returns **503** until the key is
provisioned. Provision a stable secret before starting the daemon if integrity
signing is needed.

*Verified: `src/general_ludd/integrity/scanner.py` lines 51–64.*

### `allowed_cidr` — compute inference-port restriction

`ComputeConfig.allowed_cidr` controls which source addresses can reach the
inference port on provisioned VMs. It is applied as an AWS security-group rule,
GCP firewall rule, and Azure NSG `source_address_prefix`.

**Default: `"0.0.0.0/0"` (world-open).** Set this to the narrowest CIDR for
your deployment before provisioning any VM.

*Verified: `src/general_ludd/infra/compute.py` line 68;
`src/general_ludd/infra/terraform.py` lines 199, 300, 365.*

### Ansible playbook env-scrub

`AnsibleCoreRunner` passes only an explicit allowlist of env vars to playbook
subprocesses. Secrets — `ZAI_API_KEY`, `GLUDD_PSK`, `AWS_*`, `OPENAI_*`,
`DATABASE_URL`, and anything else not on the list — are stripped before
`pb_exec.run()`. `GLUDD_PLAYBOOK_TIMEOUT` (default 300 s) passes through so
operators can tune timeouts without leaking credentials.

*Verified: `src/general_ludd/ansible/core_runner.py` lines 437–484
(`_PLAYBOOK_ENV_ALLOWLIST`).*

### `/admin/*` route gating

All `/admin/*` paths are gated by the PSK middleware. Safe read-only methods
(`GET`/`HEAD`/`OPTIONS`) on a small explicit public set (`/healthz`, `/readyz`,
`/api/status`, `/docs`, `/openapi.json`, `/redoc`) are exempt; `/admin/*` is
never on that list. When no PSK is configured the middleware returns 503
(fail-closed) for any non-public path.

*Verified: `src/general_ludd/daemon.py` lines 1499–1541.*

### Cosign private-key file permissions

`generate_cosign_key()` writes `cosign.key` via `os.open(..., 0o600)` —
owner-read/write only. No world- or group-readable private key files are
written to disk.

*Verified: `src/general_ludd/secrets/cosign.py` line 101.*

## Secrets Management

- All secrets must be stored via OpenBao or environment variables.
- Never commit API keys, tokens, or credentials to the repository.
- The `detect-secrets` pre-commit hook scans for committed secrets.
- If you discover a committed secret, rotate it immediately and contact maintainers.

## Secure Development

- All dependencies are pinned with hash verification where possible.
- GitHub Actions use hash-pinned actions (not tag references).
- Binary releases include SHA256 checksums.
- PSK (pre-shared key) authentication is available for daemon API access.
- `make pip-audit-gate` fails closed on any new dependency advisory; the two
  advisories below are the only adjudicated exceptions (W5.3).

## Known dependency advisories (adjudicated)

Audited 2026-06-13 via `make pip-audit`. Two advisories are present and have
been adjudicated; `make pip-audit-gate` ignores ONLY these two by ID and fails
on anything new.

### CVE-2025-69872 — diskcache 5.6.3 (pickle deserialization → RCE)

- **Status:** No upstream fix release exists (diskcache uses pickle by default
  through 5.6.3, the latest version).
- **Exploit precondition:** an attacker must have **write access to the cache
  directory** to plant a malicious pickle that the victim later reads.
- **Mitigation (shipped):** `models/response_cache.py` creates the cache
  directory (default `~/.cache/general-ludd/response-cache`, under the user's
  home) with mode `0o700` (owner-only) and `chmod`s it on open, removing the
  precondition on multi-user hosts. The cache only stores model-response dicts.
- **Residual risk:** none beyond an attacker who already controls the user's
  own account (in which case the cache is the least of the concerns).

### PYSEC-2026-196 — pip 26.1.1 (entry-point path handling; fixed in 26.1.2)

- **Status:** Fixed upstream in pip 26.1.2; `make pip-upgrade` installs it for
  dev/build environments.
- **Scope:** pip is a **build-time installer only**. It is NOT a runtime
  dependency (not listed in `pyproject.toml`) and is absent from the shipped
  PyInstaller binary, so the advisory cannot affect a deployed agent. The uv-
  managed dev venv currently pins 26.1.1; CI and developers run the fixed pip.
- **Residual risk:** confined to the local build machine; not exploitable in
  the distributed product.
