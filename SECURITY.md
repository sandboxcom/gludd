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
