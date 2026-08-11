"""Deep tests for the vulnerability scanner module."""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# VulnFinding dataclass
# ---------------------------------------------------------------------------


def test_vuln_finding_required_fields() -> None:
    from general_ludd.security.vuln_scanner import VulnFinding

    f = VulnFinding(
        pattern_id="HARDCODED_SECRET",
        category="secret",
        severity="critical",
        line=12,
        snippet="password = 'hunter2'",
    )
    assert f.pattern_id == "HARDCODED_SECRET"
    assert f.category == "secret"
    assert f.severity == "critical"
    assert f.line == 12
    assert f.snippet == "password = 'hunter2'"


def test_vuln_finding_defaults() -> None:
    from general_ludd.security.vuln_scanner import VulnFinding

    f = VulnFinding(
        pattern_id="XSS_REFLECTED",
        category="injection",
        severity="high",
        line=42,
    )
    assert f.snippet == ""
    assert f.file_path == ""
    assert not hasattr(f, "invalid_field")


def test_vuln_finding_with_optional_fields() -> None:
    from general_ludd.security.vuln_scanner import VulnFinding

    f = VulnFinding(
        pattern_id="UNSAFE_DESERIAL",
        category="deserialization",
        severity="high",
        line=7,
        snippet="pickle.loads(data)",
        file_path="src/app.py",
    )
    assert f.file_path == "src/app.py"
    assert f.line == 7
    assert f.snippet == "pickle.loads(data)"


def test_vuln_finding_equality() -> None:
    from general_ludd.security.vuln_scanner import VulnFinding

    a = VulnFinding("A", "cat", "low", 1, snippet="x")
    b = VulnFinding("A", "cat", "low", 1, snippet="x")
    c = VulnFinding("B", "cat", "low", 1, snippet="x")
    assert a == b
    assert a != c


def test_vuln_finding_repr() -> None:
    from general_ludd.security.vuln_scanner import VulnFinding

    f = VulnFinding(pattern_id="X", category="c", severity="low", line=1)
    r = repr(f)
    assert "VulnFinding" in r
    assert "X" in r
    assert "low" in r


# ---------------------------------------------------------------------------
# Severity rank
# ---------------------------------------------------------------------------


def test_severity_rank_values() -> None:
    from general_ludd.security.vuln_scanner import SEVERITY_RANK

    assert SEVERITY_RANK["critical"] == 4
    assert SEVERITY_RANK["high"] == 3
    assert SEVERITY_RANK["medium"] == 2
    assert SEVERITY_RANK["low"] == 1
    assert SEVERITY_RANK["info"] == 0


def test_severity_rank_ordering() -> None:
    from general_ludd.security.vuln_scanner import SEVERITY_RANK

    assert SEVERITY_RANK["critical"] > SEVERITY_RANK["high"]
    assert SEVERITY_RANK["high"] > SEVERITY_RANK["medium"]
    assert SEVERITY_RANK["medium"] > SEVERITY_RANK["low"]
    assert SEVERITY_RANK["low"] > SEVERITY_RANK["info"]


# ---------------------------------------------------------------------------
# Vulnerability patterns
# ---------------------------------------------------------------------------


def test_patterns_have_required_fields() -> None:
    from general_ludd.security.vuln_scanner import PATTERNS

    for pat in PATTERNS:
        assert "id" in pat, f"pattern missing 'id': {pat}"
        assert "category" in pat, f"pattern missing 'category': {pat}"
        assert "severity" in pat, f"pattern missing 'severity': {pat}"
        assert "regex" in pat, f"pattern missing 'regex': {pat}"
        assert "description" in pat, f"pattern missing 'description': {pat}"
        assert pat["severity"] in ("critical", "high", "medium", "low", "info")


def test_pattern_ids_are_unique() -> None:
    from general_ludd.security.vuln_scanner import PATTERNS

    ids = [p["id"] for p in PATTERNS]
    assert len(ids) == len(set(ids))


def test_patterns_have_at_least_minimum_coverage() -> None:
    from general_ludd.security.vuln_scanner import PATTERNS

    assert len(PATTERNS) >= 8


# ---------------------------------------------------------------------------
# scan_content — basic
# ---------------------------------------------------------------------------


def test_scan_content_empty_string() -> None:
    from general_ludd.security.vuln_scanner import scan_content

    result = scan_content("")
    assert isinstance(result, list)
    assert len(result) == 0


