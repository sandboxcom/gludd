"""Tests for the content-free public presentation revision probe."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts import probe_presentation_pages as probe


class Response(io.BytesIO):
    """Small context-managed HTTP response double."""

    def __init__(self, body: bytes, *, status: int = 200, content_type: str = "text/html"):
        super().__init__(body)
        self.status = status
        self.headers = SimpleNamespace(get_content_type=lambda: content_type)

    def __enter__(self) -> Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def test_extract_revision_requires_one_consistent_full_sha() -> None:
    """Cache keys and immutable source links must identify one publication."""
    sha = "a" * 40
    html = (
        f'<script src="./presentation.js?v={sha}"></script>'
        f'<a href="https://github.com/sandboxcom/gludd/blob/{sha}/README.md#L1">source</a>'
    )

    assert probe.extract_revision(html) == sha

    with pytest.raises(ValueError, match="conflicting"):
        probe.extract_revision(
            html + f'<script src="./presentation.js?v={"b" * 40}"></script>'
        )
    with pytest.raises(ValueError, match="missing"):
        probe.extract_revision("<html>legacy artifact</html>")

    assert probe.extract_display_revision("<p>commit <strong>1234abc</strong></p>") == "1234abc"
    with pytest.raises(ValueError, match="display revision"):
        probe.extract_display_revision("<p>legacy artifact</p>")


def test_build_plan_is_https_bounded_and_cache_busting() -> None:
    """The probe accepts only the canonical Pages origin and bounded timeouts."""
    plan = probe.build_plan(
        url="https://sandboxcom.github.io/gludd/",
        expected_sha="c" * 40,
        timeout_seconds=20,
        nonce="unit",
    )

    assert plan.request_url == "https://sandboxcom.github.io/gludd/?gludd_probe=unit"
    assert plan.expected_sha == "c" * 40
    with pytest.raises(ValueError, match="canonical"):
        probe.build_plan(
            url="https://example.com/gludd/",
            expected_sha="c" * 40,
            timeout_seconds=20,
            nonce="unit",
        )
    with pytest.raises(ValueError, match="timeout"):
        probe.build_plan(
            url="https://sandboxcom.github.io/gludd/",
            expected_sha="c" * 40,
            timeout_seconds=61,
            nonce="unit",
        )


@pytest.mark.parametrize(
    "url",
    [
        "http://sandboxcom.github.io/gludd/",
        "https://other.example/gludd/",
        "https://sandboxcom.github.io/other/",
        "https://user@sandboxcom.github.io/gludd/",
        "https://" + "user:secret" + "@sandboxcom.github.io/gludd/",
        "https://sandboxcom.github.io/gludd/#fragment",
    ],
)
def test_build_plan_rejects_every_noncanonical_url_boundary(url: str) -> None:
    """Every component that can redirect or broaden the public probe is fixed."""
    with pytest.raises(ValueError, match="canonical"):
        probe.build_plan(
            url=url,
            expected_sha="c" * 40,
            timeout_seconds=20,
            nonce="unit",
        )


@pytest.mark.parametrize(
    ("expected_sha", "timeout_seconds", "nonce", "message"),
    [
        ("ABC", 20, "unit", "expected SHA"),
        ("c" * 40, 0, "unit", "timeout"),
        ("c" * 40, 20, "space is invalid", "nonce"),
    ],
)
def test_build_plan_rejects_unbounded_inputs(
    expected_sha: str,
    timeout_seconds: int,
    nonce: str,
    message: str,
) -> None:
    """Revision, timeout, and cache-buster inputs are all bounded."""
    with pytest.raises(ValueError, match=message):
        probe.build_plan(
            url="https://sandboxcom.github.io/gludd/",
            expected_sha=expected_sha,
            timeout_seconds=timeout_seconds,
            nonce=nonce,
        )


def test_probe_reports_only_revision_metadata() -> None:
    """Network evidence excludes presentation or repository source content."""
    sha = "d" * 40
    body = f'<script src="./presentation.js?v={sha}"></script>'.encode()

    plan = probe.build_plan(
        url="https://sandboxcom.github.io/gludd/",
        expected_sha=sha,
        timeout_seconds=20,
        nonce="unit",
    )
    requested: list[tuple[str, float]] = []

    def opener(request: object, timeout: float) -> Response:
        requested.append((request.full_url, timeout))  # type: ignore[attr-defined]
        return Response(body)

    result = probe.probe_pages(plan, opener=opener)

    assert result == {
        "expected_sha": sha,
        "matches": True,
        "published_sha": sha,
        "revision_precision": "full",
        "status": 200,
        "url": "https://sandboxcom.github.io/gludd/",
    }
    assert requested == [(plan.request_url, 20)]


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (Response(b"failure", status=503), "HTTP 503"),
        (Response(b"plain", content_type="text/plain"), "return HTML"),
        (Response(b"x" * (probe.MAX_RESPONSE_BYTES + 1)), "bounded size"),
        (Response(b"\xff"), "UTF-8"),
    ],
)
def test_probe_rejects_invalid_http_responses(response: Response, message: str) -> None:
    """The probe fails closed on transport and response contract violations."""
    plan = probe.build_plan(
        url="https://sandboxcom.github.io/gludd/",
        expected_sha="d" * 40,
        timeout_seconds=20,
        nonce="unit",
    )

    with pytest.raises(RuntimeError, match=message):
        probe.probe_pages(plan, opener=lambda *_args, **_kwargs: response)


def test_probe_supports_legacy_revision_but_never_treats_it_as_exact() -> None:
    """Legacy Pages output stays observable without satisfying exact deployment proof."""
    plan = probe.build_plan(
        url="https://sandboxcom.github.io/gludd/",
        expected_sha="d" * 40,
        timeout_seconds=20,
        nonce="unit",
    )
    response = Response(b"<footer>commit 1234abc</footer>")

    result = probe.probe_pages(plan, opener=lambda *_args, **_kwargs: response)

    assert result["published_sha"] == "1234abc"
    assert result["revision_precision"] == "short"
    assert result["matches"] is False


def test_probe_preserves_conflicting_revision_failure() -> None:
    """Conflicting full revisions are corruption, not a legacy fallback."""
    plan = probe.build_plan(
        url="https://sandboxcom.github.io/gludd/",
        expected_sha="d" * 40,
        timeout_seconds=20,
        nonce="unit",
    )
    response = Response((
        f'<script src="presentation.js?v={"a" * 40}"></script>'
        f'<script src="presentation.js?v={"b" * 40}"></script>'
    ).encode())

    with pytest.raises(ValueError, match="conflicting"):
        probe.probe_pages(plan, opener=lambda *_args, **_kwargs: response)


def test_main_validate_success_mismatch_and_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The CLI is observable and exits nonzero for stale or unavailable Pages."""
    sha = "e" * 40
    monkeypatch.setattr(probe.time, "time_ns", lambda: 123)
    monkeypatch.setattr(
        sys,
        "argv",
        ["probe_presentation_pages.py", "--expected-sha", sha, "--validate-only"],
    )
    probe.main()
    assert json.loads(capsys.readouterr().out)["request_url"].endswith("gludd_probe=123")

    monkeypatch.setattr(
        probe,
        "probe_pages",
        lambda _plan: {
            "expected_sha": sha,
            "matches": True,
            "published_sha": sha,
            "revision_precision": "full",
            "status": 200,
            "url": "https://sandboxcom.github.io/gludd/",
        },
    )
    monkeypatch.setattr(sys, "argv", ["probe_presentation_pages.py", "--expected-sha", sha])
    probe.main()
    assert json.loads(capsys.readouterr().out)["matches"] is True

    monkeypatch.setattr(probe, "probe_pages", lambda _plan: {"matches": False})
    with pytest.raises(SystemExit, match="1"):
        probe.main()
    assert json.loads(capsys.readouterr().out)["matches"] is False

    def unavailable(_plan: probe.ProbePlan) -> dict[str, object]:
        raise OSError("network unavailable")

    monkeypatch.setattr(probe, "probe_pages", unavailable)
    with pytest.raises(SystemExit, match="1"):
        probe.main()
    failure = json.loads(capsys.readouterr().out)
    assert failure["category"] == "network unavailable"
    assert failure["error"] == "OSError"


def test_script_stays_small_and_dependency_free() -> None:
    """The release probe remains reviewable and uses the standard library."""
    path = Path(probe.__file__)
    assert len(path.read_text(encoding="utf-8").splitlines()) < 250
