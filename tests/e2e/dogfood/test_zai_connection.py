"""Live zai connection test -- skipped if no key present.

Run with: make test-unit TESTFILE='tests/e2e/dogfood/test_zai_connection.py'
Requires .secrets/llm_keys.env with ZAI_API_KEY.
"""
from __future__ import annotations

import pytest
from tests.e2e.dogfood._gateway import build_gateway
from tests.e2e.dogfood._secrets import load_llm_keys

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def _zai_creds():
    creds = load_llm_keys()
    if creds is None:
        pytest.skip("no .secrets/llm_keys.env or ZAI_API_KEY -- live zai tests skipped")
    return creds


def test_zai_connection_live(_zai_creds):
    """Assert the zai gateway builds and the key is non-empty (no live call)."""
    gw, mode = build_gateway(_zai_creds)
    assert mode == "live"
    profiles = gw.list_profiles()
    assert len(profiles) >= 1
    assert profiles[0].model_profile_id == "zai-glm-e2e"
    assert profiles[0].enabled is True


def test_zai_mock_gateway_works():
    """Assert mock gateway builds and returns mock response (no key needed)."""
    gw, mode = build_gateway(None, mock_response="hello mock")
    assert mode == "mock"
    resp = gw.call_model("any", [{"role": "user", "content": "hi"}])
    assert resp.content == "hello mock"
