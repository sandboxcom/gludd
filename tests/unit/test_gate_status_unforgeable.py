"""TDD: .gate-status unforgeable — HMAC-SHA256 provenance guard.

Tests:
  1. Hand-written bare PASS (no SIG) → verifier rejects.
  2. Valid SIG but wrong tree SHA → rejected (wrong-tree).
  3. Valid SIG + matching tree but stale EPOCH → rejected (stale).
  4. SIG computed over tampered result → rejected (bad-HMAC).
  5. Genuine gate-written status (real signing fn) → accepted.
  6. Missing SIG line entirely → rejected.
  7. EPOCH far in the future → rejected (clock manipulation).
  8. run_gate.sh writes TREE/EPOCH/SIG lines to .gate-status.
  9. Missing TREE line → rejected.
 10. Missing EPOCH line → rejected.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path

# Scripts root for run_gate.sh.
ROOT = Path(__file__).parent.parent.parent
SCRIPT = ROOT / "scripts" / "run_gate.sh"

# ---------------------------------------------------------------------------
# Helpers — import the verifier module directly (no shell).
# ---------------------------------------------------------------------------


def _import_verifier():
    """Import scripts/verify_gate_status.py as a module."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "verify_gate_status",
        str(ROOT / "scripts" / "verify_gate_status.py"),
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.util.module_from_spec(spec) if False else importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# Load once at module level.
_vgs = _import_verifier()


def _make_key() -> bytes:
    """Generate a fresh random 32-byte key for tests (no filesystem I/O)."""
    import secrets
    return secrets.token_bytes(32)


def _write_status(
    path: Path,
    *,
    tree: str,
    epoch: int,
    result: str,
    key: bytes,
    extra_lines: str = "",
    corrupt_sig: bool = False,
    omit_sig: bool = False,
    omit_tree: bool = False,
    omit_epoch: bool = False,
    omit_result: bool = False,
) -> None:
    """Write a synthetic .gate-status with correct or tampered provenance."""
    sig = _vgs.compute_sig(tree, epoch, result, key=key)
    if corrupt_sig:
        sig = "deadbeef" * 8  # wrong 64-hex chars

    lines = []
    lines.append("=== GATE 2026-01-01T00:00:00Z ===")
    lines.append("lint PASS 0")
    lines.append("typecheck PASS 0")
    lines.append("collect PASS 0")
    lines.append("test PASS 0")
    lines.append("smoke PASS")
    lines.append("---")
    if not omit_tree:
        lines.append(f"TREE {tree}")
    if not omit_epoch:
        lines.append(f"EPOCH {epoch}")
    if not omit_result:
        lines.append(f"RESULT {result}")
    if not omit_sig:
        lines.append(f"SIG {sig}")
    if extra_lines:
        lines.append(extra_lines)
    path.write_text("\n".join(lines) + "\n")


def _fake_tree(repo_root: str) -> str:
    """Return a fake tree SHA that is guaranteed NOT to equal the real one."""
    return "a" * 40


def _real_tree() -> str:
    """Return the actual HEAD^{tree} SHA for this repo."""
    return _vgs._git_tree_sha(str(ROOT))


# ---------------------------------------------------------------------------
# TestGateStatusUnforgeable
# ---------------------------------------------------------------------------


