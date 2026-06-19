"""
tests/unit/test_a11y_visual_qa.py

Unit tests for the a11y + visual-QA skill (general_ludd.quality.a11y_checker).

Strategy:
  - GOOD_HTML: a well-formed, fully-accessible page — must PASS (no errors).
  - BAD_HTML_*: targeted broken pages — must FAIL with specific check identifiers.
  - Edge-case tests for img-alt, heading order, landmark detection, viewport meta.
"""

from __future__ import annotations

from general_ludd.quality.a11y_checker import (
    A11yReport,
    Finding,
    check_html,
)

# ---------------------------------------------------------------------------
# Fixtures / HTML samples
# ---------------------------------------------------------------------------

GOOD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Accessible Test Page</title>
</head>
<body>
  <header><nav aria-label="Main navigation"><a href="/">Home</a></nav></header>
  <main>
    <h1>Welcome</h1>
    <h2>Section One</h2>
    <p>Content here.</p>
    <img src="photo.jpg" alt="A descriptive caption">
    <img src="spacer.gif" alt="">
  </main>
  <footer>Copyright 2026</footer>
</body>
</html>"""

BAD_HTML_MISSING_LANG = """<!DOCTYPE html>
<html>
<head><title>No Lang</title></head>
<body><main><h1>Hi</h1></main></body>
</html>"""

BAD_HTML_MISSING_TITLE = """<!DOCTYPE html>
<html lang="en">
<head></head>
<body><main><h1>No Title</h1></main></body>
</html>"""

BAD_HTML_IMG_NO_ALT = """<!DOCTYPE html>
<html lang="en">
<head><title>Bad Img</title></head>
<body>
  <main><h1>Images</h1>
    <img src="cat.png">
    <img src="dog.png" alt="A dog">
  </main>
</body>
</html>"""

BAD_HTML_SKIPPED_HEADING = """<!DOCTYPE html>
<html lang="en">
<head><title>Bad Headings</title></head>
<body>
  <main>
    <h1>Top</h1>
    <h3>Skipped h2!</h3>
  </main>
</body>
</html>"""

BAD_HTML_NO_H1 = """<!DOCTYPE html>
<html lang="en">
<head><title>No H1</title></head>
<body>
  <main>
    <h2>Subtitle</h2>
    <h3>Sub-sub</h3>
  </main>
</body>
</html>"""

BAD_HTML_MISSING_LANDMARKS = """<!DOCTYPE html>
<html lang="en">
<head><title>No Landmarks</title></head>
<body>
  <div>
    <h1>No semantic structure</h1>
    <p>Everything is in divs.</p>
  </div>
</body>
</html>"""

BAD_HTML_MISSING_VIEWPORT = """<!DOCTYPE html>
<html lang="en">
<head><title>No Viewport</title></head>
<body>
  <main><h1>No viewport meta</h1></main>
</body>
</html>"""

BAD_HTML_LOW_CONTRAST = """<!DOCTYPE html>
<html lang="en">
<head><title>Contrast</title></head>
<body>
  <main>
    <h1>Contrast Test</h1>
    <p style="color:white;background:white">Invisible text</p>
  </main>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def check_ids(report: A11yReport) -> set[str]:
    """Return the set of check identifiers in this report."""
    return {f.check for f in report.findings}


def error_ids(report: A11yReport) -> set[str]:
    return {f.check for f in report.findings if f.severity == "error"}


def warning_ids(report: A11yReport) -> set[str]:
    return {f.check for f in report.findings if f.severity == "warning"}


# ---------------------------------------------------------------------------
# GOOD HTML — must pass (zero errors)
# ---------------------------------------------------------------------------

