"""End-to-end tests for adversarial_detector.py — real-world diff and multi-category scans.

Exercises the AdversarialCodeDetector through realistic code-review scenarios:
  * Full git diff with multiple adversarial categories in one pass
  * Self-sabotage guardrail weakening patterns in diff format
  * Backdoor injection vectors (eval, exec, shell) in realistic contexts
  * Credential leaks (API keys, tokens, PEM armor) in added diff lines
  * Logic degradation (assertion removal, error swallowing, hash downgrade)
  * Empty / benign diffs produce zero findings
  * Severity, category, confidence, and blocked flags are correct
  * Mixed benign+malicious diffs only flag the malicious lines

These are integration-level scenarios using the detector's public API directly
— the detector is pure I/O-free code, so no subprocess or HTTP transport is
needed. Each test mirrors a real code-review workflow.
"""

from __future__ import annotations

from general_ludd.security.adversarial_detector import (
    AdversarialCodeDetector,
    Category,
    Severity,
)

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _detector() -> AdversarialCodeDetector:
    return AdversarialCodeDetector()


def _ids(result) -> list[str]:
    return [f.pattern_id for f in result.findings]


# --------------------------------------------------------------------------- #
# 1. Full diff — multi-category adversarial additions
# --------------------------------------------------------------------------- #


class TestMultiCategoryDiffScan:
    """A realistic code-review diff containing several categories of malicious additions."""

    def test_multi_category_diff_flags_all_three(self):
        diff = """\
--- a/src/general_ludd/security/guard.py
+++ b/src/general_ludd/security/guard.py
@@ -12,6 +12,8 @@
 def enforce_policy(action):
+    api_key = "test_key_abcdefghijklmnopqrstuvwxyz123456"
+    except Exception: return True  # allow
     if not validate(action):
"""
        result = _detector().scan_diff(diff)
        ids_found = _ids(result)
        assert "default_allow_auth" in ids_found
        assert "hardcoded_api_key" in ids_found
        assert result.blocked

    def test_diff_with_backdoor_and_obfuscation(self):
        diff = """\
--- a/src/app.py
+++ b/src/app.py
@@ -4,6 +4,8 @@
 def handle(request):
+    exec(base64.b64decode(request.data))
+    subprocess.run(cmd, shell=True, input=request.args)
     return safe_handler(request)
"""
        result = _detector().scan_diff(diff)
        ids_found = _ids(result)
        assert "base64_exec" in ids_found
        assert "shell_injection_subprocess" in ids_found

    def test_diff_with_self_sabotage_and_logic_degrade(self):
        diff = """\
--- a/src/guards.py
+++ b/src/guards.py
@@ -5,6 +5,8 @@
+MAX_RETRIES = 1  # weakened
+    except Exception: pass  # silent swallow
     if not valid:
"""
        result = _detector().scan_diff(diff)
        ids_found = _ids(result)
        assert any(pid in ids_found for pid in ("security_constant_weakened", "error_handler_silenced"))


# --------------------------------------------------------------------------- #
# 2. Self-sabotage — guardrail weakening via diff
# --------------------------------------------------------------------------- #


class TestSelfSabotageE2E:
    """Guardrail-weakening patterns that must be caught in diffs."""

    def test_guardrail_enabled_false_caught_in_diff(self):
        diff = """\
--- a/.opencode/plugin/enforce-floor.ts
+++ b/.opencode/plugin/enforce-floor.ts
@@ -20,6 +20,7 @@
+enabled = false  // guardrail bypass
 export function activate() {
"""
        result = _detector().scan_diff(diff)
        assert any(f.pattern_id == "guardrail_disable_registry" for f in result.findings)

    def test_coverage_threshold_lowered_in_diff(self):
        diff = """\
--- a/pyproject.toml
+++ b/pyproject.toml
@@ -30,6 +30,7 @@
+    "--cov-fail-under=5",
 [tool.pytest.ini_options]
"""
        result = _detector().scan_diff(diff)
        assert any(f.pattern_id == "test_coverage_lowered" for f in result.findings)

    def test_security_timeout_weakened_in_diff(self):
        diff = """\
--- a/src/config.py
+++ b/src/config.py
@@ -10,6 +10,7 @@
+TIMEOUT = 999999
 DATABASE_URL = "sqlite://"
"""
        result = _detector().scan_diff(diff)
        assert any(f.pattern_id == "security_constant_weakened" for f in result.findings)

    def test_assertion_skip_comment_in_diff(self):
        diff = """\
--- a/tests/test_auth.py
+++ b/tests/test_auth.py
@@ -15,6 +15,7 @@
+# skip this assert check — low signal
 def test_admin_login():
"""
        result = _detector().scan_diff(diff)
        assert any(f.pattern_id == "assertion_removal" for f in result.findings)


