#!/usr/bin/env python3
"""logging_middleware — log format validator, rotation config generator."""
import argparse
import os
import re
import sys
from pathlib import Path


KNOWN_NGINX_VARS = frozenset({
    "$remote_addr", "$time_local", "$request", "$status",
    "$body_bytes_sent", "$http_referer", "$http_user_agent",
    "$request_time", "$upstream_response_time", "$ssl_cipher",
    "$ssl_protocol", "$upstream_addr", "$upstream_status",
    "$http_host", "$request_uri", "$server_name", "$server_port",
    "$scheme", "$http_x_forwarded_for", "$http_x_forwarded_proto",
    "$remote_user", "$bytes_sent", "$gzip_ratio",
    "$request_length", "$connection", "$connection_requests",
    "$pid", "$msec", "$pipe", "$request_completion",
    "$upstream_cache_status", "$sent_http_content_type",
    "$sent_http_content_length", "$http_cookie", "$http_accept_language",
})

KNOWN_APACHE_DIRECTIVES = frozenset({
    "%h", "%l", "%u", "%t", "%r", "%s", "%b", "%D", "%T",
    "%{Referer}i", "%{User-agent}i", "%{Host}i", "%{X-Forwarded-For}i",
    "%X", "%I", "%O", "%v", "%V", "%p", "%P", "%H", "%m", "%U", "%q",
    "%{VARNAME}e", "%{VARNAME}n", "%{VARNAME}o", "%{VARNAME}i",
})


def validate_nginx_format(format_str):
    variables = re.findall(r"\$\w+", format_str)
    result = {"valid": True, "variables": variables, "unknown": []}
    for var in variables:
        if var not in KNOWN_NGINX_VARS:
            result["unknown"].append(var)
            result["valid"] = False
    return result


def generate_nginx_log_format(format_name, format_str):
    return (
        f'log_format {format_name} \'{format_str}\';\n'
        f'access_log /var/log/nginx/access.log {format_name};\n'
    )


def generate_logrotate_config(log_dir, rotation, retention):
    schedule = "daily" if rotation == "daily" else "weekly"
    base = Path(log_dir).name
    return f"""\
{log_dir}/*.log {{
    {schedule}
    missingok
    rotate {retention}
    compress
    delaycompress
    notifempty
    create 0640 www-data adm
    sharedscripts
    postrotate
        [ -f /var/run/nginx.pid ] && kill -USR1 `cat /var/run/nginx.pid`
    endscript
}}
"""


def log_stats(log_dir):
    result = {}
    for name in ["access.log", "error.log"]:
        fp = Path(log_dir) / name
        if fp.exists():
            stat = fp.stat()
            result[name] = {
                "size_bytes": stat.st_size,
                "size_mb": round(stat.st_size / (1024 * 1024), 2),
                "modified": stat.st_mtime,
            }
    return result


def main():
    parser = argparse.ArgumentParser(description="logging_middleware tool")
    sub = parser.add_subparsers(dest="command")

    val_p = sub.add_parser("validate")
    val_p.add_argument("--format", required=True)

    fmt_p = sub.add_parser("generate-format")
    fmt_p.add_argument("--format-name", default="main")
    fmt_p.add_argument("--format", required=True)
    fmt_p.add_argument("--output", help="file path to write config snippet")

    rot_p = sub.add_parser("generate-rotation")
    rot_p.add_argument("--log-dir", required=True)
    rot_p.add_argument("--rotation", choices=["daily", "size"], default="daily")
    rot_p.add_argument("--retention", type=int, default=30)
    rot_p.add_argument("--output", help="file path to write rotation config")

    stats_p = sub.add_parser("stats")
    stats_p.add_argument("--log-dir", required=True)

    args = parser.parse_args()

    if args.command == "validate":
        result = validate_nginx_format(args.format)
        print(f"valid={result['valid']}")
        print(f"variables={result['variables']}")
        if result["unknown"]:
            print(f"unknown={result['unknown']}")
        sys.exit(0 if result["valid"] else 1)

    elif args.command == "generate-format":
        config = generate_nginx_log_format(args.format_name, args.format)
        if args.output:
            Path(args.output).write_text(config)
            print(f"Written to {args.output}")
        else:
            print(config)

    elif args.command == "generate-rotation":
        config = generate_logrotate_config(
            args.log_dir, args.rotation, args.retention
        )
        if args.output:
            Path(args.output).write_text(config)
            print(f"Written to {args.output}")
        else:
            print(config)

    elif args.command == "stats":
        import json
        result = log_stats(args.log_dir)
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
