# -*- mode: python ; coding: utf-8 -*-
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# Ansible ships YAML config files (ansible/config/base.yml, etc.) that are
# loaded at runtime via importlib resources. PyInstaller doesn't auto-detect
# these — without explicit collection, the binary crashes on startup with
# "Missing base YAML definition file (bad install?)".
# collect_data_files pulls all non-.py files from the ansible package.
_ansible_datas = collect_data_files('ansible')
_ansible_binaries = []
# safehttpx reads its version.txt via importlib.resources at import time
# (general_ludd/security/url_fetch.py); PyInstaller does not auto-collect it,
# so the frozen CLI crashes with FileNotFoundError .../safehttpx/version.txt.
_safehttpx_datas = collect_data_files('safehttpx')

datas = [
    ('config', 'config'),
    ('collections', 'collections'),
    ('templates', 'templates'),
    ('playbooks', 'playbooks'),
    ('infra/terraform', 'general_ludd/terraform_assets'),
    ('LICENSE', '.'),
    ('THIRD_PARTY_LICENSES.md', '.'),
] + _ansible_datas + _safehttpx_datas

# Also collect ansible submodules that aren't auto-detected by the static
# analyzer (module_utils, plugins, etc. are imported dynamically).
_ABSENT_ANSIBLE_SUBMODULES = {
    'ansible.module_utils.distro.__main__',
    'ansible.module_utils.distro.distro',
}


def _is_collectable_ansible_submodule(name):
    return name not in _ABSENT_ANSIBLE_SUBMODULES


# Ansible is not a supported native-Windows control node. Its recursive
# packages import POSIX stdlib modules while PyInstaller enumerates dynamic
# children, even though non-Ansible Gludd commands do not use those paths.
# PyInstaller documents ``on_error="ignore"`` for skipping those known
# unimportable children without emitting misleading build warnings.
_ansible_collect_error_mode = "ignore" if sys.platform == "win32" else "warn once"

_hidden_ansible = collect_submodules(
    'ansible.module_utils',
    filter=_is_collectable_ansible_submodule,
    on_error=_ansible_collect_error_mode,
)
_hidden_ansible += collect_submodules(
    'ansible.plugins',
    on_error=_ansible_collect_error_mode,
)
_hidden_ansible += collect_submodules(
    'ansible.template',
    on_error=_ansible_collect_error_mode,
)
_hidden_ansible += collect_submodules(
    'ansible.galaxy',
    on_error=_ansible_collect_error_mode,
)

# PyInstaller follows conditional imports from both platform branches. Exclude
# unavailable stdlib modules on Windows and Windows-only implementation modules
# on POSIX so analysis stays warning-free without stripping usable code.
_platform_excludes = []
if sys.platform == "win32":
    _platform_excludes = [
        'fcntl',
        'grp',
        'pty',
        'pwd',
        'resource',
        'termios',
        'tty',
    ]
if sys.platform != "win32":
    _platform_excludes = [
        'appdirs',
        'asyncio.windows_events',
        'asyncio.windows_utils',
        'click._winconsole',
        'dateutil.tz.win',
        'filelock._windows',
        'mcp.os.win32',
        'multiprocessing.popen_spawn_win32',
        'platformdirs.windows',
        'prompt_toolkit.input.win32',
        'prompt_toolkit.output.conemu',
        'prompt_toolkit.output.win32',
        'prompt_toolkit.output.windows10',
        'prompt_toolkit.win32_types',
    ]

a = Analysis(
    ['src/general_ludd/cli.py'],
    pathex=['src'],
    binaries=[],
    datas=datas,
    hiddenimports=[
        'general_ludd',
        # general_ludd.compat is imported at package init via a dynamic
        # importlib.import_module call (src/general_ludd/__init__.py), so
        # PyInstaller's static analyzer cannot discover it. Without these
        # hiddenimports the frozen CLI crashes on startup with
        # "ModuleNotFoundError: No module named 'general_ludd.compat'".
        'general_ludd.compat',
        'general_ludd.compat.annotated_types',
        'general_ludd.cli',
        'general_ludd.daemon',
        'general_ludd.worker.app',
        'general_ludd.event_loop.loop',
        'general_ludd.event_loop.lease',
        'general_ludd.ansible.runner',
        'general_ludd.ansible.core_runner',
        'general_ludd.ansible.templating',
        'general_ludd.models.gateway',
        'general_ludd.models.router',
        'general_ludd.db.models',
        'general_ludd.db.repository',
        'general_ludd.secrets.manager',
        'general_ludd.mcp.client',
        'general_ludd.mcp.transport',
        # safe_diskcache imports this serializer through importlib so static
        # PyInstaller analysis cannot discover it.
        'msgpack',
        # Frozen daemon startup re-execs Gludd and enters Gunicorn's bundled
        # console entry point; its logger and worker load dynamically.
        'gunicorn.app.wsgiapp',
        'gunicorn.glogging',
        'uvicorn_worker',
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
    ] + _hidden_ansible,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # ansible.cli is excluded: gludd drives ansible-core's executor API
    # (ansible.runner/core_runner/templating), never the CLI. Bundling ansible.cli
    # makes pyinstaller import it at build time, and ansible.cli.initialize_locale()
    # hard-fails on Windows' cp1252 locale ("Ansible requires UTF-8; Detected 1252").
    excludes=[
        'pytest',
        'mypy',
        'ruff',
        'pre_commit',
        'molecule',
        'ansible_lint',
        'ansible.cli',
        # The application uses stdlib sqlite3 and psycopg 3. SQLAlchemy's
        # generic hook otherwise probes these absent legacy/optional drivers.
        'pysqlite2',
        'MySQLdb',
    ] + _platform_excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='gludd',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
