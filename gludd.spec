# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# Ansible ships YAML config files (ansible/config/base.yml, etc.) that are
# loaded at runtime via importlib resources. PyInstaller doesn't auto-detect
# these — without explicit collection, the binary crashes on startup with
# "Missing base YAML definition file (bad install?)".
# collect_data_files pulls all non-.py files from the ansible package.
_ansible_datas = collect_data_files('ansible')
_safehttpx_datas = collect_data_files('safehttpx')
_ansible_binaries = []

datas = [
    ('config', 'config'),
    ('templates', 'templates'),
    ('playbooks', 'playbooks'),
    ('LICENSE', '.'),
    ('THIRD_PARTY_LICENSES.md', '.'),
] + _ansible_datas + _safehttpx_datas

# Also collect ansible submodules that aren't auto-detected by the static
# analyzer (module_utils, plugins, etc. are imported dynamically).
_hidden_ansible = collect_submodules('ansible.module_utils')
_hidden_ansible += collect_submodules('ansible.plugins')
_hidden_ansible += collect_submodules('ansible.template')
_hidden_ansible += collect_submodules('ansible.galaxy')

a = Analysis(
    ['src/general_ludd/cli.py'],
    pathex=['src'],
    binaries=[],
    datas=datas,
    hiddenimports=[
        'general_ludd',
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
    excludes=['pytest', 'mypy', 'ruff', 'pre_commit', 'molecule', 'ansible_lint', 'ansible.cli'],
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
