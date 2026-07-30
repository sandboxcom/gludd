"""Shared Python module for web server operations.

nginx / apache config generation, validation, and auditing; SSL/TLS config;
CGI/WSGI scaffolding; log parsing and logrotate; reverse-proxy / forward-proxy /
load-balancer config generation; security headers and CSP generation.

Uses stdlib only: re, json, ssl, datetime, ipaddress, textwrap.
"""

from __future__ import annotations

import ipaddress
import json
import re
import ssl
import textwrap
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, cast

# ---------------------------------------------------------------------------
# HTTP Server — nginx
# ---------------------------------------------------------------------------

_NGINX_SYNTAX_ERRORS: list[tuple[str, str]] = [
    (r"^\s*server\s*\{", ""),
    (r"(?<!\})(\s*\}\s*\n?\s*(?!location|if|server|http|events)\w)", ""),
]

_NGINX_DIRECTIVE_RE = re.compile(
    r"^\s*(?P<directive>[a-z_]+)\s+(?P<value>.+?);",
    re.MULTILINE,
)
_NGINX_BLOCK_RE = re.compile(
    r"^\s*(?P<block>server|http|events|location|upstream|if|map|types|geo|split_clients|limit_req_zone|limit_conn_zone)\s+(?P<arg>[^{]*)\{",
    re.MULTILINE,
)
_NGINX_LISTEN_RE = re.compile(r"listen\s+(\d+)\s*(ssl)?", re.IGNORECASE)


