"""C27 — MCP transport argv validation for python/node launchers.

_validate_package_spec already covers the npm-family/uvx path. This extends
argv validation to ``python``/``python3``/``node`` launchers:

  * module/script paths must not contain path-traversal or shell metacharacters
  * ``-c`` (python) is rejected — arbitrary code execution from command line
  * ``-e`` / ``-p`` (node) are rejected — arbitrary code execution
  * legitimate ``python -m module`` and ``node server.js`` still pass
"""

from __future__ import annotations

import os

import pytest

from general_ludd.mcp.transport import (
    MCPTransportError,
    _validate_launch_command,
)

# ── helper: pin which() so PATH-resolution doesn't depend on ambient env ──

@pytest.fixture(autouse=True)
def _pin_which(monkeypatch):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda c: f"/usr/bin/{os.path.basename(c)}")


# ── python / python3 ───────────────────────────────────────────────────────

class TestPythonLauncherJailsPaths:
    def test_python_m_module_ok(self):
        """python -m some_mcp_server is a legitimate module-based launch."""
        _validate_launch_command(["python", "-m", "some_mcp_server"])

    def test_python_m_module_with_flags_ok(self):
        """python -u -B -m some_mcp_server — buffering/bytecode flags ok."""
        _validate_launch_command(["python", "-u", "-B", "-m", "some_mcp_server"])

    def test_python3_module_ok(self):
        """python3 -m server is legitimate."""
        _validate_launch_command(["python3", "-m", "server"])

    def test_python_script_path_ok(self):
        """python ./mcp_servers/my_server.py — repo-local script path ok."""
        _validate_launch_command(["python", "./mcp_servers/my_server.py"])

    def test_python_c_flag_rejected(self):
        """python -c 'code' is arbitrary code execution — must be rejected."""
        with pytest.raises(MCPTransportError, match=r"Refusing.*\-c"):
            _validate_launch_command(["python", "-c", "import os; os.system('evil')"])

    def test_python3_c_flag_rejected(self):
        """python3 -c is also rejected."""
        with pytest.raises(MCPTransportError, match=r"Refusing.*\-c"):
            _validate_launch_command(["python3", "-c", "print('hello')"])

    def test_python_script_path_traversal_rejected(self):
        """../../../etc/passwd must be rejected."""
        with pytest.raises(MCPTransportError, match="path traversal"):
            _validate_launch_command(["python", "../../../etc/passwd"])

    def test_python_script_shell_metachar_rejected(self):
        """python 'script.py && evil' — injection via metachar in path."""
        with pytest.raises(MCPTransportError, match="metacharacters"):
            _validate_launch_command(["python", "script.py && evil"])

    def test_python_script_pipe_injection_rejected(self):
        """python 'script.py | evil' — pipe in path argument."""
        with pytest.raises(MCPTransportError, match="metacharacters"):
            _validate_launch_command(["python", "script.py | cat /etc/passwd"])

    def test_python_m_with_traversal_rejected(self):
        """python -m ../malicious rejected."""
        with pytest.raises(MCPTransportError, match="path traversal"):
            _validate_launch_command(["python", "-m", "../malicious"])


# ── node ────────────────────────────────────────────────────────────────────

class TestNodeLauncherRejectsFlags:
    def test_node_script_ok(self):
        """node server.js — legitimate script launch."""
        _validate_launch_command(["node", "server.js"])

    def test_node_script_relative_path_ok(self):
        """node ./src/server.js — repo-relative path ok."""
        _validate_launch_command(["node", "./src/server.js"])

    def test_node_e_flag_rejected(self):
        """node -e 'code' is arbitrary code execution — must be rejected."""
        with pytest.raises(MCPTransportError, match=r"Refusing.*\-e"):
            _validate_launch_command(["node", "-e", "require('child_process').execSync('evil')"])

    def test_node_p_flag_rejected(self):
        """node -p 'code' evaluates and prints — also code execution."""
        with pytest.raises(MCPTransportError, match=r"Refusing.*\-p"):
            _validate_launch_command(["node", "-p", "1+1"])

    def test_node_script_path_traversal_rejected(self):
        """node ../../../etc/passwd must be rejected."""
        with pytest.raises(MCPTransportError, match="path traversal"):
            _validate_launch_command(["node", "../../../etc/shadow"])

    def test_node_script_shell_metachar_rejected(self):
        """node 'server.js; rm -rf /' rejected."""
        with pytest.raises(MCPTransportError, match="metacharacters"):
            _validate_launch_command(["node", "server.js; rm -rf /"])

    def test_node_with_legitimate_flags_ok(self):
        """node --no-warnings server.js — V8/node flags are fine."""
        _validate_launch_command(["node", "--no-warnings", "server.js"])


# ── end-to-end through launch (via _validate_launch_command) ──────────────────

class TestC27IntegrationWithExistingGuards:
    """Ensure the python/node argv checks don't break existing allowlist checks."""

    def test_disallowed_exec_still_rejected(self, monkeypatch):
        """Non-allowlisted exec is still blocked regardless of argv."""
        monkeypatch.delenv("GLUDD_MCP_ALLOW_ANY_EXEC", raising=False)
        with pytest.raises(MCPTransportError, match="allowlist"):
            _validate_launch_command(["/bin/sh", "-c", "echo hi"])

    def test_npx_still_works(self):
        """npm-family validation still works alongside new python/node checks."""
        _validate_launch_command(["npx", "@scope/srv@1.0.0"])

    def test_uvx_still_works(self):
        """uvx validation still works."""
        _validate_launch_command(["uvx", "mcp-server-git"])

    def test_python_with_only_flags_rejected(self):
        """python with only flag args and no module/script should still be rejected."""
        with pytest.raises(MCPTransportError, match="no module or script"):
            _validate_launch_command(["python", "-u", "-B"])
