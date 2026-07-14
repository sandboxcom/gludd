from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

from general_ludd.runtime.manifest_signer import ManifestSigner, SignResult, VerifyResult


class TestSignResult:
    def test_default_construction(self) -> None:
        r = SignResult(success=True, sig_path="x.sig")
        assert r.success is True
        assert r.sig_path == "x.sig"
        assert r.errors == []

    def test_with_errors(self) -> None:
        r = SignResult(success=False, sig_path="x.sig", errors=["bad key"])
        assert r.success is False
        assert r.errors == ["bad key"]


class TestVerifyResult:
    def test_default_construction(self) -> None:
        r = VerifyResult(success=True, identity="alice")
        assert r.success is True
        assert r.identity == "alice"
        assert r.errors == []


class TestManifestSignerInit:
    def test_default_key_paths(self) -> None:
        signer = ManifestSigner(
            private_key_path="/custom/key",
            allowed_signers_path="/custom/allowed",
        )
        assert signer._private_key == "/custom/key"
        assert signer._allowed_signers == "/custom/allowed"


class TestSign:
    @patch("pathlib.Path.exists", return_value=True)
    @patch("subprocess.run")
    def test_sign_success(self, mock_run: MagicMock, _mock_exists: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            returncode=0, stdout=b"sigdata", stderr=b""
        )
        signer = ManifestSigner(private_key_path="/fake/key")

        with patch("pathlib.Path.write_bytes") as mock_write:
            result = signer.sign("/fake/manifest.txt")

        assert result.success is True
        assert result.sig_path == "/fake/manifest.txt.sig"
        assert result.errors == []
        mock_write.assert_called_once_with(b"sigdata")

    @patch("pathlib.Path.exists", return_value=False)
    def test_sign_key_not_found(self, _mock_exists: MagicMock) -> None:
        signer = ManifestSigner(private_key_path="/nonexistent/key")
        result = signer.sign("/fake/manifest.txt")
        assert result.success is False
        assert "Private key not found" in result.errors[0]

    def test_sign_manifest_not_found(self) -> None:
        signer = ManifestSigner(private_key_path="/fake/key")
        with patch.object(Path, "exists", side_effect=lambda self: "key" in str(self)):
            result = signer.sign("/nonexistent/manifest.txt")
        assert result.success is False
        assert "Manifest not found" in result.errors[0]

    @patch("pathlib.Path.exists", return_value=True)
    @patch("subprocess.run")
    def test_sign_subprocess_failure(self, mock_run: MagicMock, _mock_exists: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            returncode=1, stderr=b"key invalid", stdout=b""
        )
        signer = ManifestSigner(private_key_path="/fake/key")
        result = signer.sign("/fake/manifest.txt")
        assert result.success is False
        assert "ssh-keygen sign exited 1" in result.errors[0]

    @patch("pathlib.Path.exists", return_value=True)
    @patch("subprocess.run")
    def test_sign_timeout(self, mock_run: MagicMock, _mock_exists: MagicMock) -> None:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["ssh-keygen"], timeout=30)
        signer = ManifestSigner(private_key_path="/fake/key")
        result = signer.sign("/fake/manifest.txt")
        assert result.success is False
        assert "timed out" in result.errors[0]

    @patch("pathlib.Path.exists", return_value=True)
    @patch("subprocess.run")
    def test_sign_sshkgen_not_found(self, mock_run: MagicMock, _mock_exists: MagicMock) -> None:
        mock_run.side_effect = FileNotFoundError
        signer = ManifestSigner(private_key_path="/fake/key")
        result = signer.sign("/fake/manifest.txt")
        assert result.success is False
        assert "ssh-keygen not found or failed" in result.errors[0]


