"""Hosted branch regressions for the web utility modules."""

from __future__ import annotations

import _ssl
import builtins
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from cryptography import x509

from general_ludd import web_server_utils, web_utils


def test_validate_certificate_reports_full_x509_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cert_path = tmp_path / "server.pem"
    cert_path.write_bytes(b"test certificate")
    now = datetime.now(UTC)
    issuer = [
        SimpleNamespace(oid=SimpleNamespace(_name="organizationName"), value=b"Issuer")
    ]
    subject = [
        SimpleNamespace(oid=SimpleNamespace(_name="commonName"), value="example.com")
    ]
    san_value = SimpleNamespace(
        get_values_for_type=lambda _name_type: ["example.com", "www.example.com"]
    )
    extensions = SimpleNamespace(
        get_extension_for_class=lambda _extension_type: SimpleNamespace(
            value=san_value
        )
    )
    certificate = SimpleNamespace(
        issuer=issuer,
        subject=subject,
        extensions=extensions,
        not_valid_before_utc=now - timedelta(days=1),
        not_valid_after_utc=now + timedelta(days=30),
        serial_number=42,
    )
    monkeypatch.setattr(
        x509, "load_pem_x509_certificate", lambda _data: certificate
    )

    result = web_server_utils.validate_certificate(str(cert_path))

    assert result["subject"] == "commonName=example.com"
    assert result["issuer"] == "organizationName=Issuer"
    assert result["serial_number"] == "0x2a"
    assert result["sans"] == ["example.com", "www.example.com"]
    assert result["expires_days"] in {29, 30}


def test_validate_certificate_missing_file_fails_closed(tmp_path: Path) -> None:
    result = web_server_utils.validate_certificate(str(tmp_path / "missing.pem"))

    assert result == {"error": f"Cannot read certificate: {tmp_path / 'missing.pem'}"}


def test_validate_certificate_stdlib_fallback_parses_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cert_path = tmp_path / "fallback.pem"
    cert_path.write_text(
        "Subject: CN=example.com\n"
        "Issuer: CN=issuer\n"
        "Not Before: Jan 01 00:00:00 2026 GMT\n"
        "Not After : Jan 01 00:00:00 2027 GMT\n"
    )
    original_import = builtins.__import__

    def _import_without_cryptography(
        name: str,
        globals: dict[str, Any] | None = None,
        locals: dict[str, Any] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> Any:
        if name == "cryptography":
            raise ImportError("optional parser unavailable")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _import_without_cryptography)
    monkeypatch.setattr(_ssl, "_test_decode_cert", lambda _path: None)

    result = web_server_utils.validate_certificate(str(cert_path))

    assert result["subject"] == "CN=example.com"
    assert result["issuer"] == "CN=issuer"
    assert result["not_before"].startswith("Jan 01")
    assert result["not_after"].startswith("Jan 01")


def test_web_server_optional_and_failure_branches() -> None:
    assert web_server_utils.validate_nginx_config("server {\n") == [
        "Unclosed block: 1 unmatched {"
    ]
    assert "unexpected closing tag" in web_server_utils.validate_apache_config(
        "</Directory>"
    )[0]
    parsed = web_server_utils.parse_nginx_config(
        "location /orphan {\n}\n"
        "upstream backend {\ninvalid\n}\n"
        "server {\ninvalid\nlocation / {\ninvalid\n}\n}\n"
    )
    assert parsed["upstreams"][0]["servers"] == []
    assert parsed["servers"][0]["locations"][0]["directives"] == {}
    assert web_server_utils.parse_access_log_line("{bad}") == {"raw": "{bad}"}

    haproxy = web_server_utils.generate_haproxy_config(
        [{"name": "front"}], [{"name": "back", "servers": []}]
    )
    assert "frontend front" in haproxy
    assert "backend back" in haproxy
    assert "bind" not in haproxy

    pac = web_server_utils.generate_pac_file(
        "proxy.local",
        8080,
        direct_domains=["2001:db8::/32", "bad/not-network", "exact.example"],
    )
    assert 'shExpMatch(host, "2001:db8::/32")' in pac
    assert 'dnsDomainIs(host, "bad/not-network")' in pac
    assert 'shExpMatch(host, "exact.example")' in pac
    assert "least_conn" not in web_server_utils.generate_upstream_config(
        [{"address": "127.0.0.1:8000"}], method="round_robin"
    )


