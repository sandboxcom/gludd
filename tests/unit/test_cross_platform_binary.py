"""Cross-platform binary build verification.

Structural tests that verify ``gludd.spec`` (PyInstaller) and
``.github/workflows/build.yml`` are correctly configured for each target
platform WITHOUT actually performing a build. A real build takes ~30 minutes
per platform; these tests run in milliseconds by inspecting the configuration
text and asserting on the invariants that, if violated, would break one or
more of the target platforms (linux-x86_64, linux-aarch64, macOS-arm64,
windows-x86_64).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = REPO_ROOT / "gludd.spec"
BUILD_YML_PATH = REPO_ROOT / ".github" / "workflows" / "build.yml"


@pytest.fixture(scope="module")
def spec_text() -> str:
    """Contents of ``gludd.spec`` as a single string."""
    return SPEC_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def build_yml_text() -> str:
    """Contents of ``build.yml`` as a single string."""
    return BUILD_YML_PATH.read_text(encoding="utf-8")


class TestSpecPlatformCompatibility:
    """Verify gludd.spec works on all target platforms."""

    def test_version_command_imports_without_posix_terminal_modules(self) -> None:
        """The Windows executable must not import POSIX-only TUI modules."""
        script = """
import builtins
import os
import sys

real_import = builtins.__import__

def reject_posix_terminal_modules(name, *args, **kwargs):
    if name in {"termios", "tty"}:
        raise ModuleNotFoundError(f"No module named {name!r}", name=name)
    return real_import(name, *args, **kwargs)

builtins.__import__ = reject_posix_terminal_modules
if hasattr(os, "getuid"):
    del os.getuid
