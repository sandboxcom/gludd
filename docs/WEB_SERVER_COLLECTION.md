# general_ludd.web_server — Ansible Collection Reference

**Namespace:** `general_ludd`
**Collection:** `web_server`
**Version:** 0.1.0
**License:** MIT
**Role count:** 7 implemented, 1 planned (`logging_middleware`)

---

## 1. Collection Overview

`general_ludd.web_server` deploys, configures, secures, and audits web servers and
proxies. It covers the full stack — from bare-metal HTTP servers through TLS
termination, reverse proxying, WSGI application serving, forward proxying, load
balancing, and security hardening against OWASP Top 10 / PCI DSS requirements.

Each role is backend-agnostic: `server_type` or `proxy_type` parameter selects
the engine (nginx, Apache, HAProxy, Squid, etc.). The collection ships with
supporting Python CLI tools for offline config generation, cipher audit, and
TLD-level validation.

**Supported backends:**

| Backend | Roles |
|---------|-------|
| nginx | http_server, ssl_config, reverse_proxy, load_balancer, security_hardening |
| Apache httpd | http_server, ssl_config, security_hardening |
| HAProxy | reverse_proxy, load_balancer |
| Traefik | reverse_proxy |
| Envoy | reverse_proxy |
| Squid | forward_proxy |
| tinyproxy | forward_proxy |
| privoxy | forward_proxy |

**Installation:**

```bash
ansible-galaxy collection install general_ludd.web_server
```

**Minimum Ansible version:** 2.14+
**Supported OS:** Debian 11+, Ubuntu 22.04+, RHEL 8+, Rocky 9+, Alpine 3.18+

---

## 2. Architecture Reference

### 2.1 Request Flow — Standard Web Stack

```
Client ──[TLS]──> TLS Termination ──[plain]──> Reverse Proxy ──[socket/HTTP]──> WSGI/ASGI App ──> Response
  │                 (ssl_config)               (reverse_proxy)                   (cgi_wsgi)
  │
  └── nginx / Apache handles TLS, optionally OCSP-stapled
```

1. **TLS Termination** (`ssl_config`): The edge server decrypts TLS. Certificates
   come from Let's Encrypt (ACME automation), a CA bundle, or a self-signed cert
   for internal use. HSTS, OCSP stapling, and cipher-profile selection happen here.
2. **Reverse Proxy** (`reverse_proxy`): Routes requests to upstream backends.
   Handles caching, WebSocket upgrades, gRPC streams, and header injection
   (`X-Forwarded-For`, `X-Real-IP`).
3. **Application Server** (`cgi_wsgi`): gunicorn, uWSGI, or mod_wsgi runs the
   Python/WSGI application. Unix-socket communication preferred over TCP for
   same-host deployments.
4. **HTTP Server** (`http_server`): Serves static assets directly from disk
   (`document_root`), handles virtual hosting, compression, and rate limiting.

### 2.2 Proxy Chain — Forward + Reverse

```
Client ──[explicit proxy config]──> Forward Proxy ──[internet]──> Reverse Proxy ──> App
              (forward_proxy)                                        (reverse_proxy)
```

- **Forward Proxy** (`forward_proxy`): Sits between internal clients and the
  internet. Authenticates users, filters outbound requests via ACLs, caches
  responses, and can transparently intercept traffic (transparent mode + SSL bump).
- **Reverse Proxy** (`reverse_proxy`): Sits in front of application servers.
  Terminates inbound connections, distributes load, caches responses.

### 2.3 Load Balancing

```
Client ──> Load Balancer ──┬──> backend-0 (10.0.1.10:80)  weight=1
              │             ├──> backend-1 (10.0.1.11:80)  weight=1
              │             └──> backend-2 (10.0.1.12:80)  weight=2 (backup)
              │
              ├── Health checks: GET /health every 10s, timeout 5s
              ├── Session stickiness: cookie-based
              ├── Circuit breaker: max 1000 conns, 500 pending, 3 retries
              └── Deployment strategies: blue-green, canary
```

- **Load Balancer** (`load_balancer`): Distributes traffic across backend pools
  with configurable algorithms (round-robin, least-conn, ip-hash). Health checks
  remove unhealthy backends automatically. Blue-green and canary deployment modes
  enable zero-downtime rollouts.

---

## 3. Role Reference

### 3.1 `general_ludd.web_server.http_server`

**Purpose:** Install and configure nginx or Apache HTTP server. Manage virtual
hosts, document roots, modules, proxy pass, rate limiting, buffering, and
compression.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `server_type` | str | `nginx` | Backend: `nginx` or `apache` |
| `port` | int | `80` | Listen port |
| `server_name` | str | `localhost` | Virtual host server name |
| `document_root` | str | `/var/www/html` | Static file root |
| `proxy_pass` | str | `""` | Upstream proxy URL (e.g. `http://127.0.0.1:3000`) |
| `extra_directives` | dict | `{}` | Arbitrary key-value directives injected into the server block |

**Python tool:** `roles/http_server/files/http_server.py`
- `parse` — Parse existing nginx config into directives
- `validate` — Validate config syntax against known directives
- `generate` — Generate virtual host config from parameters

**Example — Static site with nginx:**

