"""Operator workflow contracts for FreeLLMAPI upstream admission."""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest
import scripts.freellmapi_upstream_admission as updater
from scripts.freellmapi_upstream_admission import (
    FreeLLMAPIUpdateError,
    FreeLLMAPIUpdateFault,
    GitHubCLIClient,
    fetch_candidate_lock,
    main,
    validate_existing_lock,
    write_candidate_lock,
)

_ROOT = Path(__file__).resolve().parents[2]
_COMMIT = "4" * 40
_TAG = "v0.11.1"
_TREE = "5" * 40
_REPO_API = "repos/tashfeenahmed/freellmapi"
_EXPORTS = (
    "reliabilityPosterior",
    "expectedReliability",
    "speedScore",
    "headroomFactor",
    "rateWindowHeadroomFactor",
    "rateLimitFactor",
)


def _archive() -> bytes:
    files = {
        "LICENSE": (
            b"MIT License\nPermission is hereby granted, free of charge, to any person "
            b"obtaining a copy.\nTHE SOFTWARE IS PROVIDED \"AS IS\".\n"
        ),
        "package-lock.json": json.dumps(
            {
                "name": "@freellmapi/monorepo",
                "lockfileVersion": 3,
                "packages": {},
            }
        ).encode(),
        "server/src/services/scoring.ts": "\n".join(
            f"export function {name}() {{ return 1; }}" for name in _EXPORTS
        ).encode(),
    }
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        for relative, body in files.items():
            info = tarfile.TarInfo(f"freellmapi-release/{relative}")
            info.size = len(body)
            archive.addfile(info, io.BytesIO(body))
    return stream.getvalue()


def _json_payloads(*, annotated: bool = False) -> dict[str, dict[str, object]]:
    tag_object = {"type": "tag", "sha": "a" * 40} if annotated else {"type": "commit", "sha": _COMMIT}
    payloads: dict[str, dict[str, object]] = {
        _REPO_API: {
            "id": 8675309,
            "full_name": "tashfeenahmed/freellmapi",
            "archived": False,
        },
        f"{_REPO_API}/releases/tags/{_TAG}": {
            "id": 112233,
            "tag_name": _TAG,
            "draft": False,
            "prerelease": False,
            "published_at": "2026-09-20T15:14:00Z",
        },
        f"{_REPO_API}/git/ref/tags/{_TAG}": {
            "ref": f"refs/tags/{_TAG}",
            "object": tag_object,
        },
        f"{_REPO_API}/commits/{_COMMIT}": {
            "sha": _COMMIT,
            "commit": {
                "tree": {"sha": _TREE},
                "verification": {
                    "verified": True,
                    "reason": "valid",
                    "signature": "signed",
                    "payload": f"tree {_TREE}\nparent {'6' * 40}\n",
                    "verified_at": "2026-09-20T15:12:00Z",
                },
            },
        },
    }
    if annotated:
        payloads[f"{_REPO_API}/git/tags/{'a' * 40}"] = {
            "object": {"type": "commit", "sha": _COMMIT}
        }
    return payloads


class _FakeClient:
    def __init__(self, *, annotated: bool = False) -> None:
        self.json_payloads = _json_payloads(annotated=annotated)
        self.calls: list[tuple[str, str]] = []

    def get_json(self, endpoint: str) -> dict[str, object]:
        self.calls.append(("json", endpoint))
        return self.json_payloads[endpoint]

    def get_bytes(self, endpoint: str) -> bytes:
        self.calls.append(("bytes", endpoint))
        assert endpoint == f"{_REPO_API}/tarball/{_COMMIT}"
        return _archive()


def _copy_artifacts(repository_root: Path) -> None:
    source = _ROOT / "src/general_ludd/models/vendor/freellmapi"
    destination = repository_root / "src/general_ludd/models/vendor/freellmapi"
    destination.mkdir(parents=True)
    for name in ("scoring_kernel.json", "scoring_kernel.js"):
        (destination / name).write_bytes((source / name).read_bytes())


def test_fetch_candidate_uses_only_fixed_repository_endpoints() -> None:
    client = _FakeClient()

    lock = fetch_candidate_lock(
        expected_tag=_TAG,
        expected_commit=_COMMIT,
        client=client,
        repository_root=_ROOT,
    )

    assert lock["decision"]["runtime_admitted"] is False
    assert client.calls == [
        ("json", _REPO_API),
        ("json", f"{_REPO_API}/releases/tags/{_TAG}"),
        ("json", f"{_REPO_API}/git/ref/tags/{_TAG}"),
        ("json", f"{_REPO_API}/commits/{_COMMIT}"),
        ("bytes", f"{_REPO_API}/tarball/{_COMMIT}"),
    ]


