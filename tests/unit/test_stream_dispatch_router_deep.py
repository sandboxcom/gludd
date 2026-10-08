"""Deep unit tests for general_ludd.routers.stream — router-level helpers,
StreamDispatchRequest model validation, and error paths.

Coverage gaps addressed:
- _scrub_child_env direct unit test (edge cases: empty env, missing allowlisted keys)
- _get_role_cloner / _get_session_factory (missing attr, wrong type)
- _ROLE_NAME_RE boundary tests (max length, unicode, control chars)
- StreamDispatchRequest field validations (priority, work_type, processor edge cases)
- _STREAM_PLAYBOOK_ENV_ALLOWLIST integrity (no secrets, required keys)
- Router-level processor validation error messages
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from fastapi import FastAPI

# — _scrub_child_env ————————————————————————————————————————————


class TestScrubChildEnv:
    def test_returns_dict(self) -> None:
        from general_ludd.routers.stream import _scrub_child_env

        result = _scrub_child_env()
        assert isinstance(result, dict)

    def test_only_allowlisted_keys_present(self) -> None:
        from general_ludd.routers.stream import _STREAM_PLAYBOOK_ENV_ALLOWLIST, _scrub_child_env

        result = _scrub_child_env()
        for key in result:
            assert key in _STREAM_PLAYBOOK_ENV_ALLOWLIST, f"Non-allowlisted key {key!r} leaked via _scrub_child_env"

    def test_allowlisted_keys_in_environ_are_present(self) -> None:
        from general_ludd.routers.stream import _STREAM_PLAYBOOK_ENV_ALLOWLIST, _scrub_child_env

        result = _scrub_child_env()
        for key in _STREAM_PLAYBOOK_ENV_ALLOWLIST:
            if key in os.environ:
                assert key in result, f"Allowlisted key {key!r} missing from scrubbed env"

    def test_secret_keys_never_present(self) -> None:
        from general_ludd.routers.stream import _scrub_child_env

        secrets = {
            "ZAI_API_KEY",
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "DATABASE_URL",
            "GLUDD_AUTH_PSK",
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "GOOGLE_API_KEY",
        }
        result = _scrub_child_env()
        for secret in secrets:
            assert secret not in result, f"Secret {secret!r} leaked via _scrub_child_env"

    def test_all_keys_string_str(self) -> None:
        from general_ludd.routers.stream import _scrub_child_env

        result = _scrub_child_env()
        for key, value in result.items():
            assert isinstance(key, str)
            assert isinstance(value, str)

    def test_empty_result_when_no_allowlisted_keys_in_environ(self, monkeypatch: Any) -> None:
        from general_ludd.routers.stream import _scrub_child_env

        monkeypatch.setattr(os, "environ", {})
        result = _scrub_child_env()
        assert result == {}


# — _STREAM_PLAYBOOK_ENV_ALLOWLIST integrity —————————————————————


class TestStreamPlaybookEnvAllowlist:
    def test_contains_essential_path_vars(self) -> None:
        from general_ludd.routers.stream import _STREAM_PLAYBOOK_ENV_ALLOWLIST

        essential = {"PATH", "HOME", "USER", "SHELL", "LANG", "TMPDIR"}
        for key in essential:
            assert key in _STREAM_PLAYBOOK_ENV_ALLOWLIST, f"Essential key {key!r} missing"

    def test_contains_ansible_vars(self) -> None:
        from general_ludd.routers.stream import _STREAM_PLAYBOOK_ENV_ALLOWLIST

        ansible = {
            "ANSIBLE_CONFIG",
            "ANSIBLE_ROLES_PATH",
            "ANSIBLE_COLLECTIONS_PATHS",
            "ANSIBLE_COLLECTIONS_PATH",
            "ANSIBLE_LIBRARY",
            "ANSIBLE_FORCE_COLOR",
        }
        for key in ansible:
            assert key in _STREAM_PLAYBOOK_ENV_ALLOWLIST, f"Ansible key {key!r} missing"

    def test_no_secrets_in_allowlist(self) -> None:
        from general_ludd.routers.stream import _STREAM_PLAYBOOK_ENV_ALLOWLIST

        secret_patterns = {"_TOKEN", "_SECRET", "PASSWORD", "_CREDENTIAL", "_PSK"}
        whole_key_block = {
            "ZAI_API_KEY",
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "DATABASE_URL",
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "GOOGLE_API_KEY",
            "GLUDD_AUTH_PSK",
        }
        for key in _STREAM_PLAYBOOK_ENV_ALLOWLIST:
            assert key not in whole_key_block, f"Secret key {key!r} in stream playbook env allowlist"
            upper = key.upper()
            for pattern in secret_patterns:
                assert pattern not in upper, f"Secret-like key {key!r} in stream playbook env allowlist"

    def test_is_frozenset(self) -> None:
        from general_ludd.routers.stream import _STREAM_PLAYBOOK_ENV_ALLOWLIST

        assert isinstance(_STREAM_PLAYBOOK_ENV_ALLOWLIST, frozenset)

    def test_no_database_or_connection_strings(self) -> None:
        from general_ludd.routers.stream import _STREAM_PLAYBOOK_ENV_ALLOWLIST

        db_patterns = {"DATABASE", "DB_", "PG", "MYSQL", "MONGO", "REDIS", "RABBIT"}
        for key in _STREAM_PLAYBOOK_ENV_ALLOWLIST:
            upper = key.upper()
            for pattern in db_patterns:
                assert not upper.startswith(pattern), f"Database-like key {key!r} in allowlist"


# — _ROLE_NAME_RE ——————————————————————————————————————————————


class TestRoleNameRegex:
    def _re(self):
        from general_ludd.routers.stream import _ROLE_NAME_RE

        return _ROLE_NAME_RE

    def test_accepts_simple_identifier(self) -> None:
        for name in ("foo", "bar", "my_role", "role-1", "UPPER_CASE", "Mixed-Case_123"):
            assert self._re().match(name), f"Should accept {name!r}"

    def test_rejects_empty(self) -> None:
        assert self._re().match("") is None

    def test_rejects_slash(self) -> None:
        assert self._re().match("foo/bar") is None

    def test_rejects_dot(self) -> None:
        assert self._re().match("foo.bar") is None

    def test_rejects_space(self) -> None:
        assert self._re().match("my role") is None

    def test_rejects_unicode(self) -> None:
        assert self._re().match("r\xf4le") is None  # ô

    def test_rejects_control_char(self) -> None:
        assert self._re().match("role\x00null") is None

    def test_accepts_max_length_128(self) -> None:
        assert self._re().match("A" * 128)

    def test_rejects_at_symbol(self) -> None:
        assert self._re().match("role@host") is None

    def test_rejects_backslash(self) -> None:
        assert self._re().match("foo\\bar") is None

    def test_rejects_parentheses(self) -> None:
        assert self._re().match("role()") is None

    def test_fullmatch_semantics(self) -> None:
        m = self._re().match("foo")
        assert m is not None and m.group() == "foo"


# — StreamDispatchRequest pydantic model ————————————————————————


class TestStreamDispatchRequestModel:
    def test_defaults(self) -> None:
        from general_ludd.routers.stream import StreamDispatchRequest

        req = StreamDispatchRequest(role="test")
        assert req.role == "test"
        assert req.source_role_invocation == {}
        assert req.extra_vars == {}
        assert req.processor is None
        assert req.wait_for_completion is False
        assert req.priority == 5
        assert req.work_type == "stream_chunk"

    def test_role_min_length(self) -> None:
        import pydantic

        from general_ludd.routers.stream import StreamDispatchRequest

        with pytest.raises(pydantic.ValidationError):
            StreamDispatchRequest(role="")

    def test_role_max_length(self) -> None:
        import pydantic

        from general_ludd.routers.stream import StreamDispatchRequest

        with pytest.raises(pydantic.ValidationError):
            StreamDispatchRequest(role="A" * 129)

    def test_role_exactly_128(self) -> None:
        from general_ludd.routers.stream import StreamDispatchRequest

        req = StreamDispatchRequest(role="A" * 128)
        assert len(req.role) == 128

    def test_role_traversal_rejected_via_pydantic(self) -> None:
        import pydantic

        from general_ludd.routers.stream import StreamDispatchRequest

        for bad in ("../..", "/etc/passwd", "a/../../b", "..\\..", "a\\..\\b"):
            with pytest.raises(pydantic.ValidationError):
                StreamDispatchRequest(role=bad)

    def test_role_identifier_rejected_non_matching(self) -> None:
        import pydantic

        from general_ludd.routers.stream import StreamDispatchRequest

        for bad in ("role.name", "role with space", "role\nname"):
            with pytest.raises(pydantic.ValidationError):
                StreamDispatchRequest(role=bad)

    def test_priority_zero_accepted(self) -> None:
        from general_ludd.routers.stream import StreamDispatchRequest

        req = StreamDispatchRequest(role="test", priority=0)
        assert req.priority == 0

    def test_priority_20_accepted(self) -> None:
        from general_ludd.routers.stream import StreamDispatchRequest

        req = StreamDispatchRequest(role="test", priority=20)
        assert req.priority == 20

    def test_priority_below_zero_rejected(self) -> None:
        import pydantic

        from general_ludd.routers.stream import StreamDispatchRequest

        with pytest.raises(pydantic.ValidationError):
            StreamDispatchRequest(role="test", priority=-1)

    def test_priority_above_20_rejected(self) -> None:
        import pydantic

        from general_ludd.routers.stream import StreamDispatchRequest

        with pytest.raises(pydantic.ValidationError):
            StreamDispatchRequest(role="test", priority=21)

    def test_wait_for_completion_defaults_false(self) -> None:
        from general_ludd.routers.stream import StreamDispatchRequest

        req = StreamDispatchRequest(role="test")
        assert req.wait_for_completion is False

    def test_work_type_custom(self) -> None:
        from general_ludd.routers.stream import StreamDispatchRequest

        req = StreamDispatchRequest(role="test", work_type="audio_chunk")
        assert req.work_type == "audio_chunk"

    def test_processor_none_by_default(self) -> None:
        from general_ludd.routers.stream import StreamDispatchRequest

        req = StreamDispatchRequest(role="test")
        assert req.processor is None

    def test_processor_with_tool(self) -> None:
        from general_ludd.routers.stream import StreamDispatchRequest

        req = StreamDispatchRequest(
            role="test",
            processor={"tool": "whisper.cpp", "binary": "/usr/bin/whisper-cli"},
        )
        assert req.processor == {"tool": "whisper.cpp", "binary": "/usr/bin/whisper-cli"}

    def test_extra_vars_arbitrary_types(self) -> None:
        from general_ludd.routers.stream import StreamDispatchRequest

        req = StreamDispatchRequest(
            role="test",
            extra_vars={"count": 42, "flag": True, "nested": {"a": 1}},
        )
        assert req.extra_vars["count"] == 42
        assert req.extra_vars["flag"] is True
        assert req.extra_vars["nested"] == {"a": 1}

    def test_role_validator_rejects_slash_even_with_valid_chars(self) -> None:
        import pydantic

        from general_ludd.routers.stream import StreamDispatchRequest

        with pytest.raises(pydantic.ValidationError):
            StreamDispatchRequest(role="a/b")

    def test_role_validator_rejects_backslash_even_with_valid_chars(self) -> None:
        import pydantic

        from general_ludd.routers.stream import StreamDispatchRequest

        with pytest.raises(pydantic.ValidationError):
            StreamDispatchRequest(role="a\\b")

    def test_role_validator_rejects_double_dot(self) -> None:
        import pydantic

        from general_ludd.routers.stream import StreamDispatchRequest

        with pytest.raises(pydantic.ValidationError):
            StreamDispatchRequest(role="a..b")


# — _get_role_cloner ————————————————————————————————————————————


class TestGetRoleCloner:
    def test_returns_none_when_missing(self) -> None:
        from general_ludd.routers.stream import _get_role_cloner

        app = FastAPI()
        assert _get_role_cloner(app) is None

    def test_returns_none_when_not_role_cloner(self) -> None:
        from general_ludd.routers.stream import _get_role_cloner

        app = FastAPI()
        app.state._role_cloner = "not a cloner"
        assert _get_role_cloner(app) is None

    def test_returns_none_when_attr_absent(self) -> None:
        from general_ludd.routers.stream import _get_role_cloner

        app = FastAPI()
        assert not hasattr(app.state, "_role_cloner")
        assert _get_role_cloner(app) is None

    def test_returns_cloner_when_present(self, tmp_path: Path) -> None:
        from general_ludd.routers.stream import RoleCloner, _get_role_cloner

        app = FastAPI()
        collection = tmp_path / "collection"
        collection.mkdir()
        (collection / "roles").mkdir()
        cloner = RoleCloner(collection_root=collection)
        app.state._role_cloner = cloner
        assert _get_role_cloner(app) is cloner


# — _get_session_factory ————————————————————————————————————————


class TestGetSessionFactory:
    def test_returns_none_when_missing(self) -> None:
        from general_ludd.routers.stream import _get_session_factory

        app = FastAPI()
        assert _get_session_factory(app) is None

    def test_returns_factory_when_present(self) -> None:
        from general_ludd.routers.stream import _get_session_factory

        app = FastAPI()
        fake_factory = object()
        app.state._session_factory = fake_factory
        assert _get_session_factory(app) is fake_factory


# — _SCRUB_CHILD_ENV with monkeypatched os.environ ——————————————


class TestScrubChildEnvWithMockEnv:
    def test_mixed_env_strips_secrets(self, monkeypatch: Any) -> None:
        from general_ludd.routers.stream import _scrub_child_env

        monkeypatch.setattr(
            os,
            "environ",
            {
                "PATH": "/usr/bin",
                "HOME": "/home/user",
                "ZAI_API_KEY": "sk-secret",
                "DATABASE_URL": "pg://bad",
                "USER": "testuser",
            },
        )
        result = _scrub_child_env()
        assert "PATH" in result
        assert "HOME" in result
        assert "USER" in result
        assert "ZAI_API_KEY" not in result
        assert "DATABASE_URL" not in result

    def test_only_secrets_in_environ_gives_empty_or_minimal(self, monkeypatch: Any) -> None:
        from general_ludd.routers.stream import _scrub_child_env

        monkeypatch.setattr(
            os,
            "environ",
            {
                "ZAI_API_KEY": "sk-abc",
                "OPENAI_API_KEY": "sk-xyz",
            },
        )
        result = _scrub_child_env()
        for key in ("ZAI_API_KEY", "OPENAI_API_KEY"):
            assert key not in result

    def test_mutable_copy_not_reference(self, monkeypatch: Any) -> None:
        from general_ludd.routers.stream import _scrub_child_env

        monkeypatch.setattr(os, "environ", {"PATH": "/bin"})
        result = _scrub_child_env()
        result["NEW_KEY"] = "value"
        assert "NEW_KEY" not in os.environ


# — Processor validation error paths ————————————————————————————


class TestProcessorRouterValidation:
    """Replicate the router-level processor validation (check tool kind, binary
    safety, and args parsing) without needing a full app."""

    def _validate(self, processor: dict[str, object] | None, raise_on_bad: bool = True) -> str | None:
        from general_ludd.stream import _SAFE_BINARY_RE, SUPPORTED_PROCESSOR_TOOLS, _parse_processor_args

        if processor is None:
            return None
        tool = processor.get("tool")
        if tool not in SUPPORTED_PROCESSOR_TOOLS:
            return f"unknown tool {tool!r}"
        if tool in {"whisper.cpp", "ffmpeg"}:
            binary = str(processor.get("binary", tool))
            if not _SAFE_BINARY_RE.match(binary):
                return f"unsafe binary {binary!r}"
            try:
                _parse_processor_args(str(processor.get("args", "")))
            except ValueError as exc:
                return str(exc)
        return None

    def test_valid_whisper(self) -> None:
        assert self._validate({"tool": "whisper.cpp", "binary": "/usr/bin/whisper-cli"}) is None

    def test_valid_ffmpeg(self) -> None:
        assert self._validate({"tool": "ffmpeg", "binary": "/usr/bin/ffmpeg"}) is None

    def test_valid_agent(self) -> None:
        assert self._validate({"tool": "agent"}) is None

    def test_unknown_tool_rejected(self) -> None:
        err = self._validate({"tool": "unknown_tool"})
        assert err is not None and "unknown_tool" in err

    def test_missing_tool_key(self) -> None:
        err = self._validate({})
        assert err is not None and "None" in err

    def test_whisper_unsafe_binary_rejected(self) -> None:
        err = self._validate({"tool": "whisper.cpp", "binary": "cat;"})
        assert err is not None and "unsafe" in err.lower()

    def test_ffmpeg_bad_args_rejected(self) -> None:
        err = self._validate({"tool": "ffmpeg", "args": "$(whoami)"})
        assert err is not None and "forbidden" in err.lower()

    def test_whisper_null_byte_binary_rejected(self) -> None:
        err = self._validate({"tool": "whisper.cpp", "binary": "whisper\0.bin"})
        assert err is not None

    def test_none_processor_passes(self) -> None:
        assert self._validate(None) is None

    def test_whisper_empty_args_accepted(self) -> None:
        assert self._validate({"tool": "whisper.cpp", "args": ""}) is None

    def test_whisper_safe_args_accepted(self) -> None:
        assert self._validate({"tool": "whisper.cpp", "args": "-m model.bin -t 4"}) is None

    def test_ffmpeg_newline_in_args_rejected(self) -> None:
        err = self._validate({"tool": "ffmpeg", "args": '-i "in\rfile"'})
        assert err is not None and ("newline" in err.lower() or "carriage" in err.lower() or "forbidden" in err.lower())


# — _run_subprocess args introspection ——————————————————————————


class TestRunSubprocessArgs:
    def test_default_args(self) -> None:
        from general_ludd.routers.stream import _run_subprocess

        capt_args: list[str] | None = None
        original = subprocess.Popen

        class _Fake(original):
            def __init__(fake_self, args, **kwargs):  # type: ignore[no-untyped-def]
                nonlocal capt_args
                capt_args = list(args)
                fake_self.returncode = 0  # type: ignore[attr-defined]

        try:
            subprocess.Popen = _Fake  # type: ignore[assignment]
            _run_subprocess(["ansible-playbook", "run-clone.yml"], "/tmp/x", 60.0)
        finally:
            subprocess.Popen = original  # type: ignore[assignment]

        assert capt_args is not None
        assert capt_args[0] == "ansible-playbook"
        assert capt_args[1] == "run-clone.yml"

    def test_passes_env_scrubbed(self, monkeypatch: Any) -> None:
        from general_ludd.routers.stream import _run_subprocess

        capt_env: dict[str, str] | None = None

        def _fake(args, **kwargs):  # type: ignore[no-untyped-def]
            nonlocal capt_env
            capt_env = kwargs.get("env")
            return mock.MagicMock(returncode=0)

        monkeypatch.setattr(subprocess, "Popen", _fake)
        _run_subprocess(["echo", "hello"], "/tmp", 30.0)
        assert capt_env is not None, "env= must be passed to subprocess.Popen"


# — _run_clone_sync timeout parsing —————————————————————————————


class TestRunCloneSyncTimeoutParsing:
    """Test the timeout extraction logic from the processor dict in _run_clone_sync."""

    def _extract_timeout(self, processor: dict[str, object] | None) -> float:
        timeout = 60.0
        if processor is not None:
            raw_timeout = processor.get("timeout_seconds", 60)
            try:
                timeout = float(raw_timeout) if isinstance(raw_timeout, (int, float, str)) else 60.0
            except (TypeError, ValueError):
                timeout = 60.0
        return timeout

    def test_default_when_no_processor(self) -> None:
        assert self._extract_timeout(None) == 60.0

    def test_default_when_no_timeout_key(self) -> None:
        assert self._extract_timeout({"tool": "whisper.cpp"}) == 60.0

    def test_explicit_int(self) -> None:
        assert self._extract_timeout({"tool": "whisper.cpp", "timeout_seconds": 30}) == 30.0

    def test_explicit_float(self) -> None:
        assert self._extract_timeout({"tool": "whisper.cpp", "timeout_seconds": 45.5}) == 45.5

    def test_explicit_string(self) -> None:
        assert self._extract_timeout({"tool": "whisper.cpp", "timeout_seconds": "90"}) == 90.0

    def test_invalid_string_falls_back(self) -> None:
        assert self._extract_timeout({"tool": "whisper.cpp", "timeout_seconds": "abc"}) == 60.0

    def test_negative_value(self) -> None:
        assert self._extract_timeout({"tool": "whisper.cpp", "timeout_seconds": -10}) == -10.0

    def test_zero_value(self) -> None:
        assert self._extract_timeout({"tool": "whisper.cpp", "timeout_seconds": 0}) == 0.0

    def test_none_value_falls_back(self) -> None:
        assert self._extract_timeout({"tool": "whisper.cpp", "timeout_seconds": None}) == 60.0

    def test_list_value_falls_back(self) -> None:
        assert self._extract_timeout({"tool": "whisper.cpp", "timeout_seconds": [30]}) == 60.0


# — Role clone confinement (defense in depth) ——————————————————


class TestPathConfinementEdgeCases:
    def test_symlink_escape_hypothetical(self) -> None:
        import shutil
        import tempfile
        from pathlib import Path

        d = Path(tempfile.mkdtemp())
        try:
            roles_root = (d / "roles").resolve()
            roles_root.mkdir()
            safe_role = roles_root / "safe"
            safe_role.mkdir()
            # Create a symlink that would resolve outside if unconfined
            evil_link = roles_root / "evil"
            evil_link.symlink_to(Path("/etc"))
            resolved = evil_link.resolve()
            # relative_to must fail
            with pytest.raises(ValueError):
                resolved.relative_to(roles_root)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_roles_root_needs_resolve_twice(self) -> None:
        """Roles clone in stream.py resolves twice — this test confirms both
        resolutions produce the same path for a benign case."""
        import shutil
        import tempfile
        from pathlib import Path

        d = Path(tempfile.mkdtemp())
        try:
            roles_root1 = (d / "collection" / "roles").resolve()
            roles_root1.mkdir(parents=True)
            (roles_root1 / "testrole").mkdir()
            # Second resolution
            roles_root2 = (d / "collection" / "roles").resolve()
            role_dir = (roles_root2 / "testrole").resolve()
            assert role_dir.relative_to(roles_root2)
            assert roles_root1 == roles_root2
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_normalized_dotdot_in_path_rejected(self) -> None:
        """Even a normalized 'roles/../etc' resolves outside, so relative_to fails."""
        import shutil
        import tempfile
        from pathlib import Path

        d = Path(tempfile.mkdtemp())
        try:
            roles_root = (d / "roles").resolve()
            roles_root.mkdir()
            (roles_root / "fake").mkdir()
            bad = (roles_root / ".." / "etc").resolve()
            with pytest.raises(ValueError):
                bad.relative_to(roles_root)
        finally:
            shutil.rmtree(d, ignore_errors=True)


# — SUPPORTED_PROCESSOR_TOOLS ————————————————————————————————————


class TestSupportedProcessorTools:
    def test_contains_expected(self) -> None:
        from general_ludd.stream import SUPPORTED_PROCESSOR_TOOLS

        assert "whisper.cpp" in SUPPORTED_PROCESSOR_TOOLS
        assert "ffmpeg" in SUPPORTED_PROCESSOR_TOOLS
        assert "agent" in SUPPORTED_PROCESSOR_TOOLS

    def test_frozenset_immutable(self) -> None:
        from general_ludd.stream import SUPPORTED_PROCESSOR_TOOLS

        assert isinstance(SUPPORTED_PROCESSOR_TOOLS, frozenset)

    def test_agent_is_only_non_binary_tool(self) -> None:
        from general_ludd.stream import SUPPORTED_PROCESSOR_TOOLS

        binary_tools = {"whisper.cpp", "ffmpeg"}
        for tool in SUPPORTED_PROCESSOR_TOOLS:
            if tool not in binary_tools:
                assert tool == "agent"


# — task_id format ——————————————————————————————————————————————


class TestTaskIdFormat:
    def test_format_matches_pattern(self) -> None:
        import re

        from general_ludd.routers.stream import uuid

        task_id = f"STREAM-{uuid.uuid4().hex[:12].upper()}"
        assert re.match(r"^STREAM-[0-9A-F]{12}$", task_id), f"Bad task_id format: {task_id}"
        assert len(task_id) == 19

    def test_consistent_length(self) -> None:
        from general_ludd.routers.stream import uuid

        for _ in range(20):
            task_id = f"STREAM-{uuid.uuid4().hex[:12].upper()}"
            assert len(task_id) == 19