# --------------------------------------------------------------------------- #
# 3. Backdoor — injection vectors in realistic contexts
# --------------------------------------------------------------------------- #


class TestBackdoorE2E:
    """Backdoor patterns injected into realistic diff additions."""

    def test_eval_on_request_body_in_diff(self):
        diff = """\
--- a/src/gateway.py
+++ b/src/gateway.py
@@ -42,6 +42,7 @@
 async def process_payload(request: Request):
+    result = eval(request.body.decode())
     return JSONResponse({"status": "ok"})
"""
        result = _detector().scan_diff(diff)
        assert any(f.pattern_id == "eval_on_input" for f in result.findings)

    def test_pickle_loads_on_untrusted_data_in_diff(self):
        diff = """\
--- a/src/worker.py
+++ b/src/worker.py
@@ -55,6 +55,7 @@
+    data = pickle.loads(network_payload)
 def process_job(job: JobSpec):
"""
        result = _detector().scan_diff(diff)
        assert any(f.pattern_id == "pickle_deserialize_untrusted" for f in result.findings)

    def test_yaml_unsafe_load_in_diff(self):
        diff = """\
--- a/src/parser.py
+++ b/src/parser.py
@@ -8,6 +8,7 @@
+    cfg = yaml.load(raw, Loader=yaml.Loader)
 def parse_config(path: str):
"""
        result = _detector().scan_diff(diff)
        assert any(f.pattern_id == "yaml_unsafe_load" for f in result.findings)

    def test_os_system_fstring_injection_in_diff(self):
        diff = """\
--- a/src/cli.py
+++ b/src/cli.py
@@ -12,6 +12,7 @@
+    os.system(f"rm -rf {user_path}")
 def cleanup(user_path: str):
"""
        result = _detector().scan_diff(diff)
        assert any(f.pattern_id == "os_system_injection" for f in result.findings)


# --------------------------------------------------------------------------- #
# 4. Credential leaks — secrets in added diff lines
# --------------------------------------------------------------------------- #


class TestCredentialLeakE2E:
    """Credential leaks detected in realistic diff additions."""

    def test_hardcoded_api_key_in_diff(self):
        diff = """\
--- a/src/settings.py
+++ b/src/settings.py
@@ -3,6 +3,7 @@
+SECRET_KEY = "test_key_abcdefghijklmnopqrstuvwxyz123456-abcdefghijklmnopqrstuv12345678"
 DATABASE_URL = "postgresql://..."
"""
        result = _detector().scan_diff(diff)
        assert any(f.pattern_id == "hardcoded_api_key" for f in result.findings)

    def test_aws_key_in_diff(self):
        diff = """\
--- a/src/aws.py
+++ b/src/aws.py
@@ -8,6 +8,7 @@
+    access_key = "AKIAIOSFODNN7EXAMPLE"
 def s3_client():
"""
        result = _detector().scan_diff(diff)
        assert any(f.pattern_id == "aws_key_hardcoded" for f in result.findings)

    def test_private_key_armor_in_diff(self):
        diff = """\
--- a/src/crypto.py
+++ b/src/crypto.py
@@ -1,6 +1,8 @@
+PRIVATE_KEY = \"\"\"-----BEGIN RSA PRIVATE KEY-----
+MIICXA...\"\"\"
 import base64
"""
        result = _detector().scan_diff(diff)
        assert any(f.pattern_id == "private_key_armor" for f in result.findings)

    def test_github_token_in_diff(self):
        diff = """\
--- a/scripts/deploy.sh
+++ b/scripts/deploy.sh
@@ -1,4 +1,5 @@
+export GH_TOKEN=ghp_abcdefghijklmnopqrstuvwxyz1234567890
 set -euo pipefail
"""
        result = _detector().scan_diff(diff)
        assert any(f.pattern_id == "github_token" for f in result.findings)

    def test_env_var_reference_not_flagged(self):
        diff = """\
--- a/src/config.py
+++ b/src/config.py
@@ -5,6 +5,7 @@
+api_key = "${OPENAI_KEY}"
 port = 8000
"""
        result = _detector().scan_diff(diff)
        assert "hardcoded_api_key" not in _ids(result)


# --------------------------------------------------------------------------- #
# 5. Logic degradation — assertion removal, error swallowing, hash downgrade
# --------------------------------------------------------------------------- #