```yaml
- name: Deploy static site
  ansible.builtin.include_role:
    name: general_ludd.web_server.http_server
  vars:
    server_type: nginx
    port: 80
    server_name: example.com
    document_root: /var/www/example
```

**Example — Apache reverse proxy to backend:**

```yaml
- name: Proxy API to backend
  ansible.builtin.include_role:
    name: general_ludd.web_server.http_server
  vars:
    server_type: apache
    port: 80
    server_name: api.example.com
    proxy_pass: "http://127.0.0.1:8080"
```

### 3.2 `general_ludd.web_server.ssl_config`

**Purpose:** Generate and install certificates, configure SSL virtual hosts,
enable HSTS, set cipher suites, OCSP stapling, and TLS version enforcement.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `cert_path` | str | `/etc/ssl/certs/server.crt` | Certificate file path |
| `key_path` | str | `/etc/ssl/private/server.key` | Private key path |
| `ca_bundle` | str | `""` | CA bundle / chain file path |
| `hsts_max_age` | int | `31536000` | HSTS max-age in seconds (1 year) |
| `min_tls_version` | str | `"1.2"` | Minimum TLS version (`1.2`, `1.3`) |
| `cipher_profile` | str | `intermediate` | Mozilla profile: `modern`, `intermediate`, `old` |

**Python tool:** `roles/ssl_config/files/ssl_config.py`
- Mozilla cipher profiles (`modern`, `intermediate`, `old`) with full cipher strings
- Certificate expiry validation (`validate`) — checks not-before/not-after dates
- DH parameter generation with configurable bit length
- OCSP responder URL extraction from certificate

**Example — Let's Encrypt + HSTS:**

```yaml
- name: TLS with Let's Encrypt and strict HSTS
  ansible.builtin.include_role:
    name: general_ludd.web_server.ssl_config
  vars:
    cert_path: /etc/letsencrypt/live/example.com/fullchain.pem
    key_path: /etc/letsencrypt/live/example.com/privkey.pem
    hsts_max_age: 31536000
    min_tls_version: "1.3"
    cipher_profile: modern
```

**Example — Internal self-signed cert:**

```yaml
- name: Internal service TLS
  ansible.builtin.include_role:
    name: general_ludd.web_server.ssl_config
  vars:
    cert_path: /etc/ssl/certs/internal.crt
    key_path: /etc/ssl/private/internal.key
    hsts_max_age: 0
    cipher_profile: intermediate
```

### 3.3 `general_ludd.web_server.cgi_wsgi`

**Purpose:** Wire WSGI/ASGI application servers behind nginx/Apache. Configures
gunicorn, uWSGI, or mod_wsgi with socket binding, process/thread pools, and
application module loading.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `gateway_type` | str | `wsgi` | Gateway: `wsgi` (gunicorn/uWSGI) or `asgi` (uvicorn) |
| `socket_path` | str | `/tmp/app.sock` | Unix socket path (preferred for same-host) |
| `app_module` | str | `app:application` | Python module:callable (e.g. `myapp.wsgi:application`) |
| `processes` | str | `{{ ansible_processor_vcpus * 2 + 1 }}` | Worker processes |
| `threads` | int | `1` | Threads per worker |

**Example — Flask app behind nginx (unix socket):**

```yaml
- name: Deploy WSGI app
  ansible.builtin.include_role:
    name: general_ludd.web_server.cgi_wsgi
  vars:
    gateway_type: wsgi
    socket_path: /run/flaskapp.sock
    app_module: "myapp.app:create_app()"
    processes: 5
    threads: 2
```

**Example — Django ASGI with uvicorn:**

```yaml
- name: Deploy ASGI app
  ansible.builtin.include_role:
    name: general_ludd.web_server.cgi_wsgi
  vars:
    gateway_type: asgi
    socket_path: /run/django.sock
    app_module: "myproject.asgi:application"
    processes: "{{ ansible_processor_vcpus }}"
```

### 3.4 `general_ludd.web_server.reverse_proxy`

**Purpose:** Configure reverse proxy for load distribution, caching, WebSocket
upgrades, gRPC proxying, and TLS termination across multiple backends (nginx,
HAProxy, Traefik, Envoy).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `proxy_type` | str | `nginx` | Backend: `nginx`, `haproxy`, `traefik`, `envoy` |
| `upstream_servers` | list | `[{host: 127.0.0.1, port: 3000}]` | Backend server list |
| `lb_method` | str | `round_robin` | Load-balance method: `round_robin`, `least_conn`, `ip_hash`, `random` |
| `health_check_path` | str | `/health` | Health check endpoint |
| `cache_enabled` | bool | `false` | Enable response caching |
| `cache_ttl` | int | `3600` | Cache TTL in seconds |
| `listen_port` | int | `443` | Proxy listen port |
| `server_name` | str | `localhost` | Virtual host name |
| `ssl_enabled` | bool | `true` | Enable TLS on the proxy |
| `proxy_read_timeout` | int | `60` | Read timeout (seconds) |
| `proxy_connect_timeout` | int | `5` | Connect timeout (seconds) |
| `proxy_send_timeout` | int | `60` | Send timeout (seconds) |
| `websocket_enabled` | bool | `false` | Enable WebSocket upgrade headers |
| `grpc_enabled` | bool | `false` | Enable gRPC HTTP/2 proxying |
| `extra_backends` | list | `[]` | Additional backends appended to upstream pool |

