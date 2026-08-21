# -*- mode: python ; coding: utf-8 -*-
import sys

from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

# safehttpx reads its version.txt via importlib.resources at import time
# (general_ludd/security/url_fetch.py); PyInstaller does not auto-collect it,
# so the frozen CLI crashes with FileNotFoundError .../safehttpx/version.txt.
_safehttpx_datas = collect_data_files('safehttpx')

datas = [
    ('config', 'config'),
    ('templates', 'templates'),
    ('infra/terraform', 'general_ludd/terraform_assets'),
    ('LICENSE', '.'),
    ('THIRD_PARTY_LICENSES.md', '.'),
] + _safehttpx_datas

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
        # filelock._windows must NOT be excluded: filelock/__init__.py imports
        # it via an importlib path that fires in the frozen bundle regardless
        # of platform, so excluding it crashes the frozen daemon with
        # ModuleNotFoundError: No module named 'filelock._windows'
        # (CI binary_smoke_linux/macos, 2026-08-15).
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
        # The SPHINCS+ adapter and pqcrypto's CFFI/native backend are loaded
        # across module boundaries that PyInstaller cannot infer reliably on
        # every platform. Keep the public wrapper and its native implementation
        # explicit so a frozen Windows executable never ships a latent backend.
        'general_ludd.algorithms.sphincs_plus',
        'pqcrypto.sign.sphincs_shake_256s_simple',
        'pqcrypto._sign.sphincs_shake_256s_simple',
        'general_ludd.daemon',
        'general_ludd.worker.app',
        'general_ludd.event_loop.loop',
        'general_ludd.event_loop.lease',
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
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # The beta4 core executable and Ansible controller are separate Python
    # dependency planes. Playbooks run in the digest-pinned execution
    # environment, so no ansible-core/runner implementation belongs in this
    # frozen artifact. Excluding the complete controller runtime also avoids
    # ansible.cli's Windows cp1252 locale failure during PyInstaller analysis.
    excludes=[
        'pytest',
        'mypy',
        'ruff',
        'pre_commit',
        'molecule',
        'ansible_lint',
        # Ansible controller code and collections ship in the separately
        # locked execution-environment artifact.  The frozen core talks to
        # that controller boundary and must not carry a second Python runtime.
        'ansible',
        'ansible_runner',
        'ansible.cli',
        # The application uses stdlib sqlite3 and psycopg 3. SQLAlchemy's
        # generic hook otherwise probes these absent legacy/optional drivers.
        'pysqlite2',
        'MySQLdb',
        # PyInstaller's CFFI hook probes pycparser's optional generated table
        # modules. Modern pycparser installations build their tables in
        # memory, so these names are intentionally absent and never imported
        # by the frozen application. Excluding them prevents false missing
        # hidden-import warnings without changing the packaged runtime.
        'pycparser.lextab',
        'pycparser.yacctab',
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