class TestLogicDegradationE2E:
    """Logic-degrading changes detected in diffs."""

    def test_default_allow_auth_in_diff(self):
        diff = """\
--- a/src/auth.py
+++ b/src/auth.py
@@ -20,6 +20,7 @@
+    if not user: return True
 def check_auth(user: User) -> bool:
"""
        result = _detector().scan_diff(diff)
        assert any(f.pattern_id == "default_allow_auth" for f in result.findings)

    def test_hash_downgrade_md5_in_diff(self):
        diff = """\
--- a/src/hash_utils.py
+++ b/src/hash_utils.py
@@ -5,6 +5,7 @@
+    h = hashlib.md5()
 def compute_hash(data: bytes):
"""
        result = _detector().scan_diff(diff)
        assert any(f.pattern_id == "hash_downgrade" for f in result.findings)

    def test_identity_compare_in_diff(self):
        diff = """\
--- a/src/access.py
+++ b/src/access.py
@@ -14,6 +14,7 @@
+    if user_token is "admin":
 def grant_access(user_token: str):
"""
        result = _detector().scan_diff(diff)
        assert any(f.pattern_id == "compare_by_identity_not_value" for f in result.findings)

    def test_error_silenced_in_diff(self):
        diff = """\
--- a/src/worker.py
+++ b/src/worker.py
@@ -30,6 +30,8 @@
+    except ValueError: logger.debug('msg', exc_info=False)
 def do_work():
"""
        result = _detector().scan_diff(diff)
        assert any(f.pattern_id == "error_handler_silenced" for f in result.findings)


# --------------------------------------------------------------------------- #
# 6. Empty / benign diffs — zero findings
# --------------------------------------------------------------------------- #


class TestEmptyAndBenign:
    """Empty and benign diffs must produce zero findings."""

    def test_empty_diff_zero_findings(self):
        result = _detector().scan_diff("")
        assert result.findings == []
        assert not result.blocked
        assert not result.high_confidence

    def test_benign_diff_zero_findings(self):
        diff = """\
--- a/src/utils.py
+++ b/src/utils.py
@@ -1,3 +1,4 @@
+def add(a, b):
+    return a + b
 def subtract(a, b):
"""
        result = _detector().scan_diff(diff)
        assert result.findings == []

    def test_removal_only_diff_not_flagged(self):
        diff = """\
--- a/src/danger.py
+++ b/src/danger.py
@@ -5,3 +5,1 @@
-eval(request.data)
-subprocess.run("rm -rf /", shell=True)
+def safe():
"""
        result = _detector().scan_diff(diff)
        assert "eval_on_input" not in _ids(result)
        assert "shell_injection_subprocess" not in _ids(result)

    def test_unrelated_diff_lines_not_flagged(self):
        diff = """\
--- a/README.md
+++ b/README.md
@@ -1,4 +1,5 @@
+## New Section
+Some documentation text here.
 # Project
"""
        result = _detector().scan_diff(diff)
        assert result.findings == []


# --------------------------------------------------------------------------- #
# 7. Severity, category, confidence correctness
# --------------------------------------------------------------------------- #


class TestFindingMetadataE2E:
    """Findings carry correct severity, category, and confidence metadata."""

    def test_critical_finding_correct_metadata(self):
        result = _detector().scan_text("exec(request.json.get('cmd'))")
        assert result.blocked
        assert result.critical_count >= 1
        for f in result.findings:
            if f.pattern_id == "eval_on_input":
                assert f.severity == Severity.CRITICAL
                assert f.category == Category.BACKDOOR
                assert f.confidence == 1.0
                assert f.remediation

    def test_high_severity_blocks(self):
        result = _detector().scan_text("h = hashlib.md5()")
        assert result.blocked
        assert result.high_confidence
        for f in result.findings:
            if f.pattern_id == "hash_downgrade":
                assert f.severity == Severity.HIGH

    def test_medium_severity_does_not_block(self):
        result = _detector().scan_text('install_requires = ["requests>=2.0"]')
        assert not result.blocked
        assert not result.high_confidence
        assert result.findings
        for f in result.findings:
            if f.pattern_id == "unpinned_dependency":
                assert f.severity == Severity.MEDIUM

    def test_multiple_severities_mixed_triage(self):
        code = 'install_requires = ["requests>=2.0"]\neval(request.data)'
        result = _detector().scan_text(code)
        assert result.blocked
        severities = {f.severity for f in result.findings}
        assert Severity.CRITICAL in severities

    def test_finding_has_description_and_pattern_id(self):
        result = _detector().scan_text("exec(base64.b64decode(payload))")
        found = False
        for f in result.findings:
            if f.pattern_id == "base64_exec":
                assert f.description
                assert f.category == Category.OBFUSCATION
                assert f.match_text
                found = True
        assert found, "base64_exec finding must be present"

    def test_lines_scanned_and_scanned_files(self):
        result = _detector().scan_text("a\nb\nc\neval(request.data)\ne\nf", file_path="src/bad.py")
        assert result.scanned_files == 1
        assert result.lines_scanned == 6


# --------------------------------------------------------------------------- #
# 8. Mixed benign and malicious — only malicious flagged
# --------------------------------------------------------------------------- #