def test_scan_content_no_vulns() -> None:
    from general_ludd.security.vuln_scanner import scan_content

    result = scan_content("print('hello world')\nx = 1 + 2\n")
    assert len(result) == 0


def test_scan_content_detects_hardcoded_password() -> None:
    from general_ludd.security.vuln_scanner import scan_content

    code = "password = 'supersecret123'\nusername = 'admin'\n"
    result = scan_content(code)
    assert len(result) >= 1
    assert any("HARDCODED" in f.pattern_id for f in result)


def test_scan_content_detects_pickle_loads() -> None:
    from general_ludd.security.vuln_scanner import scan_content

    code = "data = pickle.loads(user_input)\n"
    result = scan_content(code)
    assert any("PICKLE" in f.pattern_id for f in result)


def test_scan_content_detects_eval() -> None:
    from general_ludd.security.vuln_scanner import scan_content

    result = scan_content("result = eval(user_input)\n")
    assert any("EVAL" in f.pattern_id for f in result)


def test_scan_content_detects_os_system() -> None:
    from general_ludd.security.vuln_scanner import scan_content

    result = scan_content("os.system('rm -rf /' + path)\n")
    assert any("OS_SYSTEM" in f.pattern_id for f in result)


def test_scan_content_detects_sql_concatenation() -> None:
    from general_ludd.security.vuln_scanner import scan_content

    code = "cursor.execute('SELECT * FROM users WHERE id = ' + user_id)\n"
    result = scan_content(code)
    assert any("SQL" in f.pattern_id.upper() for f in result)


def test_scan_content_detects_subprocess_shell_true() -> None:
    from general_ludd.security.vuln_scanner import scan_content

    result = scan_content("subprocess.run(cmd, shell=True)\n")
    assert any("SUBPROCESS" in f.pattern_id for f in result)


def test_scan_content_detects_http_not_https_sensitive() -> None:
    from general_ludd.security.vuln_scanner import scan_content

    result = scan_content("url = 'http://api.example.com/login'\n")
    assert any("HTTP" in f.pattern_id for f in result)


def test_scan_content_detects_yaml_load_untrusted() -> None:
    from general_ludd.security.vuln_scanner import scan_content

    result = scan_content("data = yaml.load(user_input)\n")
    assert any("YAML" in f.pattern_id for f in result)


def test_scan_content_returns_correct_line_numbers() -> None:
    from general_ludd.security.vuln_scanner import scan_content

    code = "line0\nline1\npassword = 'abc123'\nline3\nos.system('ls')\n"
    result = scan_content(code)
    lines_with_findings = {f.line for f in result}
    assert 3 in lines_with_findings  # 1-indexed: password on line 3
    assert 5 in lines_with_findings  # os.system on line 5


def test_scan_content_each_finding_has_category() -> None:
    from general_ludd.security.vuln_scanner import scan_content

    code = "password='x'\nresult = eval(input())\npickle.loads(data)\n"
    result = scan_content(code)
    valid_categories = {"secret", "injection", "deserialization", "crypto", "network", "file_access"}
    for f in result:
        assert f.category in valid_categories, f"unexpected category: {f.category}"
        assert f.severity in ("critical", "high", "medium", "low", "info")


# ---------------------------------------------------------------------------
# scan_content — severity threshold
# ---------------------------------------------------------------------------


def test_scan_content_severity_threshold_high() -> None:
    from general_ludd.security.vuln_scanner import scan_content

    code = (
        "password = 'secret123'\n"
        "API_KEY = 'sk-abcdef'\n"
        "result = eval(user_input)\n"
        "pickle.loads(data)\n"
        "os.system('rm -rf /')\n"
    )
    all_findings = scan_content(code, severity_threshold="low")
    high_only = scan_content(code, severity_threshold="high")
    assert len(high_only) <= len(all_findings)


def test_scan_content_severity_threshold_critical() -> None:
    from general_ludd.security.vuln_scanner import scan_content

    code = "password = 'secret123'\nAPI_KEY = 'sk-abcdef'\neval(user_input)\npickle.loads(data)\n"
    result = scan_content(code, severity_threshold="critical")
    for f in result:
        assert f.severity == "critical"


def test_scan_content_unknown_severity_threshold() -> None:
    from general_ludd.security.vuln_scanner import scan_content

    result = scan_content("password='x'", severity_threshold="nonexistent")
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# scan_content — file path passthrough
# ---------------------------------------------------------------------------


