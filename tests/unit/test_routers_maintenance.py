"""Structural tests for routers/maintenance.py — code intel, deps, issues, quality gate."""

from __future__ import annotations

import re
from typing import ClassVar

import pytest
from fastapi import FastAPI

from general_ludd.routers.maintenance import register

CONSTANTS = {
    "MAX_SEEN_KEYS": 256,
}


class TestModuleImport:
    def test_register_is_callable(self):
        assert callable(register)

    def test_register_accepts_two_args(self):
        app = FastAPI()
        try:
            register(app, {})
        except Exception as exc:
            raise AssertionError(f"register raised: {exc}") from exc


class TestConstants:
    def test_safe_slug_rejects_dangerous_characters(self):
        from general_ludd.routers.maintenance import _SAFE_SLUG

        for good in ("owner", "repo-name", "dot.repo", "under_score", "a" * 100):
            assert _SAFE_SLUG.match(good), f"should match {good!r}"
        for bad in ("owner;rm", "repo/evil", "a" * 101, "", "$PATH"):
            assert not _SAFE_SLUG.match(bad), f"should NOT match {bad!r}"

    def test_safe_label_rejects_dangerous_characters(self):
        from general_ludd.routers.maintenance import _SAFE_LABEL

        for good in ("area/bug", "kind:enhancement", "gludd"):
            assert _SAFE_LABEL.match(good), f"should match {good!r}"
        for bad in ("label;drop table", "", r"a\nb"):
            assert not _SAFE_LABEL.match(bad), f"should NOT match {bad!r}"

    def test_max_seen_keys_value(self):
        from general_ludd.routers.maintenance import _MAX_SEEN_KEYS

        assert isinstance(_MAX_SEEN_KEYS, int)
        assert CONSTANTS["MAX_SEEN_KEYS"] == _MAX_SEEN_KEYS

    def test_regex_are_compiled(self):
        from general_ludd.routers.maintenance import _SAFE_LABEL, _SAFE_SLUG

        assert isinstance(_SAFE_SLUG, re.Pattern)
        assert isinstance(_SAFE_LABEL, re.Pattern)

    def test_safe_slug_length_enforced(self):
        from general_ludd.routers.maintenance import _SAFE_SLUG

        assert _SAFE_SLUG.match("a" * 100)
        assert not _SAFE_SLUG.match("a" * 101)


class TestRouteRegistration:
    EXPECTED_PATHS: ClassVar[set[str]] = {
        "/admin/code-intel/hot-files",
        "/admin/deps/outdated",
        "/admin/issues/poll",
        "/admin/quality/check",
    }

    def test_registers_all_four_routes(self):
        app = FastAPI()
        register(app, {})
        registered = {r.path for r in app.routes}
        assert registered >= self.EXPECTED_PATHS

    def test_code_intel_hot_files_is_get(self):
        app = FastAPI()
        register(app, {})
        for r in app.routes:
            if r.path == "/admin/code-intel/hot-files":
                assert "GET" in r.methods
                return
        pytest.fail("route /admin/code-intel/hot-files not found")

    def test_deps_outdated_is_get(self):
        app = FastAPI()
        register(app, {})
        for r in app.routes:
            if r.path == "/admin/deps/outdated":
                assert "GET" in r.methods
                return
        pytest.fail("route /admin/deps/outdated not found")

    def test_issues_poll_is_post(self):
        app = FastAPI()
        register(app, {})
        for r in app.routes:
            if r.path == "/admin/issues/poll":
                assert "POST" in r.methods
                return
        pytest.fail("route /admin/issues/poll not found")

    def test_quality_check_is_post(self):
        app = FastAPI()
        register(app, {})
        for r in app.routes:
            if r.path == "/admin/quality/check":
                assert "POST" in r.methods
                return
        pytest.fail("route /admin/quality/check not found")


class TestRegisterReturnsNone:
    def test_register_returns_none(self):
        app = FastAPI()
        result = register(app, {})
        assert result is None


class TestDaemonStateMutation:
    def test_register_does_not_mutate_daemon_state_on_register(self):
        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)
        assert daemon_state == {}
