"""Forward proxy helpers — Squid ACL builder, PAC file generator, tinyproxy config writer."""

from __future__ import annotations


def build_squid_acls(
    allowed_networks: list[str],
    blocked_domains: list[str] | None = None,
    allowed_ports: list[int] | None = None,
) -> str:
    lines: list[str] = []
    lines.append("# ACL definitions")
    lines.append("acl localnet src 127.0.0.0/8")

    for i, net in enumerate(allowed_networks):
        lines.append(f"acl allowed_net_{i} src {net}")

    if blocked_domains:
        blocked_path = "/etc/squid/blocked_domains"
        lines.append(f"acl blocked_domains dstdomain \"{blocked_path}\"")

    safe_ports = allowed_ports or [80, 443, 21, 70, 210, 1025, 65535]
    lines.append(f"acl SSL_ports port {' '.join(str(p) for p in safe_ports)}")
    lines.append(f"acl Safe_ports port {' '.join(str(p) for p in safe_ports)}")

    lines.append("")
    lines.append("# Access rules")
    lines.append("http_access deny !Safe_ports")
    lines.append("http_access deny CONNECT !SSL_ports")

    if blocked_domains:
        lines.append("http_access deny blocked_domains")

    lines.append("http_access allow localnet")
    net_allow = " ".join(f"allowed_net_{i}" for i in range(len(allowed_networks)))
    lines.append(f"http_access allow {net_allow}")
    lines.append("http_access deny all")
    return "\n".join(lines)


def build_squid_auth_config(auth_method: str) -> str:
    base = ""
    if auth_method == "basic":
        base = (
            "auth_param basic program /usr/lib/squid/basic_ncsa_auth /etc/squid/passwd\n"
            "auth_param basic children 5\n"
            "auth_param basic realm Squid Proxy\n"
            "auth_param basic credentialsttl 2 hours\n"
            "acl authenticated proxy_auth REQUIRED\n"
            "http_access allow authenticated\n"
        )
    elif auth_method == "kerberos":
        base = (
            "auth_param negotiate program /usr/lib/squid/negotiate_kerberos_auth\n"
            "auth_param negotiate children 10\n"
            "auth_param negotiate keep_alive on\n"
            "acl authenticated proxy_auth REQUIRED\n"
            "http_access allow authenticated\n"
        )
    return base


def generate_pac_file(
    direct_hosts: list[str],
    proxy_host: str = "proxy.local",
    proxy_port: int = 3128,
) -> str:
    proxy_directive = f'PROXY {proxy_host}:{proxy_port}'
    conditions: list[str] = []
    for host in direct_hosts:
        conditions.append(f'        if (shExpMatch(host, "{host}")) return "DIRECT";')
    return f"""\
function FindProxyForURL(url, host) {{
    if (isPlainHostName(host)) return "DIRECT";
    if (dnsDomainIs(host, ".local")) return "DIRECT";
{chr(10).join(conditions)}
    return "{proxy_directive}";
}}"""


def build_tinyproxy_config(**kwargs: object) -> str:
    port = kwargs.get("port", 3128)
    allowed = kwargs.get("allowed_networks", ["10.0.0.0/8"])
    logfile = kwargs.get("logfile", "/var/log/tinyproxy/tinyproxy.log")
    timeout = kwargs.get("timeout", 600)
    transparent = kwargs.get("transparent", False)

    lines: list[str] = []
    lines.append(f"Port {port}")
    lines.append("User nobody")
    lines.append("Group nogroup")
    lines.append(f"Timeout {timeout}")
    lines.append(f'LogFile "{logfile}"')
    lines.append("LogLevel Info")
    if transparent:
        lines.append("DisableViaHeader Yes")

    for net in allowed:
        lines.append(f"Allow {net}")

    connect_ports = kwargs.get("connect_ports", [443, 563])
    for p in connect_ports:
        lines.append(f"ConnectPort {p}")

    upstream_proxy = kwargs.get("upstream_proxy")
    if upstream_proxy:
        lines.append(f"Upstream {upstream_proxy['host']} {upstream_proxy['port']}")

    return "\n".join(lines)