class TestGateStatusUnforgeable:
    """Provenance guard: forged / tampered / stale gate-status files are rejected."""

    def test_bare_pass_no_sig_rejected(self) -> None:
        """A hand-written .gate-status with just 'PASS' lines and no SIG is rejected."""
        with tempfile.NamedTemporaryFile(suffix=".gate-status", mode="w", delete=False) as f:
            f.write("=== GATE fake ===\n")
            f.write("lint PASS 0\n")
            f.write("typecheck PASS 0\n")
            f.write("collect PASS 0\n")
            f.write("test PASS 0\n")
            f.write("smoke PASS\n")
            f.write("---\n")
            f.write("epoch 9999999999\n")  # fresh-looking
            name = f.name

        try:
            key = _make_key()
            ok, reason = _vgs.verify(Path(name), key=key)
            assert not ok, f"Expected rejection of bare-PASS file, got ok=True, reason={reason!r}"
            assert "SIG" in reason or "forged" in reason or "missing" in reason.lower(), (
                f"Rejection reason should mention SIG/forged: {reason!r}"
            )
        finally:
            os.unlink(name)

    def test_wrong_tree_rejected(self) -> None:
        """SIG valid but TREE != HEAD^{tree} → rejected (replay from different commit)."""
        real_tree = _real_tree()
        # Forge a TREE that differs from real HEAD^{tree}.
        wrong_tree = "b" * 40 if not real_tree.startswith("b" * 40) else "a" * 40

        key = _make_key()
        with tempfile.TemporaryDirectory() as tmpdir:
            status = Path(tmpdir) / ".gate-status"
            now = int(time.time())
            _write_status(status, tree=wrong_tree, epoch=now, result="PASS", key=key)

            ok, reason = _vgs.verify(status, repo_root=str(ROOT), key=key)

        assert not ok, f"Expected rejection for wrong tree, got ok=True, reason={reason!r}"
        assert "wrong-tree" in reason or "tree" in reason.lower(), (
            f"Rejection should mention tree mismatch: {reason!r}"
        )

    def test_stale_epoch_rejected(self) -> None:
        """Valid SIG + correct tree but EPOCH > freshness window → rejected (stale)."""
        real_tree = _real_tree()
        key = _make_key()
        stale_epoch = int(time.time()) - 3601  # 1 hour ago (> 1800s window)

        with tempfile.TemporaryDirectory() as tmpdir:
            status = Path(tmpdir) / ".gate-status"
            _write_status(status, tree=real_tree, epoch=stale_epoch, result="PASS", key=key)

            ok, reason = _vgs.verify(
                status,
                freshness_secs=1800,
                repo_root=str(ROOT),
                key=key,
            )

        assert not ok, f"Expected rejection for stale epoch, got ok=True, reason={reason!r}"
        assert "stale" in reason.lower(), f"Rejection should mention stale: {reason!r}"

    def test_bad_hmac_rejected(self) -> None:
        """SIG computed over a different payload (tampered result) → rejected."""
        real_tree = _real_tree()
        key = _make_key()
        now = int(time.time())

        with tempfile.TemporaryDirectory() as tmpdir:
            status = Path(tmpdir) / ".gate-status"
            _write_status(
                status,
                tree=real_tree,
                epoch=now,
                result="PASS",
                key=key,
                corrupt_sig=True,  # replace SIG with deadbeef...
            )

            ok, reason = _vgs.verify(
                status,
                freshness_secs=1800,
                repo_root=str(ROOT),
                key=key,
            )

        assert not ok, f"Expected rejection for bad HMAC, got ok=True, reason={reason!r}"
        assert "hmac" in reason.lower() or "mismatch" in reason.lower() or "fabricated" in reason.lower(), (
            f"Rejection should mention HMAC mismatch: {reason!r}"
        )

    def test_genuine_signed_status_accepted(self) -> None:
        """A status signed with compute_sig (the real signing fn) is accepted."""
        real_tree = _real_tree()
        key = _make_key()
        now = int(time.time())

        with tempfile.TemporaryDirectory() as tmpdir:
            status = Path(tmpdir) / ".gate-status"
            _write_status(status, tree=real_tree, epoch=now, result="PASS", key=key)

            ok, reason = _vgs.verify(
                status,
                freshness_secs=1800,
                repo_root=str(ROOT),
                key=key,
            )

        assert ok, f"Expected acceptance of genuinely-signed status, got ok=False, reason={reason!r}"
        assert "OK" in reason, f"Acceptance reason should contain 'OK': {reason!r}"

    def test_missing_sig_line_rejected(self) -> None:
        """A status file with TREE/EPOCH/RESULT but no SIG is rejected."""
        real_tree = _real_tree()
        key = _make_key()
        now = int(time.time())

        with tempfile.TemporaryDirectory() as tmpdir:
            status = Path(tmpdir) / ".gate-status"
            _write_status(
                status,
                tree=real_tree,
                epoch=now,
                result="PASS",
                key=key,
                omit_sig=True,
            )

            ok, reason = _vgs.verify(
                status,
                freshness_secs=1800,
                repo_root=str(ROOT),
                key=key,
            )

        assert not ok, f"Expected rejection when SIG is missing, got ok=True, reason={reason!r}"
        assert "SIG" in reason or "missing" in reason.lower(), (
            f"Rejection should mention missing SIG: {reason!r}"
        )

    def test_future_epoch_rejected(self) -> None:
        """EPOCH far in the future (clock manipulation) → rejected."""
        real_tree = _real_tree()
        key = _make_key()
        future_epoch = int(time.time()) + 7200  # 2 hours in future

        with tempfile.TemporaryDirectory() as tmpdir:
            status = Path(tmpdir) / ".gate-status"
            _write_status(status, tree=real_tree, epoch=future_epoch, result="PASS", key=key)

            ok, reason = _vgs.verify(
                status,
                freshness_secs=1800,
                repo_root=str(ROOT),
                key=key,
            )

        assert not ok, f"Expected rejection for future epoch, got ok=True, reason={reason!r}"
        assert "future" in reason.lower(), f"Rejection should mention future: {reason!r}"

    def test_missing_tree_line_rejected(self) -> None:
        """A status file with SIG/EPOCH/RESULT but no TREE is rejected."""
        real_tree = _real_tree()
        key = _make_key()
        now = int(time.time())

        with tempfile.TemporaryDirectory() as tmpdir:
            status = Path(tmpdir) / ".gate-status"
            _write_status(
                status,
                tree=real_tree,
                epoch=now,
                result="PASS",
                key=key,
                omit_tree=True,
            )

            ok, reason = _vgs.verify(
                status,
                freshness_secs=1800,
                repo_root=str(ROOT),
                key=key,
            )

        assert not ok, f"Expected rejection when TREE is missing, got ok=True, reason={reason!r}"
        assert "TREE" in reason or "missing" in reason.lower(), (
            f"Rejection should mention missing TREE: {reason!r}"
        )

    def test_missing_epoch_line_rejected(self) -> None:
        """A status file with SIG/TREE/RESULT but no EPOCH is rejected."""
        real_tree = _real_tree()
        key = _make_key()

        with tempfile.TemporaryDirectory() as tmpdir:
            status = Path(tmpdir) / ".gate-status"
            _write_status(
                status,
                tree=real_tree,
                epoch=0,
                result="PASS",
                key=key,
                omit_epoch=True,
            )

            ok, reason = _vgs.verify(
                status,
                freshness_secs=1800,
                repo_root=str(ROOT),
                key=key,
            )

        assert not ok, f"Expected rejection when EPOCH is missing, got ok=True, reason={reason!r}"
        assert "EPOCH" in reason or "missing" in reason.lower(), (
            f"Rejection should mention missing EPOCH: {reason!r}"
        )

    def test_run_gate_sh_writes_tree_epoch_sig(self) -> None:
        """run_gate.sh must write TREE, EPOCH, SIG, and RESULT lines to .gate-status."""
        workdir = Path(tempfile.mkdtemp(prefix="gludd-unforgeable-test-"))
        status_file = workdir / ".gate-status"
        status_file.write_text("")

        # Use a temp key file for this test so it doesn't clobber ~/.config.
        key_file = workdir / "gate.key"
        import base64
        import secrets
        key_bytes = secrets.token_bytes(32)
        key_b64 = base64.b64encode(key_bytes).decode()
        key_file.write_text(key_b64 + "\n")
        key_file.chmod(0o600)

        env = {
            **os.environ,
            "PYTEST_CMD": 'python3 -c "import sys; sys.exit(0)"',
            "GATE_LOCK_FILE": str(workdir / "gate.lock"),
            "GLUDD_GATE_KEY_PATH": str(key_file),
        }
        # Allow run in test context (subagent guard).
        env.pop("CLAUDE_AGENT_ID", None)
        env.pop("GLUDD_SUBAGENT", None)
        env.pop("GLUDD_GATE_AUTHORIZED", None)

        result = subprocess.run(
            ["bash", str(SCRIPT)],
            capture_output=True,
            text=True,
            cwd=str(workdir),
            env=env,
            timeout=30,
        )

        assert result.returncode == 0, (
            f"run_gate.sh failed: stdout={result.stdout}\nstderr={result.stderr}"
        )

        content = status_file.read_text()
        assert "TREE " in content, f"TREE line missing from .gate-status:\n{content}"
        assert "EPOCH " in content, f"EPOCH line missing from .gate-status:\n{content}"
        assert "SIG " in content, f"SIG line missing from .gate-status:\n{content}"
        assert "RESULT " in content, f"RESULT line missing from .gate-status:\n{content}"

    def test_verifier_script_exits_nonzero_for_bare_pass(self) -> None:
        """The CLI verify_gate_status.py exits non-zero for a bare PASS file."""
        with tempfile.NamedTemporaryFile(
            suffix=".gate-status", mode="w", delete=False
        ) as f:
            f.write("lint PASS 0\ntest PASS 0\nepoch 9999999999\n")
            name = f.name

        try:
            env = {**os.environ, "GLUDD_GATE_KEY_PATH": "/tmp/nonexistent-test-key-xyz"}
            result = subprocess.run(
                ["python3", str(ROOT / "scripts" / "verify_gate_status.py"), name],
                capture_output=True,
                text=True,
                env=env,
                timeout=10,
            )
            assert result.returncode != 0, (
                f"Expected non-zero exit for bare-PASS file. "
                f"stdout={result.stdout}\nstderr={result.stderr}"
            )
        finally:
            os.unlink(name)
