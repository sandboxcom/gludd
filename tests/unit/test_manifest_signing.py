from __future__ import annotations

import io
import subprocess
from unittest.mock import MagicMock, patch

from general_ludd.runtime.manifest_signer import ManifestSigner, SignResult, VerifyResult


def _open_mock_manifest(*_args: object, **_kwargs: object) -> io.BytesIO:
    return io.BytesIO(b"mock manifest")


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
    @patch("builtins.open", _open_mock_manifest)
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
        assert "Signing key not found" in result.errors[0]

    @patch("general_ludd.runtime.manifest_signer.Path")
    def test_sign_manifest_not_found(self, MockPath: MagicMock) -> None:
        def _make_path(p: str) -> MagicMock:
            m = MagicMock()
            m.exists.return_value = "key" in str(p)
            return m
        MockPath.side_effect = _make_path
        signer = ManifestSigner(private_key_path="/fake/key")
        result = signer.sign("/nonexistent/manifest.txt")
        assert result.success is False
        assert "Manifest not found" in result.errors[0]

    @patch("builtins.open", _open_mock_manifest)
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

    @patch("builtins.open", _open_mock_manifest)
    @patch("pathlib.Path.exists", return_value=True)
    @patch("subprocess.run")
    def test_sign_timeout(self, mock_run: MagicMock, _mock_exists: MagicMock) -> None:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["ssh-keygen"], timeout=30)
        signer = ManifestSigner(private_key_path="/fake/key")
        result = signer.sign("/fake/manifest.txt")
        assert result.success is False
        assert "ssh-keygen sign failed" in result.errors[0]

    @patch("builtins.open", _open_mock_manifest)
    @patch("pathlib.Path.exists", return_value=True)
    @patch("subprocess.run")
    def test_sign_sshkgen_not_found(self, mock_run: MagicMock, _mock_exists: MagicMock) -> None:
        mock_run.side_effect = FileNotFoundError
        signer = ManifestSigner(private_key_path="/fake/key")
        result = signer.sign("/fake/manifest.txt")
        assert result.success is False
        assert "ssh-keygen sign failed" in result.errors[0]


class TestVerify:
    @patch("builtins.open", _open_mock_manifest)
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

    @patch("general_ludd.runtime.manifest_signer.Path")
    def test_verify_manifest_not_found(self, MockPath: MagicMock) -> None:
        def _make_path(p: str) -> MagicMock:
            m = MagicMock()
            m.exists.return_value = "allowed_signers" in str(p)
            return m
        MockPath.side_effect = _make_path
        signer = ManifestSigner(allowed_signers_path="/fake/allowed_signers")
        result = signer.verify("/nonexistent/manifest.txt", "/fake/manifest.txt.sig")
        assert result.success is False
        assert "Manifest not found" in result.errors[0]

    @patch("general_ludd.runtime.manifest_signer.Path")
    def test_verify_signature_not_found(self, MockPath: MagicMock) -> None:
        def _make_path(p: str) -> MagicMock:
            m = MagicMock()
            m.exists.return_value = (
                "allowed_signers" in str(p)
                or ("manifest" in str(p) and ".sig" not in str(p))
            )
            return m
        MockPath.side_effect = _make_path
        signer = ManifestSigner(allowed_signers_path="/fake/allowed_signers")
        result = signer.verify(
            "/fake/manifest.txt", "/nonexistent/manifest.txt.sig"
        )
        assert result.success is False
        assert "Signature file not found" in result.errors[0]

    @patch("builtins.open", _open_mock_manifest)
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

    @patch("builtins.open", _open_mock_manifest)
    @patch("pathlib.Path.exists", return_value=True)
    @patch("subprocess.run")
    def test_verify_timeout(self, mock_run: MagicMock, _mock_exists: MagicMock) -> None:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["ssh-keygen"], timeout=30)
        signer = ManifestSigner(allowed_signers_path="/fake/allowed_signers")
        result = signer.verify("/fake/manifest.txt", "/fake/manifest.txt.sig")
        assert result.success is False
        assert "ssh-keygen verify failed" in result.errors[0]

    @patch("builtins.open", _open_mock_manifest)
    @patch("pathlib.Path.exists", return_value=True)
    @patch("subprocess.run")
    def test_verify_sshkgen_not_found(self, mock_run: MagicMock, _mock_exists: MagicMock) -> None:
        mock_run.side_effect = FileNotFoundError
        signer = ManifestSigner(allowed_signers_path="/fake/allowed_signers")
        result = signer.verify("/fake/manifest.txt", "/fake/manifest.txt.sig")
        assert result.success is False
        assert "ssh-keygen verify failed" in result.errors[0]


class TestMakeAllowedSigners:
    @patch("builtins.open")
    def test_creates_new_file(self, mock_open_func: MagicMock) -> None:
        mock_handle = MagicMock()
        mock_open_func.return_value.__enter__.return_value = mock_handle
        with (
            patch("pathlib.Path.mkdir"),
            patch("pathlib.Path.exists", return_value=False),
        ):
            ManifestSigner.make_allowed_signers(
                "alice", "ssh-ed25519 AAAAC3...", "/fake/allowed_signers"
            )
        mock_open_func.assert_called_once()
        mock_handle.write.assert_called_once_with(
            "alice ssh-ed25519 AAAAC3...\n"
        )

    @patch("builtins.open")
    def test_appends_to_existing_file(self, mock_open_func: MagicMock) -> None:
        mock_handle = MagicMock()
        mock_open_func.return_value.__enter__.return_value = mock_handle
        existing = "bob ssh-ed25519 BBB...\n"
        with (
            patch("pathlib.Path.mkdir"),
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.read_text", return_value=existing),
        ):
            ManifestSigner.make_allowed_signers(
                "alice", "ssh-ed25519 AAA...", "/fake/allowed_signers"
            )
        mock_open_func.assert_called_once()
        mock_handle.write.assert_called_once_with(
            "alice ssh-ed25519 AAA...\n"
        )

    @patch("builtins.open")
    def test_skips_duplicate_entry(self, mock_open_func: MagicMock) -> None:
        existing = "alice ssh-ed25519 AAA...\n"
        with (
            patch("pathlib.Path.mkdir"),
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.read_text", return_value=existing),
        ):
            ManifestSigner.make_allowed_signers(
                "alice", "ssh-ed25519 AAA...", "/fake/allowed_signers"
            )
        mock_open_func.assert_not_called()
