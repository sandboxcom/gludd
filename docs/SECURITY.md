# Security Policy

This is the operator security guide for General Ludd (`gludd`). It describes how
the daemon authenticates requests, how integrity signing is keyed, how to control
network exposure, how secrets are handled, and the recommended hardening posture.
Every claim maps to code; the relevant source is cited inline as `(see <file>)`.

---

## Overview

`gludd` runs as a long-lived HTTP daemon (gunicorn + uvicorn worker) that exposes
an admin/control API, a separate worker app, and an optional MCP/Ansible
execution surface. The daemon is started via `gludd daemon` (see
`src/general_ludd/cli.py`, `_cmd_daemon`). Authentication, integrity signing,
ingress scoping, and secret resolution are controlled by the environment
variables and CLI flags documented below.

---

## Threat model

The default deployment is a **single operator running a local daemon**. The
secure baseline assumes:

- The daemon binds to loopback and is reachable only from the local host.
- The admin API is gated by a pre-shared key (PSK).
- Secrets (model API keys, OpenBao tokens) are supplied via the environment and
  referenced indirectly — never serialized into config, responses, or logs.
- Subprocesses (Ansible playbooks, MCP servers) run with a scrubbed environment
  so they cannot inherit ambient secrets.

The risks this guide addresses are: an unauthenticated admin surface reachable
over the network, a forged integrity approval, ambient secrets leaking through
introspection endpoints or child processes, and cloud inference endpoints
exposed to the public internet.

---

## Authentication (PSK)

The daemon authenticates every non-public request with a bearer pre-shared key.

### Setting the PSK

Set the `GLUDD_PSK` environment variable to a strong secret. The daemon reads it
at startup (`src/general_ludd/daemon.py`, `_psk = os.environ.get("GLUDD_PSK")`)
and the worker reads the same variable through the shared posture loader (see
`src/general_ludd/security/auth.py`, `load_auth_posture`), so the two surfaces
cannot drift.

Clients authenticate by sending the key as a bearer token on every protected
request:

```
Authorization: Bearer <GLUDD_PSK>
```

The comparison is constant-time via `hmac.compare_digest` to avoid timing
side-channels (see `src/general_ludd/security/auth.py`, `check_bearer_token` /
`verify_psk`). The PSK is never logged — only whether one is configured
(`auth_and_stats_middleware` in `daemon.py`).

### Public (unauthenticated) paths

Only a small allowlist of read-only paths is reachable without the PSK (see
`_PUBLIC_PATHS` in `src/general_ludd/daemon.py`):

- `/healthz`, `/readyz` — liveness/readiness probes
- `/api/status`, `/api/todos` — coarse status
- `/api/webmcp` — capability self-description
- `/docs`, `/docs/*`, `/openapi.json`, `/redoc` — API docs

Everything else — including the entire `/admin/*` surface, `/api/dispatch`,
`/api/spend`, `/admin/self-update/*`, and `/admin/integrity/*` — is gated.

Public access is **method-aware**: a path is public only for safe methods
(`GET`/`HEAD`/`OPTIONS`). The same path under a mutating method
(`POST`/`PUT`/`PATCH`/`DELETE`) is **not** public — e.g. `GET /api/todos` lists
todos unauthenticated, but `POST /api/todos` requires the PSK (see `_is_public` /
`_SAFE_METHODS` in `daemon.py`).

`/healthz` deliberately excludes spend/budget numbers from its unauthenticated
payload — only a coarse `budget_exhausted` boolean is public; the figures live
behind the auth'd surface (see the `/healthz` handler in `daemon.py`).

Receiver ingest paths (`/v1/*`, `/ingest/*`) are exempt from the PSK middleware
because they carry their **own** ingest-token auth (`GLUDD_INGEST_TOKEN`), kept
separate from the admin PSK so a leaked ingest token cannot reach `/admin` and a
leaked PSK cannot push telemetry (see `_RECEIVER_PREFIXES` in `daemon.py` and
`src/general_ludd/receiver/router.py`).

### Fail-closed posture

When `GLUDD_PSK` is **unset**:

- If `GLUDD_REQUIRE_AUTH` is truthy (`1`/`true`/`yes`/`on`, see `require_auth_env`
  in `security/auth.py`), the daemon **fails closed**: every non-public path
  returns `503 {"error": "auth_required"}` and a LOUD `SECURITY:` startup warning
  is logged (see `auth_and_stats_middleware` and the warning block in `daemon.py`).
