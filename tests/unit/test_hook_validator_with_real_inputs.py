"""Tests for scripts/validate_plugins_runtime.mjs realistic-input extension (BP.10).

The validator previously called every plugin hook with `null` only. That missed
bugs that only manifest when the hook processes actual tool call arguments
(branch-gated ReferenceErrors, shape-mismatch TypeErrors). This test file pins
the realistic-input behaviour:

  - the script exists and parses as valid JS
  - the script embeds realistic input cases (bash make, edit file, text)
  - the script tests bash tool with make commands
  - the script tests edit tool with file paths
  - the script catches TypeError on real inputs (not just ReferenceError)
  - the script still passes against the real plugin tree
"""
from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "validate_plugins_runtime.mjs"


def _read_script() -> str:
    return SCRIPT.read_text()


class TestScriptExists:
    def test_script_exists(self):
        assert SCRIPT.is_file(), f"missing validator: {SCRIPT}"

    def test_script_is_valid_javascript(self):
        result = subprocess.run(
            ["node", "--check", str(SCRIPT)],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, (
            f"node --check failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )


class TestScriptEmbedsRealisticInputs:
    def test_has_real_inputs_block(self):
        src = _read_script()
        assert "REAL_INPUTS" in src, "REAL_INPUTS block missing"
        assert "tool.execute.before" in src

    def test_tests_bash_tool_with_make_command(self):
        src = _read_script()
        # bash tool_input carrying a make command is the canonical make-only input
        assert 'tool: "bash"' in src, "missing bash tool case"
        assert "make lint" in src, "missing make command in bash input"

    def test_tests_edit_tool_with_file_paths(self):
        src = _read_script()
        assert 'tool: "edit"' in src, "missing edit tool case"
        assert "filePath" in src, "edit input must carry filePath"
        assert "oldString" in src and "newString" in src

    def test_tests_text_complete_with_text(self):
        src = _read_script()
        assert "text.complete" in src
        assert "some response text" in src

    def test_treats_typeerror_as_bug_on_real_inputs(self):
        """The realistic-input pass must catch TypeError, not just ReferenceError.

        Null input legitimately produces TypeError (null.length) so the null
        pass ignores it. Real inputs have valid shape, so TypeError there is a
        real bug — the extension must fail on it.
        """
        src = _read_script()
        assert "TypeError" in src, (
            "TypeError not classified as a bug on real inputs"
        )
        # The catch must be combined with ReferenceError on the real pass
        assert "ReferenceError" in src


class TestScriptRunsClean:
    def test_validator_passes_against_real_plugin_tree(self):
        """The extended validator must still exit 0 against the shipped plugins.

        Any plugin that crashes on realistic bash/edit/text inputs is a bug
        surfaced by this run. Pinned so a regression in either the validator
        or a plugin is caught here, not at gate time.
        """
        result = subprocess.run(
            ["node", "--experimental-strip-types", str(SCRIPT)],
            capture_output=True, text=True, timeout=120, cwd=str(ROOT),
        )
        msg = (
            f"exit={result.returncode}\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert result.returncode == 0, msg
        assert "PASS" in result.stdout, msg
