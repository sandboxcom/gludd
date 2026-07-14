# security_hardening

Web server security hardening role for the `general_ludd.web_server` collection. Applies security headers, hides server tokens, configures WAF (ModSecurity), hardens file permissions and TLS, restricts HTTP methods, and runs automated security audits.

## Supported server types

| Type | Package | Config path | Validation |
|------|---------|-------------|------------|
| nginx | `nginx` | `/etc/nginx/nginx.conf` | `nginx -t` |
| apache | `httpd`/`apache2` | OS-specific | `httpd -t` / `apache2ctl -t` |

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `hardening_profile` | `basic` | One of: basic, owasp, pci_dss |
| `hide_server_tokens` | `true` | Remove version from server headers |
| `security_headers` | `true` | Apply recommended security headers |
| `disable_directory_listing` | `true` | Disable directory indexing |
| `waf_enabled` | `false` | Enable ModSecurity WAF |
| `modsecurity_ruleset` | `owasp_crs` | WAF ruleset: owasp_crs, comodo, trustwave |
| `audit_mode` | `false` | Run Nikto/testssl.sh audit scans |
| `allowed_methods` | `[GET, POST, HEAD]` | Allowed HTTP methods |
| `max_body_size_mb` | `10` | Max request body size |
| `tls_protocols` | `[TLSv1.2, TLSv1.3]` | Allowed TLS protocols |
| `tls_ciphers` | see defaults | Strong cipher suite |
| `dhparam_bits` | `2048` | DH parameter key size |

## Security headers knowledge

### Content-Security-Policy (CSP)
Controls which resources the browser loads. Key directives:
- `default-src 'self'` — default policy for all resource types
- `script-src 'self'` — trusted script sources (add nonce/hash for inline)
- `style-src 'self' 'unsafe-inline'` — stylesheet sources
- `img-src 'self' data:` — image sources
- `font-src 'self'` — font sources
- `connect-src 'self'` — XHR/WebSocket/fetch destinations
- `frame-src 'self'` — iframe/embed sources
- `object-src 'none'` — Flash/plugin block
- `base-uri 'self'` — base element restriction
- `form-action 'self'` — form submission targets
- `report-uri /csp-report` — violation report endpoint
- `upgrade-insecure-requests` — auto-upgrade HTTP→HTTPS

### X-Frame-Options
- `DENY` — never in a frame
- `SAMEORIGIN` — only frames from same origin

### X-Content-Type-Options
- `nosniff` — prevents MIME-type sniffing

### Referrer-Policy
Controls Referer header: `no-referrer`, `strict-origin-when-cross-origin`, `same-origin`, etc.

### Permissions-Policy
Controls browser feature access: `camera=()`, `microphone=()`, `geolocation=(self)`, `payment=()`

### Cross-Origin headers (CORS)
- `Access-Control-Allow-Origin` — which origins can access
- `Access-Control-Allow-Methods` — allowed HTTP methods
- `Access-Control-Allow-Headers` — allowed request headers
- `Access-Control-Expose-Headers` — exposed response headers
- `Access-Control-Max-Age` — preflight cache duration

### Strict-Transport-Security (HSTS)
`max-age=31536000; includeSubDomains; preload`
Instructs browser to always use HTTPS for the domain.

## Server token hiding

### nginx
`server_tokens off;` — removes version from error pages and Server header

### Apache
```
ServerTokens Prod    → Server: Apache
ServerTokens Major   → Server: Apache/2
ServerTokens Min     → Server: Apache/2.4
ServerTokens OS      → Server: Apache/2.4 (Debian)
ServerTokens Full    → Server: Apache/2.4.59 (Debian)
ServerSignature Off  — removes version from error pages
```

## HTTP method restriction

### nginx
```
limit_except GET POST HEAD {
    deny all;
}
```
Blocks TRACE, TRACK, OPTIONS, PUT, DELETE, CONNECT, PATCH.

### Apache
```
<LimitExcept GET POST HEAD>
    Deny from all
</LimitExcept>
```

## Request limits