def test_fetch_candidate_resolves_one_annotated_tag_without_trusting_tag_text() -> None:
    client = _FakeClient(annotated=True)

    lock = fetch_candidate_lock(
        expected_tag=_TAG,
        expected_commit=_COMMIT,
        client=client,
        repository_root=_ROOT,
    )

    assert lock["upstream"]["commit"] == _COMMIT
    assert ("json", f"{_REPO_API}/git/tags/{'a' * 40}") in client.calls


def test_unsupported_tag_indirection_is_content_free() -> None:
    client = _FakeClient(annotated=True)
    client.json_payloads[f"{_REPO_API}/git/tags/{'a' * 40}"] = {
        "object": {"type": "blob", "sha": "b" * 40}
    }

    with pytest.raises(FreeLLMAPIUpdateError) as caught:
        fetch_candidate_lock(
            expected_tag=_TAG,
            expected_commit=_COMMIT,
            client=client,
            repository_root=_ROOT,
        )

    assert caught.value.fault is FreeLLMAPIUpdateFault.TAG_RESOLUTION
    assert str(caught.value) == "tag_resolution"


def test_lock_write_is_atomic_and_offline_validation_binds_current_artifact(
    tmp_path: Path,
) -> None:
    lock = fetch_candidate_lock(
        expected_tag=_TAG,
        expected_commit=_COMMIT,
        client=_FakeClient(),
        repository_root=_ROOT,
    )
    output = tmp_path / "candidate.json"

    write_candidate_lock(output, lock)
    validated = validate_existing_lock(
        output,
        expected_tag=_TAG,
        expected_commit=_COMMIT,
        repository_root=_ROOT,
    )

    assert validated["candidate_id"] == lock["candidate_id"]
    assert list(tmp_path.glob(".candidate.json.*")) == []


def test_cli_validate_is_read_only_and_emits_content_free_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _copy_artifacts(tmp_path)
    lock = fetch_candidate_lock(
        expected_tag=_TAG,
        expected_commit=_COMMIT,
        client=_FakeClient(),
        repository_root=tmp_path,
    )
    output = tmp_path / "config/freellmapi/candidate.json"
    write_candidate_lock(output, lock)
    before = output.read_bytes()

    result = main(
        [
            "--mode",
            "validate",
            "--tag",
            _TAG,
            "--commit",
            _COMMIT,
            "--output",
            str(output),
            "--repository-root",
            str(tmp_path),
        ]
    )

    assert result == 0
    assert output.read_bytes() == before
    summary = json.loads(capsys.readouterr().out)
    assert summary == {
        "candidate_id": lock["candidate_id"],
        "commit": _COMMIT,
        "mode": "validate",
        "runtime_admitted": False,
        "state": "pending_frozen_delta",
        "tag": _TAG,
    }


def test_cli_refresh_writes_only_after_complete_validation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _copy_artifacts(tmp_path)
    output = tmp_path / "config/freellmapi/candidate.json"

    result = main(
        [
            "--mode",
            "refresh",
            "--tag",
            _TAG,
            "--commit",
            _COMMIT,
            "--output",
            str(output),
            "--repository-root",
            str(tmp_path),
        ],
        client=_FakeClient(),
    )

    assert result == 0
    assert output.is_file()
    assert json.loads(capsys.readouterr().out)["mode"] == "refresh"


def test_tracked_v0111_candidate_validates_offline_and_remains_non_runnable() -> None:
    lock = validate_existing_lock(
        _ROOT / "config/freellmapi/upstream_candidate.json",
        expected_tag="v0.11.1",
        expected_commit="4191d8e7abef39fcd93fab009123467036f39750",
        repository_root=_ROOT,
    )

    assert lock["candidate_id"] == (
        "sha256:69d63b09199c37f38c02c711559b15e0fd5dc5ecc0e64e2d94c597dc5e5d3998"
    )
    assert lock["decision"]["runtime_admitted"] is False


def test_cli_refresh_rejects_output_outside_repo_config_or_namespaced_tmp(
    tmp_path: Path,
) -> None:
    output = tmp_path / "outside.json"

    with pytest.raises(FreeLLMAPIUpdateError) as caught:
        main(
            [
                "--mode",
                "refresh",
                "--tag",
                _TAG,
                "--commit",
                _COMMIT,
                "--output",
                str(output),
                "--repository-root",
                str(_ROOT),
            ],
            client=_FakeClient(),
        )

    assert caught.value.fault is FreeLLMAPIUpdateFault.INPUT
    assert not output.exists()