def test_scan_content_sets_file_path_on_findings() -> None:
    from general_ludd.security.vuln_scanner import scan_content

    result = scan_content("password = 'hunter2'", file_path="src/auth.py")
    for f in result:
        assert f.file_path == "src/auth.py"


def test_scan_content_empty_file_path_default() -> None:
    from general_ludd.security.vuln_scanner import scan_content

    result = scan_content("password = 'hunter2'")
    for f in result:
        assert f.file_path == ""


# ---------------------------------------------------------------------------
# scan_content — snippet truncation
# ---------------------------------------------------------------------------


def test_scan_content_snippet_not_entire_line() -> None:
    from general_ludd.security.vuln_scanner import scan_content

    code = "x = extremely_long_variable_name_here + password_assignment = 'secret123' + other_stuff"
    result = scan_content(code)
    for f in result:
        assert len(f.snippet) <= 200


# ---------------------------------------------------------------------------
# scan_file via scan_content (pure unit, no FS access)
# ---------------------------------------------------------------------------


def test_scan_content_multiline_multiple_patterns() -> None:
    from general_ludd.security.vuln_scanner import scan_content

    code = (
        "import os\n"
        "password = 'abc123'\n"
        "def do():\n"
        "    eval(user_input)\n"
        "    os.system('rm -rf /' + path)\n"
        "    pickle.loads(data)\n"
        "    cursor.execute('SELECT * FROM users WHERE id = ' + uid)\n"
    )
    result = scan_content(code)
    pattern_ids = {f.pattern_id for f in result}
    assert len(pattern_ids) >= 3


# ---------------------------------------------------------------------------
# scan_files (directory scan via helper)
# ---------------------------------------------------------------------------


def test_scan_files_integration_shape(tmp_path: pytest.TempPathFactory) -> None:
    from general_ludd.security.vuln_scanner import scan_files

    d = tmp_path / "scan_test"
    d.mkdir()
    (d / "a.py").write_text("password = 'secret123'\n")
    (d / "b.py").write_text("print('safe')\n")
    (d / "c.py").write_text("eval(user_input)\n")

    result = scan_files(str(d))
    assert isinstance(result, list)
    assert len(result) >= 2
    paths = {f.file_path for f in result}
    assert any("a.py" in p for p in paths)
    assert any("c.py" in p for p in paths)
    assert not any("b.py" in p for p in paths)


def test_scan_files_empty_directory(tmp_path: pytest.TempPathFactory) -> None:
    from general_ludd.security.vuln_scanner import scan_files

    d = tmp_path / "empty_scan"
    d.mkdir()
    result = scan_files(str(d))
    assert isinstance(result, list)
    assert len(result) == 0


def test_scan_files_nonexistent_directory() -> None:
    from general_ludd.security.vuln_scanner import scan_files

    result = scan_files("/nonexistent/path/for/testing")
    assert isinstance(result, list)
    assert len(result) == 0


def test_scan_files_respects_severity_threshold(tmp_path: pytest.TempPathFactory) -> None:
    from general_ludd.security.vuln_scanner import scan_files

    d = tmp_path / "thresh_test"
    d.mkdir()
    (d / "a.py").write_text("password = 'secret'\neval(user_input)\npickle.loads(x)\n")

    all_findings = scan_files(str(d), severity_threshold="low")
    high_only = scan_files(str(d), severity_threshold="high")
    assert len(high_only) <= len(all_findings)
    assert len(all_findings) >= 1