| Limit | nginx directive | Apache directive |
|-------|----------------|------------------|
| Body size | `client_max_body_size N;` | `LimitRequestBody N` |
| Header buffers | `large_client_header_buffers 4 8k;` | `LimitRequestFields 50` |
| Field size | (implicit) | `LimitRequestFieldSize 8190` |
| Request line | `large_client_header_buffers` | `LimitRequestLine 8190` |
| Rate limiting | `limit_req_zone` + `limit_req` | `mod_ratelimit` |

## WAF — ModSecurity

### Rule engine modes
| Mode | Description |
|------|-------------|
| `SecRuleEngine On` | Blocking — anomalies return 403 |
| `SecRuleEngine DetectionOnly` | Log only — anomalies logged but passed through |
| `SecRuleEngine Off` | Disabled |

### OWASP Core Rule Set (CRS)
- Over 200 rules covering the OWASP Top 10
- Anomaly scoring mode — each rule adds to a score; threshold triggers block
- Paranoia levels 1–4 (higher = stricter but more false positives)
- `crs-setup.conf` — tuning file for thresholds, allowed methods, content types

### CRS anomaly scoring
```
SecDefaultAction "phase:2,log,auditlog,pass"
SecAction "id:900000,phase:1,nolog,pass,setvar:tx.anomaly_score=0"
SecAction "id:900999,phase:2,deny,status:403,setvar:tx.anomaly_score=+%{tx.anomaly_score}"
```

### libmodsecurity3 / Coraza
- libmodsecurity3: C++ library, used by nginx ModSecurity connector
- Coraza: Go implementation, used by Caddy and Traefik

## File permissions

| Pattern | Permissions | Owner | Reason |
|---------|-------------|-------|--------|
| Document root | `0750` | deployer:deployer | Server reads, no write |
| Upload directory | `0750` | www-data:www-data | Writable but no-exec |
| Config files | `0640` | root:root | Contains passwords/secrets |
| Log directory | `0750` | root:adm | Logrotate + analysis |
| SSL private keys | `0600` | root:root | Sensitive |

## TLS hardening

### nginx
```
ssl_protocols TLSv1.2 TLSv1.3;
ssl_ciphers EECDH+AESGCM:EDH+AESGCM;
ssl_prefer_server_ciphers on;
ssl_dhparam /etc/nginx/dhparam.pem;
ssl_ecdh_curve secp384r1;
ssl_session_cache shared:SSL:10m;
ssl_session_tickets off;
ssl_stapling on;
ssl_stapling_verify on;
```

### DH parameters
Generate with: `openssl dhparam -out /etc/nginx/dhparam.pem 2048`

### Apache
```
SSLProtocol -all +TLSv1.2 +TLSv1.3
SSLCipherSuite EECDH+AESGCM:EDH+AESGCM
SSLHonorCipherOrder on
SSLSessionTickets off
SSLUseStapling on
```

## Audit tools

| Tool | Scope | Output |
|------|-------|--------|
| **Nikto** | Web server scanner, 6700+ tests | HTML/text/CSV report |
| **Wapiti** | Black-box web app scanner | HTML/XML/vuln report |
| **OWASP ZAP** | Full-featured scanner (passive + active) | HTML/JSON, integrates with CI |
| **SSL Labs** | Public TLS assessment | Letter grade A+ to F |
| **Mozilla Observatory** | HTTP headers + TLS grading | Score + detailed breakdown |
| **testssl.sh** | CLI TLS testing (no install) | Terminal/HTML/JSON |

## Finding remediation workflow

1. Run audit tool → generate findings list
2. Categorize by severity (CVSS: Critical/High/Medium/Low)
3. Apply compensating controls for immediate mitigation
4. Implement permanent fix per finding type
5. Re-audit → confirm fix
6. Document in security baseline

## Python helper

`files/security_hardening.py` — provides:
- `generate_nginx_security_headers()` — produces nginx `add_header` directives for all recommended security headers
- `generate_apache_security_headers()` — produces Apache `Header always set` directives
- `build_csp_policy()` — constructs a Content-Security-Policy string from individual directive values
- `validate_security_headers()` — checks a response header dict against required headers; returns score, grade, missing/misconfigured lists
- `map_finding_to_fix()` — maps common audit findings to their nginx remediation directives
- `generate_nginx_request_limits()` — produces body size, header buffer, and rate limiting config
- `generate_tls_hardening()` — produces TLS protocol, cipher, DH param, session cache, stapling config
