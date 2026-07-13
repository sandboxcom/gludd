# load_balancer

Load balancer role for the `general_ludd.web_server` collection. Configures load balancing algorithms, session persistence, health checks, circuit breaking, blue-green deployments, and canary releases.

## Supported LB types

| Type | Package | Config path | Validation |
|------|---------|-------------|------------|
| nginx | `nginx` | `/etc/nginx/conf.d/upstream_*.conf` | `nginx -t` |
| HAProxy | `haproxy` | `/etc/haproxy/haproxy.cfg` | `haproxy -c -f ...` |

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `lb_method` | `round_robin` | One of: round_robin, least_conn, ip_hash, random, least_time, hash |
| `sticky_session` | `cookie` | One of: cookie, ip, route, none |
| `health_check_interval` | `10` | Seconds between health checks |
| `health_check_timeout` | `5` | Health check timeout (s) |
| `unhealthy_threshold` | `3` | Unhealthy before removal |
| `healthy_threshold` | `2` | Healthy before re-add |
| `backend_servers` | see defaults | List of `{host, port, weight}` |
| `backup_servers` | `[]` | Standby servers |
| `circuit_breaker` | see defaults | `{max_connections, max_pending_requests, max_retries}` |
| `blue_green_enabled` | `false` | Blue-green deployment mode |
| `canary_enabled` | `false` | Canary release mode |

## nginx upstream balancing

| Method | Directive | Description |
|--------|-----------|-------------|
| round_robin | (default) | Sequential, weighted rotation |
| least_conn | `least_conn;` | Fewest active connections |
| ip_hash | `ip_hash;` | Client IP hash (IPv4 first 3 octets, IPv6 entire address) |
| random | `random;` | Random selection |
| least_time | `least_time header;` or `least_time last_byte;` | Lowest average response time (header = TTFB, last_byte = full) |
| hash | `hash $request_uri consistent;` | User-defined hash key with consistent hashing |

### Server options
| Option | Description |
|--------|-------------|
| `weight=N` | Relative weight (default 1) |
| `max_fails=N` | Failures before marking down (default 1) |
| `fail_timeout=T` | Time before retry (default 10s) |
| `backup` | Only used when ALL non-backup servers are down |
| `down` | Permanently unavailable |
| `max_conns=N` | Maximum concurrent connections |
| `slow_start=T` | Gradual weight increase after recovery |

### Session persistence
- **cookie**: `sticky cookie SERVERID expires=1h domain=.local path=/;`
- **ip_hash**: IP-based affinity (cons of `ip_hash`)
- **route**: `sticky route $route;` — splits by variable

## HAProxy balancing

| Method | Description |
|--------|-------------|
| `roundrobin` | Weighted round-robin |
| `leastconn` | Fewest connections (weighted) |
| `source` | Client IP hash |
| `uri` | URI hash (ignoring query string) |
| `url_param` | Hash on URL parameter value |
| `hdr` | Hash on request header value |
| `rdp-cookie` | RDP cookie persistence |

### Cookie persistence modes
| Mode | Description |
|------|-------------|
| `insert` | Insert SET-COOKIE on first response |
| `rewrite` | Rewrite existing cookie |
| `prefix` | Prefix server ID to existing cookie value |

### Server health check options
| Option | Description |
|--------|-------------|
| `check` | Enable health checking |
| `inter Ns` | Check interval |
| `rise N` | Consecutive OK checks to mark UP |
| `fall N` | Consecutive failures to mark DOWN |
| `backup` | Backup pool member |
| `disabled` | Admin-disabled, no traffic |
| `send-proxy` | Send PROXY protocol header |
| `send-proxy-v2` | Send PROXY protocol v2 |
| `observe layer4` | L4 connection observation |
| `on-error` | Action on check error (fastinter, mark-down, etc.) |

## Layer 4 vs Layer 7

| Aspect | L4 (stream/TCP) | L7 (HTTP) |
|--------|-----------------|-----------|
| Protocol | Any TCP/UDP | HTTP/HTTPS |
| Health check | TCP connect | HTTP method + path + expected status |
| Routing decision | Destination IP:port | Request headers (Host, path, cookie) |
| Session persistence | Source IP hash | Cookie-based |
| SSL termination | Passthrough | Terminate + re-encrypt |
| PROXY protocol | Recommended | Optional |

### HAProxy mode configuration
```
listen mysql_cluster
    bind *:3306
    mode tcp
    balance leastconn
    option mysql-check user haproxy
    server db1 10.0.0.11:3306 check
    server db2 10.0.0.12:3306 check
```

## Health checks

| Check type | nginx | HAProxy |
|------------|-------|---------|
| TCP connect | (implicit) | `option tcp-check` |
| HTTP | `check uri=/health` (commercial) | `option httpchk GET /health` |
| gRPC | (commercial) | `option grpc-check` |
| MySQL | n/a | `option mysql-check` |
| PostgreSQL | n/a | `option pgsql-check` |
| Custom script | n/a | `external-check command /path/script` |

## Circuit breaking

Prevents cascading failures by limiting:

| Limit | nginx directive | HAProxy option |
|-------|----------------|----------------|
| Max connections | `max_conns=N` | `maxconn N` |
| Max pending | `queue=N` | `backlog N` |
| Max requests per conn | `max_reqs=N` | (via stick-table) |
| Retries | `proxy_next_upstream_tries N` | `retries N` |

## Blue-green deployment

Two identical environments (blue = active, green = idle). Traffic shifted by weight change:
```
upstream app {
    server blue1:80 weight=100;    # active
    server green1:80 weight=0;     # idle — promote by swapping weights
}
```
Zero-downtime: validate green, swap weights, reload, drain blue.

## Canary releases

Split traffic by percentage, header, or cookie:
```
split_clients $remote_addr $canary_group {
    10% canary_upstream;
    *   stable_upstream;
}
```
Gradual rollout: start at 5%, increase to 10%, 25%, 50%, 100% with validation at each step.

## Python helper

`files/load_balancer.py` — generates:
- `generate_nginx_upstream()` — nginx upstream block with LB method, sticky sessions, health checks, backup servers
- `generate_nginx_circuit_breaker()` — circuit breaker upstream with connection/request limits
- `generate_nginx_blue_green()` — weight-based blue-green upstream
- `generate_haproxy_frontend()` — HAProxy frontend with cookie persistence and ACL routing
- `generate_haproxy_backend()` — HAProxy backend with health checks, rise/fall, backup servers
- `build_canary_split()` — canary traffic split with split_clients and header override