def test_scan_files_skips_binary_extensions(tmp_path: pytest.TempPathFactory) -> None:
    from general_ludd.security.vuln_scanner import scan_files

    d = tmp_path / "binary_test"
    d.mkdir()
    (d / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (d / "archive.zip").write_bytes(b"PK\x03\x04")

    result = scan_files(str(d))
    assert len(result) == 0


# ---------------------------------------------------------------------------
# scan_files — recursive
# ---------------------------------------------------------------------------


def test_scan_files_recursive(tmp_path: pytest.TempPathFactory) -> None:
    from general_ludd.security.vuln_scanner import scan_files

    root = tmp_path / "recursive_test"
    root.mkdir()
    sub = root / "subdir"
    sub.mkdir()
    (root / "top.py").write_text("password = 'abcd1234'\n")
    (sub / "nested.py").write_text("eval(user_input)\n")

    result = scan_files(str(root))
    paths = {f.file_path for f in result}
    assert any("top.py" in p for p in paths)
    assert any("nested.py" in p for p in paths)


# ---------------------------------------------------------------------------
# summary helpers
# ---------------------------------------------------------------------------


def test_findings_summary_empty() -> None:
    from general_ludd.security.vuln_scanner import findings_summary

    summary = findings_summary([])
    assert isinstance(summary, dict)
    assert summary["total"] == 0


def test_findings_summary_counts() -> None:
    from general_ludd.security.vuln_scanner import VulnFinding, findings_summary

    findings = [
        VulnFinding("A", "secret", "critical", 1),
        VulnFinding("B", "secret", "high", 2),
        VulnFinding("C", "injection", "critical", 3),
        VulnFinding("D", "injection", "high", 4),
        VulnFinding("E", "injection", "medium", 5),
    ]
    summary = findings_summary(findings)
    assert summary["total"] == 5
    assert summary["by_severity"]["critical"] == 2
    assert summary["by_severity"]["high"] == 2
    assert summary["by_severity"]["medium"] == 1
    assert summary["by_category"]["secret"] == 2
    assert summary["by_category"]["injection"] == 3


def test_findings_summary_all_keys_present() -> None:
    from general_ludd.security.vuln_scanner import VulnFinding, findings_summary

    findings = [VulnFinding("X", "cat", "low", 1)]
    summary = findings_summary(findings)
    required_keys = {"total", "by_severity", "by_category"}
    assert required_keys.issubset(summary.keys())


# ---------------------------------------------------------------------------
# scan_checks_passes (convenience)
# ---------------------------------------------------------------------------


def test_scan_checks_passes_clean() -> None:
    from general_ludd.security.vuln_scanner import scan_checks_passes

    assert scan_checks_passes("print('hello')\nx = 42\n") is True


def test_scan_checks_passes_detects_vuln() -> None:
    from general_ludd.security.vuln_scanner import scan_checks_passes

    assert scan_checks_passes("password = 'hunter2'\n") is False


def test_scan_checks_passes_with_threshold() -> None:
    from general_ludd.security.vuln_scanner import scan_checks_passes

    code = "print('safe')\n# only an info-level pattern\n"
    assert scan_checks_passes(code, severity_threshold="high") is True
    assert scan_checks_passes(code, severity_threshold="low") is True


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_scan_content_very_long_line() -> None:
    from general_ludd.security.vuln_scanner import scan_content

    long_line = "x" * 10000 + "password = 'secret'" + "x" * 10000
    result = scan_content(long_line)
    assert isinstance(result, list)


def test_scan_content_non_ascii_comment_with_pattern() -> None:
    from general_ludd.security.vuln_scanner import scan_content

    code = "# これはコメントです\nprint('safe')\n"
    result = scan_content(code)
    pattern_ids = {f.pattern_id for f in result}
    assert not any("EVAL" in pid for pid in pattern_ids)


def test_scan_content_unicode_identifiers() -> None:
    from general_ludd.security.vuln_scanner import scan_content

    code = "パスワード = 'secret123'\n"
    result = scan_content(code)
    assert isinstance(result, list)


def test_scan_content_multiple_matches_same_line() -> None:
    from general_ludd.security.vuln_scanner import scan_content

    code = "x = eval(input()); password = 'abcd1234'\n"
    result = scan_content(code)
    assert len(result) >= 2


def test_scan_files_respects_extension_filter(tmp_path: pytest.TempPathFactory) -> None:
    from general_ludd.security.vuln_scanner import scan_files

    d = tmp_path / "ext_test"
    d.mkdir()
    (d / "notes.txt").write_text("password = 'abcd1234'\n")
    (d / "script.py").write_text("password = 'abcd1234'\n")
    (d / "config.yml").write_text("password: 'abcd1234'\n")

    result = scan_files(str(d))
    assert any(".py" in f.file_path for f in result)
    assert any(".yml" in f.file_path for f in result)


def test_scan_files_large_volume_integration(tmp_path: pytest.TempPathFactory) -> None:
    from general_ludd.security.vuln_scanner import scan_files

    d = tmp_path / "large_scan"
    d.mkdir()
    for i in range(20):
        if i % 3 == 0:
            (d / f"safe_{i}.py").write_text("print('hello')\n")
        else:
            (d / f"vuln_{i}.py").write_text(f"password = 'pw{i:04d}'\n")

    result = scan_files(str(d))
    assert len(result) >= 10
