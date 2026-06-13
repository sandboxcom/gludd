# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in General Ludd, please
**do not** open a public issue. Instead, report it privately.

Email: security@general-ludd.dev (or file a private security advisory on GitHub)

We will respond within 72 hours and aim to release a fix within 14 days.

## Supported Versions

Only the latest release is supported for security fixes.
Prereleases (`v0.1.0-alpha-*`) receive best-effort support.

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
