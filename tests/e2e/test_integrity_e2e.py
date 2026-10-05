"""End-to-end tests for src/general_ludd/integrity/.

Covers all four modules in the integrity package:
  * scanner.py        - via the /admin/integrity/* HTTP endpoints (full
                        baseline -> detect-new/modified/removed -> report flow)
  * change_log.py     - ChangeRecordStore durability across a "restart"
                        (a new instance pointing at the same store_dir)
  * overlay_guard.py  - warn_if_overlay_unmonitored behaviour
  * fim_excludes.py   - the canonical FIM exclude set

The integrity router is exercised through an in-process ASGI transport
(reliable, no subprocess flakiness) using the ephemeral-port helper from the
e2e conftest for the client base_url, mirroring
tests/e2e/test_observability_e2e.py.

Note: the actual HTTP surface is /admin/integrity/{scan,report,approve,reject,
log}; the /api/integrity/{baseline,changes} names some callers expect map onto
the scan (first call establishes the baseline) and report endpoints.
ChangeRecordStore is not directly HTTP-wired in routers/integrity.py (it is
consumed by cli_core_changes and the self_improve recorder), so its durability
is verified at the module level with a fresh instance re-reading the same
on-disk store.
"""

from __future__ import annotations

import hashlib
import re

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from general_ludd.integrity.change_log import ChangeRecordStore
from general_ludd.integrity.fim_excludes import FIM_EXCLUDE_PATTERNS
from general_ludd.integrity.overlay_guard import (
    resolve_self_improve_enabled,
    warn_if_overlay_unmonitored,
)
from general_ludd.integrity.scanner import FileIntegrityScanner
from general_ludd.routers import integrity as integrity_router
from tests.e2e.conftest import _find_free_port


@pytest.fixture
def scan_tree(tmp_path):
    """A sample file tree (outside the isolated HOME store dir)."""
    root = tmp_path / "scan"
    root.mkdir()
    (root / "alpha.txt").write_text("alpha content")
    (root / "beta.txt").write_text("beta content")
    sub = root / "sub"
    sub.mkdir()
    (sub / "gamma.txt").write_text("gamma content")
    return root


