"""Tests for manifest_signer.py — SSH-based MANIFEST.json signing and verification.

Pin behaviour:
  - SignResult / VerifyResult field shapes
  - sign fails when private key is missing
  - sign fails when manifest is missing
  - verify fails when allowed_signers is missing
  - verify fails when sig file is missing
  - make_allowed_signers writes correct format
  - make_allowed_signers is idempotent
  - Happy path with real ssh-keygen in subprocess
  - Timeout propagation
  - Subprocess error propagation
  - Missing ssh-keygen binary

Run: make test-iso TESTFILE='tests/unit/test_manifest_signer.py'
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.runtime.manifest_signer import (
    ManifestSigner,
    SignResult,
    VerifyResult,
)

# ---------------------------------------------------------------
# 1. Dataclass shape tests
# ---------------------------------------------------------------

def test_sign_result_defaults() -> None:
    r = SignResult(success=False, sig_path="/tmp/MANIFEST.json.sig")
    assert r.success is False
    assert r.sig_path == "/tmp/MANIFEST.json.sig"
    assert r.errors == []


def test_verify_result_defaults() -> None:
    r = VerifyResult(success=False, identity="")
    assert r.success is False
    assert r.identity == ""
    assert r.errors == []


# ---------------------------------------------------------------
# 2. Sign failure paths
# ---------------------------------------------------------------

def test_sign_fails_when_key_missing(tmp_path: Path) -> None:
    manifest = tmp_path / "MANIFEST.json"
    manifest.write_text('{"version":"1.0.0"}')
    key = tmp_path / "nonexistent_key"

    signer = ManifestSigner(private_key_path=str(key))
    result = signer.sign(str(manifest))

    assert result.success is False
    assert "not found" in result.errors[0].lower() or "not found" in result.errors[0]
    assert not Path(result.sig_path).exists()


def test_sign_fails_when_manifest_missing(tmp_path: Path) -> None:
    manifest = tmp_path / "gone.json"
    key = tmp_path / "id_ed25519"
    key.write_text("placeholder")

    signer = ManifestSigner(private_key_path=str(key))
    result = signer.sign(str(manifest))

    assert result.success is False
    assert any("not found" in e.lower() for e in result.errors)


@patch("general_ludd.runtime.manifest_signer.subprocess.run")
def test_sign_fails_when_ssh_keygen_errors(mock_run: MagicMock, tmp_path: Path) -> None:
    manifest = tmp_path / "MANIFEST.json"
    manifest.write_text('{"version":"1.0.0"}')
    key = tmp_path / "id_ed25519"
    key.write_text("fake-key-data")

    completed = MagicMock()
    completed.returncode = 1
    completed.stderr = b"Could not load private key\n"
    mock_run.return_value = completed

    signer = ManifestSigner(private_key_path=str(key))
    result = signer.sign(str(manifest))

    assert result.success is False
    assert "ssh-keygen sign exited 1" in result.errors[0]


@patch("general_ludd.runtime.manifest_signer.subprocess.run")
def test_sign_propagates_timeout(mock_run: MagicMock, tmp_path: Path) -> None:
    manifest = tmp_path / "MANIFEST.json"
    manifest.write_text("{}")
    key = tmp_path / "id_ed25519"
    key.write_text("k")

    mock_run.side_effect = subprocess.TimeoutExpired(cmd=["ssh-keygen"], timeout=15)

    signer = ManifestSigner(private_key_path=str(key))
    result = signer.sign(str(manifest))

    assert result.success is False
    assert any("timeout" in e.lower() or "timed out" in e.lower() for e in result.errors)


@patch("general_ludd.runtime.manifest_signer.subprocess.run")
def test_sign_fails_when_ssh_keygen_not_found(mock_run: MagicMock, tmp_path: Path) -> None:
    manifest = tmp_path / "MANIFEST.json"
    manifest.write_text("{}")
    key = tmp_path / "id_ed25519"
    key.write_text("k")

    mock_run.side_effect = FileNotFoundError(2, "No such file or directory", "ssh-keygen")

    signer = ManifestSigner(private_key_path=str(key))
    result = signer.sign(str(manifest))

    assert result.success is False
    assert any("not found" in e.lower() or "failed" in e.lower() for e in result.errors)


# ---------------------------------------------------------------
# 3. Verify failure paths
# ---------------------------------------------------------------

@patch("general_ludd.runtime.manifest_signer.subprocess.run")
def test_verify_fails_when_allowed_signers_missing(mock_run: MagicMock, tmp_path: Path) -> None:
    manifest = tmp_path / "MANIFEST.json"
    manifest.write_text("{}")
    sig = tmp_path / "MANIFEST.json.sig"
    sig.write_bytes(b"sig")
    allowed = tmp_path / "gone_allowed_signers"

    signer = ManifestSigner(allowed_signers_path=str(allowed))
    result = signer.verify(str(manifest), str(sig))

    assert result.success is False
    assert "not found" in result.errors[0].lower()


def test_verify_fails_when_manifest_missing(tmp_path: Path) -> None:
    manifest = tmp_path / "gone.json"
    sig = tmp_path / "gone.json.sig"
    sig.write_bytes(b"sig")
    allowed = tmp_path / "allowed_signers"
    allowed.write_text("release-bundle ssh-ed25519 AAAAC3...")

    signer = ManifestSigner(allowed_signers_path=str(allowed))
    result = signer.verify(str(manifest), str(sig))

    assert result.success is False
    assert any("not found" in e.lower() for e in result.errors)


def test_verify_fails_when_sig_file_missing(tmp_path: Path) -> None:
    manifest = tmp_path / "MANIFEST.json"
    manifest.write_text("{}")
    sig = tmp_path / "gone.sig"
    allowed = tmp_path / "allowed_signers"
    allowed.write_text("release-bundle ssh-ed25519 AAAAC3...")

    signer = ManifestSigner(allowed_signers_path=str(allowed))
    result = signer.verify(str(manifest), str(sig))

    assert result.success is False
    assert any("not found" in e.lower() for e in result.errors)


@patch("general_ludd.runtime.manifest_signer.subprocess.run")
def test_verify_fails_when_ssh_keygen_errors(mock_run: MagicMock, tmp_path: Path) -> None:
    manifest = tmp_path / "MANIFEST.json"
    manifest.write_text("{}")
    sig = tmp_path / "MANIFEST.json.sig"
    sig.write_bytes(b"bad")
    allowed = tmp_path / "allowed_signers"
    allowed.write_text("release-bundle ssh-ed25519 AAAAC3...")

    completed = MagicMock()
    completed.returncode = 1
    completed.stderr = b"Signature verification failed: incorrect signature\n"
    mock_run.return_value = completed

    signer = ManifestSigner(allowed_signers_path=str(allowed))
    result = signer.verify(str(manifest), str(sig))

    assert result.success is False
    assert "ssh-keygen verify exited 1" in result.errors[0]


@patch("general_ludd.runtime.manifest_signer.subprocess.run")
def test_verify_propagates_timeout(mock_run: MagicMock, tmp_path: Path) -> None:
    manifest = tmp_path / "MANIFEST.json"
    manifest.write_text("{}")
    sig = tmp_path / "MANIFEST.json.sig"
    sig.write_bytes(b"sig")
    allowed = tmp_path / "allowed_signers"
    allowed.write_text("release-bundle ssh-ed25519 AAAAC3...")

    mock_run.side_effect = subprocess.TimeoutExpired(cmd=["ssh-keygen"], timeout=15)

    signer = ManifestSigner(allowed_signers_path=str(allowed))
    result = signer.verify(str(manifest), str(sig))

    assert result.success is False
    assert any("timeout" in e.lower() or "timed out" in e.lower() for e in result.errors)


# ---------------------------------------------------------------
# 4. Happy-path tests with real ssh-keygen subprocess
# ---------------------------------------------------------------

def test_sign_and_verify_with_real_ssh_keygen(tmp_path: Path) -> None:
    key_path = tmp_path / "id_ed25519"
    manifest = tmp_path / "MANIFEST.json"
    allowed = tmp_path / "allowed_signers"

    gen = subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-f", str(key_path), "-N", "", "-C", "test@release"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if gen.returncode != 0:
        pytest.skip(f"ssh-keygen generation failed: {gen.stderr}")

    pub_key = (Path(f"{key_path}.pub")).read_text().strip()
    ManifestSigner.make_allowed_signers("release-bundle", pub_key, str(allowed))

    manifest_content = '{"version":"1.0.0","files":["pkg.whl"]}'
    manifest.write_text(manifest_content)

    signer = ManifestSigner(
        private_key_path=str(key_path),
        allowed_signers_path=str(allowed),
    )
    sign_result = signer.sign(str(manifest))

    assert sign_result.success is True, f"sign failed: {sign_result.errors}"
    assert Path(sign_result.sig_path).exists()
    assert Path(sign_result.sig_path).stat().st_size > 0

    verify_result = signer.verify(str(manifest), sign_result.sig_path)

    assert verify_result.success is True, f"verify failed: {verify_result.errors}"
    assert "release-bundle" in verify_result.identity


def test_verify_detects_tampered_manifest(tmp_path: Path) -> None:
    key_path = tmp_path / "id_ed25519"
    manifest = tmp_path / "MANIFEST.json"
    allowed = tmp_path / "allowed_signers"

    gen = subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-f", str(key_path), "-N", "", "-C", "test@release"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if gen.returncode != 0:
        pytest.skip(f"ssh-keygen generation failed: {gen.stderr}")

    pub_key = (Path(f"{key_path}.pub")).read_text().strip()
    ManifestSigner.make_allowed_signers("release-bundle", pub_key, str(allowed))

    original = '{"version":"1.0.0","files":["pkg.whl"]}'
    manifest.write_text(original)

    signer = ManifestSigner(
        private_key_path=str(key_path),
        allowed_signers_path=str(allowed),
    )
    sign_result = signer.sign(str(manifest))
    assert sign_result.success is True

    manifest.write_text('{"version":"2.0.0","files":["evil.whl"]}')
    verify_result = signer.verify(str(manifest), sign_result.sig_path)
    assert verify_result.success is False


def test_make_allowed_signers_creates_file(tmp_path: Path) -> None:
    allowed = tmp_path / "dir" / "allowed_signers"
    pub = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIG... test@release"

    ManifestSigner.make_allowed_signers("release-bundle", pub, str(allowed))

    assert allowed.exists()
    content = allowed.read_text()
    assert "release-bundle ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIG... test@release" in content


def test_make_allowed_signers_is_idempotent(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed_signers"
    pub = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIG... test@release"

    ManifestSigner.make_allowed_signers("release-bundle", pub, str(allowed))
    ManifestSigner.make_allowed_signers("release-bundle", pub, str(allowed))

    lines = allowed.read_text().strip().splitlines()
    assert len(lines) == 1


def test_make_allowed_signers_appends_preserves_existing(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed_signers"
    allowed.write_text("existing-identity ssh-rsa AAA...\n")

    pub = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIG... test@release"
    ManifestSigner.make_allowed_signers("release-bundle", pub, str(allowed))

    content = allowed.read_text()
    assert "existing-identity" in content
    assert "release-bundle" in content
    assert len(content.strip().splitlines()) == 2


# ---------------------------------------------------------------
# 5. Env-var configuration
# ---------------------------------------------------------------

def test_env_var_signing_key_overrides_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    custom = str(tmp_path / "custom_key")
    monkeypatch.setenv("GLUDD_SIGNING_KEY", custom)

    signer = ManifestSigner()
    assert signer._private_key == custom


def test_env_var_allowed_signers_overrides_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    custom = str(tmp_path / "custom_allowed")
    monkeypatch.setenv("GLUDD_ALLOWED_SIGNERS", custom)

    signer = ManifestSigner()
    assert signer._allowed_signers == custom


def test_constructor_args_override_env_vars(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GLUDD_SIGNING_KEY", "/tmp/env_key")
    monkeypatch.setenv("GLUDD_ALLOWED_SIGNERS", "/tmp/env_allowed")

    signer = ManifestSigner(
        private_key_path="/explicit/key",
        allowed_signers_path="/explicit/allowed",
    )
    assert signer._private_key == "/explicit/key"
    assert signer._allowed_signers == "/explicit/allowed"


# ---------------------------------------------------------------
# 6. Release validator integration — _check_signature
# ---------------------------------------------------------------

@patch("general_ludd.runtime.release.ManifestSigner.verify")
def test_check_signature_returns_true_on_valid(mock_verify: MagicMock, tmp_path: Path) -> None:
    from general_ludd.runtime.release import ReleaseArtifactValidator

    manifest = tmp_path / "MANIFEST.json"
    sig = tmp_path / "MANIFEST.json.sig"
    manifest.write_text('{"version":"1.0.0"}')
    sig.write_bytes(b"sig")

    mock_verify.return_value = VerifyResult(success=True, identity="release-bundle")

    validator = ReleaseArtifactValidator()
    result = validator.validate_release("1.0.0", str(tmp_path))

    assert result.signature_valid is True
    assert "Signature" not in " ".join(result.errors)


@patch("general_ludd.runtime.release.ManifestSigner.verify")
def test_check_signature_returns_false_on_invalid(mock_verify: MagicMock, tmp_path: Path) -> None:
    from general_ludd.runtime.release import ReleaseArtifactValidator

    manifest = tmp_path / "MANIFEST.json"
    sig = tmp_path / "MANIFEST.json.sig"
    manifest.write_text('{"version":"1.0.0"}')
    sig.write_bytes(b"sig")

    mock_verify.return_value = VerifyResult(
        success=False, identity="",
        errors=["incorrect signature"],
    )

    validator = ReleaseArtifactValidator()
    result = validator.validate_release("1.0.0", str(tmp_path))

    assert result.signature_valid is False
    assert "incorrect signature" in result.errors


def test_check_signature_returns_false_when_sig_absent(tmp_path: Path) -> None:
    from general_ludd.runtime.release import ReleaseArtifactValidator

    manifest = tmp_path / "MANIFEST.json"
    manifest.write_text('{"version":"1.0.0"}')

    validator = ReleaseArtifactValidator()
    result = validator.validate_release("1.0.0", str(tmp_path))

    assert result.signature_valid is False


# ---------------------------------------------------------------
# 7. PipBundleBuilder signing integration
# ---------------------------------------------------------------

@patch("general_ludd.runtime.pip_bundle.subprocess.run")
def test_pip_bundle_includes_sig_path_on_success(mock_run: MagicMock, tmp_path: Path) -> None:
    from general_ludd.runtime.pip_bundle import PipBundleBuilder

    (tmp_path / "pkg-1.0.0-py3-none-any.whl").write_bytes(b"w")
    sig_path = os.path.join(str(tmp_path), "MANIFEST.json.sig")
    signer = MagicMock(spec=ManifestSigner)
    signer.sign.return_value = SignResult(success=True, sig_path=sig_path)

    calls: list[dict] = []

    def side_effect(cmd, **kwargs):
        calls.append({"cmd": cmd})
        if "uv" in cmd:
            m = MagicMock()
            m.returncode = 0
            m.stdout = ""
            m.stderr = ""
            return m
        if "git" in cmd:
            m = MagicMock()
            m.returncode = 0
            m.stdout = "abc123\n"
            m.stderr = ""
            return m
        return MagicMock()

    mock_run.side_effect = side_effect

    result = PipBundleBuilder(signer=signer).build(str(tmp_path), "1.0.0")

    assert result.success is True
    assert result.sig_path == sig_path
    assert result.signature_valid is True
    signer.sign.assert_called_once_with(
        os.path.join(str(tmp_path), "MANIFEST.json")
    )


# ---------------------------------------------------------------
# 8. Structural and edge-case tests for ManifestSigner
# ---------------------------------------------------------------

def test_sig_path_is_manifest_plus_dot_sig(tmp_path: Path) -> None:
    signer = ManifestSigner()
    manifest = tmp_path / "RELEASE.json"
    manifest.write_text("{}")
    key = tmp_path / "id_ed25519"
    key.write_text("k")
    signer._private_key = str(key)
    signer._allowed_signers = str(tmp_path / "allowed_signers")
    with patch("general_ludd.runtime.manifest_signer.subprocess.run") as mock_run:
        mock_run.side_effect = FileNotFoundError
        result = signer.sign(str(manifest))
        assert result.sig_path == f"{manifest}.sig"


def test_sign_errors_list_is_empty_on_success(tmp_path: Path) -> None:
    manifest = tmp_path / "MANIFEST.json"
    manifest.write_text('{"version":"1.0.0"}')
    key = tmp_path / "id_ed25519"
    key.write_text("k")
    sig_data = b"-----BEGIN SSH SIGNATURE-----\nabc\n-----END SSH SIGNATURE-----\n"
    signer = ManifestSigner(private_key_path=str(key))
    with patch("general_ludd.runtime.manifest_signer.subprocess.run") as mock_run:
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = sig_data
        mock_proc.stderr = b""
        mock_run.return_value = mock_proc
        result = signer.sign(str(manifest))
        assert result.success is True
        assert result.errors == []
        assert Path(result.sig_path).exists()


def test_verify_errors_list_is_empty_on_success(tmp_path: Path) -> None:
    manifest = tmp_path / "MANIFEST.json"
    manifest.write_text('{"version":"1.0.0"}')
    sig = tmp_path / "MANIFEST.json.sig"
    sig.write_bytes(b"sig")
    allowed = tmp_path / "allowed_signers"
    allowed.write_text("release-bundle ssh-ed25519 AAAAC3...")
    signer = ManifestSigner(allowed_signers_path=str(allowed))
    with patch("general_ludd.runtime.manifest_signer.subprocess.run") as mock_run:
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = b"release-bundle\n"
        mock_proc.stderr = b""
        mock_run.return_value = mock_proc
        result = signer.verify(str(manifest), str(sig))
        assert result.success is True
        assert result.errors == []
        assert "release-bundle" in result.identity


def test_make_allowed_signers_creates_parent_dirs(tmp_path: Path) -> None:
    allowed = tmp_path / "deep" / "path" / "allowed_signers"
    ManifestSigner.make_allowed_signers("id", "ssh-ed25519 KEY", str(allowed))
    assert allowed.exists()


def test_make_allowed_signers_duplicate_entries_skipped(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed_signers"
    pub = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIG... test@release"
    ManifestSigner.make_allowed_signers("release-bundle", pub, str(allowed))
    ManifestSigner.make_allowed_signers("release-bundle", pub, str(allowed))
    ManifestSigner.make_allowed_signers("release-bundle", pub, str(allowed))
    lines = allowed.read_text().strip().splitlines()
    assert len(lines) == 1


def test_make_allowed_signers_without_comment_in_pubkey(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed_signers"
    pub = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIG..."  # no trailing comment
    ManifestSigner.make_allowed_signers("my-id", pub, str(allowed))
    content = allowed.read_text()
    assert "my-id ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIG..." in content


def test_signer_defaults_to_ssh_home_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GLUDD_SIGNING_KEY", raising=False)
    monkeypatch.delenv("GLUDD_ALLOWED_SIGNERS", raising=False)
    signer = ManifestSigner()
    assert ".ssh/id_ed25519" in signer._private_key
    assert ".ssh/allowed_signers" in signer._allowed_signers


def test_sign_fail_when_key_not_a_file(tmp_path: Path) -> None:
    key_dir = tmp_path / "key_dir"
    key_dir.mkdir()
    signer = ManifestSigner(private_key_path=str(key_dir))
    manifest = tmp_path / "MANIFEST.json"
    manifest.write_text("{}")
    with patch("general_ludd.runtime.manifest_signer.subprocess.run") as mock_run:
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stderr = b"Load key: not a private key file\n"
        mock_run.return_value = mock_proc
        result = signer.sign(str(manifest))
        assert result.success is False
        assert "exited 1" in result.errors[0]


def test_sign_stderr_message_preserved_in_errors(tmp_path: Path) -> None:
    manifest = tmp_path / "MANIFEST.json"
    manifest.write_text("{}")
    key = tmp_path / "id_ed25519"
    key.write_text("k")
    signer = ManifestSigner(private_key_path=str(key))
    with patch("general_ludd.runtime.manifest_signer.subprocess.run") as mock_run:
        mock_proc = MagicMock()
        mock_proc.returncode = 255
        mock_proc.stderr = b"Could not read private key: bad permissions\n"
        mock_run.return_value = mock_proc
        result = signer.sign(str(manifest))
        assert result.success is False
        assert "bad permissions" in result.errors[0]


def test_verify_stderr_message_preserved_in_errors(tmp_path: Path) -> None:
    manifest = tmp_path / "MANIFEST.json"
    manifest.write_text("{}")
    sig = tmp_path / "MANIFEST.json.sig"
    sig.write_bytes(b"bad")
    allowed = tmp_path / "allowed_signers"
    allowed.write_text("bad-identity ssh-ed25519 AAAAC3...")
    signer = ManifestSigner(allowed_signers_path=str(allowed))
    with patch("general_ludd.runtime.manifest_signer.subprocess.run") as mock_run:
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stderr = b"Signature verification failed: mismatched identity\n"
        mock_run.return_value = mock_proc
        result = signer.verify(str(manifest), str(sig))
        assert result.success is False
        assert "mismatched identity" in result.errors[0]


def test_empty_manifest_path_produces_error(tmp_path: Path) -> None:
    signer = ManifestSigner()
    key = tmp_path / "id_ed25519"
    key.write_text("k")
    signer._private_key = str(key)
    with patch("general_ludd.runtime.manifest_signer.subprocess.run"):
        result = signer.sign(str(tmp_path / "nonexistent.json"))
        assert result.success is False
        assert any("not found" in e.lower() for e in result.errors)
