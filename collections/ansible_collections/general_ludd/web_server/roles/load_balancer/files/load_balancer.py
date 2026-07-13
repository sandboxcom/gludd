"""Load balancer config generators — nginx upstream, HAProxy frontend/backend."""

from __future__ import annotations

from typing import Optional


def generate_nginx_upstream(
    name: str,
    servers: list[dict[str, object]],
    lb_method: str = "round_robin",
    sticky_session: str = "cookie",
    sticky_name: str = "SERVERID",
    backup_servers: Optional[list[dict[str, object]]] = None,
) -> str:
    lb_directives = {
        "round_robin": "",
        "least_conn": "    least_conn;\n",
        "ip_hash": "    ip_hash;\n",
        "random": "    random;\n",
        "least_time": "    least_time header;\n",
        "hash": "    hash $request_uri consistent;\n",
    }
    lb_line = lb_directives.get(lb_method, "")

    if sticky_session == "cookie":
        lb_line += f"    sticky cookie {sticky_name} expires=1h domain=.local path=/;\n"
    elif sticky_session == "route":
        lb_line += "    sticky route $route;\n"

    server_lines = []
    for srv in servers:
        host = srv.get("host", "127.0.0.1")
        port = srv.get("port", 80)
        weight = srv.get("weight", 1)
        max_fails = srv.get("max_fails", 3)
        fail_timeout = srv.get("fail_timeout", 30)
        extra_opts = ""
        health_check_path = srv.get("health_check_path")
        if health_check_path:
            extra_opts += f" check uri={health_check_path}"
        server_lines.append(
            f"    server {host.replace('.', '_')}_{port} {host}:{port} "
            f"weight={weight} max_fails={max_fails} "
            f"fail_timeout={fail_timeout}s{extra_opts};"
        )

    for srv in (backup_servers or []):
        host = srv.get("host", "127.0.0.1")
        port = srv.get("port", 80)
        weight = srv.get("weight", 1)
        server_lines.append(
            f"    server {host.replace('.', '_')}_{port} {host}:{port} "
            f"weight={weight} backup;"
        )

    return f"upstream {name} {{\n{lb_line}\n" + "\n".join(server_lines) + "\n}\n"


def generate_nginx_circuit_breaker(
    name: str,
    max_connections: int = 1000,
    max_pending_requests: int = 500,
    max_requests: int = 10000,
    max_retries: int = 3,
) -> str:
    return (
        f"upstream {name} {{\n"
        f"    server unix:/var/run/backend.sock max_conns={max_connections} "
        f"max_reqs={max_requests} queue={max_pending_requests} weight=1;\n"
        f"    proxy_next_upstream error timeout http_500 http_502 http_503;\n"
        f"    proxy_next_upstream_tries {max_retries};\n"
        f"}}\n"
    )


def generate_nginx_blue_green(
    blue_servers: list[dict[str, object]],
    green_servers: list[dict[str, object]],
    active_color: str = "blue",
) -> str:
    blue_weight = 100 if active_color == "blue" else 0
    green_weight = 0 if active_color == "blue" else 100

    lines = ["upstream app {"]
    for srv in blue_servers:
        host = srv["host"]
        port = srv["port"]
        lines.append(f"    server {host}:{port} weight={blue_weight};")
    for srv in green_servers:
        host = srv["host"]
        port = srv["port"]
        lines.append(f"    server {host}:{port} weight={green_weight};")
    lines.append("}")
    return "\n".join(lines)


def generate_haproxy_frontend(
    name: str,
    port: int = 80,
    sticky_session: str = "cookie",
    acls: Optional[list[dict[str, str]]] = None,
) -> str:
    lines = [
        f"frontend {name}_frontend",
        f"    bind *:{port}",
        "    mode http",
        "    option forwardfor",
    ]

    if sticky_session == "cookie":
        lines.append("    cookie SERVERID insert indirect nocache")

    for acl in (acls or []):
        name = acl["name"]
        condition = acl["condition"]
        backend = acl.get("backend", "default_backend")
        lines.append(f"    acl {name} {condition}")
        lines.append(f"    use_backend {backend} if {name}")

    lines.append(f"    default_backend {name}_backend")
    return "\n".join(lines)


def generate_haproxy_backend(
    name: str,
    servers: list[dict[str, object]],
    lb_method: str = "roundrobin",
    sticky_session: str = "cookie",
    health_check_path: str = "/health",
    health_check_method: str = "GET",
) -> str:
    balance_map = {
        "round_robin": "roundrobin",
        "least_conn": "leastconn",
        "ip_hash": "source",
        "uri": "uri",
        "url_param": "url_param userid",
    }
    balance = balance_map.get(lb_method, "roundrobin")

    lines = [
        f"backend {name}_backend",
        "    mode http",
        f"    balance {balance}",
        f"    option httpchk {health_check_method} {health_check_path}",
    ]

    if sticky_session == "cookie":
        lines.append("    cookie SERVERID insert indirect nocache")

    for srv in servers:
        host = srv.get("host", "127.0.0.1")
        port = srv.get("port", 80)
        weight = srv.get("weight", 1)
        srv_name = srv.get("name", host.replace(".", "_"))
        rise = srv.get("rise", 3)
        fall = srv.get("fall", 3)
        lines.append(
            f"    server {srv_name} {host}:{port} check inter 10s "
            f"rise {rise} fall {fall} weight {weight}"
        )

    for srv in (servers or []):
        if srv.get("backup"):
            host = srv.get("host", "127.0.0.1")
            port = srv.get("port", 80)
            lines.append(f"    server {host.replace('.', '_')}_bkp {host}:{port} check backup")

    return "\n".join(lines)


def build_canary_split(
    stable_weight: int = 90,
    canary_weight: int = 10,
    canary_header: str = "X-Canary",
    canary_value: str = "true",
) -> str:
    return (
        f"split_clients $remote_addr $canary_split {{\n"
        f"    {canary_weight}%   canary_upstream;\n"
        f"    *                 stable_upstream;\n"
        f"}}\n"
        f"map $http_{canary_header} $selected_upstream {{\n"
        f"    default $canary_split;\n"
        f"    \"{canary_value}\" canary_upstream;\n"
        f"    \"false\"       stable_upstream;\n"
        f"}}"
    )
