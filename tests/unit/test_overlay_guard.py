"""Tests for the self-improve overlay-monitoring safety guard.

Guards that when self-improvement is enabled, FIM warns if a config overlay
(project ``.gludd/`` or user ``~/.config/gludd``) would go untracked — either
because it is outside ``watch_paths`` or because an exclude pattern skips it.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from general_ludd.integrity.overlay_guard import (
    resolve_overlay_dirs,
    resolve_self_improve_enabled,
    warn_if_overlay_unmonitored,
)

DEFAULT_EXCLUDES = [r"\.pyc$", r"__pycache__", r"\.git/", r"\.db$"]


class TestResolveSelfImproveEnabled:
    def test_absent_config_defaults_on(self):
        # interval defaults to 10 → ON out-of-the-box (matches daemon).
        assert resolve_self_improve_enabled(None) is True
        assert resolve_self_improve_enabled({}) is True

    def test_interval_zero_disables(self):
        assert resolve_self_improve_enabled({"interval": 0}) is False

    def test_positive_interval_enables(self):
        assert resolve_self_improve_enabled({"interval": 5}) is True

    def test_garbage_interval_defaults_on(self):
        assert resolve_self_improve_enabled({"interval": "nope"}) is True


class TestWarnIfOverlayUnmonitored:
    def test_enabled_and_excluded_warns(self, tmp_path: Path):
        overlay = tmp_path / "gludd"
        overlay.mkdir()
        watch_paths = [str(tmp_path)]
        excludes = [r"general-ludd\.yml$"]

        with patch("general_ludd.integrity.overlay_guard.logger.warning") as mock_warn:
            warnings = warn_if_overlay_unmonitored(
                watch_paths,
                excludes,
                self_improve_enabled=True,
                overlay_dirs=[str(overlay)],
            )

        assert len(warnings) == 1
        assert str(overlay) in warnings[0]
        assert "skipped" in warnings[0]
        assert "not tracked by FIM" in warnings[0]
        mock_warn.assert_called_once()
        assert str(overlay) in mock_warn.call_args[0][0]

    def test_enabled_and_not_monitored_warns(self, tmp_path: Path):
        overlay = tmp_path / "overlay" / "gludd"
        overlay.mkdir(parents=True)
        other = tmp_path / "elsewhere"
        other.mkdir()
        watch_paths = [str(other)]

        with patch("general_ludd.integrity.overlay_guard.logger.warning") as mock_warn:
            warnings = warn_if_overlay_unmonitored(
                watch_paths,
                DEFAULT_EXCLUDES,
                self_improve_enabled=True,
                overlay_dirs=[str(overlay)],
            )

        assert len(warnings) == 1
        assert str(overlay) in warnings[0]
        assert "NOT within any file-integrity monitored path" in warnings[0]
        mock_warn.assert_called_once()

    def test_enabled_monitored_not_excluded_no_warning(self, tmp_path: Path):
        overlay = tmp_path / "gludd"
        overlay.mkdir()
        watch_paths = [str(tmp_path)]

        with patch("general_ludd.integrity.overlay_guard.logger.warning") as mock_warn:
            warnings = warn_if_overlay_unmonitored(
                watch_paths,
                DEFAULT_EXCLUDES,
                self_improve_enabled=True,
                overlay_dirs=[str(overlay)],
            )

        assert warnings == []
        mock_warn.assert_not_called()

    def test_disabled_no_false_alarm(self, tmp_path: Path):
        overlay = tmp_path / "gludd"
        overlay.mkdir()

        with patch("general_ludd.integrity.overlay_guard.logger.warning") as mock_warn:
            warnings = warn_if_overlay_unmonitored(
                [],
                [r"gludd"],
                self_improve_enabled=False,
                overlay_dirs=[str(overlay)],
            )

        assert warnings == []
        mock_warn.assert_not_called()

    def test_no_overlays_no_warning(self):
        assert (
            warn_if_overlay_unmonitored(
                [], DEFAULT_EXCLUDES, self_improve_enabled=True, overlay_dirs=[]
            )
            == []
        )


class TestOverlayResolutionHermetic:
    """Exercise the real XDG-based user-overlay resolution, hermetically."""

    def test_xdg_user_overlay_resolved_and_warned(
        self, tmp_path: Path, monkeypatch
    ):
        xdg = tmp_path / "xdg"
        (xdg / "gludd").mkdir(parents=True)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
        # Force the project overlay OFF so only the user overlay is considered.
        monkeypatch.setenv("GLUDD_PROJECT_DIR", str(tmp_path / "no-such-project"))

        overlays = resolve_overlay_dirs()
        assert str(xdg / "gludd") in overlays
        # No project overlay leaked in from the real cwd.
        assert overlays == [str(xdg / "gludd")]

        with patch("general_ludd.integrity.overlay_guard.logger.warning") as mock_warn:
            warnings = warn_if_overlay_unmonitored(
                [],  # user overlay not monitored
                DEFAULT_EXCLUDES,
                self_improve_enabled=True,
            )

        assert len(warnings) == 1
        assert str(xdg / "gludd") in warnings[0]
        mock_warn.assert_called_once()

    def test_xdg_overlay_disabled_self_improve_no_warn(
        self, tmp_path: Path, monkeypatch
    ):
        xdg = tmp_path / "xdg"
        (xdg / "gludd").mkdir(parents=True)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
        monkeypatch.setenv("GLUDD_PROJECT_DIR", str(tmp_path / "no-such-project"))

        with patch("general_ludd.integrity.overlay_guard.logger.warning") as mock_warn:
            warnings = warn_if_overlay_unmonitored(
                [], DEFAULT_EXCLUDES, self_improve_enabled=False
            )

        assert warnings == []
        mock_warn.assert_not_called()
