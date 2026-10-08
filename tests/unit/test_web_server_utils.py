"""Tests for src/general_ludd/web_server_utils.py — web server operations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

import general_ludd.web_server_utils as web_server_utils
from general_ludd.web_server_utils import (
    CSP_DIRECTIVES,
    LOG_FORMAT_COMBINED,
    LOG_FORMAT_JSON,
    MOZILLA_INTERMEDIATE_CIPHERS,
    MOZILLA_MODERN_CIPHERS,
    SECURITY_HEADERS,
    SQUID_SAFE_PORTS,
    SQUID_SSL_PORTS,
    audit_hardening,
    audit_nginx_config,
    generate_csp,
    generate_dhparam,
    generate_haproxy_config,
    generate_logrotate_config,
    generate_nginx_upstream,
    generate_pac_file,
    generate_security_headers,
    generate_squid_acl,
    generate_squid_config,
    generate_ssl_config,
    generate_upstream_config,
    generate_uwsgi_ini,
    generate_vhost,
    generate_wsgi_nginx_config,
    parse_access_log_line,
    parse_nginx_config,
    remediate_finding,
    validate_apache_config,
    validate_certificate,
    validate_nginx_config,
)

_RFC7919_FFDHE2048_PEM = b"""-----BEGIN DH PARAMETERS-----
MIIBCAKCAQEA//////////+t+FRYortKmq/cViAnPTzx2LnFg84tNpWp4TZBFGQz
+8yTnc4kmz75fS/jY2MMddj2gbICrsRhetPfHtXV/WVhJDP1H18GbtCFY2VVPe0a
87VXE15/V8k1mE8McODmi3fipona8+/och3xWKE2rec1MKzKT0g6eXq8CrGCsyT7
YdEIqUuyyOP7uWrat2DX9GgdT0Kj3jlN9K5W7edjcrsZCwenyO4KbXCeAvzhzffi
7MA0BM0oNC9hkXL+nOmFg/+OTxIy7vKBg8P+OxtMb61zO7X8vC7CIAXFjvGDfRaD
ssbzSibBsu/6iGtCOGEoXJf//////////wIBAg==
-----END DH PARAMETERS-----
"""

# ---------------------------------------------------------------------------
# nginx: validation & parsing
# ---------------------------------------------------------------------------


class TestValidateNginxConfig:
    def test_valid_empty_config(self):
        assert validate_nginx_config("") == []

    def test_missing_semicolons_ignored(self):
        cfg = "server {\n    listen 80\n}"
        errors = validate_nginx_config(cfg)
        assert isinstance(errors, list)

    def test_extra_closing_brace(self):
        cfg = "server {\n    listen 80;\n}\n}"
        errors = validate_nginx_config(cfg)
        assert any("unexpected" in e.lower() or "closing" in e.lower() for e in errors)

    def test_unclosed_block(self):
        assert validate_nginx_config("server {") == [
            "Unclosed block: 1 unmatched {"
        ]


class TestParseNginxConfig:
    def test_single_server(self):
        cfg = "server {\n    listen 80;\n    server_name example.com;\n    root /var/www;\n}"
        result = parse_nginx_config(cfg)
        assert len(result["servers"]) == 1
        assert result["servers"][0]["server_name"] == "example.com"

    def test_server_with_locations(self):
        cfg = (
            "server {\n"
            "    listen 80;\n"
            "    server_name example.com;\n"
            "    location / {\n"
            "        proxy_pass http://backend;\n"
            "    }\n"
            "    location /static {\n"
            "        root /var/www;\n"
            "    }\n"
            "}"
        )
        result = parse_nginx_config(cfg)
        assert len(result["servers"]) == 1
        assert len(result["servers"][0]["locations"]) == 2

    def test_upstream_block(self):
        cfg = "upstream backend {\n    server 10.0.0.1:8080;\n    server 10.0.0.2:8080;\n}\n"
        result = parse_nginx_config(cfg)
        assert len(result["upstreams"]) == 1
        assert result["upstreams"][0]["name"] == "backend"


class TestGenerateVhost:
    def test_basic_http_vhost(self):
        cfg = generate_vhost("example.com", port=80)
        assert "listen 80" in cfg
        assert "server_name example.com" in cfg
        assert "root /var/www/html" in cfg

    def test_proxy_pass_vhost(self):
        cfg = generate_vhost("api.example.com", port=80, proxy_pass="http://localhost:3000")
        assert "proxy_pass http://localhost:3000" in cfg
        assert "proxy_set_header Host $host" in cfg

    def test_ssl_vhost(self):
        cfg = generate_vhost("secure.example.com", port=443, ssl=True)
        assert "listen 443 ssl" in cfg


# ---------------------------------------------------------------------------
# Apache
# ---------------------------------------------------------------------------


class TestValidateApacheConfig:
    def test_valid_config(self):
        cfg = "<VirtualHost *:80>\n    ServerName example.com\n</VirtualHost>"
        errors = validate_apache_config(cfg)
        assert errors == []

    def test_unclosed_tag(self):
        cfg = "<VirtualHost *:80>\n    ServerName example.com\n"
        errors = validate_apache_config(cfg)
        assert any("Unclosed" in e for e in errors)

    def test_mismatched_tag(self):
        cfg = "<VirtualHost *:80>\n</Directory>\n</VirtualHost>"
        errors = validate_apache_config(cfg)
        assert any("mismatched" in e.lower() for e in errors)

    def test_unexpected_closing_tag(self):
        assert validate_apache_config("</Directory>") == [
            "Line 1: unexpected closing tag </directory>"
        ]


# ---------------------------------------------------------------------------
# SSL / TLS
# ---------------------------------------------------------------------------


class TestSslConstants:
    def test_modern_ciphers_nonempty(self):
        assert len(MOZILLA_MODERN_CIPHERS) > 0
        assert "CHACHA20" in MOZILLA_MODERN_CIPHERS

    def test_intermediate_ciphers_nonempty(self):
        assert len(MOZILLA_INTERMEDIATE_CIPHERS) > 0
        assert "DHE-RSA" in MOZILLA_INTERMEDIATE_CIPHERS


class TestGenerateSslConfig:
    def test_intermediate_profile(self):
        cfg = generate_ssl_config("intermediate")
        assert "TLSv1.2" in cfg
        assert "ssl_ciphers" in cfg
        assert "ssl_prefer_server_ciphers" in cfg

    def test_modern_profile(self):
        cfg = generate_ssl_config("modern")
        assert "TLSv1.3" in cfg
        assert "ssl_session_tickets off" in cfg

    def test_unknown_profile_falls_back(self):
        cfg = generate_ssl_config("nonexistent")
        assert "ssl_protocols" in cfg


class TestValidateCertificate:
    def test_reads_complete_x509_metadata(self, tmp_path) -> None:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        now = datetime.now(UTC)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "example.test")])
        certificate = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(42)
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=30))
            .add_extension(
                x509.SubjectAlternativeName([x509.DNSName("www.example.test")]),
                critical=False,
            )
            .sign(key, hashes.SHA256())
        )
        cert_path = tmp_path / "certificate.pem"
        cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))

        metadata = validate_certificate(str(cert_path))

        assert metadata["subject"] == "commonName=example.test"
        assert metadata["issuer"] == "commonName=example.test"
        assert metadata["serial_number"] == "0x2a"
        assert metadata["sans"] == ["www.example.test"]
        assert 29 <= metadata["expires_days"] <= 30

    def test_reports_unreadable_certificate(self, tmp_path) -> None:
        missing_path = tmp_path / "missing.pem"

        assert validate_certificate(str(missing_path)) == {
            "error": f"Cannot read certificate: {missing_path}"
        }

    def test_parses_text_metadata_when_optional_parser_returns_none(
        self, tmp_path, monkeypatch
    ) -> None:
        cert_path = tmp_path / "certificate.txt"
        cert_path.write_text(
            "Subject: CN=fallback.example\n"
            "Issuer: CN=fallback-ca.example\n"
            "Not Before: Jan 01 00:00:00 2026 GMT\n"
            "Not After : Jan 01 00:00:00 2027 GMT\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(
            web_server_utils,
            "_certificate_metadata_from_cryptography",
            lambda _cert_data: None,
        )
        monkeypatch.setattr(
            web_server_utils.ssl._ssl,
            "_test_decode_cert",
            lambda _cert_path: None,
        )

        metadata = validate_certificate(str(cert_path))

        assert metadata == {
            "subject": "CN=fallback.example",
            "issuer": "CN=fallback-ca.example",
            "not_before": "Jan 01 00:00:00 2026 GMT",
            "not_after": "Jan 01 00:00:00 2027 GMT",
        }


# ---------------------------------------------------------------------------
# CGI / WSGI
# ---------------------------------------------------------------------------


class TestGenerateWsgiNginxConfig:
    def test_basic_config(self):
        cfg = generate_wsgi_nginx_config("myapp", "/tmp/myapp.sock", processes=2)
        assert "upstream myapp_app" in cfg
        assert "server unix:/tmp/myapp.sock" in cfg
        assert "uwsgi_pass" in cfg


class TestGenerateUwsgiIni:
    def test_default_values(self):
        ini = generate_uwsgi_ini("myapp", "/tmp/myapp.sock")
        assert "module = myapp" in ini
        assert "processes = 4" in ini
        assert "threads = 2" in ini
        assert "master = true" in ini


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


class TestLogFormatConstants:
    def test_combined_nonempty(self):
        assert "$remote_addr" in LOG_FORMAT_COMBINED
        assert "$status" in LOG_FORMAT_COMBINED

    def test_json_nonempty(self):
        assert "$time_iso8601" in LOG_FORMAT_JSON
        assert "json" in LOG_FORMAT_JSON.lower()


class TestParseAccessLogLine:
    def test_combined_format(self):
        line = (
            '192.168.1.1 - frank [10/Oct/2024:13:55:36 -0700] '
            '"GET /index.html HTTP/1.1" 200 2326 '
            '"http://example.com/" "Mozilla/5.0"'
        )
        result = parse_access_log_line(line)
        assert result["remote_addr"] == "192.168.1.1"
        assert result["status"] == "200"
        assert result["body_bytes_sent"] == "2326"

    def test_json_format(self):
        entry = '{"time":"2024-10-10T13:55:36Z","remote":"10.0.0.1","status":200}'
        result = parse_access_log_line(entry)
        assert result["status"] == 200

    def test_unparseable_line(self):
        result = parse_access_log_line("not a log line")
        assert "raw" in result

    def test_invalid_json_shaped_line_is_returned_raw(self):
        line = "{broken-json}"

        assert parse_access_log_line(line) == {"raw": line}


class TestGenerateLogrotateConfig:
    def test_basic_config(self):
        cfg = generate_logrotate_config("/var/log/nginx/access.log", rotate=7)
        assert "/var/log/nginx/access.log" in cfg
        assert "rotate 7" in cfg
        assert "compress" in cfg
        assert "daily" in cfg


# ---------------------------------------------------------------------------
# Reverse Proxy
# ---------------------------------------------------------------------------


class TestGenerateNginxUpstream:
    def test_round_robin(self):
        cfg = generate_nginx_upstream("backend", ["10.0.0.1:8080", "10.0.0.2:8080"])
        assert "upstream backend" in cfg
        assert "server 10.0.0.1:8080" in cfg
        assert "server 10.0.0.2:8080" in cfg

    def test_least_conn(self):
        cfg = generate_nginx_upstream("api", ["10.0.0.1:3000"], method="least_conn")
        assert "least_conn" in cfg

    def test_ip_hash(self):
        cfg = generate_nginx_upstream("sticky", ["10.0.0.1"], method="ip_hash")
        assert "ip_hash" in cfg


class TestGenerateHaproxyConfig:
    def test_basic_frontend_backend(self):
        fe = [{"name": "web", "bind": "*:80", "default_backend": "app"}]
        be = [{"name": "app", "mode": "http", "balance": "roundrobin",
               "servers": [{"name": "s1", "address": "10.0.0.1:8080"}]}]
        cfg = generate_haproxy_config(fe, be)
        assert "frontend web" in cfg
        assert "backend app" in cfg
        assert "server s1 10.0.0.1:8080 check" in cfg


# ---------------------------------------------------------------------------
# Forward Proxy
# ---------------------------------------------------------------------------


class TestSquidConstants:
    def test_safe_ports_contains_standard(self):
        assert 80 in SQUID_SAFE_PORTS
        assert 443 in SQUID_SAFE_PORTS

    def test_ssl_ports_contains_443(self):
        assert 443 in SQUID_SSL_PORTS


class TestGenerateSquidAcl:
    def test_src_acl(self):
        acl = generate_squid_acl("localnet", "src", ["192.168.0.0/16"])
        assert acl == "acl localnet src 192.168.0.0/16"


class TestGenerateSquidConfig:
    def test_default_config(self):
        cfg = generate_squid_config()
        assert "http_port 3128" in cfg
        assert "acl localnet src 192.168.0.0/16" in cfg
        assert "http_access deny all" in cfg


class TestGeneratePacFile:
    def test_default_direct_domains(self):
        pac = generate_pac_file("proxy.local", 8080)
        assert "PROXY proxy.local:8080" in pac
        assert "DIRECT" in pac

    def test_custom_direct_domains(self):
        pac = generate_pac_file("proxy.local", 8080, direct_domains=["*.example.com"])
        assert "dnsDomainIs" in pac

    def test_ipv6_invalid_network_and_literal_domain_rules(self):
        pac = generate_pac_file(
            "proxy.local",
            8080,
            direct_domains=["2001:db8::/32", "invalid/network", "host.example"],
        )

        assert 'shExpMatch(host, "2001:db8::/32")' in pac
        assert 'dnsDomainIs(host, "invalid/network")' in pac
        assert 'shExpMatch(host, "host.example")' in pac


# ---------------------------------------------------------------------------
# Load Balancer
# ---------------------------------------------------------------------------


class TestGenerateUpstreamConfig:
    def test_least_conn_default(self):
        servers = [{"address": "10.0.0.1:8080"}, {"address": "10.0.0.2:8080"}]
        cfg = generate_upstream_config(servers)
        assert "least_conn" in cfg
        assert "server 10.0.0.1:8080" in cfg
        assert "weight=1" in cfg

    def test_with_backup_server(self):
        servers = [
            {"address": "10.0.0.1:8080"},
            {"address": "10.0.0.2:8080", "backup": True},
        ]
        cfg = generate_upstream_config(servers)
        assert "backup" in cfg

    def test_ip_hash_method(self):
        servers = [{"address": "10.0.0.1:8080"}]
        cfg = generate_upstream_config(servers, method="ip_hash")
        assert "ip_hash" in cfg
        assert "least_conn" not in cfg


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------


class TestSecurityConstants:
    def test_headers_nonempty(self):
        assert "Strict-Transport-Security" in SECURITY_HEADERS
        assert "X-Content-Type-Options" in SECURITY_HEADERS

    def test_csp_defaults(self):
        assert "default-src" in CSP_DIRECTIVES
        assert "'self'" in CSP_DIRECTIVES["default-src"]


class TestGenerateSecurityHeaders:
    def test_includes_hsts(self):
        hdrs = generate_security_headers(include_csp=False)
        assert "Strict-Transport-Security" in hdrs
        assert "X-Frame-Options" in hdrs

    def test_includes_csp(self):
        hdrs = generate_security_headers(include_csp=True)
        assert "Content-Security-Policy" in hdrs


class TestGenerateCsp:
    def test_default_directives(self):
        csp = generate_csp()
        assert "default-src 'self'" in csp
        assert "object-src 'none'" in csp

    def test_custom_directives(self):
        csp = generate_csp({"default-src": "'self'", "script-src": "'self' https://cdn.example.com"})
        assert "script-src 'self' https://cdn.example.com" in csp


class TestAuditNginxConfig:
    def test_finds_missing_hsts(self):
        findings = audit_nginx_config("server {\n    listen 80;\n}")
        hsts_findings = [f for f in findings if f["check"] == "hsts-missing"]
        assert len(hsts_findings) == 1

    def test_secure_config_no_findings(self):
        cfg = (
            "server {\n"
            "    listen 443 ssl;\n"
            "    server_tokens off;\n"
            "    gzip on;\n"
            "    ssl_protocols TLSv1.2 TLSv1.3;\n"
            "    add_header Strict-Transport-Security 'max-age=63072000' always;\n"
            "    add_header X-Frame-Options 'DENY' always;\n"
            "    add_header X-Content-Type-Options 'nosniff' always;\n"
            "}"
        )
        findings = audit_nginx_config(cfg)
        missing = [f for f in findings
                   if f["check"] in ("hsts-missing", "clickjacking", "content-sniffing")]
        assert len(missing) == 0


class TestAuditHardening:
    def test_nginx_returns_findings(self):
        findings = audit_hardening("nginx", "server {\n    listen 80;\n}")
        assert len(findings) > 0
        assert any(f["server_type"] == "nginx" if "server_type" in f
                   else "nginx" in f.get("check", "") for f in findings)

    def test_unknown_server_type(self):
        findings = audit_hardening("apache", "<VirtualHost *:80>\n</VirtualHost>")
        assert isinstance(findings, list)
        assert any("apache" in f.get("check", "") for f in findings)


class TestRemediateFinding:
    def test_extracts_remediation(self):
        finding = {
            "severity": "high",
            "check": "test-check",
            "finding": "Some issue",
            "remediation": "Do X to fix it",
        }
        assert remediate_finding(finding) == "Do X to fix it"

    def test_missing_remediation_returns_empty(self):
        assert remediate_finding({}) == ""


# ---------------------------------------------------------------------------
# generate_dhparam — dependency surface
# ---------------------------------------------------------------------------
class TestGenerateDhparam:
    def test_default_group_writes_exact_rfc7919_fixture(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        generate_dhparam()
        pem_path = tmp_path / "dhparam.pem"

        assert pem_path.is_file()
        assert pem_path.read_bytes() == _RFC7919_FFDHE2048_PEM

    def test_explicit_2048_writes_exact_rfc7919_fixture(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        generate_dhparam(bits=2048)
        pem_file = tmp_path / "dhparam.pem"

        assert pem_file.is_file()
        assert pem_file.read_bytes() == _RFC7919_FFDHE2048_PEM

    @pytest.mark.parametrize("bits", [1024, 3072, 4096])
    def test_unsupported_sizes_fail_without_writing(
        self, tmp_path, monkeypatch, bits: int
    ) -> None:
        monkeypatch.chdir(tmp_path)

        with pytest.raises(ValueError, match="only RFC 7919 ffdhe2048"):
            generate_dhparam(bits=bits)

        assert not (tmp_path / "dhparam.pem").exists()
