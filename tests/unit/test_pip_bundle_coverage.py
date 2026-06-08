from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from general_ludd.runtime.pip_bundle import BundleResult, PipBundleBuilder


def _make_completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


class TestPipBundleBuilderSuccessfulBuild:
    @patch("general_ludd.runtime.pip_bundle.subprocess.run")
    def test_build_success_with_wheel_and_sdist(self, mock_run, tmp_path: Path) -> None:
        wheel = tmp_path / "pkg-1.0.0-py3-none-any.whl"
        sdist = tmp_path / "pkg-1.0.0.tar.gz"
        wheel.write_bytes(b"wheel-content")
        sdist.write_bytes(b"sdist-content")

        def side_effect(cmd, **kwargs):
            if "uv" in cmd:
                return _make_completed(returncode=0)
            if "git" in cmd:
                return _make_completed(returncode=0, stdout="abc123\n")
            return _make_completed()

        mock_run.side_effect = side_effect

        builder = PipBundleBuilder()
        result = builder.build(str(tmp_path), "1.0.0")

        assert isinstance(result, BundleResult)
        assert result.success is True
        assert result.wheel_path == str(wheel)
        assert result.sdist_path == str(sdist)
        assert result.manifest_path == str(tmp_path / "MANIFEST.json")
        assert result.checksum_path == str(tmp_path / "CHECKSUMS.sha256")

        manifest = json.loads(Path(result.manifest_path).read_text())
        assert manifest["version"] == "1.0.0"
        assert manifest["commit"] == "abc123"
        assert "pkg-1.0.0-py3-none-any.whl" in manifest["files"]
        assert "pkg-1.0.0.tar.gz" in manifest["files"]
        assert "pkg-1.0.0-py3-none-any.whl" in manifest["checksums"]

        checksums_text = Path(result.checksum_path).read_text()
        assert "pkg-1.0.0-py3-none-any.whl" in checksums_text
        assert "pkg-1.0.0.tar.gz" in checksums_text


class TestPipBundleBuilderFailedBuild:
    @patch("general_ludd.runtime.pip_bundle.subprocess.run")
    def test_build_failure_returns_not_success(self, mock_run, tmp_path: Path) -> None:
        mock_run.return_value = _make_completed(returncode=1, stderr="build failed")

        builder = PipBundleBuilder()
        result = builder.build(str(tmp_path), "1.0.0")

        assert result.success is False
        assert result.wheel_path == ""
        assert result.sdist_path == ""
        assert result.manifest_path == ""
        assert result.checksum_path == ""


class TestPipBundleBuilderOSErrorListing:
    @patch("general_ludd.runtime.pip_bundle.os.listdir", side_effect=OSError("permission denied"))
    @patch("general_ludd.runtime.pip_bundle.subprocess.run")
    def test_oserror_on_listdir_empty_files(self, mock_run, mock_listdir, tmp_path: Path) -> None:
        def side_effect(cmd, **kwargs):
            if "uv" in cmd:
                return _make_completed(returncode=0)
            if "git" in cmd:
                return _make_completed(returncode=0, stdout="deadbeef\n")
            return _make_completed()

        mock_run.side_effect = side_effect

        builder = PipBundleBuilder()
        result = builder.build(str(tmp_path), "2.0.0")

        assert result.success is True
        assert result.wheel_path == ""
        assert result.sdist_path == ""

        manifest = json.loads(Path(result.manifest_path).read_text())
        assert manifest["files"] == []
        assert manifest["checksums"] == {}


class TestPipBundleBuilderGitCommitUnavailable:
    @patch("general_ludd.runtime.pip_bundle.subprocess.run")
    def test_git_not_found(self, mock_run, tmp_path: Path) -> None:
        def side_effect(cmd, **kwargs):
            if "uv" in cmd:
                return _make_completed(returncode=0)
            if "git" in cmd:
                raise FileNotFoundError("git not installed")
            return _make_completed()

        mock_run.side_effect = side_effect

        (tmp_path / "dummy-1.0.tar.gz").write_bytes(b"x")

        builder = PipBundleBuilder()
        result = builder.build(str(tmp_path), "1.0.0")

        assert result.success is True
        manifest = json.loads(Path(result.manifest_path).read_text())
        assert manifest["commit"] == "unknown"


class TestPipBundleBuilderGitTimeout:
    @patch("general_ludd.runtime.pip_bundle.subprocess.run")
    def test_git_timeout(self, mock_run, tmp_path: Path) -> None:
        def side_effect(cmd, **kwargs):
            if "uv" in cmd:
                return _make_completed(returncode=0)
            if "git" in cmd:
                raise subprocess.TimeoutExpired(cmd="git rev-parse HEAD", timeout=10)
            return _make_completed()

        mock_run.side_effect = side_effect

        (tmp_path / "dummy-1.0.tar.gz").write_bytes(b"x")

        builder = PipBundleBuilder()
        result = builder.build(str(tmp_path), "1.0.0")

        assert result.success is True
        manifest = json.loads(Path(result.manifest_path).read_text())
        assert manifest["commit"] == "unknown"
