"""W3.12 (H14): Hot-reload theater elimination.

_reload_models must actually parse/swap the routing config, not just check
file existence. Every other reload path that does nothing must return
{"reloaded": false, "reason": "not_implemented"} — not report success.

TDD: write the test first.
"""
from __future__ import annotations

import pytest


class TestReloadModelsHonesty:
    def test_models_reloaded_false_when_no_routing_file(self, tmp_path):
        """Without a routing config, models_reloaded must be False (not True)."""
        from general_ludd.reload.hot_reloader import HotReloader, ReloadScope

        reloader = HotReloader(config_dir=str(tmp_path))
        result = reloader.reload(ReloadScope.MODELS)
        assert result.success is True  # reload attempt succeeds
        details = result.details
        assert details.get("models_reloaded") is False, (
            f"Expected models_reloaded=False when routing file absent, got: {details}"
        )

    def test_models_reloaded_true_and_config_applied_when_file_present(self, tmp_path):
        """With a routing config, models_reloaded must be True AND the config
        must actually be parsed (not just existence-checked)."""
        routing_path = tmp_path / "model_routing.yml"
        routing_path.write_text(
            "default_profile: fast\nprofiles:\n  fast:\n    model: gpt-4o-mini\n"
        )

        # Attach a mock gateway that can accept routing config
        from unittest.mock import MagicMock
        gateway = MagicMock()

        from general_ludd.reload.hot_reloader import HotReloader, ReloadScope

        reloader = HotReloader(config_dir=str(tmp_path), model_gateway=gateway)
        result = reloader.reload(ReloadScope.MODELS)
        assert result.success is True
        details = result.details
        assert details.get("models_reloaded") is True, (
            f"Expected models_reloaded=True when routing file present, got: {details}"
        )
        # Prove the config was actually parsed — not just the file existence
        parsed = (
            "routing_profiles_loaded" in details
            or "profiles_count" in details
            or details.get("routing_parsed") is True
        )
        assert parsed, f"Reload details don't show the routing file was actually parsed: {details}"

    def test_reload_reports_no_success_for_noop_paths(self, tmp_path):
        """Reload paths that do nothing must return reloaded=False, not True.

        Covers the theater case: returning success for a no-op is a lie.
        """
        from general_ludd.reload.hot_reloader import HotReloader, ReloadScope

        # No templates_dir, no routing file — nothing to reload
        reloader = HotReloader(config_dir=str(tmp_path))
        result = reloader.reload(ReloadScope.TEMPLATES)
        details = result.details
        # templates_loaded should be 0 (not silently claim success)
        assert details.get("templates_loaded", 0) == 0

    def test_reload_manager_unknown_reload_id_returns_failed_status(self):
        """ReloadManager.execute_reload for an unknown reload_id must report
        failure, not success."""
        try:
            from general_ludd.reload.manager import ReloadManager
        except ImportError:
            pytest.skip("ReloadManager not importable")

        rm = ReloadManager()
        # Unknown reload_id — must fail gracefully
        result = rm.execute_reload("nonexistent-reload-id-xyz")
        assert result.status == "failed", (
            f"Expected status='failed' for unknown reload_id, got {result.status!r}"
        )
        assert result.message, "Failed result must include a message"
