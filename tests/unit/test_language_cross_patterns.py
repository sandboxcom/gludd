"""TDD tests for Cross-Language Pattern detection.

Tests for ``src/general_ludd/language/cross_patterns.py``. Verifies:

- ``detect_cross_language_imports`` finds cross-language dependency patterns.
- ``detect_ffi_patterns`` identifies foreign function interface usage.
- ``detect_polyglot_builds`` finds projects using multiple build systems.
- ``detect_script_invocations`` finds language A calling language B via subprocess.
- Edge cases: empty input, binary files, non-existent files.

These tests FAIL until the module is implemented.
"""

from __future__ import annotations

from pathlib import Path

# ── detect_cross_language_imports ────────────────────────────────────────────


class TestCrossLanguageImports:
    """Detection of cross-language import/dependency patterns."""

    def test_module_importable(self) -> None:
        from general_ludd.language import cross_patterns

        assert cross_patterns is not None

    def test_python_calling_subprocess_shell(self, tmp_path: Path) -> None:
        from general_ludd.language.cross_patterns import (
            detect_cross_language_imports,
        )

        f = tmp_path / "script.py"
        f.write_text(
            "import subprocess\nsubprocess.run(['node', 'script.js'])\nsubprocess.check_call(['ruby', 'helper.rb'])\n",
            encoding="utf-8",
        )

        findings = detect_cross_language_imports([f])
        assert len(findings) >= 1
        sources = {item["source_language"] for item in findings}
        assert "python" in sources

    def test_python_ctypes_ffi(self, tmp_path: Path) -> None:
        from general_ludd.language.cross_patterns import detect_cross_language_imports

        f = tmp_path / "ffi.py"
        f.write_text(
            "import ctypes\nlib = ctypes.CDLL('./libfoo.so')\nfrom cffi import FFI\n",
            encoding="utf-8",
        )

        findings = detect_cross_language_imports([f])
        assert len(findings) >= 1

    def test_rust_extern_c(self, tmp_path: Path) -> None:
        from general_ludd.language.cross_patterns import detect_cross_language_imports

        f = tmp_path / "ffi.rs"
        f.write_text(
            'extern "C" {\n    fn sqrt(x: f64) -> f64;\n}\n#[link(name = "m")]\nextern {}\n',
            encoding="utf-8",
        )

        findings = detect_cross_language_imports([f])
        assert len(findings) >= 1
        assert findings[0]["source_language"] == "rust"

    def test_clean_file_no_findings(self, tmp_path: Path) -> None:
        from general_ludd.language.cross_patterns import detect_cross_language_imports

        f = tmp_path / "clean.py"
        f.write_text("def hello():\n    return 'world'\n", encoding="utf-8")

        findings = detect_cross_language_imports([f])
        assert findings == []

    def test_empty_file_list(self) -> None:
        from general_ludd.language.cross_patterns import detect_cross_language_imports

        assert detect_cross_language_imports([]) == []

    def test_nonexistent_file_skipped(self, tmp_path: Path) -> None:
        from general_ludd.language.cross_patterns import detect_cross_language_imports

        findings = detect_cross_language_imports([tmp_path / "nope.py"])
        assert findings == []

    def test_go_cgo_import(self, tmp_path: Path) -> None:
        from general_ludd.language.cross_patterns import detect_cross_language_imports

        f = tmp_path / "cgo_example.go"
        f.write_text(
            'package main\n\nimport "C"\n\n//export GoCallback\nfunc GoCallback() {}\nfunc main() { C.do_thing() }\n',
            encoding="utf-8",
        )

        findings = detect_cross_language_imports([f])
        assert len(findings) >= 1
        assert findings[0]["source_language"] == "go"


# ── detect_ffi_patterns ─────────────────────────────────────────────────────


