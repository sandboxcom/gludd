"""Tests for RendererCache (renderers/cache.py) — TTL semantics, lazy expiry,
clear/clear_all, and the ttl<=0 "do not cache" contract.

Run: make test-iso TESTFILE='tests/unit/test_renderers_cache.py'
"""

from __future__ import annotations

from general_ludd.renderers.cache import RendererCache


class _FakeClock:
    """Mutable monotonic clock stand-in for monkeypatching time.monotonic.

    Backed by a mutable dict (rather than a plain float) so the closure
    returned to ``time.monotonic`` always reads the current value even after
    ``advance()`` mutates it in place.
    """

    def __init__(self, start: float = 1000.0) -> None:
        self._state = {"t": start}

    def __call__(self) -> float:
        return self._state["t"]

    def advance(self, seconds: float) -> None:
        self._state["t"] += seconds


class TestRendererCacheBasics:
    def test_set_get_roundtrip(self) -> None:
        cache = RendererCache()
        cache.set("playbook-a", {"html": "<p>hi</p>"})
        assert cache.get("playbook-a") == {"html": "<p>hi</p>"}

    def test_miss_returns_none(self) -> None:
        cache = RendererCache()
        assert cache.get("does-not-exist") is None

    def test_overwrite_replaces_value(self) -> None:
        cache = RendererCache()
        cache.set("k", "first")
        cache.set("k", "second")
        assert cache.get("k") == "second"

    def test_len_and_contains(self) -> None:
        cache = RendererCache()
        assert len(cache) == 0
        assert "k" not in cache
        cache.set("k", "v")
        assert len(cache) == 1
        assert "k" in cache
        cache.set("k2", "v2")
        assert len(cache) == 2
        assert "k2" in cache


class TestRendererCacheClear:
    def test_clear_existing_returns_true(self) -> None:
        cache = RendererCache()
        cache.set("k", "v")
        assert cache.clear("k") is True
        assert cache.get("k") is None

    def test_clear_missing_returns_false(self) -> None:
        cache = RendererCache()
        assert cache.clear("nope") is False

    def test_clear_all_returns_count_and_empties(self) -> None:
        cache = RendererCache()
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)
        assert cache.clear_all() == 3
        assert len(cache) == 0
        assert cache.get("a") is None

    def test_clear_all_on_empty_returns_zero(self) -> None:
        cache = RendererCache()
        assert cache.clear_all() == 0


class TestRendererCacheTtl:
    def test_ttl_zero_disables_caching(self, monkeypatch) -> None:
        clock = _FakeClock()
        monkeypatch.setattr("general_ludd.renderers.cache.time.monotonic", clock)
        cache = RendererCache()
        cache.set("k", "v", ttl=0)
        assert cache.get("k") is None
        assert len(cache) == 0

    def test_negative_ttl_disables_caching(self, monkeypatch) -> None:
        clock = _FakeClock()
        monkeypatch.setattr("general_ludd.renderers.cache.time.monotonic", clock)
        cache = RendererCache()
        cache.set("k", "v", ttl=-5)
        assert cache.get("k") is None
        assert len(cache) == 0

    def test_ttl_zero_evicts_existing_entry(self, monkeypatch) -> None:
        clock = _FakeClock()
        monkeypatch.setattr("general_ludd.renderers.cache.time.monotonic", clock)
        cache = RendererCache()
        cache.set("k", "v", ttl=30)
        assert cache.get("k") == "v"
        cache.set("k", "ignored", ttl=0)
        assert cache.get("k") is None
        assert len(cache) == 0

    def test_default_ttl_is_honored(self, monkeypatch) -> None:
        clock = _FakeClock()
        monkeypatch.setattr("general_ludd.renderers.cache.time.monotonic", clock)
        cache = RendererCache(ttl_default=10.0)
        cache.set("k", "v")
        clock.advance(9.999)
        assert cache.get("k") == "v"
        clock.advance(0.002)
        assert cache.get("k") is None

    def test_expiry_at_exactly_expires_at(self, monkeypatch) -> None:
        clock = _FakeClock()
        monkeypatch.setattr("general_ludd.renderers.cache.time.monotonic", clock)
        cache = RendererCache()
        cache.set("k", "v", ttl=5)
        # expires_at = 1000.0 + 5 == 1005.0; get() expires when now >= expires_at.
        clock.advance(5.0)
        assert cache.get("k") is None

    def test_lazy_expiry_pops_the_entry(self, monkeypatch) -> None:
        clock = _FakeClock()
        monkeypatch.setattr("general_ludd.renderers.cache.time.monotonic", clock)
        cache = RendererCache()
        cache.set("k", "v", ttl=1)
        clock.advance(2.0)
        assert len(cache) == 1  # stale entry still physically present
        assert cache.get("k") is None  # triggers lazy pop
        assert len(cache) == 0

    def test_explicit_ttl_overrides_default(self, monkeypatch) -> None:
        clock = _FakeClock()
        monkeypatch.setattr("general_ludd.renderers.cache.time.monotonic", clock)
        cache = RendererCache(ttl_default=100.0)
        cache.set("k", "v", ttl=1)
        clock.advance(1.5)
        assert cache.get("k") is None

    def test_contains_reflects_expiry(self, monkeypatch) -> None:
        clock = _FakeClock()
        monkeypatch.setattr("general_ludd.renderers.cache.time.monotonic", clock)
        cache = RendererCache()
        cache.set("k", "v", ttl=3)
        assert "k" in cache
        clock.advance(3.0)
        assert "k" not in cache
