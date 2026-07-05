"""Unit tests for scripts/check_plugin_hashes.py — plugin version check and kill-switch."""

import json
import os
import tempfile
from pathlib import Path

import pytest
import scripts.check_plugin_hashes as target


@pytest.fixture
def tmp_plugin_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def tmp_manifest():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        pass
    p = Path(f.name)
    yield p
    p.unlink(missing_ok=True)


@pytest.fixture
def tmp_disengage():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        pass
    p = Path(f.name)
    yield p
    p.unlink(missing_ok=True)


@pytest.fixture
def tmp_block_counter():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        pass
    p = Path(f.name)
    yield p
    p.unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# 1. compute_hashes — basic correctness
# --------------------------------------------------------------------------- #
class TestComputeHashes:
    def test_returns_dict_of_ts_files(self, tmp_plugin_dir):
        (tmp_plugin_dir / "enforce-make.ts").write_text("console.log(1);\n")
        (tmp_plugin_dir / "enforce-stop.ts").write_text("console.log(2);\n")
        (tmp_plugin_dir / "notes.md").write_text("not ts\n")

        hashes = target.compute_hashes(tmp_plugin_dir)
        assert set(hashes) == {"enforce-make.ts", "enforce-stop.ts"}
        assert all(len(h) == 64 for h in hashes.values())

    def test_empty_dir_returns_empty(self, tmp_plugin_dir):
        hashes = target.compute_hashes(tmp_plugin_dir)
        assert hashes == {}

    def test_different_content_gets_different_hash(self, tmp_plugin_dir):
        (tmp_plugin_dir / "a.ts").write_text("A")
        (tmp_plugin_dir / "b.ts").write_text("B")
        hashes = target.compute_hashes(tmp_plugin_dir)
        assert hashes["a.ts"] != hashes["b.ts"]


# --------------------------------------------------------------------------- #
# 2. Manifest read/write roundtrip
# --------------------------------------------------------------------------- #
class TestManifestRoundtrip:
    def test_write_and_read(self, tmp_manifest):
        hashes = {"enforce-make.ts": "abc123", "enforce-stop.ts": "def456"}
        target.write_manifest(hashes, tmp_manifest)
        read = target.read_manifest(tmp_manifest)
        assert read == hashes

    def test_read_missing_returns_empty(self, tmp_manifest):
        assert target.read_manifest(tmp_manifest) == {}

    def test_read_corrupt_returns_empty(self, tmp_manifest):
        tmp_manifest.write_text("not valid json")
        assert target.read_manifest(tmp_manifest) == {}


