"""Red-team regression tests for the Ansible layer.

Three confirmed findings, fixed and pinned here:

  CRITICAL  SSTI -> RCE.  POST /admin/ansible/render fed an attacker template
            body straight into AnsibleTemplater.render -> Templar.template(),
            exposing the full lookup/plugin surface
            ({{ lookup('pipe','...') }} == shell).  FIX: untrusted bodies render
            in a jinja2 SandboxedEnvironment(StrictUndefined) with globals
            cleared and every extra-var value wrapped Ansible-unsafe; fails
            CLOSED (HTTP 400, no traceback).  The trusted full-Templar path
            stays as a documented in-process-only API.

  HIGH      Unwrapped extravars.  Untrusted extra-vars (e.g. the worker's
            model_response) reached the executor un-marked, so embedded Jinja in
            model output could be re-templated into a shell/command/template
            task.  FIX: core_runner.run_playbook wraps ALL extravars
            Ansible-unsafe (via ansible/unsafe.py) before the executor.

  HIGH      No playbook timeout + ignored process_isolation.  pb_exec.run() had
            no wall-clock bound and process_isolation was stored-and-ignored.
            FIX: execution is bounded in a killable fork child (rc 124 on
            expiry, GLUDD_PLAYBOOK_TIMEOUT default 300s); isolation is honored
            or the run fails closed when requested-but-unsupported.
"""

from __future__ import annotations

import os
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# --------------------------------------------------------------------------- #
# CRITICAL — SSTI -> RCE in POST /admin/ansible/render                          #
# --------------------------------------------------------------------------- #


def _render_client() -> Any:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from general_ludd.routers.ansible import register

    app = FastAPI()
    register(app, {})
    return TestClient(app)


