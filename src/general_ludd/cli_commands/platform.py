"""Offline platform and installation status helpers."""

from __future__ import annotations

from typing import Any

from general_ludd.config.binary_paths import BinaryPathResolver
from general_ludd.db.session import get_default_db_url, is_sqlite_url
from general_ludd.filestore.bootstrap import BinaryBootstrapper
from general_ludd.filestore.store import FileStore


def _gather_offline_status(config_dir: str | None = None) -> dict[str, Any]:
    import os
    import platform
    import sys
    from pathlib import Path

    from general_ludd import __version__

    cdir = config_dir or os.environ.get("GL_CONFIG_DIR")
    if not cdir:
        home = os.path.expanduser("~")
        cdir = os.path.join(home, ".config", "gludd")
    info: dict[str, Any] = {
        "version": __version__,
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "cwd": os.getcwd(),
    }
    cfiles: list[dict[str, Any]] = []
    if cdir and os.path.isdir(cdir):
        for f in sorted(os.listdir(cdir)):
            if f.endswith(".yml") or f.endswith(".yaml"):
                fp = os.path.join(cdir, f)
                try:
                    st = os.stat(fp)
                    cfiles.append(
                        {
                            "name": f,
                            "path": fp,
                            "size_bytes": st.st_size,
                            "modified": st.st_mtime,
                        }
                    )
                except OSError:
                    cfiles.append({"name": f, "path": fp, "size_bytes": 0, "modified": 0})
    info["config_dir"] = cdir
    info["config_files"] = cfiles

    store = FileStore()
    boot = BinaryBootstrapper(store=store)
    fs_root = store.root_path
    fs_exists = os.path.isdir(fs_root) if fs_root else False
    fs_size = 0
    fs_file_count = 0
    if fs_exists and fs_root:
        import contextlib

        for dirpath, _dirnames, filenames in os.walk(fs_root):
            for fn in filenames:
                fp = os.path.join(dirpath, fn)
                with contextlib.suppress(OSError):
                    fs_size += os.path.getsize(fp)
                fs_file_count += 1
    info["filestore_root"] = fs_root
    info["filestore_exists"] = fs_exists
    info["filestore_size_bytes"] = fs_size
    info["filestore_file_count"] = fs_file_count
    info["filestore_binaries"] = [b["name"] for b in boot.list_binaries()]

    db_url = get_default_db_url()
    db_is_sqlite = is_sqlite_url(db_url)
    db_path = db_url.replace("sqlite+aiosqlite:///", "") if db_is_sqlite else db_url
    db_exists = False
    db_size = 0
    if db_is_sqlite:
        expanded = Path(db_path).expanduser()
        if expanded.exists():
            db_exists = True
            db_size = expanded.stat().st_size
    info["db_path"] = str(db_path)
    info["db_exists"] = db_exists
    info["db_size_bytes"] = db_size
    info["db_engine"] = "sqlite" if db_is_sqlite else "postgresql"

    resolver = BinaryPathResolver()
    info["binary_paths"] = {}
    for bname in ("podman", "docker", "ansible-playbook", "openbao"):
        label = bname.replace("-playbook", "")
        info["binary_paths"][label] = resolver.resolve(bname) if resolver.is_available(bname) else None

    info["binary_versions"] = boot.get_known_versions()
    stored = boot.list_binaries_with_versions()
    info["filestore_binaries"] = [{"name": b["binary_name"], "version": b.get("version", "?")} for b in stored]
    return info


def _format_offline_status(info: dict[str, Any]) -> None:
    print(f"General Ludd Agent v{info['version']}  (python {info['python_version']}, {info['platform']})")
    print("\u2500" * 72)
    print(f"CWD:         {info['cwd']}")
    print(f"Config dir:  {info['config_dir']}")
    for cf in info.get("config_files", []):
        s = _fmt_size(cf["size_bytes"])
        print(f"  \u251c\u2500 {cf['name']}  ({s})")
    print()
    print(f"Filestore:   {info['filestore_root']}")
    if info["filestore_exists"]:
        s = _fmt_size(info["filestore_size_bytes"])
        print(f"  Files:     {info['filestore_file_count']}  ({s})")
    else:
        print("  (not created)")
    bins = info.get("filestore_binaries", [])
    if bins:
        print("  Binaries:")
        for b in bins:
            print(f"    \u251c\u2500 {b['name']:<12} v{b.get('version', '?')}")
    versions = info.get("binary_versions", {})
    if versions:
        print("  Available versions:")
        for name, ver in sorted(versions.items()):
            stored = any(b.get("name") == name for b in bins)
            status = "stored" if stored else "not downloaded"
            print(f"    \u251c\u2500 {name:<12} v{ver:<8} [{status}]")
    print()
    print(f"Database:    {info['db_path']}")
    if info["db_exists"]:
        s = _fmt_size(info["db_size_bytes"])
        print(f"  Engine:    {info['db_engine']}  ({s})")
    else:
        print(f"  Engine:    {info['db_engine']}  (not yet created)")
    print()
    print("Binary detection:")
    for name, path in info.get("binary_paths", {}).items():
        found = "found" if path else "not found"
        print(f"  {name:<12} {found:<12} {path or ''}")


def _fmt_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / 1024 / 1024:.1f} MB"

gather_offline_status = _gather_offline_status
format_offline_status = _format_offline_status
format_size = _fmt_size