- For development only, `GLUDD_ALLOW_NO_AUTH=1` opts out of fail-closed and runs
  with admin auth **disabled** — leaving the entire `/admin` surface open to any
  caller that can reach the port, with a LOUD warning. Do not use outside local
  development.

Recommended posture: always set `GLUDD_PSK`, and set `GLUDD_REQUIRE_AUTH=1` so a
missing key fails closed rather than silently serving unauthenticated.

---

## Integrity signing

Self-update and integrity-scan approvals are HMAC-signed so an approval cannot be
forged or replayed against a tampered file.

### Setting the integrity key

Set `GL_INTEGRITY_KEY` to a stable secret before starting the daemon (see
`src/general_ludd/integrity/scanner.py`, `_get_integrity_key`). The key signs
change approvals issued through `POST /admin/integrity/approve` (see
`src/general_ludd/routers/integrity.py`).

### Fail-closed posture

The key must be **stable across processes**: signing happens in one process and
verification may happen in another, so an ephemeral/random key would make
verification always fail. Rather than mint a random key, the scanner raises
`IntegrityKeyError` when `GL_INTEGRITY_KEY` is unset, and the signing endpoint
returns HTTP `503` until the key is provisioned (see the `IntegrityKeyError`
docstring and `_get_integrity_key` in `scanner.py`). This is fail-closed: no
signature is ever produced under a misconfiguration.

Integrity approvals are additionally hash-bound and path-confined: the approved
path must resolve inside an allowed root and the supplied hashes must match the
scanned change, so an approval cannot sign/exfiltrate an arbitrary file (e.g.
`/etc/passwd`) or be replayed against a modified version (see `_confine_scan_paths`
and the hash-binding checks in `routers/integrity.py`).

---

## Network exposure (host & CIDR)

### Daemon host binding

The `gludd daemon --host` flag controls the bind address. The current default is
`0.0.0.0` (see `daemon_parser.add_argument("--host", default="0.0.0.0")` in
`src/general_ludd/cli.py`).

**Recommendation:** bind to loopback (`127.0.0.1`) unless you are intentionally
exposing the daemon. Loopback keeps the admin API reachable only from the local
host, matching the single-operator threat model.

```
gludd daemon --host 127.0.0.1 --port 8000
```

When the daemon binds to a **non-loopback** interface (anything other than
`127.0.0.1`, `localhost`, or `::1`), the CLI auto-generates a 256-bit PSK with
`secrets.token_urlsafe(32)`, injects it as `GLUDD_PSK` for the spawned process,
and prints it so clients can authenticate (see `_cmd_daemon` in `cli.py`). This
ensures an externally-bound daemon is never unauthenticated by accident. You may
still set `GLUDD_PSK` explicitly to use a key of your own choosing.

### Cloud inference ingress (`allowed_cidr`)

When `gludd` provisions a remote inference VM, the inference endpoint (port
`:8000`) is unauthenticated, so the ingress CIDR defaults to **loopback-only**:
`allowed_cidr = "127.0.0.1/32"` (see `ComputeConfig.allowed_cidr` in
`src/general_ludd/infra/compute.py`). This value is interpolated into the AWS /
GCP / Azure ingress rules generated in `src/general_ludd/infra/terraform.py`
(security-group rule, GCP firewall `source_ranges`, Azure NSG
`source_address_prefix`).

To widen ingress deliberately, set `allowed_cidr` explicitly — for example
`"10.0.0.0/8"` for a private network, or `"0.0.0.0/0"` for a fully public
endpoint (only behind separate authentication). The field is validated to reject
shell/HCL metacharacters, accepting only `[0-9a-fA-F.:,/]` (see the
`allowed_cidr` validator in `compute.py`). Never widen this to `0.0.0.0/0`
without an authenticating proxy in front of the endpoint.

---

## Secrets

### Aliased, resolve-at-call-time credentials

Model profiles never carry raw API keys. A profile references its credential by
**alias** (`credential_alias`, e.g. `OPENAI_API_KEY`) and its API base by alias
(`api_base_alias`); the actual value is resolved from the environment at call
time and never serialized (see `src/general_ludd/models/gateway.py`, where the
key is looked up via `self._secrets.resolve(profile.credential_alias)` only when
a call is made).

