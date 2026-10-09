"""Tests for the pinned, project-local Pants launcher bootstrap."""

from __future__ import annotations

import hashlib
import io
import json
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts import bootstrap_pants_launcher as bootstrap
from scripts.bootstrap_pants_launcher import (
    LauncherAsset,
    acquire_launcher,
    asset_key,
    parse_checksum_sidecar,
)


def _asset(payload: bytes = b"pinned pants launcher") -> LauncherAsset:
    return LauncherAsset(
        version="0.13.2",
        filename="scie-pants-macos-aarch64",
        sha256=hashlib.sha256(payload).hexdigest(),
    )


@pytest.mark.parametrize(
    ("system", "machine", "expected"),
    [
        ("Darwin", "arm64", "macos-aarch64"),
        ("Linux", "aarch64", "linux-aarch64"),
        ("Linux", "x86_64", "linux-x86_64"),
        ("Linux", "amd64", "linux-x86_64"),
    ],
)
def test_asset_key_normalizes_supported_platforms(
    system: str,
    machine: str,
    expected: str,
) -> None:
    assert asset_key(system=system, machine=machine) == expected


def test_asset_key_rejects_unsupported_platform() -> None:
    with pytest.raises(ValueError, match="unsupported Pants launcher platform"):
        asset_key(system="Windows", machine="AMD64")


def test_checksum_sidecar_requires_exact_asset_name() -> None:
    digest = "a" * 64
    assert (
        parse_checksum_sidecar(
            f"{digest}  scie-pants-macos-aarch64\n".encode(),
            filename="scie-pants-macos-aarch64",
        )
        == digest
    )
    with pytest.raises(ValueError, match="checksum sidecar"):
        parse_checksum_sidecar(
            f"{digest}  another-file\n".encode(),
            filename="scie-pants-macos-aarch64",
        )


def test_acquire_launcher_installs_exact_executable_atomically(tmp_path: Path) -> None:
    payload = b"pinned pants launcher"
    destination = tmp_path / "bin" / "pants"
    seen: list[str] = []

    def download(url: str) -> bytes:
        seen.append(url)
        return payload

    result = acquire_launcher(
        destination=destination,
        asset=_asset(payload),
        validate_only=False,
        downloader=download,
    )

    assert result == "installed"
    assert destination.read_bytes() == payload
    assert destination.stat().st_mode & stat.S_IXUSR
    assert seen == [_asset(payload).download_url]
    assert list(destination.parent.glob("*.tmp")) == []


def test_acquire_launcher_reuses_only_exact_existing_binary(tmp_path: Path) -> None:
    payload = b"pinned pants launcher"
    destination = tmp_path / "pants"
    destination.write_bytes(payload)
    destination.chmod(0o755)

    result = acquire_launcher(
        destination=destination,
        asset=_asset(payload),
        validate_only=False,
        downloader=lambda _url: pytest.fail("exact binary must not be downloaded"),
    )

    assert result == "reused"


def test_acquire_launcher_fails_closed_on_digest_mismatch(tmp_path: Path) -> None:
    destination = tmp_path / "pants"

    with pytest.raises(ValueError, match="digest mismatch"):
        acquire_launcher(
            destination=destination,
            asset=_asset(),
            validate_only=False,
            downloader=lambda _url: b"substituted bytes",
        )

    assert not destination.exists()


def test_acquire_launcher_validate_only_is_network_and_write_free(tmp_path: Path) -> None:
    destination = tmp_path / "pants"

    result = acquire_launcher(
        destination=destination,
        asset=_asset(),
        validate_only=True,
        downloader=lambda _url: pytest.fail("validate-only must be network-free"),
    )

    assert result == "planned"
    assert not destination.exists()


class _Response(io.BytesIO):
    """Minimal bounded urllib response used by acquisition tests."""

    def __init__(self, payload: bytes, *, status: int = 200) -> None:
        super().__init__(payload)
        self.status = status

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def _config_payload() -> dict[str, object]:
    digest = "a" * 64
    return {
        "schema_version": 1,
        "launcher_version": "0.13.2",
        "assets": {
            key: {"filename": f"scie-pants-{key}", "sha256": digest}
            for key in sorted(bootstrap.SUPPORTED_ASSETS)
        },
    }


def test_download_is_https_host_bounded_and_observable(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        bootstrap.urllib.request,
        "urlopen",
        lambda _request, timeout: _Response(b"asset") if timeout == 30 else None,
    )

    assert bootstrap._download(
        "https://github.com/pantsbuild/scie-pants/asset",
        maximum_bytes=5,
    ) == b"asset"
    assert "PANTS-LAUNCHER-DOWNLOAD bytes=5" in capsys.readouterr().out

    with pytest.raises(ValueError, match="exact GitHub HTTPS host"):
        bootstrap._download("http://example.com/asset", maximum_bytes=5)


@pytest.mark.parametrize(
    ("payload", "status", "maximum", "message"),
    (
        (b"", 200, 5, "empty"),
        (b"123456", 200, 5, "exceeds"),
        (b"asset", 503, 5, "HTTP 503"),
    ),
)
def test_download_fails_closed_on_invalid_response(
    monkeypatch: pytest.MonkeyPatch,
    payload: bytes,
    status: int,
    maximum: int,
    message: str,
) -> None:
    monkeypatch.setattr(
        bootstrap.urllib.request,
        "urlopen",
        lambda _request, timeout: _Response(payload, status=status)
        if timeout == 30
        else None,
    )
    with pytest.raises(RuntimeError, match=message):
        bootstrap._download(
            "https://github.com/pantsbuild/scie-pants/asset",
            maximum_bytes=maximum,
        )