**Python tool:** `roles/reverse_proxy/files/reverse_proxy.py`
- Template-based config generation for nginx (`upstream` + `server` blocks) and
  HAProxy (`backend` + `frontend` sections)
- SAN (Subject Alternative Name) extraction from certificates for TLS routing
- WebSocket and gRPC upgrade-header injection helpers

**Example — nginx reverse proxy to Node.js backend:**

```yaml
- name: Reverse proxy to Node.js
  ansible.builtin.include_role:
    name: general_ludd.web_server.reverse_proxy
  vars:
    proxy_type: nginx
    upstream_servers:
      - host: 127.0.0.1
        port: 3000
      - host: 127.0.0.1
        port: 3001
    ssl_enabled: true
    cache_enabled: true
    cache_ttl: 600
```

**Example — HAProxy with WebSocket support:**

```yaml
- name: HAProxy with WebSocket
  ansible.builtin.include_role:
    name: general_ludd.web_server.reverse_proxy
  vars:
    proxy_type: haproxy
    lb_method: least_conn
    upstream_servers:
      - host: 10.0.1.10
        port: 8080
      - host: 10.0.1.11
        port: 8080
    websocket_enabled: true
    health_check_path: /ws-health
```

### 3.5 `general_ludd.web_server.forward_proxy`

**Purpose:** Deploy and configure a forward proxy (Squid, tinyproxy, privoxy) for
outbound HTTP/HTTPS traffic. Supports ACL-based access control, authentication,
caching, transparent interception, and SSL bump (HTTPS inspection).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `proxy_type` | str | `squid` | Backend: `squid`, `tinyproxy`, `privoxy` |
| `port` | int | `3128` | Proxy listen port |
| `allowed_networks` | list | `[10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16]` | Allowed client networks |
| `auth_method` | str | `none` | Auth: `none`, `basic`, `ntlm`, `kerberos` |
| `cache_dir` | str | `/var/spool/squid` | Cache directory on disk |
| `cache_size_mb` | int | `2048` | Max cache size in MB |
| `ssl_bump_enabled` | bool | `false` | Enable SSL bump (HTTPS interception) |
| `transparent_mode` | bool | `false` | Intercept traffic without client proxy config |
| `max_object_size_mb` | int | `128` | Max cached object size |
| `log_dir` | str | `/var/log/squid` | Access/audit log directory |
| `dns_nameservers` | list | `[8.8.8.8]` | Upstream DNS servers |
| `allow_direct` | bool | `false` | Allow clients to bypass proxy |

**Example — Squid forward proxy for internal network:**

```yaml
- name: Internal forward proxy
  ansible.builtin.include_role:
    name: general_ludd.web_server.forward_proxy
  vars:
    proxy_type: squid
    port: 3128
    allowed_networks:
      - 10.100.0.0/16
    auth_method: basic
    cache_size_mb: 4096
```

**Example — Transparent proxy with SSL inspection:**

```yaml
- name: Transparent proxy with SSL bump
  ansible.builtin.include_role:
    name: general_ludd.web_server.forward_proxy
  vars:
    proxy_type: squid
    port: 3130
    transparent_mode: true
    ssl_bump_enabled: true
    allowed_networks:
      - 10.0.0.0/8
```

### 3.6 `general_ludd.web_server.load_balancer`

**Purpose:** Configure Layer 7 load balancing with health-checked backend pools,
session stickiness, circuit breakers, and blue-green / canary deployment support.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `lb_method` | str | `round_robin` | Algorithm: `round_robin`, `least_conn`, `ip_hash`, `random` |
| `sticky_session` | str | `cookie` | Stickiness: `cookie`, `ip_hash`, `none` |
| `health_check_interval` | int | `10` | Health check interval (seconds) |
| `health_check_timeout` | int | `5` | Health check timeout (seconds) |
| `unhealthy_threshold` | int | `3` | Consecutive failures to mark unhealthy |
| `healthy_threshold` | int | `2` | Consecutive successes to mark healthy |
| `backend_servers` | list | `[{host: 10.0.1.10, port: 80, weight: 1}, ...]` | Primary backend pool |
| `backup_servers` | list | `[]` | Backup servers (used only when all primaries down) |
| `listen_port` | int | `80` | LB listen port |
| `server_name` | str | `localhost` | Virtual host name |
| `circuit_breaker.max_connections` | int | `1000` | Max connections to backend |
| `circuit_breaker.max_pending_requests` | int | `500` | Max pending requests |
| `circuit_breaker.max_retries` | int | `3` | Max retries before opening circuit |
| `blue_green_enabled` | bool | `false` | Blue-green deployment mode |
| `canary_enabled` | bool | `false` | Canary deployment mode |
| `lb_type` | str | `nginx` | Backend: `nginx` or `haproxy` |

**Example — nginx LB with health checks and stickiness:**

```yaml
- name: Load-balanced API
  ansible.builtin.include_role:
    name: general_ludd.web_server.load_balancer
  vars:
    lb_type: nginx
    lb_method: least_conn
    sticky_session: cookie
    health_check_interval: 5
    backend_servers:
      - host: 10.0.1.10
        port: 8080
        weight: 2
      - host: 10.0.1.11
        port: 8080
        weight: 1
    backup_servers:
      - host: 10.0.1.99
        port: 8080
        weight: 1
    listen_port: 443
```

