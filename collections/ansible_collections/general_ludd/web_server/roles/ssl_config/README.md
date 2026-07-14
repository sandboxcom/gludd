# ssl_config — SSL/TLS Configuration and Certificate Management

Generates and installs certificates, configures SSL virtual hosts, enables HSTS,
sets cipher suites, OCSP stapling, and TLS version enforcement.

## Certificate Types

### Self-Signed
- Generated with `openssl req -x509 -newkey rsa:4096 -sha384 -days 365 -nodes`.
- Suitable for internal/testing. Not trusted by browsers.
- Can be created ad-hoc: no CSR needed, key + cert generated together.

### Let's Encrypt (ACME / certbot)
- Free, automated, short-lived (90-day) certificates.
- `certbot certonly --nginx -d example.com -d www.example.com`.
- Auto-renewal via cron or systemd timer: `certbot renew --quiet`.
- Rate limits: 50 certs/domain/week, 5 duplicate certs/week.
- Staging environment: `--test-cert` / `--dry-run` for testing.
- ACME challenge types: HTTP-01 (port 80), DNS-01 (TXT record), TLS-ALPN-01.

### CA-Signed
- Certificate chain: end-entity → intermediate CA(s) → root CA.
- Bundle intermediate and root certs for `ssl_trusted_certificate`.
- Extended Validation (EV): organization vetted manually, green bar in browser.
- Organization Validation (OV): organization verified.
- Domain Validation (DV): only domain control verified.

### Wildcard
- `*.example.com` covers all subdomains at that level.
- Does NOT cover `example.com` itself (include both SAN entries).
- Does NOT cover `sub.sub.example.com` (only one level deep).
- DNS-01 challenge required for Let's Encrypt wildcards.

## TLS Versions

- **TLS 1.3** (preferred): 0-RTT, forward secrecy mandatory, removes obsolete features.
- **TLS 1.2** (minimum acceptable): widely supported.
- **Disabled**: TLS 1.0, TLS 1.1, SSLv2, SSLv3 — all cryptographically broken.
- nginx: `ssl_protocols TLSv1.2 TLSv1.3;`
- apache: `SSLProtocol -all +TLSv1.2 +TLSv1.3`

## Cipher Suites

### Modern Profile (Mozilla)
- TLS 1.3 only: `TLS_AES_256_GCM_SHA384:TLS_AES_128_GCM_SHA256:TLS_CHACHA20_POLY1305_SHA256`
- Best security. Drops many older clients. Prefer this for new deployments.

### Intermediate Profile (Mozilla)
- TLS 1.2 + 1.3, ECDHE forward secrecy, AEAD ciphers.
- `ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:...`
- Balances security with broad client compatibility. Recommended default.

### Old Profile (Mozilla)
- TLS 1.0+, includes DHE ciphers for legacy clients.
- Use ONLY when required by legacy internal systems. Not internet-facing.

### Cipher Properties
- **ECDHE** = elliptic-curve Diffie-Hellman ephemeral → forward secrecy.
- **AES-GCM** = AES in Galois/Counter Mode → authenticated encryption.
- **ChaCha20-Poly1305** = software-efficient AEAD, good for mobile.
- `ssl_prefer_server_ciphers on;` — server chooses from client's list.

### DH Parameters
- Generate: `openssl dhparam -out dhparam.pem 2048`
- nginx: `ssl_dhparam /etc/nginx/dhparam.pem;`
- 2048-bit minimum. 4096-bit recommended for high-security.

## HSTS (HTTP Strict Transport Security)

- `Strict-Transport-Security: max-age=31536000; includeSubDomains; preload`
- **max-age**: duration in seconds (1 year = 31536000).
- **includeSubDomains**: applies to all subdomains.
- **preload**: submit to browser preload lists (irreversible, plan carefully).
- First request still goes over HTTP unless preloaded. Set short max-age initially, then increase.
- nginx: `add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;`

## OCSP Stapling

- Server fetches OCSP response periodically, includes it in TLS handshake.
- Eliminates client OCSP lookup → faster, more private.
- nginx: `ssl_stapling on; ssl_stapling_verify on; ssl_trusted_certificate /path/to/chain.pem;`
- Requires resolver: `resolver 8.8.8.8 8.8.4.4 valid=300s;`
- Verify with: `openssl s_client -connect example.com:443 -status`

## Certificate Transparency

- `Expect-CT: max-age=86400, enforce, report-uri="https://example.com/report"`
- Forces user agents to require CT for the certificate.
- Chrome requires CT for all new certs since April 2018.
- `Expect-CT` header being deprecated in favor of CT enforcement at CA level.

## HPKP (HTTP Public Key Pinning — DEPRECATED)

- `Public-Key-Pins: pin-sha256="base64=="; max-age=5184000; includeSubDomains; report-uri="..."`
- **Deprecated by all major browsers.** Use Certificate Transparency instead.
- Risk: misconfiguration can brick a domain permanently. Do NOT deploy.