def test_graphql_parser_covers_schema_categories_and_extensions() -> None:
    schema = """
    schema { query: Query }
    scalar DateTime
    directive @auth on FIELD_DEFINITION
    type Query {
      viewer(id: ID!): User
    }
    input UserFilter {
      active: Boolean
    }
    enum Role {
      ADMIN
    }
    union SearchResult {
      User
    }
    extend type Mutation {
      updateUser(id: ID!): User
    }
    """

    result = web_utils.parse_graphql_schema(schema)

    assert result["queries"][0]["fields"] == [
        {"name": "viewer", "args": "id: ID!", "type": "User"}
    ]
    assert result["inputs"][0]["name"] == "UserFilter"
    assert result["enums"][0]["name"] == "Role"
    assert {entry["name"] for entry in result["types"]} == {"SearchResult"}
    assert result["mutations"][0]["name"] == "Mutation"


def test_accessibility_and_token_edge_branches() -> None:
    issues = web_utils.check_aria_attributes(
        '<button><span></span></button><img src="x">'
        '<div role="slider" aria-valuenow="5"></div>'
        '<div role="presentation"></div>'
    )
    tokens: dict[str, Any] = {
        "space": {"small": "0.5rem"},
        "duration": "0.5rem",
        "enabled": True,
        "count": 2,
    }

    encoded = web_utils.tokens_to_json(tokens)

    assert "button missing accessible label" in issues
    assert "img missing alt attribute" in issues
    assert any("aria-valuemin" in issue for issue in issues)
    assert '"$type": "dimension"' in encoded
    assert '"$type": "number"' in encoded


def test_web_utility_failure_and_generation_branches(tmp_path: Path) -> None:
    html_issues = web_utils.validate_html(
        "</main><br></br><section><span></section>"
    )
    assert any("Unexpected closing" in issue for issue in html_issues)
    assert any("Mismatched" in issue for issue in html_issues)
    assert web_utils.parse_css("a { invalid; : empty; color: red; }") == {
        "a": {"color": "red"}
    }
    assert web_utils.verify_source_map(str(tmp_path / "missing.map")) is False
    assert "revalidate = 60" in web_utils.generate_nextjs_page(
        "/status", page_type="isr"
    )
    assert web_utils.detect_error_patterns(
        "typeof value === 'undefined'; for (var key in obj) {}\n"
        "promise.then(function () {}); value = null;"
    ) == [
        "typeof checks for 'undefined': consider using optional chaining",
        "for-in loop without hasOwnProperty check",
        "Plain .then() chains: consider async/await",
        "null assignments without nullish coalescing fallback",
    ]
    assert web_utils.validate_heading_hierarchy("<h2>A</h2><h1>B</h1><h1>C</h1><h4>D</h4>") == [
        "Document starts with <h2>, expected <h1>",
        "Multiple h1 headings found",
        "Heading skip: h1 -> h4 (missing h2)",
    ]
    assert web_utils.calculate_readability("1234 !!!")["words"] == 0
    assert web_utils.generate_spacing_tokens(steps=-1) == {}
    assert web_utils.tokens_to_css({}) == "  /* no tokens */"
    assert web_utils.extract_z_index_contexts(
        ".modal { z-index: auto; position: fixed; }"
    ) == [{"element": ".modal", "z_index": "auto", "position": "fixed"}]


@pytest.mark.parametrize(
    ("css", "expected"),
    [
        (".button.is-primary {}", "bulma"),
        (".foundation .large-6 {}", "foundation"),
        (".uk-hidden {}", "uikit"),
        (".MuiButton {}", "mui"),
        (".chakra {}", "chakra"),
        (".ant-btn {}", "antd"),
    ],
)
def test_detects_each_supported_css_framework(css: str, expected: str) -> None:
    assert web_utils.detect_css_framework(css) == expected


@pytest.mark.parametrize("page_type", ["html4", "xhtml"])
def test_legacy_boilerplates_are_explicit(page_type: str) -> None:
    rendered = web_utils.generate_boilerplate(page_type)

    assert "DOCTYPE" in rendered
    assert "<title>Page</title>" in rendered