def validate_nginx_config(config_text: str) -> list[str]:
    errors: list[str] = []
    brace_depth = 0
    for lineno, line in enumerate(config_text.split("\n"), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        brace_delta = stripped.count("{") - stripped.count("}")
        brace_depth += brace_delta
        if brace_depth < 0:
            errors.append(f"Line {lineno}: unexpected closing brace")
            brace_depth = 0
        if (";" not in stripped and not stripped.endswith("{")
                and not stripped.endswith("}")
                and not stripped.startswith(("server", "location", "upstream", "if", "http", "events"))):
            continue
    if brace_depth > 0:
        errors.append(f"Unclosed block: {brace_depth} unmatched {{")
    return errors


def parse_nginx_config(config_text: str) -> dict[str, Any]:
    result: dict[str, Any] = {"servers": [], "upstreams": [], "maps": [], "http": {}}
    lines = config_text.split("\n")
    in_server = False
    in_upstream = False
    in_location = False
    current_server: dict[str, Any] = {}
    current_upstream: dict[str, Any] = {}
    current_location: dict[str, Any] = {}

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        m = _NGINX_BLOCK_RE.match(stripped)
        if m:
            block = m.group("block")
            arg = m.group("arg").strip()
            if block == "server":
                in_server = True
                current_server = {"locations": [], "server_name": "", "listen": []}
            elif block == "upstream":
                in_upstream = True
                current_upstream = {"name": arg, "servers": []}
            elif block == "location" and in_server:
                in_location = True
                current_location = {"name": arg, "directives": {}}
            continue

        if stripped == "}":
            if in_location:
                current_server.setdefault("locations", []).append(current_location)
                in_location = False
                current_location = {}
            elif in_server:
                result.setdefault("servers", []).append(current_server)
                in_server = False
                current_server = {}
            elif in_upstream:
                result.setdefault("upstreams", []).append(current_upstream)
                in_upstream = False
                current_upstream = {}
            continue

        if in_location:
            dm = _NGINX_DIRECTIVE_RE.match(stripped)
            if dm:
                current_location.setdefault("directives", {})[
                    dm.group("directive")
                ] = dm.group("value")
            continue
        if in_server:
            dm = _NGINX_DIRECTIVE_RE.match(stripped)
            if dm:
                directive = dm.group("directive")
                value = dm.group("value")
                if directive == "listen":
                    current_server.setdefault("listen", []).append(value)
                elif directive == "server_name":
                    current_server["server_name"] = value
                else:
                    current_server[directive] = value
            continue
        if in_upstream:
            dm = _NGINX_DIRECTIVE_RE.match(stripped)
            if dm:
                current_upstream.setdefault("servers", []).append(
                    {"address": dm.group("value")}
                )
            continue

    return result


def generate_vhost(
    server_name: str,
    port: int = 80,
    root: str = "/var/www/html",
    proxy_pass: str | None = None,
    ssl: bool = False,
) -> str:
    listen_line = f"    listen {port}"
    if ssl:
        listen_line += " ssl"
    directives = [
        "server {",
        listen_line + ";",
        f"    server_name {server_name};",
        f"    root {root};",
        "    index index.html index.htm;",
    ]
    if proxy_pass:
        directives.append("    location / {")
        directives.append(f"        proxy_pass {proxy_pass};")
        directives.append("        proxy_set_header Host $host;")
        directives.append("        proxy_set_header X-Real-IP $remote_addr;")
        directives.append("    }")
    directives.append("}")
    return "\n".join(directives)


# ---------------------------------------------------------------------------
# HTTP Server — Apache
# ---------------------------------------------------------------------------


def validate_apache_config(config_text: str) -> list[str]:
    errors: list[str] = []
    open_tags: list[str] = []
    tag_re = re.compile(r"<(/)?(\w+)", re.IGNORECASE)
    for lineno, line in enumerate(config_text.split("\n"), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for m in tag_re.finditer(stripped):
            closing = m.group(1)
            tag = m.group(2).lower()
            if closing:
                if not open_tags:
                    errors.append(f"Line {lineno}: unexpected closing tag </{tag}>")
                elif open_tags[-1] != tag:
                    errors.append(
                        f"Line {lineno}: mismatched closing tag </{tag}> "
                        f"(expected </{open_tags[-1]}>"
                    )
                else:
                    open_tags.pop()
            else:
                open_tags.append(tag)
    for tag in reversed(open_tags):
        errors.append(f"Unclosed tag: <{tag}>")
    return errors


# ---------------------------------------------------------------------------
# SSL / TLS
# ---------------------------------------------------------------------------

MOZILLA_MODERN_CIPHERS = (
    "ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:"
    "ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:"
    "ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305"
)

MOZILLA_INTERMEDIATE_CIPHERS = (
    "ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:"
    "ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:"
    "ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:"
    "DHE-RSA-AES128-GCM-SHA256:DHE-RSA-AES256-GCM-SHA384:"
    "ECDHE-ECDSA-AES128-SHA256:ECDHE-RSA-AES128-SHA256:"
    "ECDHE-ECDSA-AES256-SHA384:ECDHE-RSA-AES256-SHA384:"
    "DHE-RSA-AES128-SHA256:DHE-RSA-AES256-SHA256"
)

_SSL_PROFILES: dict[str, dict[str, str]] = {
    "modern": {
        "protocols": "TLSv1.3",
        "ciphers": MOZILLA_MODERN_CIPHERS,
    },
    "intermediate": {
        "protocols": "TLSv1.2 TLSv1.3",
        "ciphers": MOZILLA_INTERMEDIATE_CIPHERS,
    },
    "old": {
        "protocols": "TLSv1 TLSv1.1 TLSv1.2 TLSv1.3",
        "ciphers": MOZILLA_INTERMEDIATE_CIPHERS + ":"
        "ECDHE-ECDSA-AES128-SHA:ECDHE-RSA-AES128-SHA:"
        "ECDHE-ECDSA-AES256-SHA:ECDHE-RSA-AES256-SHA:"
        "DHE-RSA-AES128-SHA:DHE-RSA-AES256-SHA:"
        "AES128-GCM-SHA256:AES256-GCM-SHA384:"
        "AES128-SHA256:AES256-SHA256:AES128-SHA:AES256-SHA",
    },
}


def generate_ssl_config(profile: str = "intermediate") -> str:
    p = _SSL_PROFILES.get(profile, _SSL_PROFILES["intermediate"])
    return textwrap.dedent(f"""\
    ssl_protocols {p["protocols"]};
    ssl_ciphers {p["ciphers"]};
    ssl_prefer_server_ciphers on;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;
    ssl_session_tickets off;
    ssl_stapling on;
    ssl_stapling_verify on;
    add_header Strict-Transport-Security "max-age=63072000" always;
    """)


def validate_certificate(cert_path: str) -> dict[str, Any]:
    try:
        with open(cert_path, "rb") as fh:
            cert_data = fh.read()
    except OSError:
        return {"error": f"Cannot read certificate: {cert_path}"}

    try:
        from cryptography import x509
        cert = x509.load_pem_x509_certificate(cert_data)
        sans = []
        try:
            san_ext = cert.extensions.get_extension_for_class(
                x509.SubjectAlternativeName
            )
            sans = san_ext.value.get_values_for_type(x509.DNSName)
        except x509.ExtensionNotFound:
            pass

        issuer = ", ".join(
            f"{attr.oid._name}={attr.value if isinstance(attr.value, str) else attr.value.decode()}"
            for attr in cert.issuer
        )

        return {
            "subject": ", ".join(
                f"{attr.oid._name}={attr.value if isinstance(attr.value, str) else attr.value.decode()}"
                for attr in cert.subject
            ),
            "issuer": issuer,
            "not_before": cert.not_valid_before_utc.isoformat(),
            "not_after": cert.not_valid_after_utc.isoformat(),
            "serial_number": hex(cert.serial_number),
            "sans": sans,
            "expires_days": (
                cert.not_valid_after_utc - datetime.now(UTC)
            ).days,
        }
    except ImportError:
        pass

    decode_cert = cast(
        "Callable[[str], dict[str, Any] | None] | None",
        getattr(getattr(ssl, "_ssl", None), "_test_decode_cert", None),
    )
    if decode_cert is not None:
        try:
            cert_obj = decode_cert(cert_path)
            if cert_obj is None:
                cert_dict: dict[str, Any] = {}
                for line in cert_data.decode("utf-8", errors="replace").split("\n"):
                    line = line.strip()
                    if line.startswith("Not Before:"):
                        cert_dict["not_before"] = line.split(":", 1)[1].strip()
                    elif line.startswith("Not After :"):
                        cert_dict["not_after"] = line.split(":", 1)[1].strip()
                    elif line.startswith("Subject:"):
                        cert_dict["subject"] = line.split(":", 1)[1].strip()
                    elif line.startswith("Issuer:"):
                        cert_dict["issuer"] = line.split(":", 1)[1].strip()
                return cert_dict or {"error": "Could not parse certificate"}
            return cert_obj
        except Exception:
            pass

    try:
        cert_text = (
            ssl.DER_cert_to_PEM_cert(cert_data)
            if cert_data[0] != 0x2D
            else cert_data.decode("utf-8", errors="replace")
        )
    except Exception:
        cert_text = cert_data.decode("utf-8", errors="replace")

    cert_obj_parsed: dict[str, Any] = {}
    if decode_cert is not None:
        for part in (decode_cert(cert_path) or {}):
            cert_obj_parsed[str(part)] = part
    if not cert_obj_parsed:
        subject_match = re.search(r"Subject:\s*(.+)", str(cert_text))
        issuer_match = re.search(r"Issuer:\s*(.+)", str(cert_text))
        nb_match = re.search(r"Not Before:\s*(.+)", str(cert_text))
        na_match = re.search(r"Not After\s*:\s*(.+)", str(cert_text))
        sans_obj: list[str] = []
        for m in re.finditer(r"DNS:([^,\s]+)", str(cert_text)):
            sans_obj.append(m.group(1))

        cert_obj_parsed["subject"] = subject_match.group(1) if subject_match else ""
        cert_obj_parsed["issuer"] = issuer_match.group(1) if issuer_match else ""
        cert_obj_parsed["not_before"] = nb_match.group(1) if nb_match else ""
        cert_obj_parsed["not_after"] = na_match.group(1) if na_match else ""
        cert_obj_parsed["sans"] = sans_obj

        na_str = cert_obj_parsed.get("not_after", "")
        try:
            na_dt = datetime.strptime(na_str.strip(), "%b %d %H:%M:%S %Y %Z")
            cert_obj_parsed["expires_days"] = (na_dt - datetime.now()).days
        except (ValueError, KeyError):
            cert_obj_parsed["expires_days"] = -1

    return cert_obj_parsed


# RFC 7919 Appendix A.1 ffdhe2048, serialized as PKCS#3 for nginx/OpenSSL
# compatibility. Generating a new 2048-bit safe prime can take minutes under
# load. See https://datatracker.ietf.org/doc/html/rfc7919#appendix-A.1
_RFC7919_FFDHE2048_PEM = b"""-----BEGIN DH PARAMETERS-----
MIIBCAKCAQEA//////////+t+FRYortKmq/cViAnPTzx2LnFg84tNpWp4TZBFGQz
+8yTnc4kmz75fS/jY2MMddj2gbICrsRhetPfHtXV/WVhJDP1H18GbtCFY2VVPe0a
87VXE15/V8k1mE8McODmi3fipona8+/och3xWKE2rec1MKzKT0g6eXq8CrGCsyT7
YdEIqUuyyOP7uWrat2DX9GgdT0Kj3jlN9K5W7edjcrsZCwenyO4KbXCeAvzhzffi
7MA0BM0oNC9hkXL+nOmFg/+OTxIy7vKBg8P+OxtMb61zO7X8vC7CIAXFjvGDfRaD
ssbzSibBsu/6iGtCOGEoXJf//////////wIBAg==
-----END DH PARAMETERS-----
"""


def generate_dhparam(bits: int = 2048) -> None:
    if bits == 2048:
        pem = _RFC7919_FFDHE2048_PEM
    else:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import dh

        parameters = dh.generate_parameters(generator=2, key_size=bits)
        pem = parameters.parameter_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.ParameterFormat.PKCS3,
        )
    with open("dhparam.pem", "wb") as fh:
        fh.write(pem)


# ---------------------------------------------------------------------------
# CGI / WSGI
# ---------------------------------------------------------------------------


def generate_wsgi_nginx_config(
    app_module: str, socket_path: str, processes: int = 4
) -> str:
    return textwrap.dedent(f"""\
    upstream {app_module}_app {{
        server unix:{socket_path} fail_timeout=0;
    }}

    server {{
        listen 80;
        server_name _;

        location / {{
            uwsgi_pass {app_module}_app;
            include uwsgi_params;
            uwsgi_param UWSGI_MODULE {app_module};
            uwsgi_param UWSGI_CALLABLE application;
            uwsgi_processes {processes};
        }}
    }}
    """)


def generate_uwsgi_ini(
    app_module: str,
    socket_path: str,
    processes: int = 4,
    threads: int = 2,
) -> str:
    return textwrap.dedent(f"""\
    [uwsgi]
    module = {app_module}
    callable = application
    socket = {socket_path}
    chmod-socket = 666
    processes = {processes}
    threads = {threads}
    master = true
    vacuum = true
    die-on-term = true
    """)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_FORMAT_COMBINED = (
    '$remote_addr - $remote_user [$time_local] '
    '"$request" $status $body_bytes_sent '
    '"$http_referer" "$http_user_agent"'
)

LOG_FORMAT_JSON = 'escape=json \'{"time":"$time_iso8601","remote":"$remote_addr",'
'"method":"$request_method","uri":"$request_uri","status":$status,'
'"bytes":$body_bytes_sent,"referer":"$http_referer","ua":"$http_user_agent",'
'"host":"$host","request_time":$request_time}\''

_COMBINED_RE = re.compile(
    r'^(?P<remote_addr>\S+)\s+\S+\s+(?P<remote_user>\S+)\s+'
    r'\[(?P<time_local>[^\]]+)\]\s+'
    r'"(?P<request>[^"]*)"\s+'
    r'(?P<status>\d+)\s+'
    r'(?P<body_bytes_sent>\d+)\s+'
    r'"(?P<http_referer>[^"]*)"\s+'
    r'"(?P<http_user_agent>[^"]*)"$'
)

_JSON_LOG_RE = re.compile(r'^\s*\{.*\}\s*$')


def parse_access_log_line(
    line: str, format_str: str = LOG_FORMAT_COMBINED
) -> dict[str, Any]:
    m = _COMBINED_RE.match(line)
    if not m:
        if _JSON_LOG_RE.match(line):
            try:
                return cast(dict[str, Any], json.loads(line))
            except json.JSONDecodeError:
                pass
        return {"raw": line}
    return m.groupdict()


def generate_logrotate_config(
    log_path: str, rotate: int = 7, compress: bool = True
) -> str:
    compress_line = "    compress" if compress else "    nocompress"
    return textwrap.dedent(f"""\
    {log_path} {{
        daily
        rotate {rotate}
        missingok
        notifempty
        delaycompress
    {compress_line}
        sharedscripts
        postrotate
            /usr/bin/killall -HUP syslog-ng 2>/dev/null || true
        endscript
    }}
    """)


# ---------------------------------------------------------------------------
# Reverse Proxy
# ---------------------------------------------------------------------------

_UPSTREAM_METHODS: dict[str, str] = {
    "round_robin": "",
    "least_conn": "least_conn",
    "ip_hash": "ip_hash",
    "random": "random",
}


def generate_nginx_upstream(
    name: str, servers: list[str], method: str = "round_robin"
) -> str:
    method_directive = _UPSTREAM_METHODS.get(method, "")
    lines = [f"upstream {name} {{"]
    if method_directive:
        lines.append(f"    {method_directive};")
    for srv in servers:
        lines.append(f"    server {srv};")
    lines.append("}")
    return "\n".join(lines)


def generate_haproxy_config(
    frontends: list[dict[str, Any]], backends: list[dict[str, Any]]
) -> str:
    lines: list[str] = ["global", "    daemon", ""]
    lines.append("defaults")
    lines.append("    mode http")
    lines.append("    timeout connect 5000ms")
    lines.append("    timeout client 50000ms")
    lines.append("    timeout server 50000ms")
    lines.append("")

    for fe in frontends:
        lines.append(f"frontend {fe['name']}")
        if "bind" in fe:
            lines.append(f"    bind {fe['bind']}")
        if "default_backend" in fe:
            lines.append(f"    default_backend {fe['default_backend']}")
        lines.append("")

    for be in backends:
        lines.append(f"backend {be['name']}")
        if "mode" in be:
            lines.append(f"    mode {be['mode']}")
        if "balance" in be:
            lines.append(f"    balance {be['balance']}")
        for srv in be.get("servers", []):
            lines.append(f"    server {srv['name']} {srv['address']} check")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Forward Proxy
# ---------------------------------------------------------------------------

SQUID_SAFE_PORTS: list[int] = [80, 21, 443, 70, 210, 1025, 65535, 280, 488, 591, 777]
SQUID_SSL_PORTS: list[int] = [443]


def generate_squid_acl(name: str, acl_type: str, values: list[str]) -> str:
    return f"acl {name} {acl_type} {' '.join(values)}"


def generate_squid_config(
    port: int = 3128, allowed_networks: list[str] | None = None
) -> str:
    networks = allowed_networks or ["192.168.0.0/16", "10.0.0.0/8"]
    acl_lines = [f"acl localnet src {n}" for n in networks]
    safe_ports = [f"acl Safe_ports port {p}" for p in SQUID_SAFE_PORTS]
    ssl_ports = [f"acl SSL_ports port {p}" for p in SQUID_SSL_PORTS]

    return (
        f"http_port {port}\n"
        + "\n".join(acl_lines)
        + "\n"
        + "\n".join(safe_ports)
        + "\n"
        + "\n".join(ssl_ports)
        + "\n"
        + textwrap.dedent("""\
        acl CONNECT method CONNECT

        http_access deny !Safe_ports
        http_access deny CONNECT !SSL_ports
        http_access allow localhost manager
        http_access deny manager
        http_access allow localnet
        http_access allow localhost
        http_access deny all

        cache_dir ufs /var/spool/squid 100 16 256
        coredump_dir /var/spool/squid
        refresh_pattern ^ftp:           1440    20%     10080
        refresh_pattern ^gopher:        1440    0%      1440
        refresh_pattern -i (/cgi-bin/|\\?) 0     0%      0
        refresh_pattern .               0       20%     4320
        """)
    )


_DEFAULT_DIRECT_DOMAINS = [
    "*.local",
    "*.internal",
    "*.intranet",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
]


def generate_pac_file(
    proxy_host: str,
    proxy_port: int,
    direct_domains: list[str] | None = None,
) -> str:
    domains = direct_domains or _DEFAULT_DIRECT_DOMAINS
    conditions: list[str] = []
    for domain in domains:
        if "/" in domain:
            try:
                net = ipaddress.ip_network(domain, strict=False)
                if net.version == 4:
                    conditions.append(
                        f"        isInNet(host, \"{net.network_address}\", "
                        f"\"{net.netmask}\")"
                    )
                else:
                    conditions.append(f'        shExpMatch(host, "{domain}")')
            except ValueError:
                conditions.append(f'        dnsDomainIs(host, "{domain}")')
        elif domain.startswith("*."):
            suffix = domain[2:]
            conditions.append(f'        dnsDomainIs(host, "{suffix}")')
        else:
            conditions.append(f'        shExpMatch(host, "{domain}")')

    join_all = " ||\n".join(conditions) if conditions else "        false"

    return textwrap.dedent(f"""\
    function FindProxyForURL(url, host) {{
        if (
    {join_all}
        ) {{
            return "DIRECT";
        }}
        return "PROXY {proxy_host}:{proxy_port}";
    }}
    """)


# ---------------------------------------------------------------------------
# Load Balancer
# ---------------------------------------------------------------------------

_LB_METHODS: dict[str, str] = {
    "least_conn": "least_conn",
    "round_robin": "",
    "ip_hash": "ip_hash",
    "random": "random two",
    "least_time": "least_time header",
}


def generate_upstream_config(
    servers: list[dict[str, Any]], method: str = "least_conn"
) -> str:
    method_directive = _LB_METHODS.get(method, "least_conn")
    lines = ["upstream backend {"]
    if method_directive:
        lines.append(f"    {method_directive};")
    for srv in servers:
        weight = srv.get("weight", 1)
        max_fails = srv.get("max_fails", 3)
        fail_timeout = srv.get("fail_timeout", "30s")
        backup = "backup" if srv.get("backup") else ""
        lines.append(
            f"    server {srv['address']} weight={weight} "
            f"max_fails={max_fails} fail_timeout={fail_timeout} {backup};".strip()
        )
    lines.append("}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------

SECURITY_HEADERS: dict[str, str] = {
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains; preload",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-XSS-Protection": "0",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Cross-Origin-Embedder-Policy": "require-corp",
}

CSP_DIRECTIVES: dict[str, str] = {
    "default-src": "'self'",
    "script-src": "'self'",
    "style-src": "'self' 'unsafe-inline'",
    "img-src": "'self' data:",
    "font-src": "'self'",
    "connect-src": "'self'",
    "media-src": "'self'",
    "object-src": "'none'",
    "frame-src": "'self'",
    "base-uri": "'self'",
    "form-action": "'self'",
    "frame-ancestors": "'self'",
    "upgrade-insecure-requests": "",
}


def generate_security_headers(include_csp: bool = True) -> str:
    lines: list[str] = []
    for header, value in SECURITY_HEADERS.items():
        lines.append(f"add_header {header} \"{value}\" always;")
    if include_csp:
        lines.append("")
        lines.append(generate_csp())
    return "\n".join(lines)


def generate_csp(directives: dict[str, str] | None = None) -> str:
    d = directives or CSP_DIRECTIVES
    parts: list[str] = []
    for directive, value in d.items():
        if value:
            parts.append(f"{directive} {value}")
        else:
            parts.append(directive)
    csp_value = "; ".join(parts) + ";"
    return f'add_header Content-Security-Policy "{csp_value}" always;'


def audit_nginx_config(config_text: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    lowered = config_text.lower()

    checks: list[tuple[str, str, str, str, str]] = [
        (
            "high",
            "tls-min-version",
            "TLS version below 1.2",
            r"ssl_protocols\s+.*(TLSv1\b|TLSv1\.1)",
            "Set ssl_protocols TLSv1.2 TLSv1.3;",
        ),
        (
            "medium",
            "hsts-missing",
            "HSTS header not configured",
            r"add_header.*strict-transport-security",
            "Generate HSTS header via generate_security_headers()",
        ),
        (
            "medium",
            "server-tokens",
            "server_tokens not set to off",
            r"server_tokens\s+off",
            "Set server_tokens off;",
        ),
        (
            "low",
            "gzip-off",
            "gzip compression not enabled",
            r"gzip\s+on",
            "Enable gzip on; for performance",
        ),
        (
            "high",
            "ssl-off",
            "SSL/TLS not configured on listener",
            r"listen\s+\d+\s+ssl",
            "Add ssl to listen directive; use generate_ssl_config()",
        ),
        (
            "low",
            "clickjacking",
            "X-Frame-Options header missing",
            r"add_header.*x-frame-options",
            "Add X-Frame-Options DENY via generate_security_headers()",
        ),
        (
            "low",
            "content-sniffing",
            "X-Content-Type-Options missing",
            r"add_header.*x-content-type-options",
            "Add X-Content-Type-Options nosniff via generate_security_headers()",
        ),
    ]

    for severity, check_id, finding_text, find_re, remediation in checks:
        matches = bool(re.search(find_re, lowered))
        if (check_id.startswith("tls-") and matches) or (not check_id.startswith("tls-") and not matches):
            findings.append({
                "severity": severity,
                "check": check_id,
                "finding": finding_text,
                "remediation": remediation,
            })

    return findings


def audit_hardening(server_type: str, config: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if server_type == "nginx":
        findings.extend(audit_nginx_config(config))

    generic_checks: list[tuple[str, str, str]] = [
        (
            "medium",
            "directory-listing",
            "Auto-index / directory listing may be enabled",
        ),
        (
            "high",
            "plaintext-http",
            "Server may be listening on plain HTTP (port 80)",
        ),
        (
            "medium",
            "default-credentials",
            "Check for default credentials in config",
        ),
    ]
    for severity, check_id, desc in generic_checks:
        findings.append({
            "severity": severity,
            "check": f"{server_type}-{check_id}",
            "finding": desc,
            "remediation": f"Review {server_type} configuration for {check_id}",
        })
    return findings


def remediate_finding(finding: dict[str, Any]) -> str:
    return str(finding.get("remediation", ""))
