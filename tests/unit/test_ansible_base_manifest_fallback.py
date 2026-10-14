"""Fail-closed, bounded manifest reads after registry HEAD incompatibility."""

from __future__ import annotations

import hashlib
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request

import pytest
from scripts import ansible_runtime_artifacts as artifacts

_BODY = b'{"schemaVersion":2,"manifests":[]}'
_DIGEST = "sha256:" + hashlib.sha256(_BODY).hexdigest()


class _ManifestResponse:
    """Expose actual response bytes and record the requested read bound."""

    def __init__(self, body: bytes = _BODY, digest: str | None = _DIGEST) -> None:
        self.body = body
        self.headers = {} if digest is None else {"Docker-Content-Digest": digest}
        self.read_sizes: list[int] = []

    def __enter__(self) -> _ManifestResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, size: int) -> bytes:
        self.read_sizes.append(size)
        return self.body[:size]


@pytest.mark.parametrize("status", [404, 405])
@pytest.mark.parametrize("reference", ["stream9", _DIGEST])
def test_manifest_get_fallback_verifies_bytes_at_the_same_reference(
    status: int, reference: str, capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[Request] = []
    response = _ManifestResponse()

    def opener(request: Request, *, timeout: int) -> _ManifestResponse:
        assert timeout == 30
        calls.append(request)
        if request.get_method() == "HEAD":
            raise HTTPError(request.full_url, status, "HEAD rejected", Message(), None)
        return response

    assert artifacts._registry_manifest_digest(reference, opener=opener) == _DIGEST
    assert [request.get_method() for request in calls] == ["HEAD", "GET"]
    assert {request.full_url for request in calls} == {
        f"{artifacts.BASE_IMAGE_MANIFEST_ROOT}/{reference}"
    }
    assert all(request.get_header("Accept") == artifacts.MANIFEST_ACCEPT for request in calls)
    assert response.read_sizes == [1024 * 1024 + 1]
    assert "ANSIBLE_BASE_IMAGE_MANIFEST_GET" in capsys.readouterr().out


@pytest.mark.parametrize("status", [400, 401, 403, 429, 500, 503])
def test_manifest_head_failure_does_not_mask_other_http_errors(status: int) -> None:
    calls: list[Request] = []

    def opener(request: Request, *, timeout: int) -> _ManifestResponse:
        calls.append(request)
        raise HTTPError(request.full_url, status, "registry rejected", Message(), None)

    with pytest.raises(RuntimeError, match=f"HTTP Error {status}"):
        artifacts._registry_manifest_digest(_DIGEST, opener=opener)
    assert [request.get_method() for request in calls] == ["HEAD"]


@pytest.mark.parametrize(
    ("body", "digest", "message"),
    [
        (_BODY, None, "Docker-Content-Digest"),
        (_BODY, "sha256:short", "Docker-Content-Digest"),
        (_BODY, "sha256:" + "a" * 64, "manifest content digest mismatch"),
        (b"", _DIGEST, "manifest body size"),
        (b"x" * (1024 * 1024 + 1), _DIGEST, "manifest body size"),
    ],
    ids=["missing-header", "invalid-header", "wrong-content", "empty-body", "oversized-body"],
)
def test_manifest_get_fallback_rejects_unverifiable_content(
    body: bytes, digest: str | None, message: str,
) -> None:
    calls: list[Request] = []

    def opener(request: Request, *, timeout: int) -> _ManifestResponse:
        calls.append(request)
        if request.get_method() == "HEAD":
            raise HTTPError(request.full_url, 404, "HEAD rejected", Message(), None)
        return _ManifestResponse(body, digest)

    with pytest.raises(RuntimeError, match=message):
        artifacts._registry_manifest_digest(_DIGEST, opener=opener)
    assert [request.get_method() for request in calls] == ["HEAD", "GET"]


@pytest.mark.parametrize("status", [404, 405, 503])
def test_manifest_get_failure_is_terminal_without_retries(status: int) -> None:
    calls: list[Request] = []

    def opener(request: Request, *, timeout: int) -> _ManifestResponse:
        calls.append(request)
        code = 404 if request.get_method() == "HEAD" else status
        raise HTTPError(request.full_url, code, "registry rejected", Message(), None)

    with pytest.raises(RuntimeError, match=f"HTTP Error {status}"):
        artifacts._registry_manifest_digest(_DIGEST, opener=opener)
    assert [request.get_method() for request in calls] == ["HEAD", "GET"]


def test_manifest_get_never_substitutes_a_new_digest_for_a_pinned_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    definition = tmp_path / "execution-environment.yml"
    image = f"{artifacts.BASE_IMAGE_TAG}@sha256:" + "b" * 64
    definition.write_text(f"images:\n  base_image:\n    name: {image}\n", encoding="utf-8")
    before = definition.read_bytes()
    monkeypatch.setattr(artifacts, "DEFINITION", definition)

    def opener(request: Request, *, timeout: int) -> _ManifestResponse:
        if request.get_method() == "HEAD":
            raise HTTPError(request.full_url, 404, "HEAD rejected", Message(), None)
        return _ManifestResponse()

    with pytest.raises(RuntimeError, match="registry manifest identity mismatch"):
        artifacts.check_configured_base_image(opener=opener)
    assert definition.read_bytes() == before


def test_successful_head_does_not_read_manifest_content() -> None:
    response = _ManifestResponse()
    assert artifacts._registry_manifest_digest(
        _DIGEST, opener=lambda *_args, **_kwargs: response,
    ) == _DIGEST
    assert response.read_sizes == []


@pytest.mark.parametrize("reference", ["latest", "../stream9", "sha256:short"])
def test_unsafe_manifest_reference_is_rejected_without_network(reference: str) -> None:
    def opener(*_args: object, **_kwargs: object) -> _ManifestResponse:
        pytest.fail("unsafe reference must never reach the registry")

    with pytest.raises(ValueError, match="unsupported CentOS manifest reference"):
        artifacts._registry_manifest_digest(reference, opener=opener)
