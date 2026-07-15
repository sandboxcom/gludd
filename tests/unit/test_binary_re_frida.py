"""Tests for frida_instrument role — function interception, memory scanning,
SSL pinning bypass, and Stalker tracing generators. Report-only.
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
_FRIDA_FILES = _COLLECTION_ROOT / "roles" / "frida_instrument" / "files"

if str(_FRIDA_FILES) not in sys.path:
    sys.path.insert(0, str(_FRIDA_FILES))

try:
    _fi = importlib.import_module("frida_instrument")
    gen_function_interception = _fi.gen_function_interception
    gen_memory_scanning = _fi.gen_memory_scanning
    gen_ssl_pinning_bypass = _fi.gen_ssl_pinning_bypass
    gen_tracing = _fi.gen_tracing
    VALID_MODES = _fi.VALID_MODES
    _MODES = _fi._MODES
except ModuleNotFoundError:
    pytest.skip("frida_instrument module not available", allow_module_level=True)


class TestValidModes:
    def test_has_four_modes(self):
        assert set(VALID_MODES) == {
            "function_interception",
            "memory_scanning",
            "ssl_pinning_bypass",
            "tracing",
        }

    def test_modes_dict_describes_each_mode(self):
        for mode in VALID_MODES:
            assert mode in _MODES
            assert isinstance(_MODES[mode], str)
            assert len(_MODES[mode]) > 0


class TestFunctionInterception:
    def test_returns_dict_with_required_keys(self):
        result = gen_function_interception(
            target="/usr/bin/ls",
            targets=["open", "read"],
            process_spec="ls",
        )
        for key in ("script", "script_path", "invocation", "process_spec",
                    "target_symbols"):
            assert key in result

    def test_script_includes_targets(self):
        result = gen_function_interception(
            target="/usr/bin/ls",
            targets=["open", "close"],
            process_spec="ls",
        )
        assert "open" in result["script"]
        assert "close" in result["script"]
        assert "Interceptor.attach" in result["script"]

    def test_script_has_onenter_onleave(self):
        result = gen_function_interception(
            target="/bin/sh",
            targets=["system"],
            process_spec="sh",
        )
        assert "onEnter" in result["script"]
        assert "onLeave" in result["script"]

    def test_invocation_calls_frida(self):
        result = gen_function_interception(
            target="/usr/bin/ls",
            targets=["open"],
            process_spec="ls",
        )
        assert "frida" in result["invocation"]
        assert "ls" in result["invocation"]

    def test_empty_targets_filtered(self):
        result = gen_function_interception(
            target="/usr/bin/ls",
            targets=["open", "", "read", ""],
            process_spec="ls",
        )
        assert result["target_symbols"] == ["open", "read"]

    def test_script_path_is_stable(self):
        result = gen_function_interception(
            target="/usr/bin/ls",
            targets=["open"],
            process_spec="ls",
        )
        assert result["script_path"] == "/tmp/gludd-frida-intercept.js"


class TestMemoryScanning:
    def test_returns_dict_with_required_keys(self):
        result = gen_memory_scanning(
            target="/usr/bin/ls",
            pattern="AA BB CC DD",
            hit_cap=500,
            process_spec="ls",
        )
        for key in ("script", "script_path", "invocation", "pattern",
                    "hit_cap"):
            assert key in result

    def test_script_includes_pattern(self):
        result = gen_memory_scanning(
            target="/usr/bin/ls",
            pattern="DE AD BE EF",
            hit_cap=100,
            process_spec="ls",
        )
        assert "DE AD BE EF" in result["script"]

    def test_script_uses_memory_scan(self):
        result = gen_memory_scanning(
            target="/usr/bin/ls",
            pattern="AA BB",
            hit_cap=10,
            process_spec="ls",
        )
        assert "Memory.scan" in result["script"]
        assert "enumerateRanges" in result["script"]

    def test_empty_pattern_defaults(self):
        result = gen_memory_scanning(
            target="/usr/bin/ls",
            pattern="",
            hit_cap=10,
            process_spec="ls",
        )
        assert result["pattern"] == "AA BB CC DD"

    def test_hit_cap_serialized_as_int(self):
        result = gen_memory_scanning(
            target="/usr/bin/ls",
            pattern="AA",
            hit_cap=42,
            process_spec="ls",
        )
        assert result["hit_cap"] == 42
        assert "HIT_CAP = 42" in result["script"]

    def test_pattern_with_quotes_escaped(self):
        result = gen_memory_scanning(
            target="/usr/bin/ls",
            pattern='AA "weird" BB',
            hit_cap=10,
            process_spec="ls",
        )
        assert '\\"weird\\"' in result["script"]


class TestSSLPinningBypass:
    def test_returns_dict_with_required_keys(self):
        result = gen_ssl_pinning_bypass(
            target="/usr/bin/curl", process_spec="curl"
        )
        for key in ("script", "script_path", "invocation", "hooks"):
            assert key in result

    def test_hooks_boringssl(self):
        result = gen_ssl_pinning_bypass(
            target="/usr/bin/curl", process_spec="curl"
        )
        assert "ssl_verify_peer_cert" in result["hooks"]
        assert "ssl_verify_peer_cert" in result["script"]

    def test_hooks_sec_trust_evaluate(self):
        result = gen_ssl_pinning_bypass(
            target="/usr/bin/curl", process_spec="curl"
        )
        assert "SecTrustEvaluate" in result["hooks"]
        assert "SecTrustEvaluate" in result["script"]

    def test_hooks_java_trust_manager(self):
        result = gen_ssl_pinning_bypass(
            target="/usr/bin/curl", process_spec="curl"
        )
        assert "X509TrustManager" in result["hooks"]
        assert "X509TrustManager" in result["script"]

    def test_script_uses_interceptor(self):
        result = gen_ssl_pinning_bypass(
            target="/usr/bin/curl", process_spec="curl"
        )
        assert "Interceptor" in result["script"]


class TestTracing:
    def test_returns_dict_with_required_keys(self):
        result = gen_tracing(
            target="/usr/bin/ls",
            process_spec="ls",
            trace_ms=1000,
            trace_ret=False,
            trace_exec=False,
            call_cap=100,
        )
        for key in ("script", "script_path", "invocation", "trace_ms",
                    "trace_ret", "trace_exec", "call_cap"):
            assert key in result

    def test_script_uses_stalker(self):
        result = gen_tracing(
            target="/usr/bin/ls",
            process_spec="ls",
            trace_ms=1000,
            trace_ret=False,
            trace_exec=False,
            call_cap=100,
        )
        assert "Stalker.follow" in result["script"]
        assert "Stalker.unfollow" in result["script"]

    def test_trace_ms_in_script(self):
        result = gen_tracing(
            target="/usr/bin/ls",
            process_spec="ls",
            trace_ms=2500,
            trace_ret=False,
            trace_exec=False,
            call_cap=100,
        )
        assert result["trace_ms"] == 2500
        assert "2500" in result["script"]

    def test_call_cap_in_script(self):
        result = gen_tracing(
            target="/usr/bin/ls",
            process_spec="ls",
            trace_ms=1000,
            trace_ret=False,
            trace_exec=False,
            call_cap=999,
        )
        assert "CALL_CAP = 999" in result["script"]

    def test_trace_ret_false_default(self):
        result = gen_tracing(
            target="/usr/bin/ls",
            process_spec="ls",
            trace_ms=1000,
            trace_ret=False,
            trace_exec=False,
            call_cap=100,
        )
        assert result["trace_ret"] is False
        assert "ret: false" in result["script"]

    def test_trace_ret_true_propagates(self):
        result = gen_tracing(
            target="/usr/bin/ls",
            process_spec="ls",
            trace_ms=1000,
            trace_ret=True,
            trace_exec=False,
            call_cap=100,
        )
        assert result["trace_ret"] is True
        assert "ret: true" in result["script"]

    def test_trace_exec_true_propagates(self):
        result = gen_tracing(
            target="/usr/bin/ls",
            process_spec="ls",
            trace_ms=1000,
            trace_ret=False,
            trace_exec=True,
            call_cap=100,
        )
        assert result["trace_exec"] is True
        assert "exec: true" in result["script"]


class TestCLI:
    def _run_main(self, tmp_path, argv):
        import sys as _sys
        backup = _sys.argv
        _sys.argv = ["frida_instrument.py", *argv]
        try:
            _fi.main()
        finally:
            _sys.argv = backup

    def test_cli_writes_json_artifact_function_interception(self, tmp_path, capsys):
        out = tmp_path / "fi.json"
        self._run_main(
            tmp_path,
            [
                "--target", "/usr/bin/ls",
                "--mode", "function_interception",
                "--process", "ls",
                "--targets", "open,read,write",
                "--output", str(out),
            ],
        )
        data = json.loads(out.read_text())
        assert data["mode"] == "function_interception"
        assert data["target"] == "/usr/bin/ls"
        assert data["backend"] == "report-only"
        assert data["target_symbols"] == ["open", "read", "write"]
        assert "script" in data
        assert "Interceptor.attach" in data["script"]

    def test_cli_writes_json_artifact_tracing(self, tmp_path):
        out = tmp_path / "tr.json"
        self._run_main(
            tmp_path,
            [
                "--target", "/usr/bin/ls",
                "--mode", "tracing",
                "--process", "ls",
                "--trace-ms", "2000",
                "--trace-ret",
                "--call-cap", "200",
                "--output", str(out),
            ],
        )
        data = json.loads(out.read_text())
        assert data["mode"] == "tracing"
        assert data["trace_ms"] == 2000
        assert data["trace_ret"] is True
        assert data["call_cap"] == 200

    def test_cli_unknown_mode_exits_nonzero(self, tmp_path):
        with pytest.raises(SystemExit) as excinfo:
            self._run_main(
                tmp_path,
                [
                    "--target", "/usr/bin/ls",
                    "--mode", "not_a_real_mode",
                    "--output", str(tmp_path / "x.json"),
                ],
            )
        assert excinfo.value.code == 2


class TestModuleImportability:
    def test_all_four_generators_callable(self):
        assert callable(gen_function_interception)
        assert callable(gen_memory_scanning)
        assert callable(gen_ssl_pinning_bypass)
        assert callable(gen_tracing)

    def test_modes_dict_complete(self):
        assert set(_MODES.keys()) == set(VALID_MODES)