class TestGoodHtml:
    def test_good_html_passes(self) -> None:
        report = check_html(GOOD_HTML, file_path="good.html")
        assert report.passed, (
            f"Expected PASS but got errors: {[f.message for f in report.errors]}"
        )

    def test_good_html_no_errors(self) -> None:
        report = check_html(GOOD_HTML, file_path="good.html")
        assert report.errors == [], f"Unexpected errors: {report.errors}"

    def test_good_html_summary_starts_with_pass(self) -> None:
        report = check_html(GOOD_HTML, file_path="good.html")
        assert report.summary().startswith("PASS")

    def test_good_html_as_dict_structure(self) -> None:
        report = check_html(GOOD_HTML, file_path="good.html")
        d = report.as_dict()
        assert d["passed"] is True
        assert d["errors"] == 0
        assert "findings" in d


# ---------------------------------------------------------------------------
# BAD HTML — missing lang
# ---------------------------------------------------------------------------

class TestMissingLang:
    def test_missing_lang_fails(self) -> None:
        report = check_html(BAD_HTML_MISSING_LANG, file_path="no_lang.html")
        assert not report.passed

    def test_missing_lang_check_present(self) -> None:
        report = check_html(BAD_HTML_MISSING_LANG, file_path="no_lang.html")
        assert "lang-attribute" in error_ids(report)

    def test_missing_lang_message(self) -> None:
        report = check_html(BAD_HTML_MISSING_LANG, file_path="no_lang.html")
        msgs = [f.message for f in report.findings if f.check == "lang-attribute"]
        assert any("lang" in m.lower() for m in msgs)


# ---------------------------------------------------------------------------
# BAD HTML — missing title
# ---------------------------------------------------------------------------

class TestMissingTitle:
    def test_missing_title_fails(self) -> None:
        report = check_html(BAD_HTML_MISSING_TITLE, file_path="no_title.html")
        assert not report.passed

    def test_missing_title_check_present(self) -> None:
        report = check_html(BAD_HTML_MISSING_TITLE, file_path="no_title.html")
        assert "title-element" in error_ids(report)


# ---------------------------------------------------------------------------
# BAD HTML — img missing alt
# ---------------------------------------------------------------------------

class TestImgAlt:
    def test_img_no_alt_fails(self) -> None:
        report = check_html(BAD_HTML_IMG_NO_ALT, file_path="bad_img.html")
        assert not report.passed

    def test_img_no_alt_check_present(self) -> None:
        report = check_html(BAD_HTML_IMG_NO_ALT, file_path="bad_img.html")
        assert "img-alt" in error_ids(report)

    def test_img_alt_empty_string_is_valid(self) -> None:
        """alt="" is valid for decorative images — should not be flagged."""
        html = """<html lang="en"><head><title>T</title></head>
        <body><main><h1>H</h1><img src="s.gif" alt=""></main></body></html>"""
        report = check_html(html, file_path="decorative.html")
        assert "img-alt" not in error_ids(report)

    def test_img_only_missing_alt_flagged(self) -> None:
        """Only the img without alt is flagged; the one with alt is fine."""
        report = check_html(BAD_HTML_IMG_NO_ALT, file_path="bad_img.html")
        alt_findings = [f for f in report.findings if f.check == "img-alt"]
        # Only one img is missing alt (cat.png); dog.png has alt
        assert len(alt_findings) == 1
        assert "cat.png" in (alt_findings[0].element or "")


# ---------------------------------------------------------------------------
# BAD HTML — heading order
# ---------------------------------------------------------------------------

class TestHeadingOrder:
    def test_skipped_heading_fails(self) -> None:
        report = check_html(BAD_HTML_SKIPPED_HEADING, file_path="bad_headings.html")
        assert not report.passed

    def test_skipped_heading_check_present(self) -> None:
        report = check_html(BAD_HTML_SKIPPED_HEADING, file_path="bad_headings.html")
        assert "heading-order" in error_ids(report)

    def test_skipped_heading_message_mentions_levels(self) -> None:
        report = check_html(BAD_HTML_SKIPPED_HEADING, file_path="bad_headings.html")
        msgs = [f.message for f in report.findings if f.check == "heading-order"]
        # Should mention the skip (h1 -> h3)
        assert any("h1" in m or "h3" in m or "skip" in m.lower() for m in msgs)

    def test_no_h1_fails(self) -> None:
        report = check_html(BAD_HTML_NO_H1, file_path="no_h1.html")
        assert not report.passed

    def test_no_h1_check_present(self) -> None:
        report = check_html(BAD_HTML_NO_H1, file_path="no_h1.html")
        assert "heading-order" in error_ids(report)