def test_github_cli_failure_never_echoes_command_output(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Result:
        returncode = 1
        stdout = b"provider-secret"
        stderr = b"credential-secret"

    monkeypatch.setattr("scripts.freellmapi_upstream_admission.subprocess.run", lambda *args, **kwargs: _Result())

    with pytest.raises(FreeLLMAPIUpdateError) as caught:
        GitHubCLIClient().get_json(_REPO_API)

    assert caught.value.fault is FreeLLMAPIUpdateFault.GITHUB_API
    assert "secret" not in str(caught.value)


def test_github_cli_successfully_decodes_objects_and_bounded_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Result:
        returncode = 0
        stdout = b'{"id": 42}'

    monkeypatch.setattr(updater.subprocess, "run", lambda *args, **kwargs: _Result())
    client = GitHubCLIClient()

    assert client.get_json(_REPO_API) == {"id": 42}
    assert client.get_bytes(_REPO_API) == b'{"id": 42}'


@pytest.mark.parametrize("raw", [b"", b"{", b"[]"])
def test_github_cli_rejects_invalid_json_shapes_without_content(
    monkeypatch: pytest.MonkeyPatch, raw: bytes
) -> None:
    class _Result:
        returncode = 0
        stdout = raw

    monkeypatch.setattr(updater.subprocess, "run", lambda *args, **kwargs: _Result())

    with pytest.raises(FreeLLMAPIUpdateError) as caught:
        GitHubCLIClient().get_json(_REPO_API)

    assert caught.value.fault is FreeLLMAPIUpdateFault.GITHUB_API


def test_github_cli_rejects_transport_exception_and_oversized_responses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: object, **kwargs: object) -> object:
        raise OSError("secret transport detail")

    monkeypatch.setattr(updater.subprocess, "run", fail)
    with pytest.raises(FreeLLMAPIUpdateError) as transport:
        GitHubCLIClient().get_bytes(_REPO_API)
    assert transport.value.fault is FreeLLMAPIUpdateFault.GITHUB_API

    class _Result:
        returncode = 0
        stdout = b"large"

    monkeypatch.setattr(updater.subprocess, "run", lambda *args, **kwargs: _Result())
    monkeypatch.setattr(updater, "_MAX_ARCHIVE_RESPONSE_BYTES", 1)
    with pytest.raises(FreeLLMAPIUpdateError) as oversized:
        GitHubCLIClient().get_bytes(_REPO_API)
    assert oversized.value.fault is FreeLLMAPIUpdateFault.GITHUB_API


def test_tag_resolution_rejects_wrong_ref_missing_object_bad_sha_and_cycle() -> None:
    for tag_ref in (
        {"ref": "refs/tags/wrong", "object": {"type": "commit", "sha": _COMMIT}},
        {"ref": f"refs/tags/{_TAG}", "object": None},
        {"ref": f"refs/tags/{_TAG}", "object": {"type": "commit", "sha": "bad"}},
    ):
        client = _FakeClient()
        client.json_payloads[f"{_REPO_API}/git/ref/tags/{_TAG}"] = tag_ref
        with pytest.raises(FreeLLMAPIUpdateError) as caught:
            fetch_candidate_lock(
                expected_tag=_TAG,
                expected_commit=_COMMIT,
                client=client,
                repository_root=_ROOT,
            )
        assert caught.value.fault is FreeLLMAPIUpdateFault.TAG_RESOLUTION

    client = _FakeClient(annotated=True)
    client.json_payloads[f"{_REPO_API}/git/tags/{'a' * 40}"] = {
        "object": {"type": "tag", "sha": "a" * 40}
    }
    with pytest.raises(FreeLLMAPIUpdateError) as cycle:
        fetch_candidate_lock(
            expected_tag=_TAG,
            expected_commit=_COMMIT,
            client=client,
            repository_root=_ROOT,
        )
    assert cycle.value.fault is FreeLLMAPIUpdateFault.TAG_RESOLUTION


@pytest.mark.parametrize(
    ("tag", "commit"),
    [("v0.11.1-rc.1", _COMMIT), (_TAG, "short")],
)
def test_fetch_rejects_invalid_input_before_any_network_call(tag: str, commit: str) -> None:
    client = _FakeClient()

    with pytest.raises(FreeLLMAPIUpdateError) as caught:
        fetch_candidate_lock(
            expected_tag=tag,
            expected_commit=commit,
            client=client,
            repository_root=_ROOT,
        )

    assert caught.value.fault is FreeLLMAPIUpdateFault.INPUT
    assert client.calls == []