@pytest.fixture
def isolated_env(monkeypatch, tmp_path):
    """Isolate the integrity hash store + change log OUTSIDE the scan tree.

    The scanner writes its baseline to ``$HOME/.local/share/general-ludd/
    integrity/integrity_db.json``.  Pointing HOME at a directory that is a
    SIBLING of the scan root (not an ancestor) means the baseline files are
    never themselves hashed by the scan, and the real user store is untouched.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GL_CHANGE_STORE_DIR", str(home))
    monkeypatch.setenv("GLUDD_WORKSPACE_ROOT", str(tmp_path / "scan"))
    # Run the scanner without a signing key (tolerant mode) so the test does
    # not depend on GL_INTEGRITY_KEY being provisioned, and clear any key a
    # prior test may have cached so the env delete takes effect.
    monkeypatch.delenv("GL_INTEGRITY_KEY", raising=False)
    from general_ludd.integrity import scanner as _scanner_mod

    _scanner_mod._INTEGRITY_KEY = None
    # Reset the router's in-memory buffers so prior tests do not leak in.
    integrity_router._integrity_changes[:] = []
    integrity_router._integrity_log[:] = []
    return home


def _client_for(app: FastAPI) -> AsyncClient:
    port = _find_free_port()
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url=f"http://127.0.0.1:{port}")


def _integrity_app() -> FastAPI:
    """A minimal FastAPI app wired only with the integrity router."""
    app = FastAPI()
    app.state._config_dir = None
    app.state._startup_config = {}
    integrity_router.register(app, {})
    return app


class TestIntegrityScanE2E:
    """Full app boot -> /admin/integrity/scan -> change detection."""

    @pytest.mark.asyncio
    async def test_first_scan_records_every_file_as_new(self, isolated_env, scan_tree):
        app = _integrity_app()
        async with _client_for(app) as client:
            resp = await client.post(
                "/admin/integrity/scan", json={"paths": [str(scan_tree)]}
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()
            # First (baseline) scan: every file is "new".
            assert data["scanned"] == 3
            changes = data["changes"]
            assert len(changes) == 3
            assert {c["type"] for c in changes} == {"new"}
            files = {c["file"] for c in changes}
            assert any("alpha.txt" in f for f in files)
            assert any("beta.txt" in f for f in files)
            assert any("gamma.txt" in f for f in files)

    @pytest.mark.asyncio
    async def test_scan_detects_new_modified_removed(self, isolated_env, scan_tree):
        app = _integrity_app()
        async with _client_for(app) as client:
            # 1. Baseline.
            r1 = await client.post(
                "/admin/integrity/scan", json={"paths": [str(scan_tree)]}
            )
            assert r1.status_code == 200
            assert len(r1.json()["changes"]) == 3

            # 2. Mutate the tree: add / modify / delete.
            (scan_tree / "delta.txt").write_text("added")
            (scan_tree / "alpha.txt").write_text("alpha CHANGED")
            (scan_tree / "beta.txt").unlink()

            # 3. Second scan detects each change type.
            r2 = await client.post(
                "/admin/integrity/scan", json={"paths": [str(scan_tree)]}
            )
            assert r2.status_code == 200, r2.text
            changes = r2.json()["changes"]
            by_type: dict[str, dict] = {c["type"]: c for c in changes}
            assert set(by_type) == {"new", "modified", "removed"}
            assert any(
                "delta.txt" in c["file"] for c in changes if c["type"] == "new"
            )
            assert any(
                "alpha.txt" in c["file"] for c in changes if c["type"] == "modified"
            )
            assert any(
                "beta.txt" in c["file"] for c in changes if c["type"] == "removed"
            )
            # The modified record carries distinct old/new hashes.
            mod = by_type["modified"]
            assert mod["old_hash"]
            assert mod["new_hash"]
            assert mod["old_hash"] != mod["new_hash"]

    @pytest.mark.asyncio
    async def test_report_returns_latest_scan_changes(self, isolated_env, scan_tree):
        app = _integrity_app()
        async with _client_for(app) as client:
            await client.post(
                "/admin/integrity/scan", json={"paths": [str(scan_tree)]}
            )
            (scan_tree / "delta.txt").write_text("added")
            await client.post(
                "/admin/integrity/scan", json={"paths": [str(scan_tree)]}
            )
            resp = await client.get("/admin/integrity/report")
            assert resp.status_code == 200, resp.text
            changes = resp.json()["changes"]
            assert any(
                c["type"] == "new" and "delta.txt" in c["file"] for c in changes
            )

    @pytest.mark.asyncio
    async def test_scan_path_escape_is_rejected(self, isolated_env, scan_tree):
        app = _integrity_app()
        async with _client_for(app) as client:
            resp = await client.post(
                "/admin/integrity/scan", json={"paths": ["/etc"]}
            )
            assert resp.status_code == 422


class TestBaselinePersistenceE2E:
    """The scanner baseline (integrity_db.json) survives a "daemon restart".

    A NEW app/scanner instance reading the same HOME-backed store sees the
    previously-saved hashes, so unchanged files are NOT re-reported as "new".
    """

    @pytest.mark.asyncio
    async def test_baseline_persists_across_app_reboot(
        self, isolated_env, scan_tree
    ):
        # First app session: establish the baseline.
        app1 = _integrity_app()
        async with _client_for(app1) as client1:
            r = await client1.post(
                "/admin/integrity/scan", json={"paths": [str(scan_tree)]}
            )
            assert r.status_code == 200
            assert len(r.json()["changes"]) == 3  # all new

        # Second app session, same HOME -> same on-disk store. No files
        # changed, so the second scan must report ZERO changes.
        app2 = _integrity_app()
        async with _client_for(app2) as client2:
            r = await client2.post(
                "/admin/integrity/scan", json={"paths": [str(scan_tree)]}
            )
            assert r.status_code == 200, r.text
            assert r.json()["changes"] == []

    @pytest.mark.asyncio
    async def test_baseline_change_survives_reboot(self, isolated_env, scan_tree):
        # Baseline, then mutate before the reboot so the second session sees a
        # real change against the persisted baseline.
        app1 = _integrity_app()
        async with _client_for(app1) as client1:
            await client1.post(
                "/admin/integrity/scan", json={"paths": [str(scan_tree)]}
            )
        (scan_tree / "alpha.txt").write_text("post-reboot change")

        app2 = _integrity_app()
        async with _client_for(app2) as client2:
            r = await client2.post(
                "/admin/integrity/scan", json={"paths": [str(scan_tree)]}
            )
            assert r.status_code == 200
            changes = r.json()["changes"]
            assert len(changes) == 1
            assert changes[0]["type"] == "modified"
            assert "alpha.txt" in changes[0]["file"]


class TestChangeLogPersistenceE2E:
    """ChangeRecordStore (change_log.json) survives a "restart".

    Verified at the module level: the store is consumed by cli_core_changes
    and the self_improve recorder, not by an HTTP endpoint in
    routers/integrity.py, so durability is proven by re-opening the store from
    a fresh instance pointing at the same store_dir.
    """

    def test_records_survive_new_store_instance(self, tmp_path):
        store1 = ChangeRecordStore(store_dir=str(tmp_path))
        entry = store1.record(
            "/overlay/general-ludd.yml",
            change_type="modified",
            reason="self-improve apply",
            old_content="old: 1\n",
            new_content="new: 2\n",
            signer="agent-7",
        )
        assert entry.id == 0
        assert entry.change_type == "modified"

        # New instance, same store_dir — simulates a process restart.
        store2 = ChangeRecordStore(store_dir=str(tmp_path))
        records = store2.list_records()
        assert len(records) == 1
        loaded = records[0]
        assert loaded.id == 0
        assert loaded.file_path == "/overlay/general-ludd.yml"
        assert loaded.signer == "agent-7"
        # Captured content round-trips losslessly.
        assert loaded.old_content == "old: 1\n"
        assert loaded.new_content == "new: 2\n"

    def test_record_hash_binds_to_content(self, tmp_path):
        store = ChangeRecordStore(store_dir=str(tmp_path))
        entry = store.record(
            "/p/x.py", change_type="new", reason="init", new_content="print('hi')"
        )
        expected = hashlib.sha256(b"print('hi')").hexdigest()
        assert entry.new_hash == expected
        assert entry.old_hash is None

    def test_record_ids_are_monotonic(self, tmp_path):
        store = ChangeRecordStore(store_dir=str(tmp_path))
        ids = [
            store.record(f"/f/{i}.py", "new", "r").id for i in range(3)
        ]
        assert ids == [0, 1, 2]


class TestOverlayGuardE2E:
    """warn_if_overlay_unmonitored behaviour (overlay_guard.py)."""

    def test_silent_when_self_improve_disabled(self, tmp_path):
        warnings = warn_if_overlay_unmonitored(
            watch_paths=[str(tmp_path)],
            exclude_patterns=[],
            self_improve_enabled=False,
            overlay_dirs=[str(tmp_path / "overlay")],
        )
        assert warnings == []

    def test_warns_when_overlay_outside_watch_paths(self, tmp_path):
        overlay = tmp_path / "overlay"
        overlay.mkdir()
        (overlay / "general-ludd.yml").write_text("x: 1")
        warnings = warn_if_overlay_unmonitored(
            watch_paths=[str(tmp_path / "elsewhere")],
            exclude_patterns=[],
            self_improve_enabled=True,
            overlay_dirs=[str(overlay)],
        )
        assert len(warnings) == 1
        assert "NOT within any file-integrity monitored path" in warnings[0]

    def test_warns_when_overlay_excluded_by_pattern(self, tmp_path):
        watched = tmp_path / "watched"
        overlay = watched / "overlay"
        overlay.mkdir(parents=True)
        (overlay / "general-ludd.yml").write_text("x: 1")
        warnings = warn_if_overlay_unmonitored(
            watch_paths=[str(watched)],
            exclude_patterns=[r"overlay"],
            self_improve_enabled=True,
            overlay_dirs=[str(overlay)],
        )
        assert len(warnings) == 1
        assert "exclude pattern" in warnings[0]

    def test_silent_when_overlay_monitored_and_not_excluded(self, tmp_path):
        watched = tmp_path / "watched"
        overlay = watched / "overlay"
        overlay.mkdir(parents=True)
        (overlay / "general-ludd.yml").write_text("x: 1")
        warnings = warn_if_overlay_unmonitored(
            watch_paths=[str(watched)],
            exclude_patterns=[r"\.pyc$"],
            self_improve_enabled=True,
            overlay_dirs=[str(overlay)],
        )
        assert warnings == []

    def test_resolve_self_improve_enabled_defaults_on(self):
        assert resolve_self_improve_enabled(None) is True
        assert resolve_self_improve_enabled({}) is True

    def test_resolve_self_improve_disabled_via_interval_zero(self):
        assert resolve_self_improve_enabled({"interval": 0}) is False


class TestFimExcludesE2E:
    """The canonical FIM exclude set filters compiled/VCS/DB artefacts."""

    def test_patterns_drop_pyc_cache_git_db(self):
        compiled = [re.compile(p) for p in FIM_EXCLUDE_PATTERNS]
        excluded = [
            "pkg/mod.pyc",
            "pkg/__pycache__/mod.cpython.pyc",
            "repo/.git/HEAD",
            "state/daemon.db",
        ]
        kept = ["src/daemon.py", "config/general-ludd.yml", "README.md"]
        for s in excluded:
            assert any(p.search(s) for p in compiled), f"expected {s!r} excluded"
        for s in kept:
            assert not any(p.search(s) for p in compiled), f"expected {s!r} kept"

    def test_scan_applies_fim_excludes(self, tmp_path):
        # The scanner honouring FIM_EXCLUDE_PATTERNS skips the noisy artefacts.
        (tmp_path / "keep.py").write_text("print('keep')")
        (tmp_path / "drop.pyc").write_bytes(b"\x00\x01")
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "keep.cpython.pyc").write_bytes(b"\x00")
        (tmp_path / "state.db").write_text("SQLITE")

        scanner = FileIntegrityScanner(store_dir=str(tmp_path / "store"))
        result = scanner.scan(
            [str(tmp_path)], exclude_patterns=list(FIM_EXCLUDE_PATTERNS)
        )
        scanned = result["files"]
        assert any("keep.py" in f for f in scanned)
        assert not any("drop.pyc" in f for f in scanned)
        assert not any("__pycache__" in f for f in scanned)
        assert not any("state.db" in f for f in scanned)