# ---------------------------------------------------------------------------
# BAD HTML — missing landmarks
# ---------------------------------------------------------------------------

class TestLandmarks:
    def test_missing_landmarks_flagged(self) -> None:
        report = check_html(BAD_HTML_MISSING_LANDMARKS, file_path="no_landmarks.html")
        assert "landmark-missing" in warning_ids(report)

    def test_all_required_landmarks_flagged(self) -> None:
        report = check_html(BAD_HTML_MISSING_LANDMARKS, file_path="no_landmarks.html")
        lm_findings = [f for f in report.findings if f.check == "landmark-missing"]
        flagged = {f.message for f in lm_findings}
        # All four required landmarks are absent
        assert any("main" in m for m in flagged)
        assert any("nav" in m for m in flagged)

    def test_role_equivalents_accepted(self) -> None:
        """role=main / role=navigation should satisfy main/nav requirements."""
        html = """<html lang="en"><head><title>T</title></head>
        <body>
          <div role="banner"><div role="navigation"><a href="/">H</a></div></div>
          <div role="main"><h1>Hi</h1></div>
          <div role="contentinfo">footer</div>
        </body></html>"""
        report = check_html(html, file_path="roles.html")
        lm_findings = [f for f in report.findings if f.check == "landmark-missing"]
        flagged_elements = {f.message for f in lm_findings}
        # main and nav satisfied via roles
        assert not any("main" in m for m in flagged_elements)
        assert not any("nav" in m for m in flagged_elements)


# ---------------------------------------------------------------------------
# Visual QA — viewport meta
# ---------------------------------------------------------------------------

class TestViewportMeta:
    def test_missing_viewport_flagged(self) -> None:
        report = check_html(BAD_HTML_MISSING_VIEWPORT, file_path="no_vp.html")
        assert "viewport-meta" in warning_ids(report)

    def test_present_viewport_not_flagged(self) -> None:
        report = check_html(GOOD_HTML, file_path="good.html")
        assert "viewport-meta" not in warning_ids(report)


# ---------------------------------------------------------------------------
# Visual QA — color contrast heuristic
# ---------------------------------------------------------------------------

class TestColorContrast:
    def test_white_on_white_flagged(self) -> None:
        report = check_html(BAD_HTML_LOW_CONTRAST, file_path="contrast.html")
        assert "color-contrast" in warning_ids(report)

    def test_good_contrast_not_flagged(self) -> None:
        html = """<html lang="en"><head><title>T</title></head>
        <body><main><h1>H</h1>
        <p style="color:black;background:white">Fine</p>
        </main></body></html>"""
        report = check_html(html, file_path="good_contrast.html")
        assert "color-contrast" not in warning_ids(report)


# ---------------------------------------------------------------------------
# Report data model
# ---------------------------------------------------------------------------

class TestReportModel:
    def test_finding_dataclass(self) -> None:
        f = Finding(check="test", severity="error", message="oops")
        assert f.check == "test"
        assert f.severity == "error"
        assert f.element is None

    def test_report_passed_no_errors(self) -> None:
        r = A11yReport(file_path="x.html")
        r.findings.append(Finding("vp", "warning", "w"))
        assert r.passed  # warnings don't fail

    def test_report_failed_with_error(self) -> None:
        r = A11yReport(file_path="x.html")
        r.findings.append(Finding("lang-attribute", "error", "missing lang"))
        assert not r.passed

    def test_summary_fail_prefix(self) -> None:
        r = A11yReport(file_path="x.html")
        r.findings.append(Finding("lang-attribute", "error", "missing lang"))
        assert r.summary().startswith("FAIL")

    def test_as_dict_keys(self) -> None:
        r = A11yReport(file_path="x.html")
        d = r.as_dict()
        assert set(d.keys()) >= {"file", "passed", "errors", "warnings", "findings"}