def test_artifact_and_candidate_lock_io_failures_are_typed(tmp_path: Path) -> None:
    with pytest.raises(FreeLLMAPIUpdateError) as artifacts:
        fetch_candidate_lock(
            expected_tag=_TAG,
            expected_commit=_COMMIT,
            client=_FakeClient(),
            repository_root=tmp_path,
        )
    assert artifacts.value.fault is FreeLLMAPIUpdateFault.LOCK_IO

    missing = tmp_path / "missing.json"
    with pytest.raises(FreeLLMAPIUpdateError) as missing_error:
        validate_existing_lock(
            missing,
            expected_tag=_TAG,
            expected_commit=_COMMIT,
            repository_root=_ROOT,
        )
    assert missing_error.value.fault is FreeLLMAPIUpdateFault.LOCK_IO

    for index, body in enumerate((b"", b"{", b"[]")):
        invalid = tmp_path / f"invalid-{index}.json"
        invalid.write_bytes(body)
        with pytest.raises(FreeLLMAPIUpdateError) as invalid_error:
            validate_existing_lock(
                invalid,
                expected_tag=_TAG,
                expected_commit=_COMMIT,
                repository_root=_ROOT,
            )
        assert invalid_error.value.fault is FreeLLMAPIUpdateFault.LOCK_INVALID


def test_candidate_lock_write_rejects_oversize_and_non_json_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(updater, "_MAX_LOCK_BYTES", 1)
    with pytest.raises(FreeLLMAPIUpdateError) as oversized:
        write_candidate_lock(tmp_path / "large.json", {"value": "large"})
    assert oversized.value.fault is FreeLLMAPIUpdateFault.LOCK_INVALID

    monkeypatch.setattr(updater, "_MAX_LOCK_BYTES", 1024)
    with pytest.raises(FreeLLMAPIUpdateError) as invalid:
        write_candidate_lock(tmp_path / "invalid.json", {"value": {object()}})
    assert invalid.value.fault is FreeLLMAPIUpdateFault.LOCK_IO


def test_namespaced_tmp_output_is_allowed_without_broad_tmp_access() -> None:
    allowed = updater._safe_output_path(
        Path("/tmp/gludd-freellmapi-admission/candidate.json"), _ROOT
    )
    assert allowed.name == "candidate.json"

    with pytest.raises(FreeLLMAPIUpdateError) as broad:
        updater._safe_output_path(Path("/tmp/candidate.json"), _ROOT)
    assert broad.value.fault is FreeLLMAPIUpdateFault.INPUT


def test_entrypoint_converts_typed_errors_to_content_free_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail() -> int:
        raise FreeLLMAPIUpdateError(FreeLLMAPIUpdateFault.INPUT)

    monkeypatch.setattr(updater, "main", fail)

    assert updater._entrypoint() == 2
    assert json.loads(capsys.readouterr().err) == {
        "fault": "input_invalid",
        "ok": False,
    }


def test_make_target_exposes_serial_universal_update_contract() -> None:
    makefile = (_ROOT / "Makefile").read_text(encoding="utf-8")
    target = makefile.split("\nfreellmapi-upstream-admission:", 1)[1].split("\n\n", 1)[0]
    assert "scripts/freellmapi_upstream_admission.py" in target
    assert "FREELLMAPI_ADMISSION_LIVE" in target
    assert "self-improve" not in target

    contract = json.loads(
        (_ROOT / "config/make_target_contract.json").read_text(encoding="utf-8")
    )
    entry = next(
        item
        for item in contract["targets"]
        if item["name"] == "freellmapi-upstream-admission"
    )
    assert entry["make_variables"] == [
        "FREELLMAPI_ADMISSION_TAG",
        "FREELLMAPI_ADMISSION_COMMIT",
        "FREELLMAPI_ADMISSION_LIVE",
        "FREELLMAPI_ADMISSION_OUTPUT",
    ]
    assert entry["behavior"] == (
        "make freellmapi-upstream-admission "
        "FREELLMAPI_ADMISSION_TAG=v0.11.1 "
        "FREELLMAPI_ADMISSION_COMMIT=4191d8e7abef39fcd93fab009123467036f39750 "
        "FREELLMAPI_ADMISSION_LIVE=0 "
        "FREELLMAPI_ADMISSION_OUTPUT=config/freellmapi/upstream_candidate.json"
    )
