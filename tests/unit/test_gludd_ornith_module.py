"""TDD tests for the gludd_ornith ansible module.

These tests are written FIRST (red) and drive the implementation of
``collections/ansible_collections/general_ludd/agent/plugins/modules/gludd_ornith.py``.
The module does not exist yet, so every test fails initially.

The module is imported directly via :mod:`importlib` from its file path so the
tests can inspect :func:`build_argument_spec` without standing up an
``AnsibleModule`` execution context.
"""

from __future__ import annotations

import ast
import importlib.util
import textwrap
from pathlib import Path

import pytest

MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "collections"
    / "ansible_collections"
    / "general_ludd"
    / "agent"
    / "plugins"
    / "modules"
    / "gludd_ornith.py"
)


def _load_module():
    """Import gludd_ornith.py from disk, bypassing the ansible collection path."""
    spec = importlib.util.spec_from_file_location("gludd_ornith", str(MODULE_PATH))
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _read_source() -> str:
    return MODULE_PATH.read_text(encoding="utf-8")


def test_module_file_exists() -> None:
    assert MODULE_PATH.exists(), f"expected module at {MODULE_PATH}"


def test_module_has_documentation_block() -> None:
    src = _read_source()
    block = _extract_docstring_block(src, "DOCUMENTATION")
    assert block is not None, "DOCUMENTATION block not found"
    assert "module: gludd_ornith" in block, "DOCUMENTATION must name module: gludd_ornith"

    try:
        import yaml
    except ImportError:
        pytest.skip("pyyaml not available")
    parsed = yaml.safe_load(block)
    assert isinstance(parsed, dict)
    assert parsed.get("module") == "gludd_ornith"


def test_module_has_examples_block() -> None:
    src = _read_source()
    assert _extract_docstring_block(src, "EXAMPLES") is not None, (
        "EXAMPLES block not found"
    )


def test_module_has_return_block() -> None:
    src = _read_source()
    assert _extract_docstring_block(src, "RETURN") is not None, (
        "RETURN block not found"
    )


def test_argument_spec_has_required_keys() -> None:
    mod = _load_module()
    spec = _get_argument_spec(mod)
    required = {"daemon_url", "psk", "task_description", "target_files", "max_iterations"}
    missing = required - set(spec)
    assert not missing, f"argument_spec missing keys: {sorted(missing)}"


def test_psk_is_no_log() -> None:
    mod = _load_module()
    spec = _get_argument_spec(mod)
    psk_entry = spec.get("psk")
    assert isinstance(psk_entry, dict), "psk entry must be a dict"
    assert psk_entry.get("no_log") is True, "psk must set no_log=True"


def test_target_files_default_is_empty_list() -> None:
    mod = _load_module()
    spec = _get_argument_spec(mod)
    entry = spec.get("target_files")
    assert isinstance(entry, dict)
    assert entry.get("default") == [], "target_files default must be []"


def test_max_iterations_default() -> None:
    mod = _load_module()
    spec = _get_argument_spec(mod)
    entry = spec.get("max_iterations")
    assert isinstance(entry, dict)
    assert entry.get("default") == 5, "max_iterations default must be 5"
    assert entry.get("type") == "int", "max_iterations type must be int"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _extract_docstring_block(src: str, name: str) -> str | None:
    """Return the body of a ``NAME = r'''...'''`` / triple-quote block.

    Accepts either a raw triple-quoted assignment (``DOCUMENTATION = r'''...''')``
    or a docstring-prefixed header (``DOCUMENTATION:``). Returns ``None`` when
    no block is found.
    """
    marker = f"{name} = r'''"
    idx = src.find(marker)
    if idx != -1:
        start = idx + len(marker)
        end = src.find("'''", start)
        if end == -1:
            return None
        return textwrap.dedent(src[start:end])

    dq_marker = f'{name} = """'
    idx = src.find(dq_marker)
    if idx != -1:
        start = idx + len(dq_marker)
        end = src.find('"""', start)
        if end == -1:
            return None
        return textwrap.dedent(src[start:end])

    # Fallback: scan the module-level docstring for "NAME:" header.
    doc_match = src.count('"""')
    if doc_match >= 2:
        ds_start = src.index('"""') + 3
        ds_end = src.index('"""', ds_start)
        docstring = src[ds_start:ds_end]
        header = f"\n{name}:\n"
        hd_idx = docstring.find(header)
        if hd_idx == -1:
            header = f"{name}:\n"
            hd_idx = docstring.find(header)
            if hd_idx == -1:
                return None
            hd_idx = docstring.find(header)
        start_line = hd_idx + len(header)
        # Find the next top-level header (SAME indent level) or end of string.
        remainder = docstring[start_line:]
        next_header = None
        for m_name in ("DOCUMENTATION", "EXAMPLES", "RETURN"):
            if m_name == name:
                continue
            mh = f"\n{m_name}:\n"
            mh_idx = remainder.find(mh)
            if mh_idx != -1 and (next_header is None or mh_idx < next_header):
                next_header = mh_idx
        if next_header is not None:
            return textwrap.dedent(remainder[:next_header]).strip()
        return textwrap.dedent(remainder).strip()

    return None


