"""Tests for gludd selftest command with podman detection and molecule runner."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestSelftest:
    def test_podman_available_detected(self):
        from general_ludd.config.binary_paths import BinaryPathResolver

        with patch("shutil.which", return_value="/usr/bin/podman"):
            resolver = BinaryPathResolver()
            assert resolver.is_available("podman") is True

    def test_podman_not_available_detected(self):
        from general_ludd.config.binary_paths import BinaryPathResolver

        with patch("shutil.which", return_value=None):
            resolver = BinaryPathResolver()
            assert resolver.is_available("podman") is False

    def test_container_runtime_prefers_podman(self):
        from general_ludd.config.binary_paths import BinaryPathResolver

        with patch("shutil.which", side_effect=lambda x: "/usr/bin/podman" if x == "podman" else "/usr/bin/docker"):
            resolver = BinaryPathResolver()
            assert resolver.get_container_runtime() == "podman"

    def test_container_runtime_falls_back_to_docker(self):
        from general_ludd.config.binary_paths import BinaryPathResolver

        with patch("shutil.which", side_effect=lambda x: None if x == "podman" else "/usr/bin/docker"):
            resolver = BinaryPathResolver()
            assert resolver.get_container_runtime() == "docker"

    def test_selftest_cli_parsing(self):
        import sys
        from unittest.mock import patch

        with patch.object(sys, "argv", ["gludd", "selftest"]):
            try:
                import general_ludd.cli as cli_mod

                cli_mod.main()
            except SystemExit:
                pass

    def test_selftest_runs_molecule(self):
        import sys
        from unittest.mock import patch

        mock_resp = MagicMock(status_code=200, json=lambda: {
            "success": True,
            "scenarios_run": 3,
            "scenarios_passed": 3,
            "results": [],
            "podman_available": True,
        })
        with patch("httpx.post", return_value=mock_resp), patch.object(sys, "argv", ["gludd", "selftest"]):
            import general_ludd.cli as cli_mod

            try:
                cli_mod.main()
            except SystemExit as exc:
                assert exc.code == 0

    def test_selftest_offline_error(self):
        import sys
        from unittest.mock import patch

        import httpx

        with patch(
            "httpx.post", side_effect=httpx.ConnectError("refused")
        ), patch.object(sys, "argv", ["gludd", "selftest"]):
                import general_ludd.cli as cli_mod

                try:
                    cli_mod.main()
                except SystemExit as exc:
                    assert exc.code == 1