class TestMixedContent:
    """Only adversarial lines are flagged in mixed-content scans."""

    def test_benign_code_with_one_backdoor_added(self):
        diff = """\
--- a/src/service.py
+++ b/src/service.py
@@ -1,5 +1,7 @@
+import logging
+logger = logging.getLogger(__name__)
+subprocess.run(cmd, shell=True, input=user_data)
 def handle_request():
-    pass
+    logger.info("handling request")
+    return {"ok": True}
"""
        result = _detector().scan_diff(diff)
        ids_found = _ids(result)
        assert "shell_injection_subprocess" in ids_found
        assert result.blocked

    def test_multiple_benign_plus_one_credential(self):
        diff = """\
--- a/src/settings.py
+++ b/src/settings.py
@@ -10,6 +10,10 @@
+DATABASE_URL = "postgresql://user:pass@localhost/db"
+LOG_LEVEL = "INFO"
+WORKER_COUNT = 4
+api_key = "test_key_abcdefghijklmnopqrstuvwxyz123456_abcdefghijklmnopqrst12345678"
+RETRY_DELAY = 2
"""
        result = _detector().scan_diff(diff)
        ids_found = _ids(result)
        assert "hardcoded_api_key" in ids_found
        assert result.blocked


# --------------------------------------------------------------------------- #
# 9. Edge-case diff formats
# --------------------------------------------------------------------------- #


class TestDiffEdgeCases:
    """Non-standard diff formats and edge cases."""

    def test_no_file_header_only_additions(self):
        diff = "+eval(request.data)\n+pass"
        result = _detector().scan_diff(diff)
        assert any(f.pattern_id == "eval_on_input" for f in result.findings)

    def test_new_file_diff(self):
        diff = """\
--- /dev/null
+++ b/src/backdoor.py
@@ -0,0 +1,3 @@
+import pickle
+pickle.loads(user_input)
+os.system(f"rm -rf {user_input}")
"""
        result = _detector().scan_diff(diff)
        ids_found = _ids(result)
        assert "pickle_deserialize_untrusted" in ids_found
        assert "os_system_injection" in ids_found

    def test_large_benign_diff_not_slowed(self):
        lines = [f"+x_{i} = {i}" for i in range(500)]
        diff = f"--- a/big.py\n+++ b/big.py\n@@ -0,0 +1,{len(lines)} @@\n"
        diff += "\n".join(lines)
        result = _detector().scan_diff(diff)
        assert result.findings == []

    def test_diff_with_context_lines_not_plus(self):
        diff = """\
--- a/src/app.py
+++ b/src/app.py
@@ -10,6 +10,8 @@
 nothing to see here
+exec(request.body)
 just context
-another context
"""
        result = _detector().scan_diff(diff)
        assert any(f.pattern_id == "eval_on_input" for f in result.findings)


# --------------------------------------------------------------------------- #
# 10. Real-world mixed-attack diff (all categories in one review)
# --------------------------------------------------------------------------- #


class TestRealWorldMixedAttack:
    """A single diff that combines all attack categories — simulates a real adversarial PR."""

    def test_all_categories_in_one_diff(self):
        diff = """\
--- a/src/general_ludd/gateway.py
+++ b/src/general_ludd/gateway.py
@@ -20,6 +20,12 @@
+api_key = "test_key_abcdefghijklmnopqrstuvwxyz123456-1234567890abcdefghijklmnopqrstuv"
+MAX_RETRIES = 1
+def validate_input(x):
+    return True
+    exec(base64.b64decode(user_input))
+    h = hashlib.md5()
+    subprocess.run(cmd, shell=True, input=query_params)
+import requsts
 def handle_agent_request():
"""
        result = _detector().scan_diff(diff)
        ids_found = _ids(result)
        assert "hardcoded_api_key" in ids_found
        assert "security_constant_weakened" in ids_found
        assert "base64_exec" in ids_found
        assert "hash_downgrade" in ids_found
        assert "shell_injection_subprocess" in ids_found
        assert "typosquatted_import" in ids_found

    def test_summary_reflects_all_categories(self):
        diff = """\
--- a/src/app.py
+++ b/src/app.py
@@ -1,3 +1,5 @@
+eval(request.data)
+api_key = "test_key_abcdefghijklmnopqrstuvwxyz123456"
 def main():
"""
        result = _detector().scan_diff(diff)
        summary = result.summary
        assert "backdoor" in summary
        assert "credential_leak" in summary

    def test_blocked_on_multiple_critical(self):
        diff = """\
--- a/src/app.py
+++ b/src/app.py
@@ -1,3 +1,4 @@
+eval(request.data)
+exec(request.json)
 def main():
"""
        result = _detector().scan_diff(diff)
        assert result.blocked
        assert result.critical_count >= 2