class TestVerify:
    @patch("pathlib.Path.exists", return_value=True)
    @patch("subprocess.run")
    def test_verify_success(self, mock_run: MagicMock, _mock_exists: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            returncode=0, stdout=b"alice@example.com", stderr=b""
        )
        signer = ManifestSigner(allowed_signers_path="/fake/allowed_signers")
        result = signer.verify("/fake/manifest.txt", "/fake/manifest.txt.sig")
        assert result.success is True
        assert result.identity == "alice@example.com"
        assert result.errors == []

    @patch("pathlib.Path.exists", return_value=False)
    def test_verify_allowed_signers_not_found(self, _mock_exists: MagicMock) -> None:
        signer = ManifestSigner(allowed_signers_path="/nonexistent/allowed_signers")
        result = signer.verify("/fake/manifest.txt", "/fake/manifest.txt.sig")
        assert result.success is False
        assert "Allowed signers file not found" in result.errors[0]

    def test_verify_manifest_not_found(self) -> None:
        signer = ManifestSigner(allowed_signers_path="/fake/allowed_signers")
        with patch.object(Path, "exists", side_effect=lambda self: "allowed_signers" in str(self)):
            result = signer.verify("/nonexistent/manifest.txt", "/fake/manifest.txt.sig")
        assert result.success is False
        assert "Manifest not found" in result.errors[0]

    def test_verify_signature_not_found(self) -> None:
        signer = ManifestSigner(allowed_signers_path="/fake/allowed_signers")
        with patch.object(
            Path,
            "exists",
            side_effect=lambda self: (
                "allowed_signers" in str(self)
                or ("manifest" in str(self) and ".sig" not in str(self))
            ),
        ):
            result = signer.verify(
                "/fake/manifest.txt", "/nonexistent/manifest.txt.sig"
            )
        assert result.success is False
        assert "Signature file not found" in result.errors[0]

    @patch("pathlib.Path.exists", return_value=True)
    @patch("subprocess.run")
    def test_verify_subprocess_failure(self, mock_run: MagicMock, _mock_exists: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            returncode=3, stderr=b"invalid signature", stdout=b""
        )
        signer = ManifestSigner(allowed_signers_path="/fake/allowed_signers")
        result = signer.verify("/fake/manifest.txt", "/fake/manifest.txt.sig")
        assert result.success is False
        assert "ssh-keygen verify exited 3" in result.errors[0]

    @patch("pathlib.Path.exists", return_value=True)
    @patch("subprocess.run")
    def test_verify_timeout(self, mock_run: MagicMock, _mock_exists: MagicMock) -> None:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["ssh-keygen"], timeout=30)
        signer = ManifestSigner(allowed_signers_path="/fake/allowed_signers")
        result = signer.verify("/fake/manifest.txt", "/fake/manifest.txt.sig")
        assert result.success is False
        assert "timed out" in result.errors[0]

    @patch("pathlib.Path.exists", return_value=True)
    @patch("subprocess.run")
    def test_verify_sshkgen_not_found(self, mock_run: MagicMock, _mock_exists: MagicMock) -> None:
        mock_run.side_effect = FileNotFoundError
        signer = ManifestSigner(allowed_signers_path="/fake/allowed_signers")
        result = signer.verify("/fake/manifest.txt", "/fake/manifest.txt.sig")
        assert result.success is False
        assert "ssh-keygen not found or failed" in result.errors[0]


class TestMakeAllowedSigners:
    def test_creates_new_file(self) -> None:
        with (
            patch("pathlib.Path.mkdir"),
            patch("pathlib.Path.exists", return_value=False),
            patch("pathlib.Path.open", mock_open()) as mock_fh,
        ):
            ManifestSigner.make_allowed_signers(
                "alice", "ssh-ed25519 AAAAC3...", "/fake/allowed_signers"
            )
        mock_fh().write.assert_called_once_with(
            "alice ssh-ed25519 AAAAC3...\n"
        )

    def test_appends_to_existing_file(self) -> None:
        existing = "bob ssh-ed25519 BBB...\n"
        with (
            patch("pathlib.Path.mkdir"),
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.read_text", return_value=existing),
            patch("pathlib.Path.open", mock_open()) as mock_fh,
        ):
            ManifestSigner.make_allowed_signers(
                "alice", "ssh-ed25519 AAA...", "/fake/allowed_signers"
            )
        assert mock_fh().write.call_count == 2
        mock_fh().write.assert_any_call("\n")
        mock_fh().write.assert_any_call("alice ssh-ed25519 AAA...\n")

    def test_skips_duplicate_entry(self) -> None:
        existing = "alice ssh-ed25519 AAA...\n"
        with (
            patch("pathlib.Path.mkdir"),
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.read_text", return_value=existing),
            patch("pathlib.Path.open", mock_open()),
        ):
            ManifestSigner.make_allowed_signers(
                "alice", "ssh-ed25519 AAA...", "/fake/allowed_signers"
            )
