"""Skeleton for greenfield todo-website dogfood scenario.

This is alpha.3 scaffolding -- the full live-model run is a TODO.
Offline machinery (fixtures, temp workspace, teardown) is exercised here.
Skip live assertions when no key present.

Run with: make test-unit TESTFILE='tests/e2e/dogfood/test_dogfood_todo_site.py'
"""
from __future__ import annotations

import pytest
from tests.e2e.dogfood._gateway import build_gateway
from tests.e2e.dogfood._secrets import load_llm_keys
from tests.e2e.dogfood._site import run_site_crud_tests

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def _zai_creds():
    """Returns creds or None (offline mode)."""
    return load_llm_keys()


def test_secrets_loader_returns_none_gracefully(tmp_path):
    """Secrets loader returns None when no file and no env var."""
    # tmp_path has no .secrets/ subdir
    result = load_llm_keys(repo_root=tmp_path)
    assert result is None


def test_mock_gateway_offline_mode():
    """Offline: mock gateway returns deterministic response."""
    MOCK_SITE_CODE = "# mock FastAPI app\n"
    gw, mode = build_gateway(None, mock_response=MOCK_SITE_CODE)
    assert mode == "mock"
    resp = gw.call_model("any-profile", [{"role": "user", "content": "build a todo site"}])
    assert resp.content == MOCK_SITE_CODE


def test_site_crud_no_app(tmp_path):
    """Site helper returns app_importable=False gracefully for empty workspace."""
    results = run_site_crud_tests(tmp_path)
    assert results.get("app_importable") is False


@pytest.mark.skipif(
    load_llm_keys() is None,
    reason="no .secrets/llm_keys.env -- live zai scenario skipped",
)
def test_todo_website_live_scenario(_zai_creds):
    """TODO: full live greenfield scenario (alpha.3 skeleton only)."""
    pytest.skip("TODO: implement live scenario (alpha.3 skeleton only)")