@pytest.mark.parametrize(
    "payload",
    (b"", b"\xff", b"a" * 64 + b"  file\n" + b"b" * 64 + b"  file\n"),
)
def test_checksum_sidecar_rejects_malformed_bytes(payload: bytes) -> None:
    with pytest.raises(ValueError, match="checksum sidecar"):
        parse_checksum_sidecar(payload, filename="file")


def test_acquire_launcher_validates_existing_and_rejects_unsafe_inputs(
    tmp_path: Path,
) -> None:
    payload = b"pinned pants launcher"
    destination = tmp_path / "pants"
    destination.write_bytes(payload)
    destination.chmod(0o600)
    assert acquire_launcher(
        destination=destination,
        asset=_asset(payload),
        validate_only=True,
    ) == "validated"

    unsafe = tmp_path / "linked"
    unsafe.symlink_to(destination)
    with pytest.raises(ValueError, match="symlink"):
        acquire_launcher(destination=unsafe, asset=_asset(payload), validate_only=True)

    for bad_asset, message in (
        (LauncherAsset("latest", _asset().filename, _asset().sha256), "version"),
        (LauncherAsset("0.13.2", _asset().filename, "nope"), "digest"),
        (LauncherAsset("0.13.2", "pants", _asset().sha256), "filename"),
    ):
        with pytest.raises(ValueError, match=message):
            acquire_launcher(
                destination=tmp_path / message,
                asset=bad_asset,
                validate_only=True,
            )


@pytest.mark.parametrize("payload", (b"", b"xxxx"))
def test_acquire_launcher_rejects_empty_or_oversized_payload(
    tmp_path: Path,
    payload: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if payload:
        monkeypatch.setattr(bootstrap, "_MAX_LAUNCHER_BYTES", 3)
    with pytest.raises(ValueError, match="empty or exceeds"):
        acquire_launcher(
            destination=tmp_path / "pants",
            asset=_asset(),
            validate_only=False,
            downloader=lambda _url: payload,
        )


def test_filename_key_and_config_loader_are_exact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = tmp_path / "assets.json"
    config.write_text(json.dumps(_config_payload()), encoding="utf-8")
    monkeypatch.setattr(bootstrap.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(bootstrap.platform, "machine", lambda: "arm64")

    selected = bootstrap.load_asset(config, requested_asset="auto")
    assert selected.filename == "scie-pants-macos-aarch64"
    assert bootstrap.asset_key_from_filename(selected.filename) == "macos-aarch64"
    assert bootstrap.load_asset(
        config,
        requested_asset="linux-x86_64",
    ).filename.endswith("linux-x86_64")

    with pytest.raises(ValueError, match="filename"):
        bootstrap.asset_key_from_filename("pants")
    with pytest.raises(ValueError, match="filename"):
        bootstrap.asset_key_from_filename("scie-pants-windows-x86_64")
    with pytest.raises(ValueError, match="requested"):
        bootstrap.load_asset(config, requested_asset="windows-x86_64")


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (lambda payload: payload.update(extra=True), "schema"),
        (lambda payload: payload.update(schema_version=2), "version"),
        (lambda payload: payload.update(assets={}), "asset set"),
    ),
)
def test_config_loader_rejects_malformed_contract(
    tmp_path: Path,
    mutate: object,
    message: str,
) -> None:
    payload = _config_payload()
    assert callable(mutate)
    mutate(payload)
    config = tmp_path / "assets.json"
    config.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        bootstrap.load_asset(config, requested_asset="macos-aarch64")


def test_config_loader_rejects_unreadable_entry_and_filename_mismatch(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="unreadable"):
        bootstrap.load_asset(tmp_path / "missing.json", requested_asset="macos-aarch64")

    payload = _config_payload()
    assets = payload["assets"]
    assert isinstance(assets, dict)
    assets["macos-aarch64"] = {"filename": "wrong", "sha256": "a" * 64}
    config = tmp_path / "assets.json"
    config.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="filename"):
        bootstrap.load_asset(config, requested_asset="macos-aarch64")


def test_main_covers_metadata_acquisition_and_failure_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asset = _asset()
    args = SimpleNamespace(
        config=tmp_path / "assets.json",
        destination=tmp_path / "pants",
        asset="auto",
        validate_only=1,
        metadata_only=1,
    )
    parser = SimpleNamespace(parse_args=lambda: args)
    monkeypatch.setattr(bootstrap, "_parser", lambda: parser)
    monkeypatch.setattr(bootstrap, "load_asset", lambda *_args, **_kwargs: asset)
    sidecar = f"{asset.sha256}  {asset.filename}\n".encode()
    monkeypatch.setattr(bootstrap, "_download", lambda *_args, **_kwargs: sidecar)
    assert bootstrap.main() == 0

    monkeypatch.setattr(
        bootstrap,
        "_download",
        lambda *_args, **_kwargs: f"{'b' * 64}  {asset.filename}\n".encode(),
    )
    assert bootstrap.main() == 1

    args.metadata_only = 0
    called: list[bool] = []
    monkeypatch.setattr(
        bootstrap,
        "acquire_launcher",
        lambda **kwargs: called.append(kwargs["validate_only"]) or "planned",
    )
    assert bootstrap.main() == 0
    assert called == [True]

    monkeypatch.setattr(
        bootstrap,
        "load_asset",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad config")),
    )
    assert bootstrap.main() == 2
