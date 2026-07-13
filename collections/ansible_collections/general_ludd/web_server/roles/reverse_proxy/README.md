# reverse_proxy

Reverse proxy role for the `general_ludd.web_server` collection. Configures upstream pools, proxy pass, caching, and health checks across four proxy engines.

## Supported proxy types

| Type | Package | Validation | Config reload |
|------|---------|-----------|---------------|
| nginx | `nginx` | `nginx -t` | `systemctl reload nginx` |
| HAProxy | `haproxy` | `haproxy -c -f ...` | `systemctl reload haproxy` |
| Traefik | manual | n/a | `systemctl reload traefik` |
| Envoy | `envoy` | `envoy --mode validate` | hot restart |

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `proxy_type` | `nginx` | One of: nginx, haproxy, traefik, envoy |
| `upstream_servers` | `[{host: 127.0.0.1, port: 3000}]` | Backend server list |
| `lb_method` | `round_robin` | Load balancing algorithm |
| `health_check_path` | `/health` | Health check endpoint |
| `cache_enabled` | `false` | Enable proxy caching |
| `cache_ttl` | `3600` | Cache TTL in seconds |
| `listen_port` | `443` | Proxy listen port |
| `ssl_enabled` | `true` | Enable TLS |
| `proxy_read_timeout` | `60` | Proxy read timeout (s) |
| `websocket_enabled` | `false` | Enable WebSocket proxying |
| `grpc_enabled` | `false` | Enable gRPC proxying |

## nginx reverse proxy knowledge

### Core directives
- `proxy_pass` — forwards requests to upstream server or group
- `proxy_set_header` — sets forwarded headers:
  - `X-Real-IP`: client IP address
  - `X-Forwarded-For`: chain of proxy IPs
  - `X-Forwarded-Proto`: original scheme (http/https)
  - `Host`: original Host header
- `proxy_redirect` — rewrites Location/Refresh headers from backend
- `proxy_buffering` — controls response buffering (off for SSE/streaming)
- `proxy_cache_path` — defines cache storage location and parameters
- `proxy_cache_key` — cache key template (e.g. `"$scheme$request_method$host$request_uri"`)
- `proxy_cache_valid` — per-status-code cache validity

### WebSocket proxying
```
location /ws/ {
    proxy_pass http://backend;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "Upgrade";
    proxy_read_timeout 3600s;
}
```

### Server-Sent Events
```
proxy_buffering off;
chunked_transfer_encoding on;
proxy_cache off;
```

### gRPC proxying
```
location / {
    grpc_pass grpc://backend;
}
```
Requires HTTP/2 end-to-end.

### Proxy Protocol
PROXY protocol v1/v2 preserves client IP through multiple proxy layers. nginx accepts via `listen 443 ssl proxy_protocol` and sends via `proxy_protocol on`.

## HAProxy reverse proxy knowledge

### Architecture
- **frontend** — defines bind address/port, ACLs, default backend
- **backend** — server pool, balance algorithm, health checks
- **ACLs** — access control lists for routing (host header, path, method, headers)
- **stick tables** — in-memory key-value store for rate limiting, persistence
- **stats socket** — runtime control via unix socket (enable/disable servers, drain connections)
- **seamless reload** — `haproxy -sf $(cat /var/run/haproxy.pid)` preserves connections

### Rate limiting
```
stick-table type ip size 100k expire 30s store http_req_rate(10s)
http-request track-sc0 src
http-request deny if { sc_http_req_rate(0) gt 100 }
```

## Traefik reverse proxy knowledge

### Configuration
- **Static config** — entry points, providers, API, global settings
- **Dynamic config** — routers, services, middlewares, TLS certificates
- **Providers** — file, etcd, consul, docker, kubernetes

### Router → Service → Middleware chain
- **Router**: matches request (Host, Path, Headers) → routes to service
- **Service**: load balances across backend servers
- **Middleware**: chains of request/response transformations (auth, rate limit, headers, retry, circuit breaker)

### Auto TLS
Traefik integrates with Let's Encrypt for automatic certificate provisioning via ACME. Configure certificate resolvers in static config; routers reference them with `tls.certResolver`.

### Kubernetes ingress
Traefik acts as an Ingress Controller via `IngressRoute` CRD, supporting TCP/UDP ingress routes.

## Envoy reverse proxy knowledge

### Core concepts
- **Listeners** — bind address/port, filter chains
- **Clusters** — upstream service endpoints, load balancing, circuit breaking
- **Routes** — virtual host matching, path rewriting, redirects
- **Filters** — HTTP connection manager, rate limit, RBAC, ext_authz, CORS, fault injection

### xDS dynamic configuration
Envoy discovers configuration dynamically via xDS APIs:
- LDS (Listener Discovery Service)
- CDS (Cluster Discovery Service)
- RDS (Route Discovery Service)
- EDS (Endpoint Discovery Service)

### Hot restart
Envoy supports zero-downtime restarts via shared memory and unix domain sockets. The parent process drains connections while the child takes over.

## Python helper

`files/reverse_proxy.py` — generates:
- `generate_nginx_upstream()` — nginx upstream block with LB method and server entries
- `generate_nginx_proxy_server()` — nginx server block with proxy_pass, headers, caching
- `generate_haproxy_frontend()` — HAProxy frontend with ACLs
- `generate_haproxy_backend()` — HAProxy backend with health checks
- `build_full_haproxy_config()` — complete HAProxy configuration
