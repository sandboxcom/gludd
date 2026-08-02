"""Security hardening helpers — header validator, CSP builder, finding-to-fix mapper."""

from __future__ import annotations

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-XSS-Protection": "1; mode=block",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": (
        "camera=(), microphone=(), geolocation=(), payment=()"
    ),
    "Strict-Transport-Security": (
        "max-age=31536000; includeSubDomains; preload"
    ),
}


def generate_nginx_security_headers(
    csp_policy: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> str:
    headers = dict(SECURITY_HEADERS)
    if csp_policy:
        headers["Content-Security-Policy"] = csp_policy
    if extra_headers:
        headers.update(extra_headers)

    lines: list[str] = []
    for header, value in headers.items():
        lines.append(f"    add_header {header} \"{value}\" always;")
    return "\n".join(lines)


def generate_apache_security_headers(
    csp_policy: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> str:
    headers = dict(SECURITY_HEADERS)
    if csp_policy:
        headers["Content-Security-Policy"] = csp_policy
    if extra_headers:
        headers.update(extra_headers)

    lines: list[str] = ['<IfModule mod_headers.c>']
    for header, value in headers.items():
        lines.append(f'    Header always set {header} "{value}"')
    lines.append('</IfModule>')
    return "\n".join(lines)


def build_csp_policy(
    default_src: str = "'self'",
    script_src: str | None = None,
    style_src: str | None = None,
    img_src: str | None = None,
    font_src: str | None = None,
    connect_src: str | None = None,
    frame_src: str | None = None,
    object_src: str = "'none'",
    base_uri: str = "'self'",
    form_action: str = "'self'",
) -> str:
    directives: list[str] = []
    directives.append(f"default-src {default_src}")
    if script_src:
        directives.append(f"script-src {script_src}")
    if style_src:
        directives.append(f"style-src {style_src}")
    if img_src:
        directives.append(f"img-src {img_src}")
    if font_src:
        directives.append(f"font-src {font_src}")
    if connect_src:
        directives.append(f"connect-src {connect_src}")
    if frame_src:
        directives.append(f"frame-src {frame_src}")
    directives.append(f"object-src {object_src}")
    directives.append(f"base-uri {base_uri}")
    directives.append(f"form-action {form_action}")
    return "; ".join(directives)


def validate_security_headers(
    response_headers: dict[str, str],
    required_headers: set[str] | None = None,
) -> dict[str, object]:
    required = required_headers or {
        "X-Content-Type-Options",
        "X-Frame-Options",
        "Content-Security-Policy",
        "Strict-Transport-Security",
        "Referrer-Policy",
    }
    present: set[str] = set()
    missing: set[str] = set()
    misconfigured: dict[str, str] = {}

    for header in required:
        found = False
        for resp_name, resp_value in response_headers.items():
            if resp_name.lower() == header.lower():
                found = True
                present.add(header)
                if header == "X-Frame-Options":
                    if resp_value.upper() not in ("DENY", "SAMEORIGIN"):
                        misconfigured[header] = f"Unexpected value: {resp_value}"
                if header == "X-Content-Type-Options":
                    if resp_value.lower() != "nosniff":
                        misconfigured[header] = f"Expected 'nosniff', got '{resp_value}'"
                break
        if not found:
            missing.add(header)

    score = max(0, len(required) - len(missing) - len(misconfigured))
    grade = (
        "A" if score >= len(required) * 0.9
        else "B" if score >= len(required) * 0.7
        else "C" if score >= len(required) * 0.5
        else "D" if score >= len(required) * 0.3
        else "F"
    )

    return {
        "score": score,
        "max_score": len(required),
        "grade": grade,
        "present": sorted(present),
        "missing": sorted(missing),
        "misconfigured": misconfigured,
        "pass": len(missing) == 0 and len(misconfigured) == 0,
    }


FINDING_FIX_MAP: dict[str, str] = {
    "X-Content-Type-Options header missing": (
        "add_header X-Content-Type-Options 'nosniff' always;"
    ),
    "X-Frame-Options header missing": (
        "add_header X-Frame-Options 'DENY' always;"
    ),
    "CSP header missing": (
        "add_header Content-Security-Policy \"default-src 'self'\" always;"
    ),
    "HSTS header missing": (
        "add_header Strict-Transport-Security "
        "'max-age=31536000; includeSubDomains' always;"
    ),
    "server_tokens visible": (
        "server_tokens off;"
    ),
    "directory listing enabled": (
        "autoindex off;"
    ),
    "TLSv1.0 enabled": (
        "ssl_protocols TLSv1.2 TLSv1.3;"
    ),
    "weak cipher suites": (
        "ssl_ciphers EECDH+AESGCM:EDH+AESGCM;"
    ),
    "TRACE method enabled": (
        "if ($request_method = TRACE) { return 405; }"
    ),
    "verbose error pages": (
        "error_page 500 502 503 504 /50x.html;"
    ),
}


def map_finding_to_fix(findings: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for finding in findings:
        if finding in FINDING_FIX_MAP:
            result[finding] = FINDING_FIX_MAP[finding]
        else:
            result[finding] = "manual review required"
    return result


def generate_nginx_request_limits(
    max_body_size_mb: int = 10,
    large_header_buffers: str = "4 8k",
    request_timeout: int = 60,
) -> str:
    return (
        f"client_max_body_size {max_body_size_mb}m;\n"
        f"client_body_timeout {request_timeout}s;\n"
        f"client_header_timeout {request_timeout}s;\n"
        f"large_client_header_buffers {large_header_buffers};\n"
        f"limit_req_zone $binary_remote_addr zone=ratelimit:10m rate=10r/s;\n"
        f"limit_conn_zone $binary_remote_addr zone=connlimit:10m;"
    )


def generate_tls_hardening(
    protocols: list[str] | None = None,
    ciphers: str | None = None,
    dhparam_path: str = "/etc/nginx/dhparam.pem",
) -> str:
    proto_list = protocols or ["TLSv1.2", "TLSv1.3"]
    cipher_str = ciphers or "EECDH+AESGCM:EDH+AESGCM:AES256+EECDH:AES256+EDH"
    return (
        f"ssl_protocols {' '.join(proto_list)};\n"
        f"ssl_ciphers {cipher_str};\n"
        f"ssl_prefer_server_ciphers on;\n"
        f"ssl_dhparam {dhparam_path};\n"
        f"ssl_ecdh_curve secp384r1;\n"
        f"ssl_session_cache shared:SSL:10m;\n"
        f"ssl_session_tickets off;\n"
        f"ssl_stapling on;\n"
        f"ssl_stapling_verify on;"
    )