class TestRenderSandboxNoCodeExecution:
    def test_lookup_pipe_does_not_execute(self):
        """{{ lookup('pipe', 'echo PWNED') }} must NOT run a shell.

        In the sandbox there is no `lookup` global at all, so the name is
        undefined and the render fails closed with HTTP 400 — the marker string
        never appears.
        """
        client = _render_client()
        resp = client.post(
            "/admin/ansible/render",
            json={"template": "{{ lookup('pipe', 'echo PWNED') }}"},
        )
        assert resp.status_code == 400
        assert "PWNED" not in resp.text

    def test_dunder_escape_is_blocked(self):
        """A classic sandbox-escape via dunder attribute access is rejected."""
        client = _render_client()
        payload = "{{ ''.__class__.__mro__[1].__subclasses__() }}"
        resp = client.post("/admin/ansible/render", json={"template": payload})
        assert resp.status_code == 400
        # No object-graph leakage in the body.
        assert "subclasses" not in resp.text.lower() or "rejected" in resp.text.lower()

    def test_payload_via_variable_value_renders_literally(self):
        """A Jinja payload smuggled through an extra-var VALUE is emitted literally.

        Because every value is wrapped Ansible-unsafe and the sandbox does not
        re-evaluate it, `{{ x }}` where x == "{{ 7*7 }}" must render the literal
        string "{{ 7*7 }}", never "49".
        """
        client = _render_client()
        resp = client.post(
            "/admin/ansible/render",
            json={
                "template": "value is {{ x }}",
                "extra_vars": {"x": "{{ 7*7 }}"},
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["rendered"] == "value is {{ 7*7 }}"
        assert "49" not in body["rendered"]

    def test_lookup_payload_via_variable_value_renders_literally(self):
        """A lookup payload in a VALUE is inert (literal), not executed.

        The payload string is emitted verbatim — the marker appears only as
        literal template text, never as the OUTPUT of an executed command.
        """
        client = _render_client()
        resp = client.post(
            "/admin/ansible/render",
            json={
                "template": "{{ note }}",
                "extra_vars": {"note": "{{ lookup('pipe','echo PWNED') }}"},
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        # Rendered literally and unchanged — the lookup was never evaluated, so
        # no command ran and the output is exactly the inert source text.
        assert body["rendered"] == "{{ lookup('pipe','echo PWNED') }}"

    def test_plain_variable_substitution_still_works(self):
        """The fix must not break legitimate, simple variable substitution."""
        client = _render_client()
        resp = client.post(
            "/admin/ansible/render",
            json={"template": "Hello {{ greeting }}!", "extra_vars": {"greeting": "world"}},
        )
        assert resp.status_code == 200
        assert resp.json()["rendered"] == "Hello world!"

    def test_undefined_variable_fails_closed(self):
        """StrictUndefined: an unknown name is a 400, not a silent empty string."""
        client = _render_client()
        resp = client.post("/admin/ansible/render", json={"template": "{{ missing }}"})
        assert resp.status_code == 400

    def test_no_traceback_leaked_on_rejection(self):
        """Fail-closed must not leak a Jinja/Python traceback in the response."""
        client = _render_client()
        resp = client.post(
            "/admin/ansible/render",
            json={"template": "{{ ().__class__.__bases__ }}"},
        )
        assert resp.status_code == 400
        lowered = resp.text.lower()
        assert "traceback" not in lowered
        assert "file \"" not in lowered

    def test_range_global_unavailable(self):
        """The global namespace is cleared — `range` and friends are gone."""
        client = _render_client()
        resp = client.post(
            "/admin/ansible/render",
            json={"template": "{{ range(3) | list }}"},
        )
        assert resp.status_code == 400


class TestRenderEndpointRequiresPsk:
    """The render route must require the daemon PSK (not in _PUBLIC_PATHS)."""

    def test_render_path_not_in_public_allowlist(self):
        env = dict(os.environ)
        env["GLUDD_AUTH_PSK"] = "redteam-render-psk"
        with patch.dict(os.environ, env, clear=True):
            from general_ludd.daemon import create_daemon_app

            app = create_daemon_app(tick_interval=0.01)
            from fastapi.testclient import TestClient

            with TestClient(app) as client:
                # No Authorization header -> the auth middleware must 401 BEFORE
                # the handler runs (so the body is never rendered).
                resp = client.post(
                    "/admin/ansible/render",
                    json={"template": "{{ 7*7 }}"},
                )
        assert resp.status_code == 401

    def test_render_path_authorized_with_psk(self):
        env = dict(os.environ)
        env["GLUDD_AUTH_PSK"] = "redteam-render-psk"
        with patch.dict(os.environ, env, clear=True):
            from general_ludd.daemon import create_daemon_app

            app = create_daemon_app(tick_interval=0.01)
            from fastapi.testclient import TestClient

            with TestClient(app) as client:
                resp = client.post(
                    "/admin/ansible/render",
                    json={"template": "hi {{ name }}", "extra_vars": {"name": "ok"}},
                    headers={"Authorization": "Bearer redteam-render-psk"},
                )
        assert resp.status_code == 200
        assert resp.json()["rendered"] == "hi ok"


class TestTemplaterTrustedPathDocumented:
    """The full-Templar render() remains available as the trusted-only path."""

    def test_render_sandboxed_method_exists(self):
        from general_ludd.ansible.templating import AnsibleTemplater

        assert hasattr(AnsibleTemplater, "render_sandboxed")
        assert hasattr(AnsibleTemplater, "render")

    def test_sandboxed_render_raises_template_render_error_on_abuse(self):
        from general_ludd.ansible.templating import AnsibleTemplater, TemplateRenderError

        t = AnsibleTemplater()
        with pytest.raises(TemplateRenderError):
            t.render_sandboxed("{{ lookup('pipe', 'echo PWNED') }}")


# --------------------------------------------------------------------------- #
# HIGH — unwrapped extravars (central Ansible-unsafe wrap)                      #
# --------------------------------------------------------------------------- #


def _assert_not_trusted_template(value: Any) -> None:
    """Assert a wrapped value is not trusted for templating (version-spanning).

    ansible-core >= 2.19: "unsafe" == lacking the TrustedAsTemplate tag.
    Older cores: wrap_var returns an AnsibleUnsafe* subclass.
    """
    try:
        from ansible._internal._datatag._tags import TrustedAsTemplate
    except Exception:
        # Legacy (<2.19) model: must be an AnsibleUnsafe instance.
        from ansible.utils.unsafe_proxy import AnsibleUnsafe

        assert isinstance(value, AnsibleUnsafe)
        return
    assert not TrustedAsTemplate.is_tagged_on(value), (
        "extra-var value must NOT be trusted-as-template (i.e. must be unsafe)"
    )


class TestExtravarsWrappedUnsafe:
    def test_wrap_unsafe_recurses_containers(self):
        from general_ludd.ansible.unsafe import wrap_unsafe

        out = wrap_unsafe({"a": "{{ x }}", "b": ["{{ y }}", 1], "c": 2})
        assert out["a"] == "{{ x }}"
        assert out["b"][0] == "{{ y }}"
        assert out["c"] == 2  # non-string scalar untouched

    def test_wrap_extravars_none_passthrough(self):
        from general_ludd.ansible.unsafe import wrap_extravars

        assert wrap_extravars(None) is None

    def test_run_playbook_wraps_extravars_before_executor(self):
        """core_runner.run_playbook must wrap extravars unsafe before executing.

        We stub the executor and assert the extravars it receives are the
        wrapped form — proving the wrap happens centrally, before any task runs.
        """
        from general_ludd.ansible import core_runner as cr
        from general_ludd.ansible.core_runner import AnsibleResult, CoreAnsibleRunner

        runner = CoreAnsibleRunner()
        captured: dict[str, Any] = {}

        def _fake_exec(**kwargs: Any) -> AnsibleResult:
            captured.update(kwargs)
            return AnsibleResult(status="successful", rc=0)

        evil = {"model_response": "{{ lookup('pipe','echo PWNED') }}"}
        with (
            patch.object(cr, "_HAS_ANSIBLE_CORE", True),
            patch.object(runner, "_execute_with_core", side_effect=_fake_exec),
        ):
            # Explicit timeout=0 -> inline run (no fork child) so the captured
            # kwargs are observable in this process.
            result = runner.run_playbook("/tmp/x.yml", extravars=evil, timeout=0)

        assert result.status == "successful"
        passed = captured["extravars"]["model_response"]
        # The literal text is preserved...
        assert str(passed) == "{{ lookup('pipe','echo PWNED') }}"
        # ...and it is marked Ansible-unsafe. On ansible-core >= 2.19 "unsafe"
        # means NOT carrying the TrustedAsTemplate tag (wrap_var untags), so the
        # robust, version-spanning check is "this value is not trusted for
        # templating". On the legacy model wrap_var returns an AnsibleUnsafe*
        # subclass; either way the templating engine refuses to evaluate it,
        # which the end-to-end Templar test below asserts directly.
        from general_ludd.ansible.unsafe import has_wrap_var

        if has_wrap_var():
            _assert_not_trusted_template(passed)

    def test_unsafe_value_is_not_re_templated_by_templar(self):
        """End-to-end: an unsafe-wrapped value with Jinja is NOT re-evaluated.

        Feed an unsafe-wrapped {{ 7*7 }} as a variable to ansible's own Templar.
        Even when the SURROUNDING template ``{{ resp }}`` is trusted (the worst
        case — a real task body the operator authored), the unsafe VALUE is
        emitted literally instead of being re-evaluated. This is the property
        that stops a model response from being executed as a shell when a
        shell/command/template task re-templates it.

        Skips gracefully on a non-2.19+ ansible (no trust_as_template); the
        wrap-based defence still holds there via the legacy trust-by-default
        model, covered by test_wrap_unsafe_recurses_containers + the router
        end-to-end tests.
        """
        pytest.importorskip("ansible")
        from ansible.parsing.dataloader import DataLoader
        from ansible.template import Templar

        from general_ludd.ansible.unsafe import wrap_unsafe

        try:
            from ansible.template import trust_as_template
        except ImportError:  # pragma: no cover - pre-2.19 ansible
            pytest.skip("trust_as_template unavailable (<2.19 trust model)")

        payload = wrap_unsafe("{{ 7*7 }}")
        templar = Templar(loader=DataLoader(), variables={"resp": payload})
        # Trust ONLY the outer template, not the value.
        rendered = templar.template(trust_as_template("{{ resp }}"))
        assert str(rendered) == "{{ 7*7 }}"
        assert "49" not in str(rendered)


# --------------------------------------------------------------------------- #
# HIGH — playbook wall-clock timeout + process_isolation honored / fail-closed  #
# --------------------------------------------------------------------------- #


def _sleep_forever_exec(**kwargs: Any) -> Any:
    """Module-level stub for _execute_with_core that sleeps past any bound.

    Defined at module scope so a fork child inherits it cleanly (a MagicMock
    would not survive the fork as a callable target).
    """
    import time as _t

    from general_ludd.ansible.core_runner import AnsibleResult

    _t.sleep(120)
    return AnsibleResult(status="successful", rc=0)


class TestPlaybookTimeout:
    def test_sleeping_playbook_killed_and_returns_failed(self, tmp_path):
        """A sleeping playbook is killed and returns failed (rc 124) within bound.

        Uses a real ansible playbook whose task sleeps for 60s; the 3s wall-clock
        bound must terminate the fork child and return rc 124 long before that.
        """
        pytest.importorskip("ansible")
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        pb = tmp_path / "sleep.yml"
        pb.write_text(
            "---\n"
            "- name: sleeper\n"
            "  hosts: localhost\n"
            "  connection: local\n"
            "  gather_facts: false\n"
            "  tasks:\n"
            "    - name: sleep long\n"
            "      ansible.builtin.shell: sleep 60\n"
        )

        runner = CoreAnsibleRunner()
        start = time.monotonic()
        result = runner.run_playbook(str(pb), timeout=3.0)
        elapsed = time.monotonic() - start

        # The 60s task cannot have completed within the 3s bound: a 'successful'
        # result here would mean the timeout never fired.
        assert elapsed < 30.0, f"timeout did not bound the run (took {elapsed:.1f}s)"
        assert result.status == "failed", (
            f"expected killed/failed within bound, got {result!r} in {elapsed:.1f}s"
        )
        assert result.rc == 124
        assert result.error is not None and "timed out" in result.error

    def test_timeout_kill_path_returns_124(self):
        """Unit-level proof of the kill path independent of ansible internals.

        A stubbed _execute_with_core that sleeps past the bound must be killed
        and yield rc 124 within the deadline (no dependence on module/collection
        resolution inside the fork child).
        """
        from general_ludd.ansible import core_runner as cr
        from general_ludd.ansible.core_runner import CoreAnsibleRunner

        runner = CoreAnsibleRunner()
        with patch.object(cr, "_HAS_ANSIBLE_CORE", True), patch.object(
            runner, "_execute_with_core", _sleep_forever_exec
        ):
            start = time.monotonic()
            result = runner.run_playbook("/tmp/x.yml", timeout=2.0)
            elapsed = time.monotonic() - start

        assert result.status == "failed"
        assert result.rc == 124
        assert elapsed < 15.0, f"kill path did not bound the run (took {elapsed:.1f}s)"

    def test_env_default_timeout_resolution(self):
        from general_ludd.ansible.core_runner import _DEFAULT_PLAYBOOK_TIMEOUT, _env_default_timeout

        with patch.dict(os.environ, {"GLUDD_PLAYBOOK_TIMEOUT": "45"}, clear=False):
            assert _env_default_timeout() == 45.0
        with patch.dict(os.environ, {"GLUDD_PLAYBOOK_TIMEOUT": "garbage"}, clear=False):
            assert _env_default_timeout() == _DEFAULT_PLAYBOOK_TIMEOUT
        with patch.dict(os.environ, {"GLUDD_PLAYBOOK_TIMEOUT": "-5"}, clear=False):
            assert _env_default_timeout() == _DEFAULT_PLAYBOOK_TIMEOUT

    def test_adapter_passes_finite_timeout_to_core_runner(self):
        """The network-exposed adapter must forward a timeout to the core runner."""
        import tempfile
        from unittest.mock import MagicMock

        with patch("general_ludd.ansible.runner.CoreAnsibleRunner") as mock_cls:
            mock_core = MagicMock()
            mock_result = MagicMock()
            mock_result.model_dump.return_value = {"status": "successful", "rc": 0, "events": []}
            mock_core.run_playbook.return_value = mock_result
            mock_cls.return_value = mock_core

            from general_ludd.ansible.runner import AnsibleRunnerAdapter

            with tempfile.TemporaryDirectory() as tmp:
                adapter = AnsibleRunnerAdapter(private_data_dir=tmp)
                adapter.run_playbook(playbook_name="noop.yml", private_data_dir=tmp, timeout=120.0)

        _, kwargs = mock_core.run_playbook.call_args
        assert "timeout" in kwargs
        assert kwargs["timeout"] == 120.0


class TestProcessIsolationRouting:
    """Finding #1 real fix: isolation enabled → ansible-runner subprocess backend.

    The Wave 13 fail-closed guard (refuse to run unconfined) is superseded by
    actual confinement: when process_isolation.enabled=True, CoreAnsibleRunner
    delegates to ansible-runner.run() which spawns podman/bwrap. These tests
    pin that routing and the disabled-path fallback.
    """

    def test_unsupported_isolation_fails_closed_when_runner_missing(self):
        """Isolation requested but ansible-runner package absent → failed."""
        from general_ludd.ansible import core_runner as cr
        from general_ludd.ansible.core_runner import CoreAnsibleRunner
        from general_ludd.ansible.isolation import ProcessIsolationConfig

        iso = ProcessIsolationConfig(
            enabled=True,
            executable="definitely-not-a-real-binary-xyz",
            container_image="registry.example/gludd-ee:test@sha256:" + "a" * 64,
        )
        runner = CoreAnsibleRunner(process_isolation=iso)
        with (
            patch.object(cr, "_HAS_ANSIBLE_CORE", True),
            patch.object(cr, "_HAS_ANSIBLE_RUNNER", False),
        ):
            result = runner.run_playbook("/tmp/whatever.yml")
        assert result.status == "failed"
        assert result.error is not None
        assert "isolation" in result.error.lower()

    def test_enabled_isolation_invokes_ansible_runner(self):
        """enabled=True delegates to ansible_runner.run with process_isolation=True."""
        from general_ludd.ansible.core_runner import CoreAnsibleRunner
        from general_ludd.ansible.isolation import ProcessIsolationConfig

        iso = ProcessIsolationConfig(
            enabled=True,
            container_image="registry.example/gludd-ee:test@sha256:" + "a" * 64,
            executable="podman",
            hide_paths=["/secret"],
        )
        runner = CoreAnsibleRunner(process_isolation=iso)

        fake_runner_obj = MagicMock()
        fake_runner_obj.rc = 0
        fake_runner_obj.status = "successful"
        fake_runner_obj.stats = {"ok": 1}
        fake_runner_obj.events = []

        with (
            patch("general_ludd.ansible.core_runner._HAS_ANSIBLE_CORE", True),
            patch("general_ludd.ansible.core_runner._HAS_ANSIBLE_RUNNER", True),
            patch("general_ludd.ansible.core_runner.ansible_runner") as mock_ar,
        ):
            mock_ar.run.return_value = fake_runner_obj
            result = runner.run_playbook("/tmp/whatever.yml", extravars={"k": "v"})

        mock_ar.run.assert_called_once()
        _, kwargs = mock_ar.run.call_args
        assert kwargs["process_isolation"] is True
        assert kwargs["process_isolation_executable"] == "podman"
        assert "/secret" in kwargs["process_isolation_hide_paths"]
        assert kwargs["playbook"] == "/tmp/whatever.yml"
        assert result.status == "successful"
        assert result.rc == 0

    def test_disabled_isolation_uses_playbook_executor(self):
        """enabled=False keeps the in-process PlaybookExecutor path (not ansible-runner)."""
        from general_ludd.ansible import core_runner as cr
        from general_ludd.ansible.core_runner import AnsibleResult, CoreAnsibleRunner
        from general_ludd.ansible.isolation import ProcessIsolationConfig

        iso = ProcessIsolationConfig(enabled=False, executable="podman")
        runner = CoreAnsibleRunner(process_isolation=iso)
        with (
            patch.object(cr, "_HAS_ANSIBLE_CORE", True),
            patch.object(
                runner, "_execute_with_core",
                return_value=AnsibleResult(status="successful", rc=0),
            ),
            # This unit test pins routing, not the process timeout boundary.
            # Keep the optional env timeout unset so the patched in-process
            # executor remains the seam under spawn/forkserver semantics.
            patch.dict(os.environ, {"GLUDD_PLAYBOOK_TIMEOUT": ""}, clear=False),
            patch("general_ludd.ansible.core_runner.ansible_runner") as mock_ar,
        ):
            result = runner.run_playbook("/tmp/whatever.yml")
        mock_ar.run.assert_not_called()
        assert result.status == "successful"

    def test_ansible_runner_failure_propagates(self):
        """runner.run returns failure → AnsibleResult.status='failed'."""
        from general_ludd.ansible.core_runner import CoreAnsibleRunner
        from general_ludd.ansible.isolation import ProcessIsolationConfig

        iso = ProcessIsolationConfig(
            enabled=True,
            executable="podman",
            container_image="registry.example/gludd-ee:test@sha256:" + "a" * 64,
        )
        runner = CoreAnsibleRunner(process_isolation=iso)

        fake_runner_obj = MagicMock()
        fake_runner_obj.rc = 2
        fake_runner_obj.status = "failed"
        fake_runner_obj.stats = {"failures": 1}
        fake_runner_obj.events = []

        with (
            patch("general_ludd.ansible.core_runner._HAS_ANSIBLE_CORE", True),
            patch("general_ludd.ansible.core_runner._HAS_ANSIBLE_RUNNER", True),
            patch("general_ludd.ansible.core_runner.ansible_runner") as mock_ar,
        ):
            mock_ar.run.return_value = fake_runner_obj
            result = runner.run_playbook("/tmp/whatever.yml")

        assert result.status == "failed"
        assert result.rc == 2
        assert result.error is not None
        assert "failed" in result.error

    def test_disabled_isolation_does_not_fail_closed(self):
        """Isolation disabled must NOT trip the isolation routing."""
        from general_ludd.ansible import core_runner as cr
        from general_ludd.ansible.core_runner import AnsibleResult, CoreAnsibleRunner
        from general_ludd.ansible.isolation import ProcessIsolationConfig

        iso = ProcessIsolationConfig(enabled=False, executable="podman")
        runner = CoreAnsibleRunner(process_isolation=iso)
        with (
            patch.object(cr, "_HAS_ANSIBLE_CORE", True),
            patch.object(
                runner, "_execute_with_core",
                return_value=AnsibleResult(status="successful", rc=0),
            ),
            # This unit test pins routing, not the process timeout boundary.
            # Keep the optional env timeout unset so the patched in-process
            # executor remains the seam under spawn/forkserver semantics.
            patch.dict(os.environ, {"GLUDD_PLAYBOOK_TIMEOUT": ""}, clear=False),
        ):
            result = runner.run_playbook("/tmp/whatever.yml")
        assert result.status == "successful"
