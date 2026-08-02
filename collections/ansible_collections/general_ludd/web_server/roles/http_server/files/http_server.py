#!/usr/bin/env python3
"""http_server — nginx/apache config parser, validator, virtual host generator."""
import argparse
import json
import re


def parse_nginx_config(config_text):
    directives = []
    for line in config_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = re.match(r"^(\S+)\s+(.+);$", stripped)
        if match:
            directives.append({"directive": match.group(1), "value": match.group(2)})
    return directives


def parse_apache_config(config_text):
    directives = []
    for line in config_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = re.match(r"^(\S+)\s+(.+)$", stripped)
        if match:
            directives.append({"directive": match.group(1), "value": match.group(2)})
    return directives


def validate_nginx_config(config_text):
    required = ["server", "listen", "server_name"]
    found = {d["directive"] for d in parse_nginx_config(config_text)}
    missing = [r for r in required if r not in found]
    return {"valid": len(missing) == 0, "missing": missing}


def validate_apache_config(config_text):
    required = ["VirtualHost", "ServerName"]
    has_vhost = "VirtualHost" in config_text
    has_server_name = "ServerName" in config_text
    missing = []
    if not has_vhost:
        missing.append("VirtualHost")
    if not has_server_name:
        missing.append("ServerName")
    return {"valid": len(missing) == 0, "missing": missing}


def generate_nginx_vhost(server_name, port, document_root, proxy_pass=None,
                         extra=None):
    lines = [
        "server {",
        f"    listen {port};",
        f"    server_name {server_name};",
        f"    root {document_root};",
        "",
        "    access_log /var/log/nginx/{server_name}_access.log;",
        "    error_log /var/log/nginx/{server_name}_error.log;",
        "",
    ]
    if proxy_pass:
        lines.append("    location / {")
        lines.append(f"        proxy_pass {proxy_pass};")
        lines.append("        proxy_set_header Host $host;")
        lines.append("        proxy_set_header X-Real-IP $remote_addr;")
        lines.append("    }")
    else:
        lines.append("    location / {")
        lines.append("        try_files $uri $uri/ =404;")
        lines.append("    }")

    if extra:
        for k, v in extra.items():
            if isinstance(v, list):
                for item in v:
                    lines.append(f"    {k} {item};")
            else:
                lines.append(f"    {k} {v};")

    lines.append("}")
    return "\n".join(lines).format(server_name=server_name)


def generate_apache_vhost(server_name, port, document_root, proxy_pass=None,
                          extra=None):
    lines = [
        f"<VirtualHost *:{port}>",
        f"    ServerName {server_name}",
        f"    DocumentRoot {document_root}",
        "",
    ]
    if proxy_pass:
        lines.extend([
            f"    ProxyPass / {proxy_pass}",
            f"    ProxyPassReverse / {proxy_pass}",
        ])

    lines.extend([
        '    <Directory {document_root}>',
        '        Options -Indexes +FollowSymLinks',
        '        AllowOverride All',
        '        Require all granted',
        '    </Directory>',
        "",
    ])

    if extra:
        for k, v in extra.items():
            if isinstance(v, list):
                for item in v:
                    lines.append(f"    {k} {item}")
            else:
                lines.append(f"    {k} {v}")

    lines.append("</VirtualHost>")
    return "\n".join(lines).format(document_root=document_root)


def main():
    parser = argparse.ArgumentParser(description="http_server config tool")
    sub = parser.add_subparsers(dest="command")

    parse_p = sub.add_parser("parse")
    parse_p.add_argument("--server-type", choices=["nginx", "apache"], required=True)
    parse_p.add_argument("--config", required=True, help="config text or file path")

    validate_p = sub.add_parser("validate")
    validate_p.add_argument("--server-type", choices=["nginx", "apache"], required=True)
    validate_p.add_argument("--config", required=True)

    gen_p = sub.add_parser("generate")
    gen_p.add_argument("--server-type", choices=["nginx", "apache"], required=True)
    gen_p.add_argument("--server-name", required=True)
    gen_p.add_argument("--port", type=int, default=80)
    gen_p.add_argument("--document-root", default="/var/www/html")
    gen_p.add_argument("--proxy-pass", default="")
    gen_p.add_argument("--extra", default="{}")

    args = parser.parse_args()

    if args.command == "parse":
        config = args.config
        try:
            with open(config) as f:
                config = f.read()
        except FileNotFoundError:
            pass
        if args.server_type == "nginx":
            result = parse_nginx_config(config)
        else:
            result = parse_apache_config(config)
        print(json.dumps(result, indent=2))

    elif args.command == "validate":
        config = args.config
        try:
            with open(config) as f:
                config = f.read()
        except FileNotFoundError:
            pass
        if args.server_type == "nginx":
            result = validate_nginx_config(config)
        else:
            result = validate_apache_config(config)
        print(json.dumps(result, indent=2))

    elif args.command == "generate":
        extra = json.loads(args.extra) if args.extra else {}
        proxy = args.proxy_pass if args.proxy_pass else None
        if args.server_type == "nginx":
            print(generate_nginx_vhost(
                args.server_name, args.port, args.document_root, proxy, extra
            ))
        else:
            print(generate_apache_vhost(
                args.server_name, args.port, args.document_root, proxy, extra
            ))


if __name__ == "__main__":
    main()
