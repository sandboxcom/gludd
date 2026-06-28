"""Regression test: _accounting_facet must NOT leak internal exception detail.

The /api/facts accounting facet fails soft (returns an "error" key instead of
raising) so a degraded accounting subsystem does not take down the whole facts
endpoint. Previously the except path returned ``str(exc)`` in the response body,
which leaks internal paths / DB DSNs / type names to any caller. The fix returns
a generic, static message while logging the real exception (with traceback) for
operators. This test pins the no-leak contract.
"""

from __future__ import annotations

import pytest

from general_ludd.routers import facts


class _BoomAccountant:
    """Stand-in whose build raises with a detail-bearing message."""


@pytest.mark.asyncio
async def test_accounting_facet_does_not_leak_exception_detail(monkeypatch):
    secret_detail = "postgresql://admin:s3cr3t@db.internal:5432/ledger — connection refused"

    async def _boom(_app):
        raise RuntimeError(secret_detail)

    # Force the except branch by making the accountant build blow up.
    monkeypatch.setattr(facts, "_build_accounting_accountant", _boom)

    result = await facts._accounting_facet(app=object(), project_id=None)

    # Degraded shape preserved so callers can still detect the failure.
    assert result["projects"] == []
    assert "error" in result
    # The generic message is returned...
    assert result["error"] == "accounting facet unavailable"
    # ...and NONE of the internal exception detail leaks into the body.
    assert secret_detail not in result["error"]
    assert "s3cr3t" not in result["error"]
    assert "postgresql" not in result["error"]


@pytest.mark.asyncio
async def test_accounting_facet_single_project_path_also_no_leak(monkeypatch):
    secret_detail = "/var/lib/gludd/private/keyring.db is locked"

    async def _boom(_app):
        raise RuntimeError(secret_detail)

    monkeypatch.setattr(facts, "_build_accounting_accountant", _boom)

    result = await facts._accounting_facet(app=object(), project_id="proj-1")

    assert result["projects"] == []
    assert result["error"] == "accounting facet unavailable"
    assert secret_detail not in result["error"]