Resolution goes through a fail-closed allowlist (see `EnvSecretsManager` in
`src/general_ludd/secrets/env.py`): the manager only reads an ambient environment
variable if its name matches a recognized credential naming convention
(`*_API_KEY`, `*_API_BASE`, `*_BASE_URL`, `*_API_URL`, `*_AUTH_TOKEN`,
`GLUDD_SECRET_*`) or was explicitly registered. Arbitrary names such as
`GLUDD_PSK`, `PATH`, or `HOME` resolve to `None`, so a maliciously-crafted
`credential_alias` cannot exfiltrate non-credential process state.

### Introspection endpoints exclude secrets

The `/api/environment` brief exposes model metadata through an explicit allowlist
of safe fields (see `_SAFE_MODEL_FIELDS` in
`src/general_ludd/routers/environment.py`). `credential_alias` / `api_base_alias`
are deliberately **not** on that list, so no blanket model dump can leak a secret
reference.

### OpenBao token redaction

When using OpenBao/Vault as the secrets backend, `OpenBaoConfig.external_token`
is declared `repr=False` and is masked to `"**REDACTED**"` by a field serializer,
so it never appears in `repr`, `model_dump()`, or `model_dump_json()` output (see
`_mask_external_token` in `src/general_ludd/secrets/config.py`). TLS verification
for the external client is on by default (`external_tls_verify=True`).

### Subprocess environment scrubbing

Child processes never inherit the full host environment:

- **Ansible playbooks**: before a playbook subprocess runs, the environment is
  reduced to a non-secret allowlist (`PATH`, `HOME`, `USER`, locale/`TMPDIR`,
  selected `ANSIBLE_*` and Python-runtime vars, SSH connection metadata only).
  Secret-bearing vars — `ZAI_API_KEY`, `GLUDD_PSK`, `AWS_*`, `OPENAI_*`,
  `DATABASE_URL`, etc. — are stripped; `GLUDD_PLAYBOOK_TIMEOUT` passes through so
  operators can tune timeouts without leaking credentials (see
  `_PLAYBOOK_ENV_ALLOWLIST` and the `scrubbed_env` construction that swaps
  `os.environ` for the duration of the run in
  `src/general_ludd/ansible/core_runner.py`).
- **MCP servers**: each MCP subprocess receives only a minimal base environment
  (`PATH`, `HOME`, `LANG`, `LC_ALL`, `TMPDIR`) plus the server's own declared /
  resolved secrets — the full host environment (with `ANTHROPIC_API_KEY`,
  `GLUDD_PSK`, cloud creds) is never inherited (see `_ENV_ALLOWLIST` /
  `_build_env` in `src/general_ludd/mcp/transport.py`).

### Signing-key file permissions

`generate_cosign_key()` writes `cosign.key` via
`os.open(..., O_WRONLY|O_CREAT|O_EXCL, 0o600)` into a `0o700` directory —
owner-read/write only. No world- or group-readable private-key files are written
to disk (see `src/general_ludd/secrets/cosign.py`).

---

## Operational hardening checklist

- [ ] Set `GLUDD_PSK` to a strong, unique secret for every deployment.
- [ ] Set `GLUDD_REQUIRE_AUTH=1` so a missing PSK fails closed (503) instead of
      serving unauthenticated.
- [ ] Never set `GLUDD_ALLOW_NO_AUTH=1` outside local development.
- [ ] Bind the daemon to `127.0.0.1` (`gludd daemon --host 127.0.0.1`) unless you
      are intentionally exposing it; if you must bind externally, confirm a PSK is
      configured (one is auto-generated for non-loopback binds, but prefer an
      explicit `GLUDD_PSK`).
- [ ] Set `GL_INTEGRITY_KEY` to a stable secret before starting the daemon so
      integrity/self-update approvals can be signed.
- [ ] Keep cloud-inference `allowed_cidr` at its loopback default and widen it
      only deliberately; never use `0.0.0.0/0` without an authenticating proxy.
- [ ] Supply all model/provider credentials via aliased environment variables
      (`*_API_KEY`, etc.); never embed raw keys in config or profiles.
- [ ] Keep the receiver ingest token (`GLUDD_INGEST_TOKEN`) distinct from the
      admin `GLUDD_PSK`.
- [ ] If using OpenBao/Vault, leave TLS verification enabled
      (`external_tls_verify`, default `True`).
- [ ] Never commit API keys, tokens, or credentials; the `detect-secrets`
      pre-commit hook scans for them. If a secret is committed, rotate it
      immediately.

---

## Game and image dependency hardening

