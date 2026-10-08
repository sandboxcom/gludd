"""Native collection artifact verification security and module contracts."""

from __future__ import annotations

import hashlib
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from ansible.module_utils import basic as ansible_basic
from ansible_collections.general_ludd.git_release.plugins.module_utils import provenance
from ansible_collections.general_ludd.git_release.plugins.modules import git_release
from scripts.check_ansible_executable_stubs import scan_collection_tree

from general_ludd.git_release import provenance as core_provenance

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_COLLECTION_ROOT = _REPOSITORY_ROOT / "collections/ansible_collections/general_ludd/git_release"


class _Exit(Exception):
    """Capture a successful Ansible module exit."""


class _Failure(Exception):
    """Capture a failed Ansible module exit."""


class _FakeModule:
    def __init__(self, params: dict[str, Any], *, check_mode: bool = False) -> None:
        self.params = params
        self.check_mode = check_mode
        self.result: dict[str, Any] | None = None
        self.failure: dict[str, Any] | None = None

    def exit_json(self, **kwargs: Any) -> None:
        self.result = kwargs
        raise _Exit

    def fail_json(self, **kwargs: Any) -> None:
        self.failure = kwargs
        raise _Failure


class _ReturningFailureModule(_FakeModule):
    """Model a defensive fake whose fail method unexpectedly returns."""

    def fail_json(self, **kwargs: Any) -> None:
        self.failure = kwargs


def _write_inputs(root: Path) -> tuple[Path, Path, str, str]:
    artifact = root / "dist" / "gludd.whl"
    lock = root / "uv.lock"
    artifact.parent.mkdir()
    artifact.write_bytes((b"verified-release-artifact\n" * 65_537) + b"tail")
    lock.write_bytes(b'version = 1\nrequires-python = ">=3.11"\n')
    return (
        artifact,
        lock,
        hashlib.sha256(artifact.read_bytes()).hexdigest(),
        hashlib.sha256(lock.read_bytes()).hexdigest(),
    )


def _params(root: Path) -> dict[str, str]:
    _artifact, _lock, artifact_digest, lock_digest = _write_inputs(root)
    return {
        "root": str(root),
        "artifact_path": "dist/gludd.whl",
        "artifact_sha256": artifact_digest,
        "lock_path": "uv.lock",
        "lock_sha256": lock_digest,
    }


def test_collection_is_canonical_and_core_is_only_a_compatibility_reexport() -> None:
    assert core_provenance.verify_release_artifact is provenance.verify_release_artifact
    assert core_provenance.verify_provenance is provenance.verify_provenance


def test_exact_sha256_verification_is_bounded_and_streamed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    params = _params(tmp_path)
    requested_sizes: list[int] = []
    real_read = provenance.os.read

    def recording_read(fd: int, count: int) -> bytes:
        requested_sizes.append(count)
        return real_read(fd, count)

    monkeypatch.setattr(provenance.os, "read", recording_read)
    result = provenance.verify_release_artifact(**params)

    assert result["verified"] is True
    assert result["algorithm"] == "sha256"
    assert result["artifact"]["sha256"] == params["artifact_sha256"]
    assert result["dependency_lock"]["sha256"] == params["lock_sha256"]
    assert requested_sizes
    assert max(requested_sizes) == provenance.READ_CHUNK_BYTES == 1_048_576


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("artifact_sha256", "A" * 64, "lowercase SHA-256"),
        ("artifact_sha256", "0" * 63, "lowercase SHA-256"),
        ("lock_sha256", "sha256:" + "0" * 64, "lowercase SHA-256"),
        ("artifact_path", "/etc/passwd", "relative"),
        ("artifact_path", "../outside.whl", "parent traversal"),
        ("lock_path", "nested/../../outside.lock", "parent traversal"),
    ],
)
def test_invalid_digests_and_root_escapes_fail_closed(
    tmp_path: Path,
    field: str,
    value: str,
    message: str,
) -> None:
    params = _params(tmp_path)
    params[field] = value

    with pytest.raises(provenance.ArtifactVerificationError, match=message):
        provenance.verify_release_artifact(**params)