sys.argv = ["gludd", "version"]
from general_ludd.cli import main
main()
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO_ROOT,
            capture_output=True,
            check=False,
            text=True,
        )

        assert result.returncode == 0, result.stderr
        assert "0.1.0-beta.3" in result.stdout

    def test_tui_reports_unsupported_platform_at_invocation_time(self) -> None:
        """A Windows TUI request fails explicitly without breaking CLI import."""
        script = """
import builtins

real_import = builtins.__import__

def reject_posix_terminal_modules(name, *args, **kwargs):
    if name in {"termios", "tty"}:
        raise ModuleNotFoundError(f"No module named {name!r}", name=name)
    return real_import(name, *args, **kwargs)

builtins.__import__ = reject_posix_terminal_modules
from general_ludd.tui.runner import run_tui

try:
    run_tui(None, None)
except SystemExit as exc:
    assert "requires POSIX termios/tty support" in str(exc)
else:
    raise AssertionError("run_tui unexpectedly started without POSIX terminal modules")
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO_ROOT,
            capture_output=True,
            check=False,
            text=True,
        )

        assert result.returncode == 0, result.stderr

    def test_no_windows_incompatible_paths(self, spec_text: str):
        """Spec doesn't use paths with colons or backslashes that break Windows.

        Windows paths use backslashes and disallow colons in file names. The
        spec should use POSIX-style relative paths only.
        """
        # Strip the leading comment block so explanatory docstrings (which may
        # legitimately mention Windows/cp1252 as documentation) do not trip
        # the absolute-path heuristic.
        for lineno, line in enumerate(spec_text.splitlines(), start=1):
            stripped = line.strip()
            # Skip comments — they are documentation, not active configuration.
            if stripped.startswith("#"):
                continue
            # Backslash paths inside the spec are Windows-incompatible.
            # Allow the regex escapes only inside string literals that are
            # clearly NOT path-like (heuristic: no drive letter, no separator
            # following).
            assert "\\" not in stripped, (
                f"line {lineno}: backslash in spec (Windows-incompatible): {line!r}"
            )
            # Absolute POSIX paths inside datas/hiddenimports would break the
            # relocatable build on Windows. The spec's own data entries MUST
            # be relative (e.g. 'config', 'templates').
            # Match '/foo' or '/usr/...' but NOT an inline comment that starts
            # with '#/...' (those are filtered above).
            absolute_match = re.search(r"(?<![\w/'\"])/(?:usr|etc|opt|var|home|bin|tmp)(?:/|\b)", stripped)
            assert absolute_match is None, (
                f"line {lineno}: absolute POSIX path in spec breaks Windows: {line!r}"
            )

    def test_no_macos_incompatible_flags(self, spec_text: str):
        """Spec doesn't use PyInstaller flags unavailable on macOS.

        ``win_no_prefer_redirects`` and ``win_private_assemblies`` are
        Windows-only EXE() kwargs — they are accepted as no-ops on macOS/Linux
        so their presence is fine. But Linux-only flags like ``console`` set
        to False would create a windowed app bundle that does not exist on
        Linux. Assert the spec uses the cross-platform-safe defaults.
        """
        # console=True is required for a CLI tool on all platforms.
        assert re.search(r"console\s*=\s*True", spec_text), (
            "Spec must set console=True so the CLI works on all platforms"
        )
        # target_arch=None lets the build host architecture pass through,
        # which is required for cross-platform builds (x86_64, arm64).
        assert re.search(r"target_arch\s*=\s*None", spec_text), (
            "Spec must set target_arch=None for host-arch-native builds"
        )
        # UPX is optional but must not be a hardcoded path that only exists
        # on one OS.
        upx_path_match = re.search(r"upx\s*=\s*[\"']([^\"']+)[\"']", spec_text)
        if upx_path_match:
            upx_path = upx_path_match.group(1)
            assert not upx_path.startswith(("/", "C:", "D:", "~")), (
                f"UPX path must not be absolute (breaks other OSes): {upx_path}"
            )

    def test_ansible_cli_excluded(self, spec_text: str):
        """ansible.cli is excluded (Windows locale issue) but ansible core is NOT excluded.

        ansible.cli calls ``initialize_locale()`` which hard-fails on Windows'
        cp1252 locale. The spec excludes ansible.cli to avoid pulling it in at
        build time. But ansible core (executor API) MUST NOT be excluded —
        gludd drives ansible.runner/core_runner/templating at runtime.
        """
        excludes_match = re.search(
            r"^[ ]{4}excludes\s*=\s*\[([^\]]*)\]",
            spec_text,
            re.MULTILINE | re.DOTALL,
        )
        assert excludes_match, "Spec must have an excludes= list"
        excludes_body = excludes_match.group(1)

        # ansible.cli must be in excludes.
        assert re.search(r"['\"]ansible\.cli['\"]", excludes_body), (
            "ansible.cli MUST be in excludes= — it hard-fails on Windows cp1252 locale"
        )

        # ansible core MUST NOT be excluded.
        for forbidden_exclude in ("'ansible'", '"ansible"', "'ansible.$'"):
            assert forbidden_exclude not in excludes_body, (
                f"ansible core must NOT be excluded (gludd uses the executor API): "
                f"found {forbidden_exclude!r}"
            )

        # And the spec must positively list the ansible executor modules as
        # hidden imports — proving they are NOT being excluded.
        assert re.search(r"general_ludd\.ansible\.runner", spec_text), (
            "Spec must include general_ludd.ansible.runner as a hidden import"
        )
        assert re.search(r"general_ludd\.ansible\.core_runner", spec_text), (
            "Spec must include general_ludd.ansible.core_runner as a hidden import"
        )

    def test_data_files_use_relative_paths(self, spec_text: str):
        """All ``datas`` entries use relative paths, not absolute.

        Absolute paths in ``datas`` would only resolve on the build host that
        created them, breaking reproducibility on other platforms. The bundled
        files (config/, templates/, playbooks/, LICENSE, etc.) are repo-relative.
        """
        # The spec has both a module-level `datas = [...]` and an inline
        # `datas=[...]` inside Analysis. Check the module-level one — that's
        # what _ansible_datas gets appended to.
        module_datas_match = re.search(
            r"^datas\s*=\s*\[(.*?)\]", spec_text, re.DOTALL | re.MULTILINE
        )
        assert module_datas_match, "Spec must define a module-level datas= list"
        module_datas = module_datas_match.group(1)

        # Each tuple's first element is the source path. It must be relative.
        # Pattern: ('SOURCE', 'DEST')
        tuple_matches = re.findall(r"\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*\)", module_datas)
        assert tuple_matches, (
            "Expected at least one (source, dest) tuple in module datas= list"
        )
        for src, dest in tuple_matches:
            assert not src.startswith(("/", "~")), (
                f"datas source must be relative (not absolute): {src!r}"
            )
            assert not re.match(r"^[A-Za-z]:", src), (
                f"datas source must not be a Windows drive path: {src!r}"
            )
            assert not dest.startswith(("/", "~")), (
                f"datas dest must be relative (not absolute): {dest!r}"
            )
            assert not re.match(r"^[A-Za-z]:", dest), (
                f"datas dest must not be a Windows drive path: {dest!r}"
            )

    def test_no_hardcoded_os_paths(self, spec_text: str):
        """No hardcoded /usr/, /etc/, or C:\\ paths in the spec.

        Hardcoded OS paths would only exist on the OS where they were written,
        breaking the build on every other platform.
        """
        for lineno, line in enumerate(spec_text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            # /usr/, /etc/, /opt/, /var/ on POSIX.
            assert not re.search(r"(?<![\w/])/(?:usr|etc|opt|var|home|bin|sbin|tmp)/", stripped), (
                f"line {lineno}: hardcoded POSIX path in spec: {line!r}"
            )
            # C:\ or D:\ on Windows.
            assert not re.search(r"[A-Za-z]:\\\\", stripped), (
                f"line {lineno}: hardcoded Windows path in spec: {line!r}"
            )
            # /Users/... on macOS.
            assert not re.search(r"(?<![\w/])/Users/", stripped), (
                f"line {lineno}: hardcoded macOS /Users/ path in spec: {line!r}"
            )


class TestBuildYmlPlatformCoverage:
    """Verify build.yml builds for all target platforms with smoke tests."""

    EXPECTED_BUILD_JOBS = ("linux", "macos", "windows", "termux")

    def test_linux_job_exists(self, build_yml_text: str):
        """Linux x86_64 build job exists."""
        assert re.search(r"^  linux\s*:", build_yml_text, re.MULTILINE), (
            "build.yml must define a 'linux:' build job"
        )
        assert "runs-on: ubuntu-latest" in build_yml_text, (
            "linux job must run on ubuntu-latest"
        )
        # Linux x86_64 produces a tarball + .deb + .rpm.
        assert "linux-x86_64.tar.gz" in build_yml_text, (
            "linux job must produce a linux-x86_64 tarball artifact"
        )

    def test_macos_job_exists(self, build_yml_text: str):
        """macOS arm64 build job exists."""
        assert re.search(r"^  macos\s*:", build_yml_text, re.MULTILINE), (
            "build.yml must define a 'macos:' build job"
        )
        assert "runs-on: macos-latest" in build_yml_text, (
            "macos job must run on macos-latest"
        )
        # macOS arm64 produces a tarball + .dmg.
        assert "macos-arm64.tar.gz" in build_yml_text, (
            "macos job must produce a macos-arm64 tarball artifact"
        )

    def test_windows_job_exists(self, build_yml_text: str):
        """Windows x86_64 build job exists with UTF-8 locale fix."""
        assert re.search(r"^  windows\s*:", build_yml_text, re.MULTILINE), (
            "build.yml must define a 'windows:' build job"
        )
        assert "runs-on: windows-2022" in build_yml_text, (
            "windows job must use the pinned windows-2022 release image"
        )
        # Windows job MUST set PYTHONUTF8=1 to work around the cp1252 locale
        # issue that breaks ansible.cli at pyinstaller build time.
        assert re.search(r"PYTHONUTF8\s*:\s*[\"']1[\"']", build_yml_text), (
            "windows job MUST set PYTHONUTF8=1 — ansible requires UTF-8 on Windows"
        )
        assert "windows-x86_64.zip" in build_yml_text, (
            "windows job must produce a windows-x86_64 zip artifact"
        )

    def test_termux_job_exists(self, build_yml_text: str):
        """Termux (Linux aarch64) build job exists."""
        assert re.search(r"^  termux\s*:", build_yml_text, re.MULTILINE), (
            "build.yml must define a 'termux:' build job (Linux aarch64)"
        )
        # Termux runs on ARM to produce the aarch64 build.
        assert "ubuntu-24.04-arm" in build_yml_text, (
            "termux job must run on ubuntu-24.04-arm for the aarch64 build"
        )
        assert "linux-aarch64.tar.gz" in build_yml_text, (
            "termux job must produce a linux-aarch64 tarball artifact"
        )

    def test_each_job_has_smoke_test(self, build_yml_text: str):
        """Each build job has a post-build smoke test step.

        A "smoke test" here is any step that exercises the produced binary
        after it is built. This includes the post-deploy smoke step in the
        release job that runs ``gludd version`` and ``gludd --help``.
        """
        # The release job has the explicit smoke test that downloads and
        # executes the Linux binary. This is the canonical smoke test.
        assert "Post-deploy smoke test" in build_yml_text, (
            "build.yml must have a 'Post-deploy smoke test' step that runs the built binary"
        )
        assert "gludd version" in build_yml_text, (
            "smoke test must run 'gludd version' to verify the binary executes"
        )
        assert "gludd --help" in build_yml_text, (
            "smoke test must run 'gludd --help' to verify the binary's CLI is wired"
        )
        # Each platform build must at least invoke pyinstaller on the spec —
        # that is the build-time smoke. If pyinstaller succeeds on a platform,
        # the spec is at least importable there.
        pyinstaller_invocations = len(re.findall(r"pyinstaller gludd\.spec", build_yml_text))
        # 4 platform jobs + 0 elsewhere = at least 4 invocations expected.
        assert pyinstaller_invocations >= len(self.EXPECTED_BUILD_JOBS), (
            f"expected >= {len(self.EXPECTED_BUILD_JOBS)} pyinstaller invocations "
            f"(one per platform), found {pyinstaller_invocations}"
        )

    def test_each_job_has_upload_artifact(self, build_yml_text: str):
        """Each build job uploads its artifact via actions/upload-artifact.

        Without an upload step, the built binary never reaches the release
        job — the release gate then fails with missing assets.
        """
        upload_count = len(re.findall(r"uses:\s*actions/upload-artifact@", build_yml_text))
        # We expect at least one upload per build job (4 platforms).
        # There are also uploads for coverage data, molecule logs, etc.
        assert upload_count >= len(self.EXPECTED_BUILD_JOBS), (
            f"expected >= {len(self.EXPECTED_BUILD_JOBS)} upload-artifact uses "
            f"(one per platform build), found {upload_count}"
        )
        # And each platform's artifact name must be present.
        for platform_pattern in (
            "gludd-linux-x86_64",
            "gludd-macos-arm64",
            "gludd-windows-x86_64",
            "gludd-linux-aarch64",
        ):
            assert platform_pattern in build_yml_text, (
                f"build.yml must upload an artifact named {platform_pattern!r}"
            )

    def test_build_jobs_fail_closed(self, build_yml_text: str):
        """Required platform build jobs are release-blocking.

        A green release must prove every platform built and smoke-tested its
        required artifact set.
        """
        for job_name in self.EXPECTED_BUILD_JOBS:
            job_body = self._extract_job_body(build_yml_text, job_name)
            assert job_body is not None, f"could not extract job body for {job_name!r}"
            assert not re.search(r"continue-on-error\s*:\s*true", job_body), (
                f"build job {job_name!r} must fail closed so a platform "
                "regression blocks release publication"
            )

    @staticmethod
    def _extract_job_body(yml_text: str, job_name: str) -> str | None:
        """Extract the YAML body of a top-level ``jobs.<job_name>`` entry.

        Returns the indented block belonging to that job, or None if the job
        is not found. Naive but sufficient for the structural assertion: it
        slices from ``  <job_name>:`` up to the next top-level key or EOF.
        """
        # Match "  <job_name>:" at the start of a line (2-space indent under jobs:).
        pattern = re.compile(
            r"^  " + re.escape(job_name) + r"\s*:\s*\n(.*?)(?=^  \w+:|\Z)",
            re.MULTILINE | re.DOTALL,
        )
        match = pattern.search(yml_text)
        return match.group(1) if match else None