class TestFFIPatterns:
    """Detection of Foreign Function Interface patterns."""

    def test_detects_python_cffi_builder(self, tmp_path: Path) -> None:
        from general_ludd.language.cross_patterns import detect_ffi_patterns

        f = tmp_path / "build_ffi.py"
        f.write_text(
            'from cffi import FFI\nffibuilder = FFI()\nffibuilder.cdef("int add(int, int);")\n',
            encoding="utf-8",
        )

        findings = detect_ffi_patterns([f])
        assert len(findings) >= 1
        assert findings[0]["ffi_type"] in {"cffi", "ctypes"}

    def test_detects_rust_extern_block(self, tmp_path: Path) -> None:
        from general_ludd.language.cross_patterns import detect_ffi_patterns

        f = tmp_path / "bindings.rs"
        f.write_text(
            '#[link(name = "readline")]\nextern {\n    fn readline(prompt: *const c_char) -> *mut c_char;\n}\n',
            encoding="utf-8",
        )

        findings = detect_ffi_patterns([f])
        assert len(findings) >= 1
        assert findings[0]["ffi_type"] == "extern_block"

    def test_detects_swig_interface(self, tmp_path: Path) -> None:
        from general_ludd.language.cross_patterns import detect_ffi_patterns

        f = tmp_path / "module.i"
        f.write_text('%module example\n%{\n#include "header.h"\n%}\n', encoding="utf-8")

        findings = detect_ffi_patterns([f])
        assert len(findings) >= 1
        assert findings[0]["ffi_type"] == "swig"

    def test_clean_file_no_ffi(self, tmp_path: Path) -> None:
        from general_ludd.language.cross_patterns import detect_ffi_patterns

        f = tmp_path / "plain.py"
        f.write_text("print('hello')\n", encoding="utf-8")

        assert detect_ffi_patterns([f]) == []

    def test_empty_input(self) -> None:
        from general_ludd.language.cross_patterns import detect_ffi_patterns

        assert detect_ffi_patterns([]) == []


# ── detect_polyglot_builds ───────────────────────────────────────────────────


class TestPolyglotBuilds:
    """Detection of repositories using multiple build systems."""

    def test_detects_python_and_node_build_files(self, tmp_path: Path) -> None:
        from general_ludd.language.cross_patterns import detect_polyglot_builds

        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
        (tmp_path / "package.json").write_text('{"name":"x"}\n', encoding="utf-8")

        result = detect_polyglot_builds(str(tmp_path))
        assert isinstance(result, list)
        assert len(result) >= 2
        systems = {item["build_system"] for item in result}
        assert "python" in systems or "uv" in systems or "pip" in systems

    def test_single_build_system_returns_one_entry(self, tmp_path: Path) -> None:
        from general_ludd.language.cross_patterns import detect_polyglot_builds

        (tmp_path / "Cargo.toml").write_text("[package]\nname='x'\n", encoding="utf-8")

        result = detect_polyglot_builds(str(tmp_path))
        assert len(result) == 1

    def test_empty_directory_returns_empty(self, tmp_path: Path) -> None:
        from general_ludd.language.cross_patterns import detect_polyglot_builds

        assert detect_polyglot_builds(str(tmp_path)) == []

    def test_nonexistent_path_returns_empty(self) -> None:
        from general_ludd.language.cross_patterns import detect_polyglot_builds

        assert detect_polyglot_builds("/no/such/path") == []

    def test_each_build_hit_has_file_and_system(self, tmp_path: Path) -> None:
        from general_ludd.language.cross_patterns import detect_polyglot_builds

        (tmp_path / "Makefile").write_text("all:\n\techo hi\n", encoding="utf-8")
        (tmp_path / "go.mod").write_text("module x\n", encoding="utf-8")

        result = detect_polyglot_builds(str(tmp_path))
        for item in result:
            assert "build_system" in item
            assert "file" in item
            assert isinstance(item["build_system"], str)


# ── detect_script_invocations ────────────────────────────────────────────────


class TestScriptInvocations:
    """Detection of one scripting language invoking another via subprocess/exec."""

    def test_python_executing_node(self, tmp_path: Path) -> None:
        from general_ludd.language.cross_patterns import detect_script_invocations

        f = tmp_path / "runner.py"
        f.write_text(
            "import os\nos.system('node build.js')\nos.popen('ruby -e puts')\n",
            encoding="utf-8",
        )

        findings = detect_script_invocations([f])
        assert len(findings) >= 1

    def test_bash_calling_python(self, tmp_path: Path) -> None:
        from general_ludd.language.cross_patterns import detect_script_invocations

        f = tmp_path / "deploy.sh"
        f.write_text(
            "#!/bin/bash\npython3 deploy.py\nnpm run build\n",
            encoding="utf-8",
        )

        findings = detect_script_invocations([f])
        assert len(findings) >= 1

    def test_clean_file_no_invocations(self, tmp_path: Path) -> None:
        from general_ludd.language.cross_patterns import detect_script_invocations

        f = tmp_path / "math.py"
        f.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

        assert detect_script_invocations([f]) == []

    def test_empty_file_list(self) -> None:
        from general_ludd.language.cross_patterns import detect_script_invocations

        assert detect_script_invocations([]) == []
