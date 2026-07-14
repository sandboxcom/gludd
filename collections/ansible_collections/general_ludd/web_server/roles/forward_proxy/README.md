# forward_proxy

Forward and transparent proxy role for the `general_ludd.web_server` collection. Installs and configures Squid, tinyproxy, or privoxy with ACLs, authentication, caching, and SSL interception.

## Supported proxy types

| Type | Package | Config path | Validation |
|------|---------|-------------|------------|
| Squid | `squid` | `/etc/squid/squid.conf` | `squid -k parse` |
| tinyproxy | `tinyproxy` | `/etc/tinyproxy/tinyproxy.conf` | n/a |
| privoxy | `privoxy` | `/etc/privoxy/config` | n/a |

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `proxy_type` | `squid` | One of: squid, tinyproxy, privoxy |
| `port` | `3128` | Proxy listen port |
| `allowed_networks` | `[10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16]` | Allowed source CIDRs |
| `auth_method` | `none` | One of: none, basic, ntlm, kerberos |
| `cache_dir` | `/var/spool/squid` | Cache storage location |
| `cache_size_mb` | `2048` | Cache size in MB |
| `ssl_bump_enabled` | `false` | Enable SSL/TLS interception |
| `transparent_mode` | `false` | Enable transparent interception |
| `max_object_size_mb` | `128` | Maximum cacheable object size |
| `allow_direct` | `false` | Allow direct (non-proxied) connections |

## Squid knowledge

### http_access ACLs
ACLs are evaluated top-to-bottom; first match wins. Common ACL types:

| ACL type | Example | Description |
|----------|---------|-------------|
| `src` | `src 10.0.0.0/8` | Source IP CIDR |
| `dst` | `dst 203.0.113.0/24` | Destination IP |
| `dstdomain` | `dstdomain .example.com` | Destination domain |
| `url_regex` | `url_regex \.exe$` | URL regex match |
| `time` | `time M-F 09:00-17:00` | Time-of-day restriction |
| `port` | `port 80 443` | Destination port |
| `method` | `method GET POST` | HTTP method |
| `proto` | `proto HTTP` | Protocol |
| `ssl::server_name` | `ssl::server_name .bank.com` | TLS SNI (with ssl_bump) |

### Authentication
- **Basic (NCSA)**: `auth_param basic program /usr/lib/squid/basic_ncsa_auth /etc/squid/passwd`
- **Digest**: `auth_param digest program /usr/lib/squid/digest_file_auth /etc/squid/digest_passwd`
- **Negotiate (Kerberos)**: `auth_param negotiate program /usr/lib/squid/negotiate_kerberos_auth`
- **NTLM**: `auth_param ntlm program /usr/bin/ntlm_auth --helper-protocol=squid-2.5-ntlmssp`
- **LDAP**: `auth_param basic program /usr/lib/squid/basic_ldap_auth`

### Cache storage types
| Type | Description | Use case |
|------|-------------|----------|
| `ufs` | Unix File System | Default, widely compatible |
| `aufs` | Async UFS | Better performance, threaded |
| `diskd` | Disk daemon | External process, avoids blocking |
| `rock` | Rock store | Shared-memory cache, no fsync |

### refresh_pattern
Controls cache freshness: `refresh_pattern \.jpg$ 1440 50% 2880 ignore-reload`

### SSL/TLS interception (ssl_bump)
Steps in the bump chain:
1. **step1 (client hello)** — peek at SNI
2. **step2 (server hello)** — peek at server cert
3. **step3 (finished)** — bump (terminate + re-encrypt with generated cert)

Requires a CA certificate distributed to all clients.

### Delay pools (bandwidth throttling)
```
delay_pools 1
delay_class 1 2
delay_parameters 1 64000/64000 -1/-1
```

### ICAP/ECAP (content adaptation)
Integrates with external filtering engines for content scanning, transformation, or blocking.

## Transparent proxy knowledge

Two methods for intercepting traffic without client configuration:

### iptables REDIRECT
```
iptables -t nat -A PREROUTING -i eth0 -p tcp --dport 80 -j REDIRECT --to-port 3128
```
Clients are unaware; no proxy config needed. Source IP is lost — connection appears from proxy.

### TPROXY
Preserves original client IP using `TPROXY` iptables target. Requires policy routing:
```
iptables -t mangle -A PREROUTING -p tcp --dport 80 -j TPROXY --tproxy-mark 0x1/0x1 --on-port 3128
```
Squid must be built with `--enable-linux-netfilter` and configured with `http_port 3128 tproxy`.

### WCCP (Web Cache Communication Protocol)
Cisco-proprietary protocol for transparent redirection at the router level. Squid supports WCCPv2 for GRE-encapsulated traffic interception.

## tinyproxy knowledge
- Lightweight, single-process, ~2MB memory footprint
- Non-caching by design
- Filter/FilterURLs — regex-based URL filtering
- ConnectPort — allowed CONNECT tunnel ports (default 443, 563)
- Upstream proxy chaining: `Upstream proxy2.local 3128`

## privoxy knowledge
- HTTP/1.1 compliant, no caching
- Content filtering engine with pattern-based rules
- Cookie management (crunch, session-only, accept)
- `user.action` — custom action file for fine-grained control
- Trust mechanism — trusted referrers bypass filtering
- Toggle — enable/disable filtering per-request via CGI

## PAC files

Proxy Auto-Configuration (PAC) files are JavaScript functions (`FindProxyForURL(url, host)`) that determine proxy selection dynamically.

### Key functions
- `isPlainHostName(host)` — no dots → DIRECT
- `dnsDomainIs(host, domain)` — domain match
- `shExpMatch(str, pattern)` — shell-style glob
- `isInNet(host, pattern, mask)` — subnet match
- `myIpAddress()` — client IP

### WPAD (Web Proxy Auto-Discovery)
Clients discover PAC files automatically via:
1. DHCP option 252 → PAC URL
2. DNS lookup for `wpad.<domain>` → HTTP server serving `/wpad.dat`

### PAC file serving
Serve `.pac` files with MIME type `application/x-ns-proxy-autoconfig` from any HTTP server.

## Python helper

`files/forward_proxy.py` — provides:
- `build_squid_acls()` — generates Squid ACL definitions and access rules from network/domain/port lists
- `build_squid_auth_config()` — generates authentication config blocks for basic or Kerberos
- `generate_pac_file()` — builds a PAC file with DIRECT exceptions and a PROXY fallback
- `build_tinyproxy_config()` — writes a complete tinyproxy.conf from keyword arguments