**Example — HAProxy with circuit breaker and blue-green:**

```yaml
- name: Blue-green API deployment
  ansible.builtin.include_role:
    name: general_ludd.web_server.load_balancer
  vars:
    lb_type: haproxy
    blue_green_enabled: true
    circuit_breaker:
      max_connections: 2000
      max_pending_requests: 1000
      max_retries: 3
    backend_servers:
      - host: 10.0.1.10
        port: 8080
      - host: 10.0.1.11
        port: 8080
```

### 3.7 `general_ludd.web_server.security_hardening`

**Purpose:** Apply security hardening to web servers. Hide server tokens, inject
security headers, disable directory listing, enforce TLS protocols/ciphers,
enable ModSecurity WAF (OWASP CRS), and run external audit tools (nikto,
testssl.sh). Supports an `audit_mode` for dry-run compliance scanning.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `hardening_profile` | str | `basic` | Profile: `basic`, `pci`, `owasp`, `cis` |
| `hide_server_tokens` | bool | `true` | Suppress version banners (`server_tokens off`, `ServerTokens Prod`) |
| `security_headers` | bool | `true` | Inject HSTS, X-Frame-Options, X-Content-Type-Options, CSP, etc. |
| `disable_directory_listing` | bool | `true` | Disable autoindex / directory browsing |
| `waf_enabled` | bool | `false` | Enable ModSecurity WAF |
| `modsecurity_ruleset` | str | `owasp_crs` | Ruleset: `owasp_crs` (default) |
| `audit_mode` | bool | `false` | Dry-run: scan only, do not apply changes |
| `allowed_methods` | list | `[GET, POST, HEAD]` | Restrict HTTP methods |
| `max_body_size_mb` | int | `10` | Max request body size |
| `tls_protocols` | list | `[TLSv1.2, TLSv1.3]` | Allowed TLS protocols |
| `tls_ciphers` | str | `EECDH+AESGCM:EDH+AESGCM:AES256+EECDH:AES256+EDH` | Cipher suite string |
| `dhparam_bits` | int | `2048` | DH parameter bit length |
| `server_type` | str | `nginx` | Backend: `nginx` or `apache` |
| `audit_tools` | list | `[nikto, testssl.sh]` | External audit tools to run |

**Example — Audit-only scan:**

```yaml
- name: Security audit without applying changes
  ansible.builtin.include_role:
    name: general_ludd.web_server.security_hardening
  vars:
    audit_mode: true
    server_type: nginx
    hardening_profile: owasp
```

**Example — Full hardening with WAF:**

```yaml
- name: PCI-compliant hardening with WAF
  ansible.builtin.include_role:
    name: general_ludd.web_server.security_hardening
  vars:
    hardening_profile: pci
    waf_enabled: true
    modsecurity_ruleset: owasp_crs
    hide_server_tokens: true
    max_body_size_mb: 10
    server_type: nginx
```

### 3.8 `general_ludd.web_server.logging_middleware` (planned)

**Status:** Not yet implemented. Planned role for access/error log configuration,
log rotation (logrotate), structured JSON logging, and log analysis. Will cover:
- Combined/JSON log format selection
- Per-virtual-host log files
- Log rotation by size and time
- Syslog and ELK pipeline integration
- Request ID injection for distributed tracing

---

## 4. SSL/TLS Quick Reference

### 4.1 Certificate Types

| Type | Use Case | Expiry | Trust |
|------|----------|--------|-------|
| **Self-signed** | Internal, dev, testing | Configurable | Not trusted by browsers |
| **Let's Encrypt** | Public-facing HTTPS | 90 days (auto-renewed) | Trusted by all major root stores |
| **CA-signed (DV)** | Production (domain validated) | 1-2 years | Trusted |
| **CA-signed (OV)** | Organization identity | 1-2 years | Higher trust |
| **CA-signed (EV)** | Extended validation | 1-2 years | Highest trust (green bar deprecated) |
| **Wildcard** | `*.example.com` (one level) | Per provider | Trusted |

### 4.2 Mozilla Cipher Profiles

**Modern** (TLS 1.3 only — best security, most restrictive):

```
TLS_AES_256_GCM_SHA384:TLS_AES_128_GCM_SHA256:TLS_CHACHA20_POLY1305_SHA256
```

**Intermediate** (TLS 1.2–1.3 — recommended default, broad compatibility):

```
ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:
ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:
ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:
DHE-RSA-AES128-GCM-SHA256:DHE-RSA-AES256-GCM-SHA384
```

**Old** (TLS 1.0+ — legacy-compatible, use only when required):

```
ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:
ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:
ECDHE-ECDSA-CHACHA20-POLY1305:DHE-RSA-AES128-GCM-SHA256:
DHE-RSA-AES256-GCM-SHA384:DHE-RSA-AES128-SHA256:DHE-RSA-AES256-SHA256
```

Provide the profile name to the `ssl_config` role via the `cipher_profile` parameter.
The Python tool in `ssl_config/files/ssl_config.py` maps each profile to a full
cipher string. The role applies it to nginx (`ssl_ciphers`) or Apache
(`SSLCipherSuite`).

### 4.3 HSTS Quick Setup

