"""Structural tests for event_loop/lease.py — lease acquisition functions."""

from __future__ import annotations

from general_ludd.event_loop.lease import acquire_lease, release_lease


class TestAcquireLease:
    def test_function_exists_and_callable(self):
        assert callable(acquire_lease)

    def test_signature_has_required_params(self):
        import inspect

        sig = inspect.signature(acquire_lease)
        params = list(sig.parameters.keys())
        assert "session" in params
        assert "bucket_key" in params
        assert "holder_id" in params

    def test_optional_params(self):
        import inspect

        sig = inspect.signature(acquire_lease)
        params = sig.parameters
        assert params["ttl_seconds"].default == 300
        assert params["project_id"].default is None


class TestReleaseLease:
    def test_function_exists_and_callable(self):
        assert callable(release_lease)

    def test_signature_has_required_params(self):
        import inspect

        sig = inspect.signature(release_lease)
        params = list(sig.parameters.keys())
        assert "session" in params
        assert "bucket_key" in params

    def test_holder_id_optional(self):
        import inspect

        sig = inspect.signature(release_lease)
        params = sig.parameters
        assert params["holder_id"].default is None
