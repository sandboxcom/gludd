"""Structural pin for the daemon-lifecycle molecule scenario.

Asserts the scenario ships the required files (molecule.yml, converge.yml,
verify.yml) and that each playbook encodes the lifecycle phases mandated by
the scenario spec:

  start -> health probe -> /api/facts -> /api/tasks -> /api/playbook/run ->
  /health -> SIGTERM -> clean exit -> port released.

If a future refactor drops a file or renames a phase, this test fails before
CI's ``make molecule-test-all`` runs an unverified or structurally incomplete
scenario. Mirrors the pattern in ``test_ci_regression_guards.py``'s
``test_every_molecule_scenario_is_structurally_complete`` plus the targeted
content assertions used in ``test_build_presentation_role.py``'s
``test_molecule_scenario_exists``.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_SCENARIO_ROOT = (
    Path(__file__).resolve().parents[2]
    / "molecule" / "playbooks" / "daemon_lifecycle"
)
_REQUIRED_FILES = (
    "molecule.yml",
    "default/converge.yml",
    "default/verify.yml",
)


def _scenario_files_present() -> dict[str, bool]:
    return {rel: (_SCENARIO_ROOT / rel).is_file() for rel in _REQUIRED_FILES}


class TestDaemonLifecycleScenarioFiles:
    """The scenario directory ships every required playbook file."""

    def test_scenario_directory_exists(self) -> None:
        assert _SCENARIO_ROOT.is_dir(), (
            f"daemon_lifecycle scenario missing: expected {_SCENARIO_ROOT} "
            "to exist. Create the directory and its playbooks."
        )

    def test_required_files_present(self) -> None:
        presence = _scenario_files_present()
        missing = [rel for rel, ok in presence.items() if not ok]
        assert not missing, (
            "daemon_lifecycle scenario is missing required files: "
            f"{missing}. Every molecule scenario must ship "
            "molecule.yml + default/converge.yml + default/verify.yml."
        )

    def test_molecule_yml_uses_default_driver(self) -> None:
        path = _SCENARIO_ROOT / "molecule.yml"
        data = yaml.safe_load(path.read_text()) or {}
        assert data.get("driver", {}).get("name") == "default", (
            "daemon_lifecycle molecule.yml must declare driver.name=default "
            "to run the real `./gludd daemon` binary on localhost."
        )

    def test_molecule_yml_registers_converge_and_verify(self) -> None:
        path = _SCENARIO_ROOT / "molecule.yml"
        data = yaml.safe_load(path.read_text()) or {}
        playbooks = data.get("provisioner", {}).get("playbooks", {}) or {}
        assert playbooks.get("converge") == "default/converge.yml", (
            "daemon_lifecycle molecule.yml must wire converge: default/converge.yml"
        )
        assert playbooks.get("verify") == "default/verify.yml", (
            "daemon_lifecycle molecule.yml must wire verify: default/verify.yml"
        )


class TestDaemonLifecycleConvergePhases:
    """converge.yml encodes every mandated lifecycle phase."""

    @classmethod
    def _converge_text(cls) -> str:
        return (_SCENARIO_ROOT / "default" / "converge.yml").read_text()

    def test_converge_starts_real_daemon_binary(self) -> None:
        text = self._converge_text()
        assert "general_ludd.cli daemon" in text or "gludd daemon" in text, (
            "converge.yml must start the REAL daemon via `general_ludd.cli daemon` "
            "or `gludd daemon` — the lifecycle scenario exists to exercise the "
            "actual binary, not a mock."
        )

    def test_converge_polls_health_endpoint(self) -> None:
        text = self._converge_text()
        assert "/healthz" in text, (
            "converge.yml must poll /healthz — health-readiness is the startup gate."
        )
        assert "retries: 30" in text, (
            "converge.yml must poll /healthz with retries: 30 — the 30-second "
            "startup budget is mandated by the scenario spec."
        )

    def test_converge_calls_api_facts(self) -> None:
        text = self._converge_text()
        assert "/api/facts" in text, (
            "converge.yml must GET /api/facts — verifies the facts aggregator "
            "responds after startup."
        )

    def test_converge_calls_api_tasks(self) -> None:
        text = self._converge_text()
        assert "/api/tasks" in text, (
            "converge.yml must GET /api/tasks — verifies the tasks facet endpoint. "
            "(If unimplemented, this scenario is the spec that drives the contract.)"
        )

    def test_converge_posts_playbook_run(self) -> None:
        text = self._converge_text()
        assert "/api/playbook/run" in text, (
            "converge.yml must POST /api/playbook/run — verifies the daemon "
            "accepts playbook-run requests."
        )

    def test_converge_calls_health_endpoint(self) -> None:
        text = self._converge_text()
        assert "/health" in text, (
            "converge.yml must GET /health — distinct from /healthz; this is "
            "the public status probe."
        )

    def test_converge_sends_sigterm(self) -> None:
        text = self._converge_text()
        assert "SIGTERM" in text or "kill -TERM" in text, (
            "converge.yml must send SIGTERM to the daemon — graceful shutdown "
            "is the lifecycle phase under test."
        )

    def test_converge_verifies_clean_exit(self) -> None:
        text = self._converge_text()
        assert "exit" in text.lower(), (
            "converge.yml must verify the daemon exits cleanly after SIGTERM."
        )

    def test_converge_verifies_port_released(self) -> None:
        text = self._converge_text()
        assert "nc -z" in text or "port" in text.lower(), (
            "converge.yml must verify the daemon port is released after shutdown."
        )


class TestDaemonLifecycleVerifyAssertions:
    """verify.yml encodes every mandated assertion with its exact error message."""

    @classmethod
    def _verify_text(cls) -> str:
        return (_SCENARIO_ROOT / "default" / "verify.yml").read_text()

    def test_verify_asserts_startup_within_30s(self) -> None:
        text = self._verify_text()
        assert "Daemon must start within 30 seconds" in text, (
            "verify.yml must assert 'Daemon must start within 30 seconds' — "
            "exact mandated error message."
        )

    def test_verify_asserts_health_endpoint_200(self) -> None:
        text = self._verify_text()
        assert "Health endpoint must return 200" in text, (
            "verify.yml must assert 'Health endpoint must return 200' — "
            "exact mandated error message."
        )

    def test_verify_asserts_api_facts_list(self) -> None:
        text = self._verify_text()
        assert "/api/facts must return" in text, (
            "verify.yml must assert /api/facts returns a JSON list/object — "
            "exact mandated error message."
        )

    def test_verify_asserts_playbook_run_accepted(self) -> None:
        text = self._verify_text()
        assert "Daemon must accept playbook run requests" in text, (
            "verify.yml must assert 'Daemon must accept playbook run requests' — "
            "exact mandated error message."
        )

    def test_verify_asserts_clean_shutdown(self) -> None:
        text = self._verify_text()
        assert "Daemon must shut down cleanly on SIGTERM" in text, (
            "verify.yml must assert 'Daemon must shut down cleanly on SIGTERM' — "
            "exact mandated error message."
        )

    def test_verify_asserts_port_free_after_shutdown(self) -> None:
        text = self._verify_text()
        assert "must be free after shutdown" in text, (
            "verify.yml must assert 'Port 8000 must be free after shutdown' — "
            "exact mandated error message."
        )


class TestDaemonLifecycleSideEffectChecks:
    """verify.yml encodes the four mandated side-effect hygiene checks."""

    @classmethod
    def _verify_text(cls) -> str:
        return (_SCENARIO_ROOT / "default" / "verify.yml").read_text()

    def test_verify_checks_no_zombie_processes(self) -> None:
        text = self._verify_text()
        assert "zombie" in text.lower(), (
            "verify.yml must check for zombie processes after shutdown — "
            "side-effect hygiene requirement."
        )

    def test_verify_checks_no_leftover_sockets(self) -> None:
        text = self._verify_text()
        assert "socket" in text.lower(), (
            "verify.yml must check for leftover socket files — "
            "side-effect hygiene requirement."
        )

    def test_verify_checks_no_tracebacks_in_log(self) -> None:
        text = self._verify_text()
        assert "Traceback" in text, (
            "verify.yml must scan daemon log for tracebacks — "
            "side-effect hygiene requirement."
        )

    def test_verify_checks_pidfile_cleanup(self) -> None:
        text = self._verify_text()
        assert "PID file" in text or "pid file" in text.lower(), (
            "verify.yml must verify the PID file is cleaned up after shutdown — "
            "side-effect hygiene requirement."
        )
