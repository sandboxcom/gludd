"""Deep tests for ``general_ludd.routers.adversarial``.

Covers the router layer beyond the existing happy-path / 422 / 400 smoke in
``test_routers_endpoints.py``: ``_get_detector`` caching, ``_finding_to_dict`` /
``_result_to_response`` edge cases, scan-file ``parsed`` field invariant,
ValueError→400, report category-filtering, and response-model shape guards.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.routers.adversarial import (
    ScanFileRequest,
    ScanFileResponse,
    ScanTextRequest,
    ScanTextResponse,
    _finding_to_dict,
    _get_detector,
    _result_to_response,
)
from general_ludd.security.adversarial_detector import (
    AdversarialCodeDetector,
    AdversarialFinding,
    AdversarialScanResult,
    Category,
    Severity,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_finding(**overrides: Any) -> AdversarialFinding:
    kw: dict[str, Any] = {
        "pattern_id": "ADV-001",
        "category": Category.BACKDOOR,
        "severity": Severity.CRITICAL,
        "description": "eval injection",
        "match_text": "eval(",
        "file_path": None,
        "line_number": 1,
        "confidence": 0.95,
        "remediation": "Remove eval()",
    }
    kw.update(overrides)
    return AdversarialFinding(**kw)


def _make_result(*findings: AdversarialFinding, **kw: Any) -> AdversarialScanResult:
    overrides: dict[str, Any] = {
        "findings": list(findings),
        "high_confidence": any(
            f.confidence >= 0.9 and f.severity in (Severity.CRITICAL, Severity.HIGH) for f in findings
        ),
        "scanned_files": 1,
        "lines_scanned": 10,
    }
    for key in ("blocked", "critical_count"):
        kw.pop(key, None)  # computed properties, not init args
    overrides.update(kw)
    return AdversarialScanResult(**overrides)


def _make_client(detector: AdversarialCodeDetector | None = None) -> TestClient:
    import general_ludd.routers.adversarial as adv_router

    app = FastAPI()
    if detector is not None:
        app.state._adversarial_detector = detector
    adv_router.register(app, {})
    return TestClient(app)


# ---------------------------------------------------------------------------
# 1. TestGetDetector — caching / default-factory
# ---------------------------------------------------------------------------


class TestGetDetector:
    """_get_detector: uses app.state cache, creates default on miss."""

    def test_returns_existing_from_state(self):
        app = FastAPI()
        det = AdversarialCodeDetector()
        app.state._adversarial_detector = det
        assert _get_detector(app) is det

    def test_creates_default_when_not_on_state(self):
        app = FastAPI()
        det = _get_detector(app)
        assert isinstance(det, AdversarialCodeDetector)

    def test_second_call_returns_cached(self):
        app = FastAPI()
        det1 = _get_detector(app)
        det2 = _get_detector(app)
        assert det1 is det2

    def test_cached_detector_still_scans(self):
        app = FastAPI()
        det = _get_detector(app)
        result = det.scan_text("eval(request.data)")
        assert result.findings


# ---------------------------------------------------------------------------
# 2. TestFindingToDict — enum and str fallback
# ---------------------------------------------------------------------------


class TestFindingToDict:
    """_finding_to_dict: enum .value and plain-str fallback."""

    def test_known_fields_present(self):
        f = _make_finding()
        d = _finding_to_dict(f)
        assert d["pattern_id"] == "ADV-001"
        assert d["category"] == "backdoor"
        assert d["severity"] == "critical"
        assert d["description"] == "eval injection"
        assert d["match_text"] == "eval("
        assert d["confidence"] == 0.95
        assert d["remediation"] == "Remove eval()"

    def test_file_path_and_line_number(self):
        f = _make_finding(file_path="src/danger.py", line_number=42)
        d = _finding_to_dict(f)
        assert d["file_path"] == "src/danger.py"
        assert d["line_number"] == 42

    def test_category_not_enum_uses_str_fallback(self):
        f = _make_finding(category="unknown_category")
        d = _finding_to_dict(f)
        assert d["category"] == "unknown_category"

    def test_severity_not_enum_uses_str_fallback(self):
        f = _make_finding(severity="extreme")
        d = _finding_to_dict(f)
        assert d["severity"] == "extreme"

    def test_category_is_str_enum_uses_value(self):
        f = _make_finding(category=Category.CREDENTIAL_LEAK)
        d = _finding_to_dict(f)
        assert d["category"] == "credential_leak"


# ---------------------------------------------------------------------------
# 3. TestResultToResponse — summary + blocked/PASS + field completeness
# ---------------------------------------------------------------------------


class TestResultToResponse:
    """_result_to_response: summary string, blocked/PASS, all keys present."""

    def test_empty_findings_returns_pass_summary(self):
        r = AdversarialScanResult(scanned_files=0, lines_scanned=0)
        d = _result_to_response(r)
        assert "0 finding(s)" in d["summary"]
        assert "PASS" in d["summary"]
        assert d["critical_count"] == 0
        assert d["blocked"] is False

    def test_single_critical_returns_blocked_summary(self):
        f = _make_finding()
        r = _make_result(f, blocked=True)
        d = _result_to_response(r)
        assert "1 finding(s)" in d["summary"]
        assert "1 critical" in d["summary"]
        assert "BLOCKED" in d["summary"]
        assert d["critical_count"] == 1
        assert d["blocked"] is True

    def test_multiple_mixed_findings(self):
        r = _make_result(
            _make_finding(pattern_id="A", severity=Severity.CRITICAL),
            _make_finding(pattern_id="B", severity=Severity.HIGH),
            _make_finding(pattern_id="C", severity=Severity.MEDIUM),
            critical_count=1,
            blocked=True,
        )
        d = _result_to_response(r)
        assert "3 finding(s)" in d["summary"]
        assert "1 critical" in d["summary"]
        assert "BLOCKED" in d["summary"]

    def test_all_fields_present(self):
        r = _make_result()
        d = _result_to_response(r)
        for key in (
            "findings",
            "high_confidence",
            "scanned_files",
            "lines_scanned",
            "critical_count",
            "blocked",
            "summary",
        ):
            assert key in d, f"missing key {key}"

    def test_findings_each_have_required_keys(self):
        r = _make_result(_make_finding())
        d = _result_to_response(r)
        f = d["findings"][0]
        for key in (
            "pattern_id",
            "category",
            "severity",
            "description",
            "match_text",
            "file_path",
            "line_number",
            "confidence",
            "remediation",
        ):
            assert key in f, f"finding missing key {key}"

    def test_not_blocked_when_no_critical_or_high(self):
        r = _make_result(
            _make_finding(severity=Severity.MEDIUM),
            blocked=False,
        )
        d = _result_to_response(r)
        assert d["blocked"] is False
        assert "PASS" in d["summary"]


# ---------------------------------------------------------------------------
# 4. TestScanFileResponseShape — parsed field invariant
# ---------------------------------------------------------------------------


class TestScanFileResponseShape:
    """scan-file endpoint always includes ``parsed: True`` in the payload."""

    def test_success_response_includes_parsed_true(self):
        det = MagicMock()
        result = _make_result(_make_finding())
        det.scan_file.return_value = result
        client = _make_client(detector=det)
        resp = client.post(
            "/admin/security/scan-file",
            json={"file_path": "/tmp/test.py"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["parsed"] is True

    def test_parsed_value_is_always_true(self):
        det = MagicMock()
        det.scan_file.return_value = _make_result()
        client = _make_client(detector=det)
        resp = client.post(
            "/admin/security/scan-file",
            json={"file_path": "/tmp/clean.py"},
        )
        assert resp.status_code == 200
        assert resp.json()["parsed"] is True

    def test_scan_file_response_has_all_scan_text_keys_plus_parsed(self):
        det = MagicMock()
        det.scan_file.return_value = _make_result(_make_finding())
        client = _make_client(detector=det)
        resp = client.post(
            "/admin/security/scan-file",
            json={"file_path": "/tmp/test.py"},
        )
        body = resp.json()
        for key in ScanTextResponse.model_fields:
            if key not in ("scanned_files",):  # ScanFileResponse lacks some fields
                continue
        for key in ScanFileResponse.model_fields:
            assert key in body, f"ScanFileResponse missing field {key}"


# ---------------------------------------------------------------------------
# 5. TestScanFileErrors — ValueError, OSError fallthrough
# ---------------------------------------------------------------------------


class TestScanFileErrors:
    """scan-file error handling at the router layer."""

    def test_value_error_returns_400(self):
        det = MagicMock()
        det.scan_file.side_effect = ValueError("bad path")
        client = _make_client(detector=det)
        resp = client.post(
            "/admin/security/scan-file",
            json={"file_path": "/bad/path"},
        )
        assert resp.status_code == 400
        assert "bad path" in resp.json()["detail"]

    def test_permission_error_returns_400(self):
        det = MagicMock()
        det.scan_file.side_effect = PermissionError("escapes roots")
        client = _make_client(detector=det)
        resp = client.post(
            "/admin/security/scan-file",
            json={"file_path": "/etc/shadow"},
        )
        assert resp.status_code == 400

    def test_unexpected_error_not_caught_by_router_propagates(self):
        det = MagicMock()
        det.scan_file.side_effect = RuntimeError("boom")
        client = _make_client(detector=det)
        with pytest.raises(RuntimeError, match="boom"):
            client.post(
                "/admin/security/scan-file",
                json={"file_path": "/tmp/x.py"},
            )

    def test_error_detail_includes_exception_message(self):
        det = MagicMock()
        det.scan_file.side_effect = ValueError("confined path not found")
        client = _make_client(detector=det)
        resp = client.post(
            "/admin/security/scan-file",
            json={"file_path": "/bad"},
        )
        assert resp.status_code == 400
        assert "confined path not found" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 6. TestScanTextResponseModel — field validation
# ---------------------------------------------------------------------------


class TestScanTextResponseModel:
    """ScanTextResponse model fields are documented and accessible."""

    def test_response_model_fields_match_payload(self):
        det = MagicMock()
        det.scan_text.return_value = _make_result(_make_finding())
        client = _make_client(detector=det)
        resp = client.post(
            "/admin/security/scan-text",
            json={"text": "eval(x)"},
        )
        assert resp.status_code == 200
        body = resp.json()
        for key in (
            "findings",
            "high_confidence",
            "scanned_files",
            "lines_scanned",
            "critical_count",
            "blocked",
            "summary",
        ):
            assert key in body

    def test_scan_text_with_file_path_context(self):
        det = MagicMock()
        det.scan_text.return_value = _make_result(_make_finding(file_path="/src/foo.py"))
        client = _make_client(detector=det)
        resp = client.post(
            "/admin/security/scan-text",
            json={"text": "x=1", "file_path": "/src/foo.py"},
        )
        assert resp.status_code == 200

    def test_scan_text_passes_optional_file_path_to_detector(self):
        det = MagicMock()
        det.scan_text.return_value = _make_result()
        client = _make_client(detector=det)
        client.post(
            "/admin/security/scan-text",
            json={"text": "x=1", "file_path": "/src/main.py"},
        )
        det.scan_text.assert_called_once_with("x=1", file_path="/src/main.py")

    def test_scan_text_passes_none_file_path_when_absent(self):
        det = MagicMock()
        det.scan_text.return_value = _make_result()
        client = _make_client(detector=det)
        client.post("/admin/security/scan-text", json={"text": "x=1"})
        det.scan_text.assert_called_once_with("x=1", file_path=None)


# ---------------------------------------------------------------------------
# 7. TestReportEndpoint — category filtering, limits, empty state
# ---------------------------------------------------------------------------


class TestReportEndpoint:
    """GET /admin/security/adversarial/report: filters, limits, shape."""

    def test_empty_categories_returns_zero_total(self):
        det = MagicMock()
        det.get_all_categories.return_value = []
        client = _make_client(detector=det)
        resp = client.get("/admin/security/adversarial/report")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_patterns"] == 0
        assert body["categories"] == []

    def test_category_filter_not_matching_returns_empty_dict(self):
        det = MagicMock()
        cat = MagicMock(value="backdoor")
        det.get_all_categories.return_value = [cat]
        det.get_patterns_by_category.return_value = []
        client = _make_client(detector=det)
        resp = client.get("/admin/security/adversarial/report?category=obfuscation")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_patterns"] == 0

    def test_category_filter_matching_returns_only_that_category(self):
        det = MagicMock()
        cat = MagicMock(value="backdoor")
        det.get_all_categories.return_value = [cat]
        pattern = MagicMock()
        pattern.id = "ADV-backdoor-01"
        pattern.description = "eval injection"
        pattern.severity = MagicMock(value="critical")
        det.get_patterns_by_category.return_value = [pattern]
        client = _make_client(detector=det)
        resp = client.get("/admin/security/adversarial/report?category=backdoor")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_patterns"] == 1
        assert "backdoor" in body["patterns_by_category"]
        assert body["patterns_by_category"]["backdoor"][0]["id"] == "ADV-backdoor-01"

    def test_default_limit_50_applied(self):
        det = MagicMock()
        cat = MagicMock(value="backdoor")
        det.get_all_categories.return_value = [cat]
        det.get_patterns_by_category.return_value = [MagicMock()] * 100
        client = _make_client(detector=det)
        resp = client.get("/admin/security/adversarial/report")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_patterns"] <= 50  # clipped

    def test_custom_limit_respected(self):
        det = MagicMock()
        cat = MagicMock(value="backdoor")
        det.get_all_categories.return_value = [cat]
        det.get_patterns_by_category.return_value = [MagicMock()] * 30
        client = _make_client(detector=det)
        resp = client.get("/admin/security/adversarial/report?limit=10")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_patterns"] <= 10

    def test_generated_at_is_iso_format(self):
        det = MagicMock()
        det.get_all_categories.return_value = []
        client = _make_client(detector=det)
        resp = client.get("/admin/security/adversarial/report")
        assert resp.status_code == 200
        ts = resp.json()["generated_at"]
        assert "T" in ts
        assert ts.endswith("+00:00") or ts.endswith("Z")

    def test_multiple_categories_all_present(self):
        det = MagicMock()
        cat1, cat2 = MagicMock(value="backdoor"), MagicMock(value="self_sabotage")
        det.get_all_categories.return_value = [cat1, cat2]
        p = MagicMock()
        p.id = "P-01"
        p.description = "desc"
        p.severity = MagicMock(value="high")
        det.get_patterns_by_category.return_value = [p]
        client = _make_client(detector=det)
        resp = client.get("/admin/security/adversarial/report")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body["categories"]) == {"backdoor", "self_sabotage"}
        assert body["total_patterns"] == 2

    def test_category_str_not_enum_uses_str(self):
        det = MagicMock()
        cat = "plain_string_category"
        cat_obj = MagicMock(value=cat)
        det.get_all_categories.return_value = [cat_obj]
        det.get_patterns_by_category.return_value = [MagicMock()] * 2
        client = _make_client(detector=det)
        resp = client.get("/admin/security/adversarial/report")
        assert resp.status_code == 200
        body = resp.json()
        assert cat in body["categories"]

    def test_pattern_dict_keys_in_report(self):
        det = MagicMock()
        cat = MagicMock(value="backdoor")
        det.get_all_categories.return_value = [cat]
        p = MagicMock()
        p.id = "ADV-01"
        p.description = "test"
        p.severity = MagicMock(value="critical")
        det.get_patterns_by_category.return_value = [p]
        client = _make_client(detector=det)
        resp = client.get("/admin/security/adversarial/report")
        assert resp.status_code == 200
        entry = resp.json()["patterns_by_category"]["backdoor"][0]
        for key in ("id", "description", "severity"):
            assert key in entry


# ---------------------------------------------------------------------------
# 8. TestRequestModelValidation — Pydantic edge cases
# ---------------------------------------------------------------------------


class TestRequestModelValidation:
    """ScanTextRequest / ScanFileRequest Pydantic validation."""

    def test_scan_text_request_min_length_enforced(self):
        with pytest.raises(ValueError):
            ScanTextRequest(text="")

    def test_scan_text_request_valid(self):
        req = ScanTextRequest(text="hello")
        assert req.text == "hello"
        assert req.file_path is None

    def test_scan_text_request_with_optional_file_path(self):
        req = ScanTextRequest(text="x", file_path="/a/b.py")
        assert req.file_path == "/a/b.py"

    def test_scan_file_request_min_length_enforced(self):
        with pytest.raises(ValueError):
            ScanFileRequest(file_path="")

    def test_scan_file_request_valid(self):
        req = ScanFileRequest(file_path="/tmp/test.py")
        assert req.file_path == "/tmp/test.py"


# ---------------------------------------------------------------------------
# 9. TestScanFileResponseParsedInvariant — behavioral proof that parsed cannot
#    be missed (the latent 500 bug before the fix)
# ---------------------------------------------------------------------------


class TestScanFileResponseParsedInvariant:
    """The router always adds ``parsed`` — ScanFileResponse requires it."""

    def test_parsed_always_present_in_success(self):
        det = MagicMock()
        det.scan_file.return_value = _make_result()
        client = _make_client(detector=det)
        resp = client.post("/admin/security/scan-file", json={"file_path": "/tmp/x.py"})
        assert resp.status_code == 200
        assert "parsed" in resp.json()

    def test_parsed_not_in_scan_text_response(self):
        det = MagicMock()
        det.scan_text.return_value = _make_result(_make_finding())
        client = _make_client(detector=det)
        resp = client.post("/admin/security/scan-text", json={"text": "eval(x)"})
        assert resp.status_code == 200
        assert "parsed" not in resp.json()

    def test_scan_file_error_response_does_not_need_parsed(self):
        det = MagicMock()
        det.scan_file.side_effect = PermissionError("nope")
        client = _make_client(detector=det)
        resp = client.post("/admin/security/scan-file", json={"file_path": "/etc/hosts"})
        assert resp.status_code == 400
        assert "parsed" not in resp.json()


# ---------------------------------------------------------------------------
# 10. TestDetectorIntegration — real detector through real router
# ---------------------------------------------------------------------------


class TestDetectorIntegration:
    """End-to-end: real AdversarialCodeDetector wired through the router."""

    def test_real_detector_scan_text_via_router(self, tmp_path):
        app = FastAPI()
        det = AdversarialCodeDetector()
        app.state._adversarial_detector = det
        import general_ludd.routers.adversarial as adv_router

        adv_router.register(app, {})
        client = TestClient(app)
        resp = client.post(
            "/admin/security/scan-text",
            json={"text": "eval(request.data)"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["blocked"] is True
        assert body["critical_count"] >= 1
        assert any(f["pattern_id"] == "eval_on_input" for f in body["findings"])

    def test_real_detector_scan_clean_text_returns_pass(self, tmp_path):
        app = FastAPI()
        det = AdversarialCodeDetector()
        app.state._adversarial_detector = det
        import general_ludd.routers.adversarial as adv_router

        adv_router.register(app, {})
        client = TestClient(app)
        resp = client.post(
            "/admin/security/scan-text",
            json={"text": "def add(a, b): return a + b"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["blocked"] is False
        assert body["critical_count"] == 0
        assert "PASS" in body["summary"]

    def test_real_detector_scan_file_via_router(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "danger.py"
        target.write_text("eval(request.data)\n")
        app = FastAPI()
        det = AdversarialCodeDetector()
        app.state._adversarial_detector = det
        import general_ludd.routers.adversarial as adv_router

        adv_router.register(app, {})
        client = TestClient(app)
        resp = client.post(
            "/admin/security/scan-file",
            json={"file_path": str(target)},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["parsed"] is True
        assert body["blocked"] is True
        assert any(f["pattern_id"] == "eval_on_input" for f in body["findings"])

    def test_real_detector_report_has_all_real_categories(self):
        app = FastAPI()
        det = AdversarialCodeDetector()
        app.state._adversarial_detector = det
        import general_ludd.routers.adversarial as adv_router

        adv_router.register(app, {})
        client = TestClient(app)
        resp = client.get("/admin/security/adversarial/report")
        assert resp.status_code == 200
        body = resp.json()
        assert "generated_at" in body
        for cat_name in (
            "backdoor",
            "credential_leak",
            "self_sabotage",
            "logic_degrade",
            "dependency_attack",
            "obfuscation",
        ):
            assert cat_name in body["categories"], f"missing category {cat_name}"
        assert body["total_patterns"] > 0

    def test_real_detector_report_category_filter(self):
        app = FastAPI()
        det = AdversarialCodeDetector()
        app.state._adversarial_detector = det
        import general_ludd.routers.adversarial as adv_router

        adv_router.register(app, {})
        client = TestClient(app)
        resp = client.get("/admin/security/adversarial/report?category=backdoor")
        assert resp.status_code == 200
        body = resp.json()
        assert body["categories"] == ["backdoor"]
        assert "backdoor" in body["patterns_by_category"]
        assert body["total_patterns"] > 0