# --------------------------------------------------------------------------- #
# 3. check() — disengage triggered when hashes differ
# --------------------------------------------------------------------------- #
class TestCheckTriggersDisengage:
    def test_hash_match_no_disengage(self, tmp_plugin_dir, tmp_manifest, tmp_disengage, tmp_block_counter):
        tmp_disengage.unlink()
        tmp_block_counter.unlink()
        (tmp_plugin_dir / "enforce-make.ts").write_text("const x = 1;\n")
        hashes = target.compute_hashes(tmp_plugin_dir)
        target.write_manifest(hashes, tmp_manifest)

        exit_code = target.check(
            plugin_dir=tmp_plugin_dir,
            manifest_path=tmp_manifest,
            disengage_path=tmp_disengage,
            block_counter_path=tmp_block_counter,
            quiet=True,
        )
        assert exit_code == 0
        assert not tmp_disengage.exists()
        assert not tmp_block_counter.exists()

    def test_hash_mismatch_writes_disengage(self, tmp_plugin_dir, tmp_manifest, tmp_disengage, tmp_block_counter):
        (tmp_plugin_dir / "enforce-make.ts").write_text("const x = 1;\n")
        hashes = target.compute_hashes(tmp_plugin_dir)
        target.write_manifest(hashes, tmp_manifest)

        # Modify a plugin file
        (tmp_plugin_dir / "enforce-make.ts").write_text("const x = 2;\n")

        exit_code = target.check(
            plugin_dir=tmp_plugin_dir,
            manifest_path=tmp_manifest,
            quiet=True,
            disengage_path=tmp_disengage,
            block_counter_path=tmp_block_counter,
        )

        assert exit_code == 1
        assert tmp_disengage.exists()
        data = json.loads(tmp_disengage.read_text())
        assert "disengage_until" in data
        assert data["disengage_until"] > 0
        assert data["reason"] == "changed: enforce-make.ts"

    def test_new_plugin_file_triggers_disengage(self, tmp_plugin_dir, tmp_manifest, tmp_disengage, tmp_block_counter):
        (tmp_plugin_dir / "enforce-make.ts").write_text("a")
        hashes = target.compute_hashes(tmp_plugin_dir)
        target.write_manifest(hashes, tmp_manifest)

        # Add a new plugin file
        (tmp_plugin_dir / "enforce-new.ts").write_text("new plugin")

        exit_code = target.check(
            plugin_dir=tmp_plugin_dir,
            manifest_path=tmp_manifest,
            quiet=True,
            disengage_path=tmp_disengage,
            block_counter_path=tmp_block_counter,
        )

        assert exit_code == 1
        data = json.loads(tmp_disengage.read_text())
        assert "new: enforce-new.ts" in data["reason"]

    def test_removed_plugin_file_triggers_disengage(
        self, tmp_plugin_dir, tmp_manifest, tmp_disengage, tmp_block_counter
    ):
        (tmp_plugin_dir / "enforce-make.ts").write_text("a")
        (tmp_plugin_dir / "enforce-stop.ts").write_text("b")
        hashes = target.compute_hashes(tmp_plugin_dir)
        target.write_manifest(hashes, tmp_manifest)

        # Remove a plugin file
        (tmp_plugin_dir / "enforce-stop.ts").unlink()

        exit_code = target.check(
            plugin_dir=tmp_plugin_dir,
            manifest_path=tmp_manifest,
            quiet=True,
            disengage_path=tmp_disengage,
            block_counter_path=tmp_block_counter,
        )

        assert exit_code == 1
        data = json.loads(tmp_disengage.read_text())
        assert "removed: enforce-stop.ts" in data["reason"]

    def test_disengage_writes_block_counter(self, tmp_plugin_dir, tmp_manifest, tmp_disengage, tmp_block_counter):
        (tmp_plugin_dir / "enforce-make.ts").write_text("original")
        hashes = target.compute_hashes(tmp_plugin_dir)
        target.write_manifest(hashes, tmp_manifest)

        (tmp_plugin_dir / "enforce-make.ts").write_text("modified")

        target.check(
            plugin_dir=tmp_plugin_dir,
            manifest_path=tmp_manifest,
            quiet=True,
            disengage_path=tmp_disengage,
            block_counter_path=tmp_block_counter,
        )

        assert tmp_block_counter.exists()
        bc = json.loads(tmp_block_counter.read_text())
        assert bc["consecutiveBlocks"] == 0
        assert bc["totalBlocks"] == 0
        assert bc["disengageUntil"] == 9999999999999

    def test_empty_plugin_dir_no_error(self, tmp_plugin_dir, tmp_manifest):
        exit_code = target.check(
            plugin_dir=tmp_plugin_dir,
            manifest_path=tmp_manifest,
            quiet=True,
        )
        assert exit_code == 0

    def test_auto_write_manifest_when_missing(self, tmp_plugin_dir, tmp_manifest):
        (tmp_plugin_dir / "enforce-make.ts").write_text("hello")
        exit_code = target.check(
            plugin_dir=tmp_plugin_dir,
            manifest_path=tmp_manifest,
            quiet=True,
        )
        assert exit_code == 0
        assert tmp_manifest.exists()
        stored = target.read_manifest(tmp_manifest)
        assert "enforce-make.ts" in stored

    def test_max_age_refreshes_manifest(self, tmp_plugin_dir, tmp_manifest):
        (tmp_plugin_dir / "enforce-make.ts").write_text("content1")
        hashes = target.compute_hashes(tmp_plugin_dir)
        target.write_manifest(hashes, tmp_manifest)

        # Make manifest very old
        import time
        old_mtime = time.time() - 1000
        os.utime(str(tmp_manifest), (old_mtime, old_mtime))

        exit_code = target.check(
            plugin_dir=tmp_plugin_dir,
            manifest_path=tmp_manifest,
            quiet=True,
            max_age_seconds=500,
        )
        assert exit_code == 0


# --------------------------------------------------------------------------- #
# 4. write_disengage — format correctness
# --------------------------------------------------------------------------- #
class TestWriteDisengage:
    def test_format_has_required_keys(self, tmp_disengage):
        target.write_disengage(3600, "test reason", tmp_disengage)
        data = json.loads(tmp_disengage.read_text())
        assert "disengage_until" in data
        assert "disengage_until_epoch_ms" in data
        assert "reason" in data
        assert data["reason"] == "test reason"
        assert data["disengage_until"] == data["disengage_until_epoch_ms"]
        # Value should be in the future in milliseconds
        assert data["disengage_until"] > 0