1. Start with a short max-age for testing:
   ```
   Strict-Transport-Security: max-age=300
   ```
2. Once confirmed working, increase to 1 year:
   ```
   Strict-Transport-Security: max-age=31536000; includeSubDomains
   ```
3. For maximum protection, add `preload` and submit to
   [hstspreload.org](https://hstspreload.org). Note: preload is difficult to
   reverse — ensure all subdomains support HTTPS first.

The `ssl_config` role configures HSTS via nginx's `add_header` or Apache's
`Header always set` directive.

### 4.4 Deterministic DH Parameters

`generate_dhparam()` uses the standardized 2048-bit `ffdhe2048` group from
[RFC 7919 Appendix A.1](https://datatracker.ietf.org/doc/html/rfc7919#appendix-A.1)
for its default size. This produces interoperable PEM parameters in bounded
time instead of generating a fresh safe prime, an operation whose runtime can
vary dramatically on contended CI runners and production hosts. Explicit
non-default bit sizes continue to request freshly generated parameters.

Long-lived operator discussions show both the recurring need for correctly
sized DH parameters and the operational confusion around generating and
deploying them:

- [Server Fault: Should I set Diffie-Hellman parameters for nginx SSL?](https://serverfault.com/questions/345830/should-i-set-diffie-hellman-parameters-for-nginx-ssl)
  records nginx operators weighing generation cost, reuse, and deployment.
- [Stack Overflow: How to check my server Diffie-Hellman MODP size and increase it?](https://stackoverflow.com/questions/61326004/how-to-check-my-server-diffie-hellman-modp-size-bits-and-increase-it)
  documents users needing a reliable way to verify and raise deployed group
  strength.

### 4.5 OCSP Stapling

Enabled by the `ssl_config` role. The server periodically fetches the OCSP
response from the CA and staples it into the TLS handshake. Benefits:

- **Faster:** Client does not make a separate OCSP request.
- **More private:** CA does not see which clients are visiting.
- **Resilient:** If OCSP responder is unreachable, the cached response is used.

nginx requires a resolver for OCSP:

```nginx
ssl_stapling on;
ssl_stapling_verify on;
ssl_trusted_certificate /etc/nginx/ssl/chain.pem;
resolver 8.8.8.8 8.8.4.4 valid=300s;
resolver_timeout 5s;
```

### 4.6 Let's Encrypt Automation

The `ssl_config` role can integrate with certbot for automated certificate
issuance and renewal. Typical workflow:

1. Install certbot via the role's package dependency.
2. Obtain cert: `certbot certonly --nginx -d example.com -d www.example.com`
3. Test renewal: `certbot renew --dry-run`
4. Cron/systemd timer: `0 3 * * * certbot renew --quiet --post-hook "nginx -s reload"`

Rate limits: 50 certificates per registered domain per week, 5 duplicate
certificates per week. Use `--test-cert` for staging during development.

### 4.7 Certificate Validation

The `ssl_config.py` tool validates certificates:

```bash
python3 ssl_config.py validate --cert /path/to/cert.pem
```

Reports: subject, issuer, not-before, not-after, SAN entries, days until expiry,
and whether the certificate is currently valid.

---

## 5. Audit → Remediate Workflow

The `security_hardening` role implements a repeatable audit → remediate →
re-audit → report cycle. This workflow maps to PCI DSS Requirement 6
(develop and maintain secure systems) and OWASP ASVS verification.

### Step 1: Run Audit

```yaml
- name: Security audit in dry-run mode
  hosts: webservers
  roles:
    - role: general_ludd.web_server.security_hardening
      vars:
        audit_mode: true
        server_type: nginx
        hardening_profile: owasp
```

The role runs all checks without modifying configuration. It produces a machine-
readable findings list:

```json
{
  "findings": [
    {
      "id": "F-001",
      "category": "server_tokens",
      "severity": "LOW",
      "cvss": 5.0,
      "directive": "server_tokens on",
      "location": "/etc/nginx/nginx.conf:24",
      "remediation": "Set 'server_tokens off;' in http block",
      "owasp": "A05:2021 — Security Misconfiguration",
      "pci_dss": "6.5.10"
    },
    {
      "id": "F-002",
      "category": "missing_hsts",
      "severity": "MEDIUM",
      "cvss": 6.5,
      "directive": "(missing)",
      "location": "/etc/nginx/sites-enabled/default:12",
      "remediation": "Add 'add_header Strict-Transport-Security ...' to server block",
      "owasp": "A02:2021 — Cryptographic Failures",
      "pci_dss": "4.1"
    }
  ],
  "summary": {
    "total_findings": 2,
    "by_severity": {"LOW": 1, "MEDIUM": 1, "HIGH": 0, "CRITICAL": 0},
    "audit_timestamp": "2026-07-12T10:30:00Z"
  }
}
```

### Step 2: Review Findings

Each finding includes:
- **ID** — unique identifier for tracking
- **CVSS severity** — 0.0–10.0 score (NVD-compatible)
- **Affected directive** — the config line that violates the policy
- **Remediation** — the exact config change needed to close the finding
- **OWASP category** — mapped to OWASP Top 10:2021
- **PCI DSS reference** — mapped to the relevant PCI DSS requirement

### Step 3: Apply Fixes

Apply remediation per finding or in bulk:

```yaml
- name: Remediate specific findings
  hosts: webservers
  roles:
    - role: general_ludd.web_server.security_hardening
      vars:
        audit_mode: false
        remediate_findings:
          - "F-001"    # Disable server tokens
          - "F-002"    # Add HSTS header
        hide_server_tokens: true
        security_headers: true
```

Or apply the full hardening profile:

```yaml
- name: Full PCI hardening
  hosts: webservers
  roles:
    - role: general_ludd.web_server.security_hardening
      vars:
        audit_mode: false
        hardening_profile: pci
        server_type: nginx
```

### Step 4: Re-Audit to Confirm Closure

Re-run the audit to verify all findings are resolved:

```yaml
- name: Confirm remediation
  hosts: webservers
  roles:
    - role: general_ludd.web_server.security_hardening
      vars:
        audit_mode: true
        server_type: nginx
```

The output should show `"total_findings": 0` or only accepted-risk findings.

### Step 5: Generate Compliance Report

The `security_hardening` role can produce a compliance report mapping findings
to specific regulatory requirements:

| Framework | Coverage |
|-----------|----------|
| **PCI DSS v4.0** | Requirements 4.1 (encrypted transmission), 6.5 (security patches), 6.5.10 (WAF), 11.2 (vulnerability scanning) |
| **OWASP Top 10:2021** | A01 (Broken Access Control), A02 (Cryptographic Failures), A05 (Security Misconfiguration), A06 (Vulnerable Components) |
| **CIS Benchmarks** | nginx 1.24 and Apache 2.4 Level 1 and Level 2 profiles |
| **NIST SP 800-53** | AC-3 (Access Enforcement), SC-8 (Transmission Confidentiality), SC-13 (Cryptographic Protection) |

---

## 6. Common Configurations

### 6.1 Static Site with HTTPS

```yaml
- name: Static site with Let's Encrypt
  hosts: webserver
  roles:
    - role: general_ludd.web_server.http_server
      vars:
        server_type: nginx
        port: 80
        server_name: example.com
        document_root: /var/www/example

    - role: general_ludd.web_server.ssl_config
      vars:
        cert_path: /etc/letsencrypt/live/example.com/fullchain.pem
        key_path: /etc/letsencrypt/live/example.com/privkey.pem
        hsts_max_age: 31536000
        cipher_profile: intermediate
```

Result: nginx serves static files over HTTPS with HSTS, modern TLS ciphers, and
HTTP→HTTPS redirect. Certbot handles certificate renewal.

### 6.2 Python WSGI App Behind nginx

```yaml
- name: Flask app behind nginx
  hosts: appserver
  roles:
    - role: general_ludd.web_server.cgi_wsgi
      vars:
        gateway_type: wsgi
        socket_path: /run/myapp.sock
        app_module: "myapp.wsgi:application"
        processes: 5

    - role: general_ludd.web_server.http_server
      vars:
        server_type: nginx
        port: 80
        server_name: api.example.com
        proxy_pass: "http://unix:/run/myapp.sock"
        extra_directives:
          client_max_body_size: "20m"
          proxy_read_timeout: "120s"

    - role: general_ludd.web_server.ssl_config
      vars:
        cert_path: /etc/letsencrypt/live/api.example.com/fullchain.pem
        key_path: /etc/letsencrypt/live/api.example.com/privkey.pem
```

Result: gunicorn binds to a Unix socket. nginx proxies requests through the
socket with appropriate timeout and body-size settings. TLS terminates at nginx.

### 6.3 Load-Balanced API with Health Checks

```yaml
- name: Load-balanced REST API
  hosts: loadbalancer
  roles:
    - role: general_ludd.web_server.load_balancer
      vars:
        lb_type: nginx
        lb_method: least_conn
        sticky_session: cookie
        health_check_interval: 5
        health_check_timeout: 3
        unhealthy_threshold: 3
        healthy_threshold: 2
        listen_port: 443
        backend_servers:
          - host: 10.0.1.10
            port: 8080
            weight: 1
          - host: 10.0.1.11
            port: 8080
            weight: 1
          - host: 10.0.1.12
            port: 8080
            weight: 2
        circuit_breaker:
          max_connections: 1000
          max_pending_requests: 500
          max_retries: 3

    - role: general_ludd.web_server.ssl_config
      vars:
        cert_path: /etc/letsencrypt/live/api.example.com/fullchain.pem
        key_path: /etc/letsencrypt/live/api.example.com/privkey.pem
        cipher_profile: modern
```

Result: nginx distributes requests across 3 backends with cookie-based
stickiness. Health checks at `/health` remove unhealthy backends. Circuit
breaker prevents cascading failures. TLS 1.3 with modern ciphers.

### 6.4 Forward Proxy for Outbound Filtering

```yaml
- name: Outbound filtering proxy
  hosts: gateway
  roles:
    - role: general_ludd.web_server.forward_proxy
      vars:
        proxy_type: squid
        port: 3128
        allowed_networks:
          - 10.100.0.0/16
        auth_method: basic
        cache_size_mb: 4096
        ssl_bump_enabled: true
        transparent_mode: false
        max_object_size_mb: 256

    - role: general_ludd.web_server.security_hardening
      vars:
        server_type: nginx
        hardening_profile: basic
        hide_server_tokens: true
        security_headers: true
```

Result: Squid serves as an explicit forward proxy for the 10.100.0.0/16 network.
Basic authentication controls access. SSL bump decrypts HTTPS traffic for
inspection. A 4 GB disk cache stores frequently accessed objects.

### 6.5 Full-Stack: Squid → nginx → Flask/Django

```yaml
- name: Full-stack web infrastructure
  hosts: all
  tasks:
    - name: Outbound proxy (internal clients)
      ansible.builtin.include_role:
        name: general_ludd.web_server.forward_proxy
      vars:
        proxy_type: squid
        port: 3128
        allowed_networks:
          - 10.0.0.0/8

    - name: WSGI application
      ansible.builtin.include_role:
        name: general_ludd.web_server.cgi_wsgi
      vars:
        gateway_type: wsgi
        socket_path: /run/app.sock
        app_module: "myproject.wsgi:application"

    - name: Reverse proxy + TLS
      ansible.builtin.include_role:
        name: general_ludd.web_server.reverse_proxy
      vars:
        proxy_type: nginx
        ssl_enabled: true
        upstream_servers:
          - host: "unix:/run/app.sock"
            port: 0
        cache_enabled: true

    - name: Security hardening
      ansible.builtin.include_role:
        name: general_ludd.web_server.security_hardening
      vars:
        audit_mode: true
        hardening_profile: owasp
```

---

## 7. Security Checklist

Apply these hardening steps via `security_hardening` (nginx directives shown;
Apache equivalents supported via `server_type: apache`).

### Server Information Leakage

| Step | nginx Directive | Effect |
|------|----------------|--------|
| Hide version | `server_tokens off;` | Suppresses `Server: nginx` version |
| Hide proxy info | `proxy_hide_header X-Powered-By;` | Removes backend framework headers |
| Custom error pages | `error_page 500 502 503 504 /50x.html;` | Avoids default error-page leaks |

### Security Headers

| Header | nginx Directive | Protection |
|--------|----------------|------------|
| HSTS | `add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;` | Forces HTTPS |
| X-Frame-Options | `add_header X-Frame-Options "DENY" always;` | Prevents clickjacking |
| X-Content-Type-Options | `add_header X-Content-Type-Options "nosniff" always;` | Prevents MIME sniffing |
| CSP | `add_header Content-Security-Policy "default-src 'self'" always;` | Restricts resource origins |
| Referrer-Policy | `add_header Referrer-Policy "strict-origin-when-cross-origin" always;` | Controls referrer info |
| Permissions-Policy | `add_header Permissions-Policy "camera=(), microphone=()" always;` | Restricts browser APIs |
| X-XSS-Protection | `add_header X-XSS-Protection "1; mode=block" always;` | Legacy XSS filter (deprecated, CSP preferred) |
| Cache-Control | `add_header Cache-Control "no-store, no-cache, must-revalidate" always;` | Prevents sensitive-page caching |

### TLS Configuration

| Step | nginx Directive | Recommendation |
|------|----------------|----------------|
| Protocols | `ssl_protocols TLSv1.2 TLSv1.3;` | Disable TLS 1.0/1.1 |
| Ciphers | `ssl_ciphers 'EECDH+AESGCM:EDH+AESGCM';` | AEAD ciphers only |
| Server cipher preference | `ssl_prefer_server_ciphers on;` | Server picks strongest |
| DH parameters | `ssl_dhparam /etc/nginx/dhparam.pem;` | 2048-bit minimum |
| Session cache | `ssl_session_cache shared:SSL:10m;` | Reduce handshake cost |
| Session tickets | `ssl_session_tickets off;` | Forces perfect forward secrecy on resume |
| OCSP stapling | `ssl_stapling on; ssl_stapling_verify on;` | Reduces latency, improves privacy |

### Request Filtering

| Step | nginx Directive | Protection |
|------|----------------|------------|
| Rate limit | `limit_req_zone $binary_remote_addr zone=api:10m rate=10r/s;` | DDoS mitigation |
| Body size | `client_max_body_size 10m;` | Prevents oversized upload DoS |
| Header size | `large_client_header_buffers 4 8k;` | Prevents header-injection DoS |
| HTTP methods | `if ($request_method !~ ^(GET|POST|HEAD)$ ) { return 405; }` | Blocks dangerous methods |
| User agent | `if ($http_user_agent ~* (nmap|nikto|sqlmap) ) { return 403; }` | Blocks scanner UAs |
| Hotlinking | `valid_referers none blocked example.com; if ($invalid_referer) { return 403; }` | Prevents hotlinking |

### Apache-Specific (when `server_type: apache`)

| Step | Apache Directive | Protection |
|------|-----------------|------------|
| Server tokens | `ServerTokens Prod` / `ServerSignature Off` | Hide version |
| Directory listing | `Options -Indexes` | Disable browsing |
| Trace method | `TraceEnable off` | Blocks TRACE (XST attacks) |
| Header injection | `Header unset X-Powered-By` | Remove framework ID |
| ETags | `FileETag None` | Prevents inode leakage |
| Timeout | `TimeOut 60` | Connection timeout |

### WAF (ModSecurity)

When `waf_enabled: true`, the role installs ModSecurity with the OWASP Core
Rule Set (CRS). CRS provides ~200 rules covering:
- SQL injection (SQLi)
- Cross-site scripting (XSS)
- Local/remote file inclusion (LFI/RFI)
- Command injection
- HTTP protocol violations
- Scanner/bot detection

CRS runs by default in anomaly-scoring mode: each rule hit adds to a per-
transaction anomaly score. When the score crosses the threshold (default 5
for paranoia level 1), the request is blocked.

---

## 8. Tool Matrix

### 8.1 Python Modules (shipped with collection)

| Tool | File | Functions |
|------|------|-----------|
| `http_server.py` | `roles/http_server/files/` | `parse` (parse config), `validate` (syntax check), `generate` (vhost gen from params) |
| `ssl_config.py` | `roles/ssl_config/files/` | Mozilla cipher profiles (`modern`, `intermediate`, `old`), cert expiry validation, DH param generation, OCSP URI extraction |
| `reverse_proxy.py` | `roles/reverse_proxy/files/` | Template-based config generation for nginx/haproxy, SAN extraction for TLS routing, WebSocket/gRPC upgrade-header helpers |

### 8.2 External CLI Tools

| Tool | Role | Purpose | Key Commands |
|------|------|---------|--------------|
| **certbot** | ssl_config | Let's Encrypt automation | `certbot certonly --nginx`, `certbot renew --quiet` |
| **testssl.sh** | security_hardening | TLS configuration audit | `testssl.sh https://example.com` — scores protocols, ciphers, vulnerabilities |
| **nikto** | security_hardening | Web server scanner | `nikto -h https://example.com` — finds outdated software, misconfigurations |
| **openssl** | ssl_config | Certificate operations | `openssl s_client -connect`, `openssl dhparam` |

### 8.3 Testing and Validation Framework

| Framework | Role Coverage | Purpose |
|-----------|--------------|---------|
| **Molecule** | All roles (planned) | Ansible role integration testing with Docker/Vagrant drivers |
| **testinfra / pytest** | All roles | Infrastructure validation: assert port listening, header presence, cipher acceptance |
| **curl** | http_server, reverse_proxy | HTTP response validation: status codes, headers, body content |
| **ab / wrk** | load_balancer | Load testing: validate LB distribution and circuit breaker behavior |
| **nmap** | security_hardening | Port scanning: confirm only expected ports are open |
| **sslyze** | ssl_config | TLS configuration analysis: protocol/cipher enumeration, certificate chain validation |
| **wafw00f** | security_hardening | WAF detection: verify ModSecurity is active and blocking |

### 8.4 Role-to-Backend Matrix

| Role | nginx | Apache | HAProxy | Squid | Other |
|------|-------|--------|---------|-------|-------|
| http_server | yes | yes | — | — | — |
| ssl_config | yes | yes | — | — | — |
| cgi_wsgi | yes | yes | — | — | — |
| reverse_proxy | yes | — | yes | — | Traefik, Envoy |
| forward_proxy | — | — | — | yes | tinyproxy, privoxy |
| load_balancer | yes | — | yes | — | — |
| security_hardening | yes | yes | — | — | — |
| logging_middleware | planned | planned | — | — | — |

### 8.5 Role-to-Framework Mapping

| Role | PCI DSS | OWASP ASVS | CIS Benchmarks |
|------|---------|------------|----------------|
| security_hardening | Req 4.1, 6.5, 6.5.10, 11.2 | V4 (Access), V9 (Communications), V14 (Config) | nginx L1/L2, Apache L1/L2 |
| ssl_config | Req 4.1 (encrypted transmission) | V9.1 (TLS), V9.2 (cipher suites) | TLS benchmarks |
| forward_proxy | Req 1.1.2 (network diagram) | V1.9 (network architecture) | — |
| http_server | Req 2.2.4 (secure config) | V14.1 (build security) | Server hardening |

---

## Appendix: Role Quick Reference

### All Role FQCNs

```yaml
general_ludd.web_server.http_server        # HTTP server (nginx, Apache)
general_ludd.web_server.ssl_config         # TLS/SSL + HSTS + cert management
general_ludd.web_server.cgi_wsgi           # WSGI/ASGI app server wiring
general_ludd.web_server.reverse_proxy      # Reverse proxy (nginx, HAProxy, Traefik, Envoy)
general_ludd.web_server.forward_proxy      # Forward proxy (Squid, tinyproxy, privoxy)
general_ludd.web_server.load_balancer      # L7 load balancer (nginx, HAProxy)
general_ludd.web_server.security_hardening # Security hardening + audit (nginx, Apache)
```

### Parameter Inheritance

Roles accept parameters via:
1. **Role defaults** (`roles/<name>/defaults/main.yml`) — lowest precedence
2. **Playbook `vars:`** — overrides defaults
3. **`extra_directives`** (http_server) — arbitrary key-value injection for
   directives not exposed as top-level parameters

### Minimum Playbook for a Full Stack

```yaml
---
- name: Deploy full web stack
  hosts: web
  become: true
  roles:
    - general_ludd.web_server.http_server
    - general_ludd.web_server.ssl_config
    - general_ludd.web_server.cgi_wsgi
    - general_ludd.web_server.reverse_proxy
    - general_ludd.web_server.security_hardening
```

---

*Documentation generated for `general_ludd.web_server` v0.1.0. Last updated 2026-07-12.*