def test_digest_mismatch_fails_closed_without_partial_success(tmp_path: Path) -> None:
    params = _params(tmp_path)
    params["artifact_sha256"] = "0" * 64

    with pytest.raises(provenance.ArtifactVerificationError, match="artifact SHA-256 mismatch"):
        provenance.verify_release_artifact(**params)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("artifact_path", "", "non-empty relative"),
        ("artifact_path", "dist\\gludd.whl", "POSIX"),
        ("root", "relative/root", "absolute"),
        ("root", None, "absolute"),
    ],
)
def test_empty_non_posix_and_invalid_root_inputs_are_rejected(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    params: dict[str, Any] = _params(tmp_path)
    params[field] = value

    with pytest.raises(provenance.ArtifactVerificationError, match=message):
        provenance.verify_release_artifact(**params)


def test_unnormalized_missing_and_unsupported_roots_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    params = _params(tmp_path)
    params["root"] = f"{tmp_path}/"
    with pytest.raises(provenance.ArtifactVerificationError, match="normalized"):
        provenance.verify_release_artifact(**params)

    params["root"] = str(tmp_path / "missing")
    with pytest.raises(provenance.ArtifactVerificationError, match="safe directory"):
        provenance.verify_release_artifact(**params)

    params["root"] = str(tmp_path)
    monkeypatch.delattr(provenance.os, "O_NOFOLLOW")
    with pytest.raises(provenance.ArtifactVerificationError, match="no-follow"):
        provenance.verify_release_artifact(**params)


def test_root_is_opened_component_by_component_without_a_full_path_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    params = _params(tmp_path)
    real_open = provenance.os.open
    full_path_opens: list[object] = []

    def guarded_open(path: object, *args: object, **kwargs: object) -> int:
        if path == str(tmp_path):
            full_path_opens.append(path)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(provenance.os, "open", guarded_open)

    assert provenance.verify_release_artifact(**params)["verified"] is True
    assert full_path_opens == []


@pytest.mark.parametrize("link_kind", ["root", "directory", "artifact", "lock"])
def test_every_symlink_position_is_rejected(tmp_path: Path, link_kind: str) -> None:
    real_root = tmp_path / "real"
    real_root.mkdir()
    params = _params(real_root)

    if link_kind == "root":
        link = tmp_path / "linked-root"
        link.symlink_to(real_root, target_is_directory=True)
        params["root"] = str(link)
    elif link_kind == "directory":
        real_dist = real_root / "real-dist"
        (real_root / "dist").rename(real_dist)
        (real_root / "dist").symlink_to(real_dist, target_is_directory=True)
    elif link_kind == "artifact":
        artifact = real_root / "dist" / "gludd.whl"
        target = real_root / "dist" / "target.whl"
        artifact.rename(target)
        artifact.symlink_to(target)
    else:
        lock = real_root / "uv.lock"
        target = real_root / "target.lock"
        lock.rename(target)
        lock.symlink_to(target)

    with pytest.raises(provenance.ArtifactVerificationError, match="symlink"):
        provenance.verify_release_artifact(**params)


def test_non_regular_and_oversized_inputs_are_rejected(tmp_path: Path) -> None:
    params = _params(tmp_path)
    artifact = tmp_path / "dist" / "gludd.whl"
    artifact.unlink()
    artifact.mkdir()
    with pytest.raises(provenance.ArtifactVerificationError, match="regular file"):
        provenance.verify_release_artifact(**params)

    artifact.rmdir()
    with artifact.open("wb") as stream:
        stream.truncate(provenance.MAX_ARTIFACT_BYTES + 1)
    with pytest.raises(provenance.ArtifactVerificationError, match="2 GiB"):
        provenance.verify_release_artifact(**params)

    artifact.unlink()
    artifact.write_bytes(b"artifact")
    lock = tmp_path / "uv.lock"
    with lock.open("wb") as stream:
        stream.truncate(provenance.MAX_LOCK_BYTES + 1)
    params["artifact_sha256"] = hashlib.sha256(b"artifact").hexdigest()
    with pytest.raises(provenance.ArtifactVerificationError, match="16 MiB"):
        provenance.verify_release_artifact(**params)


def test_pre_and_post_fstat_detects_in_place_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    params = _params(tmp_path)
    real_fstat = provenance.os.fstat
    regular_calls = 0

    def unstable_fstat(fd: int) -> Any:
        nonlocal regular_calls
        value = real_fstat(fd)
        if not provenance.stat.S_ISREG(value.st_mode):
            return value
        regular_calls += 1
        if regular_calls == 2:
            return SimpleNamespace(
                st_mode=value.st_mode,
                st_dev=value.st_dev,
                st_ino=value.st_ino,
                st_nlink=value.st_nlink,
                st_size=value.st_size + 1,
                st_mtime_ns=value.st_mtime_ns,
                st_ctime_ns=value.st_ctime_ns,
            )
        return value

    monkeypatch.setattr(provenance.os, "fstat", unstable_fstat)
    with pytest.raises(provenance.ArtifactVerificationError, match="changed while hashing"):
        provenance.verify_release_artifact(**params)


def test_stream_growth_cannot_cross_the_declared_read_ceiling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "gludd.whl").write_bytes(b"x")
    root_fd = provenance._open_root(str(tmp_path))
    monkeypatch.setattr(provenance.os, "read", lambda _fd, _count: b"xx")
    try:
        with pytest.raises(provenance.ArtifactVerificationError, match="read ceiling"):
            provenance._hash_regular_file(
                root_fd,
                ("dist", "gludd.whl"),
                label="artifact",
                maximum_bytes=1,
                maximum_label="read ceiling",
            )
    finally:
        provenance.os.close(root_fd)


