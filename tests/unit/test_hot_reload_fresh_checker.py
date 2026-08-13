"""Pin scripts/check_hot_reload_fresh.py stale-artifact detection.

Regression: CI run 29449765249 failed because the bare ``ReferenceError``
pattern matched a JS *comment* inside the generated hot module for
enforce-stop ("// ... (ReferenceError on every session.idle) ..."), which is
inert text, not a stale TS artifact. The checker must ignore comments and
only flag real error dumps / surviving TS syntax.
"""

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_hot_reload_fresh.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_hot_reload_fresh", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["check_hot_reload_fresh"] = mod
    spec.loader.exec_module(mod)
    return mod


class TestStaleArtifactDetection:
    def test_word_in_line_comment_is_not_stale(self):
        mod = _load_module()
        content = (
            "// FIXED 2026-07-15: `text` was previously referenced without being\n"
            "// defined in this scope (ReferenceError on every session.idle). The\n"
            "var text = String(ev.text || '');\n"
        )
        assert mod.is_stale_content(content) == []

    def test_actual_error_dump_is_stale(self):
        mod = _load_module()
        content = "ReferenceError: hasRealPendingWork is not defined\n    at eval\n"
        assert mod.is_stale_content(content) != []

    def test_type_annotation_in_comment_is_not_stale(self):
        mod = _load_module()
        content = "// the field shape is name: string per the spec\nvar name = 'x';\n"
        assert mod.is_stale_content(content) == []

    def test_surviving_ts_type_annotation_is_stale(self):
        mod = _load_module()
        content = "function foo(name: string) { return name; }\n"
        assert mod.is_stale_content(content) != []

    def test_esbuild_void_ternary_is_valid_javascript(self):
        mod = _load_module()
        content = (
            "async function invoke(hooks, name, input, output) {\n"
            "  const fn = hooks[name];\n"
            "  return fn ? await fn(input, output) : void 0;\n"
            "}\n"
        )
        assert mod.is_stale_content(content) == []

    def test_surviving_export_const_is_stale(self):
        mod = _load_module()
        content = "export const FLOOR = 10;\n"
        assert mod.is_stale_content(content) != []

    def test_export_mentioned_in_comment_is_not_stale(self):
        mod = _load_module()
        content = "// export const was stripped by tsToJs\nvar FLOOR = 10;\n"
        assert mod.is_stale_content(content) == []

    def test_url_in_string_survives_comment_stripping(self):
        mod = _load_module()
        content = 'var url = "https://opencode.ai"; export const X = 1;\n'
        assert mod.is_stale_content(content) != []


def test_hot_module_name_matches_proxy_lookup(tmp_path):
    mod = _load_module()
    source = tmp_path / "enforce-example.ts"
    source.write_text(
        'const defaultImpl = {};\nloadHotModule("example", defaultImpl);\n',
        encoding="utf-8",
    )

    assert mod.hot_module_name(source, "enforce-example") == "example"


def test_implementation_source_follows_thin_proxy(tmp_path):
    mod = _load_module()
    plugin_dir = tmp_path / "plugin"
    impl_dir = plugin_dir / "impl"
    impl_dir.mkdir(parents=True)
    wrapper = plugin_dir / "enforce-example.ts"
    implementation = impl_dir / "enforce_example_impl.ts"
    wrapper.write_text(
        'import impl from "./impl/enforce_example_impl.ts";\nexport default impl;\n',
        encoding="utf-8",
    )
    implementation.write_text(
        'const defaultImpl = {};\nloadHotModule("example", defaultImpl);\n',
        encoding="utf-8",
    )

    assert mod.implementation_source(wrapper) == implementation
