"""Reverse proxy config generators for nginx and HAProxy."""

from __future__ import annotations

from typing import Optional


NGINX_UPSTREAM_TEMPLATE = """\
upstream {name} {{
    {lb_method}
{servers}
    {health_check}
}}
"""

NGINX_SERVER_TEMPLATE = """\
server {{
    listen {port}{ssl};
    server_name {server_name};

{locations}
}}
"""

NGINX_LOCATION_TEMPLATE = """\
    location {path} {{
        proxy_pass {upstream};
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout {connect_timeout}s;
        proxy_read_timeout {read_timeout}s;
        proxy_send_timeout {send_timeout}s;
        {cache}
    }}
"""

HAPROXY_FRONTEND_TEMPLATE = """\
frontend {name}_frontend
    bind *:{port}
    mode http
    default_backend {name}_backend
    {acl_entries}
"""

HAPROXY_BACKEND_TEMPLATE = """\
backend {name}_backend
    mode http
    balance {lb_method}
{servers}
    {health_check}
"""


def _build_lb_method(lb_method: str) -> str:
    method_map = {
        "round_robin": "",
        "least_conn": "least_conn;",
        "ip_hash": "ip_hash;",
        "random": "random;",
    }
    upstream_directive = method_map.get(lb_method, "")
    if upstream_directive:
        return f"    {upstream_directive}"
    return ""


def _build_cache_config(cache_enabled: bool, cache_ttl: int) -> str:
    if not cache_enabled:
        return ""
    path = "/var/cache/nginx/proxy_cache"
    levels = "1:2"
    return (
        f"    add_header X-Cache-Status $upstream_cache_status;\n"
        f"    proxy_cache proxy_cache;\n"
        f"    proxy_cache_key \"$scheme$request_method$host$request_uri\";\n"
        f"    proxy_cache_valid 200 {cache_ttl}s;"
    )


def generate_nginx_upstream(
    name: str,
    servers: list[dict[str, object]],
    lb_method: str = "round_robin",
    health_check_path: str = "/health",
) -> str:
    server_lines = []
    for i, srv in enumerate(servers):
        host = srv.get("host", "127.0.0.1")
        port = srv.get("port", 80)
        weight = srv.get("weight", 1)
        backup = " backup" if srv.get("backup", False) else ""
        max_fails = srv.get("max_fails", 3)
        fail_timeout = srv.get("fail_timeout", 30)
        server_lines.append(
            f"    server {host}:{port} weight={weight} "
            f"max_fails={max_fails} fail_timeout={fail_timeout}s{backup};"
        )
    return NGINX_UPSTREAM_TEMPLATE.format(
        name=name,
        lb_method=_build_lb_method(lb_method),
        servers="\n".join(server_lines),
        health_check=f"    # health_check uri={health_check_path};",
    )


def generate_nginx_proxy_server(
    server_name: str,
    upstream_name: str,
    port: int = 443,
    ssl: bool = True,
    cache_enabled: bool = False,
    cache_ttl: int = 3600,
    connect_timeout: int = 5,
    read_timeout: int = 60,
    send_timeout: int = 60,
    locations: Optional[list[dict[str, str]]] = None,
) -> str:
    path_entries = locations or [{"path": "/", "upstream": f"http://{upstream_name}"}]
    location_blocks = []
    for loc in path_entries:
        cache = _build_cache_config(
            cache_enabled=cache_enabled,
            cache_ttl=cache_ttl,
        )
        location_blocks.append(
            NGINX_LOCATION_TEMPLATE.format(
                path=loc["path"],
                upstream=loc["upstream"],
                connect_timeout=connect_timeout,
                read_timeout=read_timeout,
                send_timeout=send_timeout,
                cache=cache,
            )
        )
    ssl_directive = " ssl" if ssl else ""
    return NGINX_SERVER_TEMPLATE.format(
        port=port,
        ssl=ssl_directive,
        server_name=server_name,
        locations="\n".join(location_blocks),
    )


def generate_haproxy_frontend(
    name: str,
    port: int = 443,
    acls: Optional[list[str]] = None,
) -> str:
    acl_entries = acls or []
    return HAPROXY_FRONTEND_TEMPLATE.format(
        name=name,
        port=port,
        acl_entries="\n    " + "\n    ".join(acl_entries) if acl_entries else "",
    )


def generate_haproxy_backend(
    name: str,
    servers: list[dict[str, object]],
    lb_method: str = "roundrobin",
    health_check_path: str = "/health",
    health_check_port: Optional[int] = None,
) -> str:
    haproxy_lb_map = {
        "round_robin": "roundrobin",
        "least_conn": "leastconn",
        "ip_hash": "source",
        "uri": "uri",
    }
    balance_alg = haproxy_lb_map.get(lb_method, "roundrobin")

    server_lines = []
    for srv in servers:
        host = srv.get("host", "127.0.0.1")
        port = health_check_port or srv.get("port", 80)
        weight = srv.get("weight", 1)
        backup = " backup" if srv.get("backup", False) else ""
        check_port = srv.get("health_check_port", port)
        server_lines.append(
            f"    server {host.replace('.', '_')} {host}:{port} "
            f"weight {weight} check inter 5s port {check_port}{backup}"
        )
    health_check = (
        f"    option httpchk GET {health_check_path}"
    )
    return HAPROXY_BACKEND_TEMPLATE.format(
        name=name,
        lb_method=balance_alg,
        servers="\n".join(server_lines),
        health_check=health_check,
    )


def build_full_haproxy_config(
    name: str,
    servers: list[dict[str, object]],
    port: int = 443,
    lb_method: str = "roundrobin",
    health_check_path: str = "/health",
    acls: Optional[list[str]] = None,
) -> str:
    frontend = generate_haproxy_frontend(name, port, acls)
    backend = generate_haproxy_backend(name, servers, lb_method, health_check_path)
    return "global\n    daemon\n    stats socket /run/haproxy/admin.sock mode 660 level admin\n\ndefaults\n    mode http\n    timeout connect 5s\n    timeout client 50s\n    timeout server 50s\n\n" + frontend + "\n" + backend