def test_module_normal_and_check_mode_are_identical_read_only_verification(
    tmp_path: Path,
) -> None:
    params = _params(tmp_path)
    normal = _FakeModule(params)
    check = _FakeModule(params, check_mode=True)

    with pytest.raises(_Exit):
        git_release.run(normal)
    with pytest.raises(_Exit):
        git_release.run(check)

    assert normal.failure is None
    assert normal.result == check.result
    assert normal.result is not None
    assert normal.result["changed"] is False
    assert normal.result["result"]["verified"] is True


def test_module_reports_a_bounded_failure_without_secret_or_transport_fields(
    tmp_path: Path,
) -> None:
    params = _params(tmp_path)
    params["lock_sha256"] = "f" * 64
    module = _FakeModule(params)

    with pytest.raises(_Failure):
        git_release.run(module)

    assert module.result is None
    assert module.failure is not None
    assert module.failure["changed"] is False
    assert "dependency lock SHA-256 mismatch" in module.failure["msg"]
    assert not ({"daemon_url", "psk", "timeout", "command"} & module.failure.keys())


def test_module_defensively_returns_after_a_non_raising_failure(tmp_path: Path) -> None:
    params = _params(tmp_path)
    params["artifact_sha256"] = "0" * 64
    module = _ReturningFailureModule(params)

    git_release.run(module)

    assert module.result is None
    assert module.failure is not None


def test_module_main_and_script_entrypoint_build_the_narrow_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    direct_root = tmp_path / "direct"
    direct_root.mkdir()
    direct = _FakeModule(_params(direct_root))
    monkeypatch.setattr(git_release, "AnsibleModule", lambda **_kwargs: direct)
    with pytest.raises(_Exit):
        git_release.main()

    script_root = tmp_path / "script"
    script_root.mkdir()
    script = _FakeModule(_params(script_root))
    monkeypatch.setattr(ansible_basic, "AnsibleModule", lambda **_kwargs: script)
    loaded = sys.modules.pop(git_release.__name__)
    try:
        with pytest.raises(_Exit):
            runpy.run_module(git_release.__name__, run_name="__main__")
    finally:
        sys.modules[git_release.__name__] = loaded

    assert direct.result == script.result


def test_native_module_has_no_transport_subprocess_listener_or_key_surface() -> None:
    source = Path(git_release.__file__).read_text(encoding="utf-8")
    utility_source = Path(provenance.__file__).read_text(encoding="utf-8")
    forbidden = (
        "subprocess",
        "socket",
        "requests",
        "urllib",
        "httpx",
        "GluddClient",
        "daemon_url",
        "private_key",
        "signing_key",
    )

    for token in forbidden:
        assert token not in source
        assert token not in utility_source


def test_git_release_collection_has_zero_executable_stub_findings() -> None:
    assert scan_collection_tree(_COLLECTION_ROOT) == []
