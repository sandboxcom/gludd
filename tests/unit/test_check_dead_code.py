"""Structural + behavioral pin for scripts/check_dead_code.py.

Proves the dead-code checker:
  - finds a class used only in tests
  - finds a function with zero references anywhere
  - does NOT flag a class imported in production code
  - treats static __all__ and exact registry entries as public API registration
  - rejects malformed, duplicate, and stale registry entries
  - exit-code contract (1 = dead code found, 0 = clean)
  - --json and --quiet output formats
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_dead_code.py"


def _load_checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_check_dead_code_under_test", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CHECKER = _load_checker()


def _run(*args: str, repo_root: Path | None = None) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, str(SCRIPT)]
    if repo_root is not None:
        cmd.extend(["--repo-root", str(repo_root)])
    cmd.extend(args)
    return subprocess.run(cmd, capture_output=True, text=True)


class TestDeadCodeCheckExitCodes:
    def test_flag_test_only_class(self, tmp_path: Path) -> None:
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

    def test_flag_orphan_function(self, tmp_path: Path) -> None:
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

    def test_clean_exit_when_no_dead_code(self, tmp_path: Path) -> None:
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

    def test_skips_private_classes(self, tmp_path: Path) -> None:
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

    def test_does_not_flag_module_with_src_reference(self, tmp_path: Path) -> None:
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

    def test_does_not_flag_symbol_used_inside_its_own_module(self, tmp_path: Path) -> None:
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

    def test_static_all_registers_only_named_public_api(self, tmp_path: Path) -> None:
        src = tmp_path / "src" / "general_ludd"
        tests = tmp_path / "tests"
        src.mkdir(parents=True)
        tests.mkdir()

        (src / "__init__.py").write_text("")
        (src / "library.py").write_text(
            "def exported_api():\n"
            "    return 1\n\n"
            "def test_only_helper():\n"
            "    return 2\n\n"
            "__all__ = [\"exported_api\"]\n"
        )
        (tests / "test_library.py").write_text(
            "from general_ludd.library import exported_api, test_only_helper\n"
        )

        result = _run("--json", repo_root=tmp_path)
        assert result.returncode == 1
        names = {item["name"] for item in json.loads(result.stdout)["dead"]}
        assert "exported_api" not in names
        assert "test_only_helper" in names

    def test_static_registry_registers_exact_module_symbol(self, tmp_path: Path) -> None:
        src = tmp_path / "src" / "general_ludd"
        tests = tmp_path / "tests"
        config = tmp_path / "config"
        src.mkdir(parents=True)
        tests.mkdir()
        config.mkdir()

        (src / "__init__.py").write_text("")
        (src / "library.py").write_text(
            "def exported_api():\n"
            "    return 1\n\n"
            "def test_only_helper():\n"
            "    return 2\n"
        )
        (tests / "test_library.py").write_text(
            "from general_ludd.library import exported_api, test_only_helper\n"
        )
        (config / "dead_code_public_api.txt").write_text(
            "src/general_ludd/library.py:exported_api\n"
        )

        result = _run("--json", repo_root=tmp_path)
        assert result.returncode == 1
        names = {item["name"] for item in json.loads(result.stdout)["dead"]}
        assert "exported_api" not in names
        assert "test_only_helper" in names


class TestDirectCheckerContracts:
    def _repo(self, root: Path) -> Path:
        src = root / "src" / "general_ludd"
        tests = root / "tests"
        src.mkdir(parents=True)
        tests.mkdir()
        (src / "__init__.py").write_text("")
        (src / "library.py").write_text(
            "def exported_api():\n"
            "    return 1\n\n"
            "def test_only_helper():\n"
            "    return 2\n\n"
            "__all__: tuple[str, ...] = (\"exported_api\",)\n"
        )
        (tests / "test_library.py").write_text(
            "from general_ludd.library import exported_api, test_only_helper\n"
        )
        return root

    def test_direct_run_and_format_contract(self, tmp_path: Path) -> None:
        result = CHECKER.run(self._repo(tmp_path))
        assert [item.symbol.name for item in result.dead] == ["test_only_helper"]
        assert "Test-only (1)" in CHECKER.format_text(result)
        assert json.loads(CHECKER.format_json(result))["dead_count"] == 1

    def test_direct_main_update_baseline_and_quiet(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = self._repo(tmp_path)
        assert CHECKER.main(["--repo-root", str(repo), "--json"]) == 1
        assert json.loads(capsys.readouterr().out)["new_dead_count"] == 1

        assert CHECKER.main(["--repo-root", str(repo), "--update-baseline"]) == 0
        baseline = repo / "config" / "dead_code_baseline.txt"
        assert "test_only_helper" in baseline.read_text()
        capsys.readouterr()

        assert CHECKER.main(["--repo-root", str(repo), "--quiet"]) == 0
        assert "0 new dead symbol" in capsys.readouterr().out

    def test_baseline_read_and_write_modes_are_mutually_exclusive(self, tmp_path: Path) -> None:
        repo = self._repo(tmp_path)
        with pytest.raises(SystemExit) as raised:
            CHECKER.main([
                "--repo-root",
                str(repo),
                "--check-baseline-current",
                "--update-baseline",
            ])
        assert raised.value.code == 2
        assert not (repo / "config" / "dead_code_baseline.txt").exists()

    def test_check_baseline_current_is_read_only_and_detects_drift(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = self._repo(tmp_path)
        baseline = repo / "config" / "dead_code_baseline.txt"

        assert CHECKER.main(["--repo-root", str(repo), "--check-baseline-current"]) == 1
        assert not baseline.exists()
        assert "added=1" in capsys.readouterr().out

        assert CHECKER.main(["--repo-root", str(repo), "--update-baseline"]) == 0
        capsys.readouterr()
        original = baseline.read_bytes()
        assert CHECKER.main(["--repo-root", str(repo), "--check-baseline-current"]) == 0
        assert baseline.read_bytes() == original
        assert "current" in capsys.readouterr().out

        library = repo / "src" / "general_ludd" / "library.py"
        library.write_text(library.read_text().replace(
            '__all__: tuple[str, ...] = ("exported_api",)',
            '__all__: tuple[str, ...] = ("exported_api", "test_only_helper")',
        ))
        assert CHECKER.main(["--repo-root", str(repo), "--check-baseline-current"]) == 1
        assert baseline.read_bytes() == original
        assert "stale=1" in capsys.readouterr().out

    def test_compact_runtime_baseline_keeps_only_reviewed_compatibility_api(self) -> None:
        """Pin the reviewed runtime API while production wiring removes stale debt."""
        repo = SCRIPT.parents[1]
        entries = set(
            (repo / "config" / "dead_code_baseline.txt")
            .read_text(encoding="utf-8")
            .splitlines()
        )

        assert (
            "src/general_ludd/self_improve/runtime.py:generate_local_proposal_plan"
            in entries
        )
        assert "src/general_ludd/self_improve/runtime.py:build_failure_diagnostic" not in entries
        assert "src/general_ludd/self_improve/runtime.py:evaluate_attempt_feedback" not in entries
        assert "src/general_ludd/hardware/model_fit.py:unified_probe" not in entries

    def test_static_export_parser_is_conservative(self, tmp_path: Path) -> None:
        static_file = tmp_path / "static.py"
        static_file.write_text('__all__: tuple[str, ...] = ("public",)\n')
        assert CHECKER._declared_public_names(static_file) == {"public"}

        mixed_file = tmp_path / "mixed.py"
        mixed_file.write_text('__all__ = ["public", 3]\n')
        assert CHECKER._declared_public_names(mixed_file) == set()

        dynamic_file = tmp_path / "dynamic.py"
        dynamic_file.write_text("__all__ = build_exports()\n")
        assert CHECKER._declared_public_names(dynamic_file) == set()

        invalid_file = tmp_path / "invalid.py"
        invalid_file.write_text("def broken(:\n")
        assert CHECKER._declared_public_names(invalid_file) == set()
        assert CHECKER._declared_public_names(tmp_path / "missing.py") == set()

    def test_missing_source_root_exits_two(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit, match="2"):
            CHECKER.run(tmp_path)

    @pytest.mark.parametrize(
        "registry",
        [
            "not-a-registry-key\n",
            "src/general_ludd/library.py:missing_api\n",
            (
                "src/general_ludd/library.py:exported_api\n"
                "src/general_ludd/library.py:exported_api\n"
            ),
        ],
    )
    def test_public_registry_rejects_invalid_or_stale_entries(
        self, tmp_path: Path, registry: str
    ) -> None:
        repo = self._repo(tmp_path)
        config = repo / "config"
        config.mkdir()
        (config / "dead_code_public_api.txt").write_text(registry)

        with pytest.raises(SystemExit, match="2"):
            CHECKER.run(repo)


class TestCheckerBranchContracts:
    def test_registry_and_baseline_loaders_are_bounded(self, tmp_path: Path) -> None:
        missing = tmp_path / "missing.txt"
        assert CHECKER._load_baseline(None) == set()
        assert CHECKER._load_baseline(missing) == set()
        assert CHECKER._load_public_api_registry(missing, set()) == set()

        baseline = tmp_path / "baseline.txt"
        baseline.write_text("# comment\n\nkey\nkey\n", encoding="utf-8")
        assert CHECKER._load_baseline(baseline) == {"key"}

    def test_symbol_and_reference_parsers_handle_all_static_forms(
        self, tmp_path: Path
    ) -> None:
        repo = tmp_path
        src = repo / "src" / "general_ludd"
        src.mkdir(parents=True)
        invalid = src / "invalid.py"
        invalid.write_text("def broken(:\n", encoding="utf-8")
        assert CHECKER._extract_symbols(invalid, repo) == []
        assert CHECKER._extract_symbols(src / "missing.py", repo) == []

        module = src / "module.py"
        module.write_text(
            "class PublicClass:\n"
            "    pass\n\n"
            "class _PrivateClass:\n"
            "    pass\n\n"
            "async def public_async():\n"
            "    return None\n\n"
            "def main():\n"
            "    return None\n",
            encoding="utf-8",
        )
        assert {item.name for item in CHECKER._extract_symbols(module, repo)} == {
            "PublicClass",
            "public_async",
        }

        references = src / "references.py"
        references.write_text(
            "from package import PublicClass as Alias\n"
            "result = module.public_async\n",
            encoding="utf-8",
        )
        assert CHECKER._referenced_names(
            references, {"PublicClass", "Alias", "public_async"}
        ) == {"PublicClass", "Alias", "public_async"}
        assert CHECKER._referenced_names(invalid, {"broken"}) == set()

    def test_formatters_cover_detailed_and_empty_reports(self) -> None:
        public = CHECKER.Symbol(
            name="Public",
            kind="class",
            file="src/general_ludd/public.py",
            line=7,
            module="general_ludd.public",
        )
        orphan = CHECKER.Symbol(
            name="Orphan",
            kind="function",
            file="src/general_ludd/orphan.py",
            line=9,
            module="general_ludd.orphan",
        )
        detailed = CHECKER.ScanResult(
            symbols=[public, orphan],
            dead=[
                CHECKER.DeadSymbol(
                    symbol=public,
                    referenced_in=["a.py", "b.py", "c.py", "d.py"],
                ),
                CHECKER.DeadSymbol(symbol=orphan),
            ],
            files_scanned=2,
        )
        report = CHECKER.format_text(detailed)
        assert "Test-only (1)" in report
        assert "(+1 more)" in report
        assert "Orphans (1)" in report
        assert "no references found" in report
        assert json.loads(CHECKER.format_json(detailed))["dead_count"] == 2

        empty = CHECKER.ScanResult(files_scanned=3)
        assert CHECKER.format_text(empty) == "dead-code: 0 dead symbol(s) across 3 file(s)\n"

    def test_direct_main_quiet_mode_reports_new_findings(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        src = tmp_path / "src" / "general_ludd"
        tests = tmp_path / "tests"
        src.mkdir(parents=True)
        tests.mkdir()
        (src / "__init__.py").write_text("", encoding="utf-8")
        (src / "module.py").write_text(
            "def orphan():\n    return 1\n", encoding="utf-8"
        )
        (tests / "__init__.py").write_text("", encoding="utf-8")

        assert CHECKER.main(
            ["--repo-root", str(tmp_path), "--quiet"]
        ) == 1
        assert "1 NEW dead symbol" in capsys.readouterr().out


class TestJsonOutput:
    def test_json_structure(self, tmp_path: Path) -> None:
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
    def test_quiet_format(self, tmp_path: Path) -> None:
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
    def test_makefile_has_check_dead_code_target(self) -> None:
        makefile = Path(__file__).resolve().parents[2] / "Makefile"
        content = makefile.read_text()
        assert "\ncheck-dead-code:" in content
        assert "\ncheck-dead-code-json:" in content
        assert "\ncheck-dead-code-quiet:" in content