The `game-e2e` and `e2e-all` extras require Pillow 12.3.0 or newer. Pillow's
[12.3.0 release notes](https://pillow.readthedocs.io/en/stable/releasenotes/12.3.0.html)
record fixes for decompression denial of service and multiple out-of-bounds reads
and writes. The lockfile pins the selected release and hashes so the audited
dependency graph is reproducible.

That version floor is a library patch, not an application-level image-size or
resource limit. Pillow operators have discussed decompression-bomb risk since
[issue #515](https://github.com/python-pillow/Pillow/issues/515): small encoded
files can expand to very large images, and decoding is often lazy. Any production
feature that accepts untrusted images must still bound encoded bytes, decoded
pixel dimensions, processing time, and sandbox memory; preserve Pillow's
decompression-bomb checks; and reject or isolate malformed inputs. See Pillow's
[image security guidance](https://pillow.readthedocs.io/en/stable/handbook/security.html)
for the upstream threat model and operational mitigations.

---

## Known dependency advisories (adjudicated)

Audited via `make pip-audit`. `make pip-audit-gate` fails closed on any new
advisory and ignores ONLY the adjudicated exceptions below by ID.

- **CVE-2025-69872 — diskcache (pickle deserialization → RCE):** no upstream
  package release fixes the unsafe default serializer. Gludd does not use that
  serializer: every production DiskCache constructor, including model-response
  caching, is routed through `security.safe_diskcache`, which stores only strict
  MessagePack data in an owner-only `msgpack-v1` namespace, rejects
  file-like/extension values, and refuses every legacy pickle mode without
  deserializing it. Existing cache files are preserved but never opened by the
  safe namespace. On rolling deployment this is a cache-cold transition:
  requests continue normally and repopulate the safe namespace; operators should
  expect only a temporary hit-rate dip, not a data migration or outage. The
  advisory therefore remains explicitly ignored by package ID while a structural
  test fails if any direct application `diskcache.Cache` construction returns.
  DiskCache's own documentation confirms that its default uses pickle and
  recommends a custom `Disk` serializer; the still-open
  [upstream no-fix report #362](https://github.com/grantjenks/python-diskcache/issues/362)
  records the continuing scanner impact. An earlier
  [vulnerability report #357](https://github.com/grantjenks/python-diskcache/issues/357)
  and a separate, still-open
  [maintenance-status thread](https://github.com/grantjenks/python-diskcache/issues/355)
  records that no upstream release had shipped since 2023. See the
  [DiskCache serializer documentation](https://grantjenks.com/docs/diskcache/tutorial.html#disk).
- **PYSEC-2026-3552 — cryptography PKCS#7 decrypt oracle:** the advisory is
  limited to `pkcs7_decrypt_der`, `pkcs7_decrypt_pem`, and
  `pkcs7_decrypt_smime`. Gludd calls none of those APIs; an executable source
  scan fails if any production file adopts them. The advisory lists 50.0.0 as
  the fix, but that version is not yet a stable published release, so the lock
  remains on the current 49.x wheel and permits a future 50.x relock. Once a
  stable fixed wheel is available, upgrade it and remove both the VEX entry and
  ignore. The [PyCA changelog](https://github.com/pyca/cryptography/blob/main/CHANGELOG.rst)
  defines the affected API surface. The maintained
  [pip-audit guidance](https://github.com/pypa/pip-audit#pip-audit-shows-irrelevant-vulnerability-reports)
  explicitly supports ID-scoped ignores for application-inapplicable findings,
  while practitioner request
  [pip-audit #1018](https://github.com/pypa/pip-audit/issues/1018) asks for
  import-aware filtering of this same class of report; Gludd makes that filter
  fail-closed in its own regression test.

The 2026-08-13 beta.4 audit remediated, rather than ignored,
`PYSEC-2026-3458` by requiring stable ansible-core 2.19.11 on Python 3.11 and 2.21.2 or newer on Python 3.12+. Earlier remediation
also fixed `PYSEC-2026-196` with pip 26.1.2 and `PYSEC-2026-3447` with
setuptools 83.0.0. The Ansible patch upgrade is wire- and state-neutral: deploy
the updated automation runtime before invoking new jobs, while already-running
jobs continue under their original process image.

---

## Reporting a vulnerability

If you discover a security vulnerability in `gludd`, please report it **privately**
to the repository maintainer / security contact rather than opening a public
issue. Provide a description, reproduction steps, and the affected version or
commit. Please allow a reasonable window for a fix before any public disclosure.

> Maintainers: replace this line with your project's real security contact (a
> private email or a GitHub Security Advisory link) before publishing.
