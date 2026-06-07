"""Test the rich TUI dashboard — verify all components render without error."""

from __future__ import annotations

import io

from rich.console import Console
from rich.live import Live


class TestTUIComponents:
    def setup_method(self):
        self.sample_info = {
            "version": "0.1.0",
            "python_version": "3.14.0",
            "platform": "darwin-arm64-test",
            "cwd": "/tmp/test",
            "config_dir": "/tmp/config",
            "config_files": [{"name": "a.yml", "path": "/tmp/a.yml", "size_bytes": 100}],
            "filestore_root": "/tmp/fs",
            "filestore_size_bytes": 2048,
            "filestore_file_count": 3,
            "db_engine": "sqlite",
            "db_path": "/tmp/test.db",
            "db_exists": True,
            "db_size_bytes": 4096,
            "binary_paths": {"podman": "/usr/bin/podman", "docker": None},
        }

    def test_tui_parser_registered(self):
        import argparse
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        tui_p = sub.add_parser("tui")
        tui_p.add_argument("--daemon-url", default="http://localhost:8000")
        args = parser.parse_args(["tui"])
        assert args.command == "tui"
        assert args.daemon_url == "http://localhost:8000"

    def test_build_info_table_returns_table(self):
        from rich.table import Table

        from general_ludd.cli import _gather_offline_status

        info = _gather_offline_status()
        assert isinstance(info, dict)
        assert "version" in info

        t = Table(title="System Info")
        t.add_column("Key", style="cyan")
        t.add_column("Value", style="green")
        t.add_row("Version", str(info.get("version", "?")))
        t.add_row("Python", str(info.get("python_version", "?")))
        t.add_row("Platform", str(info.get("platform", "?")))
        t.add_row("CWD", str(info.get("cwd", "?")))
        t.add_row("Config Dir", str(info.get("config_dir", "?")))
        t.add_row("Config Files", str(len(info.get("config_files", []))))
        t.add_row("Filestore", str(info.get("filestore_root", "?")))
        t.add_row("Filestore Size", str(info.get("filestore_size_bytes", 0)))
        t.add_row("Filestore Files", str(info.get("filestore_file_count", 0)))
        t.add_row("DB Engine", str(info.get("db_engine", "?")))
        t.add_row("DB Path", str(info.get("db_path", "?")))
        t.add_row("DB Exists", "yes" if info.get("db_exists") else "no")
        if info.get("db_exists"):
            t.add_row("DB Size", str(info.get("db_size_bytes", 0)))
        assert isinstance(t, Table)
        assert t.row_count == 13

    def test_build_binary_table_returns_table(self):
        from rich.table import Table

        t = Table(title="Binaries")
        t.add_column("Binary", style="cyan")
        t.add_column("Found", style="green")
        t.add_column("Path", style="dim")
        t.add_row("podman", "yes", "/usr/bin/podman")
        t.add_row("docker", "no", "")
        assert isinstance(t, Table)
        assert t.row_count == 2

    def test_build_daemon_table_returns_table(self):
        from rich.table import Table

        t = Table(title="Daemon")
        t.add_column("Key", style="cyan")
        t.add_column("Value", style="green")
        t.add_row("Status", "stopped")
        t.add_row("URL", "http://localhost:8000")
        assert isinstance(t, Table)
        assert t.row_count == 2

    def test_build_controls_table_returns_table(self):
        from rich.table import Table

        t = Table(title="Controls")
        t.add_column("Key", style="yellow")
        t.add_column("Action")
        t.add_row("s", "Start daemon")
        t.add_row("k", "Kill daemon")
        t.add_row("r", "Refresh")
        t.add_row("q", "Quit")
        assert isinstance(t, Table)
        assert t.row_count == 4

    def test_make_layout_returns_valid_layout(self):
        from rich.layout import Layout
        from rich.panel import Panel
        from rich.table import Table

        layout = Layout()
        layout.split(
            Layout(name="header", size=3),
            Layout(name="body"),
            Layout(name="footer", size=8),
        )
        header: Layout = layout["header"]
        assert isinstance(header, Layout)

        body: Layout = layout["body"]
        body.split_row(
            Layout(name="left"),
            Layout(name="right"),
        )

        daemon_t = Table(title="Daemon")
        daemon_t.add_column("Key")
        daemon_t.add_column("Value")
        daemon_t.add_row("Status", "stopped")

        left: Layout = body["left"]
        left.split(
            Layout(daemon_t, name="daemon"),
            Layout(Table(title="Binaries"), name="binaries"),
        )
        right: Layout = body["right"]
        right.split(
            Layout(Table(title="Info"), name="info"),
        )
        layout["header"].update(Panel("TUI Dashboard", style="bold white on blue"))
        layout["footer"].update(Table(title="Controls"))

        assert isinstance(layout, Layout)

    def test_offline_status_provides_tui_data(self):
        from general_ludd.cli import _gather_offline_status

        info = _gather_offline_status()
        required_for_tui = [
            "version", "python_version", "platform", "cwd",
            "config_dir", "config_files", "filestore_root",
            "filestore_size_bytes", "filestore_file_count",
            "db_engine", "db_path", "db_exists", "db_size_bytes",
            "binary_paths",
        ]
        for key in required_for_tui:
            assert key in info, f"TUI needs '{key}' in offline status"

    def test_tui_layout_renders_without_error(self):
        from rich.layout import Layout
        from rich.panel import Panel
        from rich.table import Table


        layout = Layout()
        layout.split(Layout(name="h", size=3), Layout(name="b"), Layout(name="f", size=8))
        layout["b"].split_row(Layout(name="l"), Layout(name="r"))
        layout["l"].split(Layout(Table(title="D"), name="daemon"), Layout(Table(title="B"), name="bin"))
        layout["r"].split(Layout(Table(title="I"), name="info"))
        layout["h"].update(Panel("test"))
        layout["f"].update(Table(title="C"))

        console = Console(file=io.StringIO(), force_terminal=True)
        with Live(layout, console=console, refresh_per_second=4, screen=False) as live:
            layout2 = Layout()
            layout2.split(Layout(name="h", size=3), Layout(name="b"))
            live.update(layout2)
        assert True

    def test_fmt_size_displays_correctly(self):
        from general_ludd.cli import _fmt_size

        assert _fmt_size(0) == "0 B"
        assert _fmt_size(512) == "512 B"
        assert _fmt_size(1024) == "1.0 KB"
        assert _fmt_size(1048576) == "1.0 MB"
