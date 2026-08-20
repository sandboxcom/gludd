"""Integration test safety net — prevent real HuggingFace Hub downloads."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

# Integration tests execute collection modules from the source checkout while
# production keeps using the installed Galaxy FQCN namespace. This test-only
# path mirrors Galaxy's package root without coupling collection Python to the
# Gludd core interpreter.
_COLLECTIONS_ROOT = Path(__file__).resolve().parents[2] / "collections"
if str(_COLLECTIONS_ROOT) not in sys.path:
    sys.path.insert(0, str(_COLLECTIONS_ROOT))


@pytest.fixture(autouse=True, scope="session")
def _block_hf_downloads() -> Iterator[None]:
    """Replace huggingface_hub download functions with no-op stubs.

    Prevents any integration test from accidentally triggering a real
    HuggingFace Hub download. Every test that legitimately needs a mock
    should still apply its own per-test mock to control return values;
    this session-level fixture is a backstop for tests that forget.
    """
    _dummy_path: str = ""

    def _fake_download(*_a: object, **_kw: object) -> str:
        return _dummy_path

    try:
        import huggingface_hub
    except ImportError:
        yield
        return

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(huggingface_hub, "hf_hub_download", _fake_download)
        monkeypatch.setattr(huggingface_hub, "snapshot_download", _fake_download)
        yield
