"""Integration tests for binary_re roles — CLI entry points and artifact
structure across all 5 backend generators (gdb, radare2, ghidra, frida,
cyberchef). Validates that each role's main() produces well-formed JSON
artifacts with the expected fields, exercising the full argparse → generator
→ json.dump pipeline rather than individual generators in isolation.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

_COLLECTION_ROOT = (
    Path(__file__).resolve().parents[2]
    / "collections/ansible_collections/general_ludd/binary_re"
)

_ROLE_FILES = {
    "gdb": _COLLECTION_ROOT / "roles" / "gdb_analyze" / "files",
    "radare2": _COLLECTION_ROOT / "roles" / "radare2_analyze" / "files",
    "ghidra": _COLLECTION_ROOT / "roles" / "ghidra_analyze" / "files",
    "frida": _COLLECTION_ROOT / "roles" / "frida_instrument" / "files",
    "cyberchef": _COLLECTION_ROOT / "roles" / "cyberchef_transform" / "files",
}

for _p in _ROLE_FILES.values():
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

try:
    _gdb = importlib.import_module("gdb_analyze")
    _r2 = importlib.import_module("radare2_analyze")
    _ghidra = importlib.import_module("ghidra_analyze")
    _frida = importlib.import_module("frida_instrument")
    _cyberchef = importlib.import_module("cyberchef")
except ModuleNotFoundError as e:
    pytest.skip(
        f"binary_re backend module not available: {e}",
        allow_module_level=True,
    )


def _cli(module, args: list[str]) -> None:
    backup = sys.argv
    sys.argv = args
    try:
        module.main()
    finally:
        sys.argv = backup


def _cli_json(module, args: list[str], tmp_path: Path) -> dict:
    out = tmp_path / "artifact.json"
    _cli(module, [*args, "--output", str(out)])
    return json.loads(out.read_text())


class TestGdbAnalyzeCLI:
    def test_breakpoint_artifact_structure(self, tmp_path):
        data = _cli_json(
            _gdb,
            ["gdb_analyze.py", "--target", "/bin/ls",
             "--mode", "breakpoint", "--breakpoints", "main,foo,bar"],
            tmp_path,
        )
        assert data["mode"] == "breakpoint"
        assert data["target"] == "/bin/ls"
        assert data["backend"] == "report-only"
        assert isinstance(data["commands"], list)
        assert any("break main" in c for c in data["commands"])
        assert any("break foo" in c for c in data["commands"])
        assert any("break bar" in c for c in data["commands"])
        assert data["commands"][-1] == "quit"

    def test_stack_trace_artifact_structure(self, tmp_path):
        data = _cli_json(
            _gdb,
            ["gdb_analyze.py", "--target", "/bin/sh",
             "--mode", "stack_trace"],
            tmp_path,
        )
        assert data["mode"] == "stack_trace"
        assert data["backend"] == "report-only"
        cmds = data["commands"]
        assert "bt full" in cmds
        assert "catch throw" in cmds
        assert "set pagination off" in cmds

    def test_register_dump_artifact_structure(self, tmp_path):
        data = _cli_json(
            _gdb,
            ["gdb_analyze.py", "--target", "/bin/cat",
             "--mode", "register_dump"],
            tmp_path,
        )
        assert data["mode"] == "register_dump"
        cmds = data["commands"]
        assert "info registers" in cmds
        assert any("print $pc" in c for c in cmds)
        assert "break main" in cmds

    def test_scripted_artifact_structure(self, tmp_path):
        data = _cli_json(
            _gdb,
            ["gdb_analyze.py", "--target", "/bin/ls",
             "--mode", "scripted"],
            tmp_path,
        )
        assert data["mode"] == "scripted"
        assert "script" in data
        assert "log_file" in data
        assert "import gdb" in data["script"]
        assert data["log_file"] == "/tmp/gludd-gdb-scripted.log"


class TestRadare2AnalyzeCLI:
    def test_disassembly_artifact_structure(self, tmp_path):
        data = _cli_json(
            _r2,
            ["radare2_analyze.py", "--target", "/bin/ls",
             "--mode", "disassembly", "--depth", "2"],
            tmp_path,
        )
        assert data["mode"] == "disassembly"
        assert data["backend"] == "report-only"
        cmds = data["commands"]
        assert "aaa" in cmds
        assert "afl" in cmds
        assert "s main" in cmds
        assert any(c.startswith("agCd") for c in cmds)

    def test_entropy_scan_artifact_structure(self, tmp_path):
        data = _cli_json(
            _r2,
            ["radare2_analyze.py", "--target", "/bin/ls",
             "--mode", "entropy_scan"],
            tmp_path,
        )
        assert data["mode"] == "entropy_scan"
        cmds = data["commands"]
        assert "p=e 100" in cmds
        assert "iS~entropy" in cmds

    def test_string_search_artifact_structure(self, tmp_path):
        data = _cli_json(
            _r2,
            ["radare2_analyze.py", "--target", "/bin/ls",
             "--mode", "string_search", "--string-regex", "secret"],
            tmp_path,
        )
        assert data["mode"] == "string_search"
        assert data["regex"] == "secret"
        cmds = data["commands"]
        assert "/ secret" in cmds
        assert "/j secret" in cmds

    def test_cfg_analysis_artifact_structure(self, tmp_path):
        data = _cli_json(
            _r2,
            ["radare2_analyze.py", "--target", "/bin/ls",
             "--mode", "cfg_analysis"],
            tmp_path,
        )
        assert data["mode"] == "cfg_analysis"
        assert data["dot_file"] == "/tmp/gludd-r2-cfg.dot"
        cmds = data["commands"]
        assert any("agCd" in c for c in cmds)
        assert any("agCj" in c for c in cmds)


class TestGhidraAnalyzeCLI:
    def test_headless_analysis_artifact_structure(self, tmp_path):
        data = _cli_json(
            _ghidra,
            ["ghidra_analyze.py", "--target", "/bin/ls",
             "--mode", "headless_analysis",
             "--ghidra-path", "/opt/ghidra",
             "--project-dir", "/tmp/proj"],
            tmp_path,
        )
        assert data["mode"] == "headless_analysis"
        assert data["backend"] == "report-only"
        assert data["ghidra_path"] == "/opt/ghidra"
        assert data["project_dir"] == "/tmp/proj"
        assert "analyzeHeadless" in data["invocation"]
        assert "/bin/ls" in data["invocation"]

    def test_scripted_export_artifact_structure(self, tmp_path):
        data = _cli_json(
            _ghidra,
            ["ghidra_analyze.py", "--target", "/bin/ls",
             "--mode", "scripted_export"],
            tmp_path,
        )
        assert data["mode"] == "scripted_export"
        assert "postscript" in data
        assert "DecompInterface" in data["postscript"]
        assert data["postscript_path"] == "/tmp/gludd-ghidra-decompile.py"
        assert "-postScript" in data["invocation"]

    def test_function_signature_artifact_structure(self, tmp_path):
        data = _cli_json(
            _ghidra,
            ["ghidra_analyze.py", "--target", "/bin/ls",
             "--mode", "function_signature"],
            tmp_path,
        )
        assert data["mode"] == "function_signature"
        assert "postscript" in data
        assert "getSignature" in data["postscript"]
        assert data["postscript_path"] == "/tmp/gludd-ghidra-fnsig.py"


class TestFridaInstrumentCLI:
    def test_function_interception_artifact(self, tmp_path):
        data = _cli_json(
            _frida,
            ["frida_instrument.py", "--target", "/usr/bin/curl",
             "--mode", "function_interception", "--process", "curl",
             "--targets", "open,read,close"],
            tmp_path,
        )
        assert data["mode"] == "function_interception"
        assert data["backend"] == "report-only"
        assert data["target_symbols"] == ["open", "read", "close"]
        assert "Interceptor.attach" in data["script"]
        assert "curl" in data["invocation"]

    def test_memory_scanning_artifact(self, tmp_path):
        data = _cli_json(
            _frida,
            ["frida_instrument.py", "--target", "/usr/bin/curl",
             "--mode", "memory_scanning", "--process", "curl",
             "--pattern", "DE AD BE EF", "--hit-cap", "250"],
            tmp_path,
        )
        assert data["mode"] == "memory_scanning"
        assert data["hit_cap"] == 250
        assert "DE AD BE EF" in data["script"]
        assert "Memory.scan" in data["script"]

    def test_ssl_pinning_bypass_artifact(self, tmp_path):
        data = _cli_json(
            _frida,
            ["frida_instrument.py", "--target", "/usr/bin/curl",
             "--mode", "ssl_pinning_bypass", "--process", "curl"],
            tmp_path,
        )
        assert data["mode"] == "ssl_pinning_bypass"
        assert "ssl_verify_peer_cert" in data["hooks"]
        assert "SecTrustEvaluate" in data["hooks"]
        assert "ssl_verify_peer_cert" in data["script"]


class TestCyberChefCLI:
    def test_base64_decode_local(self, tmp_path):
        data = _cli_json(
            _cyberchef,
            ["cyberchef.py", "--input", "aGVsbG8=",
             "--recipe", "base64_decode"],
            tmp_path,
        )
        assert data["recipe"] == "base64_decode"
        assert data["backend"] == "local"
        assert data["output"] == "hello"
        assert data["module"] == "encoding"

    def test_rot13_local(self, tmp_path):
        data = _cli_json(
            _cyberchef,
            ["cyberchef.py", "--input", "hello", "--recipe", "rot13"],
            tmp_path,
        )
        assert data["recipe"] == "rot13"
        assert data["backend"] == "local"
        assert data["output"] == "uryyb"

    def test_xor_with_key(self, tmp_path):
        data = _cli_json(
            _cyberchef,
            ["cyberchef.py", "--input", "AAAA", "--recipe", "xor",
             "--key", "B"],
            tmp_path,
        )
        assert data["recipe"] == "xor"
        assert data["module"] == "encryption"
        assert data["key_used"] == "B"

    def test_list_recipes_to_stdout(self, capsys):
        _cli(
            _cyberchef,
            ["cyberchef.py", "--input", "x", "--recipe", "x",
             "--list-recipes"],
        )
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "base64_decode" in data
        assert "xor" in data
        assert data["base64_decode"]["module"] == "encoding"
        assert data["xor"]["input_type"] == "text+key"


class TestAllBackendsImportable:
    def test_all_five_modules_have_main_callable(self):
        for mod in (_gdb, _r2, _ghidra, _frida, _cyberchef):
            assert hasattr(mod, "main")
            assert callable(mod.main)

    def test_report_only_backends_advertise_mode(self):
        for mod in (_gdb, _r2, _ghidra, _frida):
            assert hasattr(mod, "VALID_MODES")
            assert len(mod.VALID_MODES) >= 2