def _get_argument_spec(mod):
    """Return the argument_spec from ``build_argument_spec()`` or ``ARGUMENT_SPEC``."""
    if hasattr(mod, "build_argument_spec"):
        spec = mod.build_argument_spec()
        assert isinstance(spec, dict), "build_argument_spec() must return a dict"
        return spec
    if hasattr(mod, "ARGUMENT_SPEC"):
        spec = mod.ARGUMENT_SPEC
        assert isinstance(spec, dict), "ARGUMENT_SPEC must be a dict"
        return spec
    src = MODULE_PATH.read_text(encoding="utf-8")
    arg_match = _extract_argument_spec_from_src(src)
    if arg_match is not None:
        return arg_match
    pytest.fail(
        "module must expose build_argument_spec() or a top-level ARGUMENT_SPEC dict"
    )


def _extract_argument_spec_from_src(src: str) -> dict | None:
    """Parse inline ``argument_spec=dict(...)`` from the module source.

    Handles the pattern used in ``main()`` where the AnsibleModule is
    constructed with ``argument_spec=dict(state=dict(...))``.
    """
    import ast
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "argument_spec":
                    val = kw.value
                    if isinstance(val, ast.Dict):
                        return _parse_ast_dict(val)
                    if isinstance(val, ast.Call):
                        return _parse_dict_call(val)
    return None


def _parse_dict_call(call: ast.Call) -> dict:
    """Parse a ``dict(key=value, ...)`` call into a dict."""
    result = {}
    for ckw in call.keywords:
        if ckw.arg is None:
            continue
        if isinstance(ckw.value, ast.Call) and _is_dict_call(ckw.value):
            result[ckw.arg] = _parse_dict_call(ckw.value)
        elif isinstance(ckw.value, ast.Dict):
            result[ckw.arg] = _parse_ast_single_dict(ckw.value)
        else:
            result[ckw.arg] = _ast_val(ckw.value)
    return result


def _is_dict_call(node: ast.Call) -> bool:
    """Check if an ast.Call is ``dict(...)``."""
    return isinstance(node.func, ast.Name) and node.func.id == "dict"


def _parse_ast_single_dict(d: ast.Dict) -> dict:
    """Convert an inner ast.Dict (e.g. ``dict(type="str", default=...)``) to a Python dict."""
    entry = {}
    for ek, ev in zip(d.keys, d.values, strict=False):
        if isinstance(ek, ast.Constant) and isinstance(ek.value, str):
            entry[ek.value] = _ast_val(ev)
    return entry


def _parse_ast_dict(d: ast.Dict) -> dict:
    """Convert an ast.Dict literal to a Python dict."""
    result = {}
    for k, v in zip(d.keys, d.values, strict=False):
        if isinstance(k, ast.Constant) and isinstance(k.value, str):
            key = k.value
            if isinstance(v, ast.Dict):
                result[key] = _parse_ast_single_dict(v)
            else:
                result[key] = _ast_val(v)
    return result


def _ast_val(node) -> object:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.List):
        return [_ast_val(e) for e in node.elts]
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_ast_val(node.value)}.{node.attr}"
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "dict":
            d = {}
            for kw in node.keywords:
                if kw.arg and isinstance(kw.value, ast.Constant):
                    d[kw.arg] = kw.value.value
            return d
    return None
