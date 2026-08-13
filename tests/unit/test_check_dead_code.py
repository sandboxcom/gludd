"""Structural + behavioral pin for scripts/check_dead_code.py.

Proves the dead-code checker:
  - finds a class used only in tests
  - finds a function with zero references anywhere
  - does NOT flag a class imported in production code
  - exit-code contract (1 = dead code found, 0 = clean)
  - --json and --quiet output formats
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_dead_code.py"


def _run(*args: str, repo_root: Path | None = None) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, str(SCRIPT)]
    if repo_root is not None:
        cmd.extend(["--repo-root", str(repo_root)])
    cmd.extend(args)
    return subprocess.run(cmd, capture_output=True, text=True)


class TestDeadCodeCheckExitCodes:
    def test_flag_test_only_class(self, tmp_path: Path):
        src = tmp_path / "src" / "general_ludd"
        tests = tmp_path / "tests"
        src.mkdir(parents=True)
        tests.mkdir()

        (src / "__init__.py").write_text("")
        (src / "mod.py").write_text("class UnusedHelper:\n    pass\n")
        (tests / "test_mod.py").write_text(
            "from general_ludd.mod import UnusedHelper\n\ndef test_foo():\n    pass\n"
        )

        result = _run("--json", repo_root=tmp_path)
        assert result.returncode == 1
        data = json.loads(result.stdout)
        assert data["dead_count"] >= 1
        names = [d["name"] for d in data["dead"]]
        assert "UnusedHelper" in names

    def test_flag_orphan_function(self, tmp_path: Path):
        src = tmp_path / "src" / "general_ludd"
        tests = tmp_path / "tests"
        src.mkdir(parents=True)
        tests.mkdir()

        (src / "__init__.py").write_text("")
        (src / "util.py").write_text(
            "def forgotten_util():\n    return 42\n"
        )
        (tests / "__init__.py").write_text("")

        result = _run("--json", repo_root=tmp_path)
        assert result.returncode == 1
        data = json.loads(result.stdout)
        names = [d["name"] for d in data["dead"]]
        assert "forgotten_util" in names

    def test_clean_exit_when_no_dead_code(self, tmp_path: Path):
        src = tmp_path / "src" / "general_ludd"
        tests = tmp_path / "tests"
        src.mkdir(parents=True)
        tests.mkdir()

        (src / "__init__.py").write_text("")
        (src / "lib.py").write_text("class Used:\n    pass\n")
        (src / "consumer.py").write_text(
            "from general_ludd.lib import Used\n"
        )
        (tests / "__init__.py").write_text("")

        result = _run("--json", repo_root=tmp_path)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["dead_count"] == 0

    def test_skips_private_classes(self, tmp_path: Path):
        src = tmp_path / "src" / "general_ludd"
        tests = tmp_path / "tests"
        src.mkdir(parents=True)
        tests.mkdir()

        (src / "__init__.py").write_text("")
        (src / "mod.py").write_text("class _Internal:\n    pass\n")
        (tests / "__init__.py").write_text("")

        result = _run("--json", repo_root=tmp_path)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["dead_count"] == 0

    def test_does_not_flag_module_with_src_reference(self, tmp_path: Path):
        src = tmp_path / "src" / "general_ludd"
        tests = tmp_path / "tests"
        src.mkdir(parents=True)
        tests.mkdir()

        (src / "__init__.py").write_text("")
        (src / "provider.py").write_text("class Provider:\n    pass\n")
        (src / "caller.py").write_text(
            "from general_ludd.provider import Provider\n"
        )
        (tests / "test_provider.py").write_text(
            "from general_ludd.provider import Provider\n\ndef test_p():\n    pass\n"
        )

        result = _run("--json", repo_root=tmp_path)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        names = [d["name"] for d in data["dead"]]
        assert "Provider" not in names

    def test_does_not_flag_symbol_used_inside_its_own_module(self, tmp_path: Path):
        src = tmp_path / "src" / "general_ludd"
        tests = tmp_path / "tests"
        src.mkdir(parents=True)
        tests.mkdir()

        (src / "__init__.py").write_text("")
        (src / "provider.py").write_text(
            "class Provider:\n"
            "    pass\n\n"
            "def build_provider() -> Provider:\n"
            "    return Provider()\n"
        )
        (src / "consumer.py").write_text(
            "from general_ludd.provider import build_provider\n\n"
            "provider = build_provider()\n"
        )
        (tests / "__init__.py").write_text("")

        result = _run("--json", repo_root=tmp_path)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        names = [d["name"] for d in data["dead"]]
        assert "Provider" not in names


class TestJsonOutput:
    def test_json_structure(self, tmp_path: Path):
        src = tmp_path / "src" / "general_ludd"
        src.mkdir(parents=True)
        (src / "__init__.py").write_text("")
        (src / "mod.py").write_text("class Ghost:\n    pass\n")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "__init__.py").write_text("")

        result = _run("--json", repo_root=tmp_path)
        data = json.loads(result.stdout)
        assert "files_scanned" in data
        assert "symbols_total" in data
        assert "dead_count" in data
        assert "dead" in data
        assert data["files_scanned"] >= 1
        for d in data["dead"]:
            for key in ("file", "line", "name", "kind", "module", "orphan"):
                assert key in d, f"missing key {key} in {d}"


class TestQuietOutput:
    def test_quiet_format(self, tmp_path: Path):
        src = tmp_path / "src" / "general_ludd"
        src.mkdir(parents=True)
        (src / "__init__.py").write_text("")
        (src / "mod.py").write_text("class Ghost:\n    pass\n")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "__init__.py").write_text("")

        result = _run("--quiet")
        assert "dead-code:" in result.stdout
        assert "\n" not in result.stdout.strip()


class TestMakeTargetExists:
    def test_makefile_has_check_dead_code_target(self):
        makefile = Path(__file__).resolve().parents[2] / "Makefile"
        content = makefile.read_text()
        assert "\ncheck-dead-code:" in content
        assert "\ncheck-dead-code-json:" in content
        assert "\ncheck-dead-code-quiet:" in content
