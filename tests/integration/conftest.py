"""Integration test safety net — prevent real HuggingFace Hub downloads."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True, scope="session")
def _block_hf_downloads():
    """Replace huggingface_hub download functions with no-op stubs.

    Prevents any integration test from accidentally triggering a real
    HuggingFace Hub download. Every test that legitimately needs a mock
    should still apply its own per-test mock to control return values;
    this session-level fixture is a backstop for tests that forget.
    """
    _dummy_path: str = ""

    def _fake_download(*_a, **_kw):
        return _dummy_path

    try:
        import huggingface_hub

        _orig_hub_download = getattr(huggingface_hub, "hf_hub_download", None)
        _orig_snapshot = getattr(huggingface_hub, "snapshot_download", None)

        huggingface_hub.hf_hub_download = _fake_download
        huggingface_hub.snapshot_download = _fake_download

        yield

        if _orig_hub_download is not None:
            huggingface_hub.hf_hub_download = _orig_hub_download
        if _orig_snapshot is not None:
            huggingface_hub.snapshot_download = _orig_snapshot
    except ImportError:
        yield
