"""Regression contract for CI run 32437385366 daemon ownership failures."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
PLAYBOOKS = ROOT / "molecule" / "playbooks"
SHARED = ROOT / "molecule" / "shared"
MOCK_SERVER = ROOT / "molecule" / "mock_daemon" / "server.py"

# Every entry reached /healthz in prepare and then failed its first daemon call
# in converge.  Keep the inventory exact to the 2026-08-21 GHE failure wave.
DAEMON_LIFECYCLE_SCENARIOS = (
    "ornith_self_improve",
    "role_agent_task",
    "role_implement_change",
    "role_refactor_code",
    "role_self_improve_ab_test",
    "role_self_improve_ab_test_benign",
    "role_self_improve_promote",
    "role_self_improve_propose",
    "test_gludd_abtest",
    "test_gludd_git",
    "test_gludd_make",
    "test_gludd_observe",
    "test_gludd_reload",
    "test_gludd_skill",
    "test_gludd_worktree",
)

LANGUAGE_DAEMON_ROLES = {
    "bom_detect",
    "encoding_detect",
    "homoglyph_scan",
    "language_detect",
    "locale_format",
    "phonetic_transcribe",
    "translate",
    "transliterate",
    "unicode_analyze",
}


def _config(scenario: str) -> dict[str, object]:
    loaded = yaml.safe_load((PLAYBOOKS / scenario / "molecule.yml").read_text())
    assert isinstance(loaded, dict)
    return loaded


def test_mock_server_publishes_bound_ephemeral_endpoint_atomically() -> None:
    source = MOCK_SERVER.read_text()

    assert 'parser.add_argument("--ready-file"' in source
    assert 'parser.add_argument("--instance-id"' in source
    assert '"--lease-seconds"' in source
    assert "server.server_address" in source
    assert "os.replace" in source
    assert '"base_url"' in source
    assert '"instance_id"' in source
    assert "threading.Timer" in source


def test_common_session_is_bounded_owned_and_has_no_poll_loops() -> None:
    create = (SHARED / "create.yml").read_text()
    start = (SHARED / "mock_daemon_start.yml").read_text()
    stop = (SHARED / "mock_daemon_stop.yml").read_text()

    assert "GLUDD_MOCK_PORT" not in create
    assert "MOLECULE_SCENARIO_NAME" in create
    assert "MOLECULE_EPHEMERAL_DIRECTORY" in start
    assert "--port\n" in start and "- \"0\"" in start
    assert "--ready-file" in start
    assert "--instance-id" in start
    assert "--lease-seconds" in start
    assert "ansible.builtin.wait_for" in start
    assert "ansible.builtin.uri" in start
    assert "retries:" not in start
    assert "delay:" not in start
    assert "sleep " not in start
    assert "ansible.builtin.shell" not in start

    assert "expected_server_path" in stop
    assert "--ready-file" in stop
    assert "_gludd_mock_owned" in stop
    assert "ansible.builtin.wait_for" in stop
    assert "ansible.builtin.async_status" in stop
    assert "failed_when: false" not in stop
    assert "ansible.builtin.shell" not in stop


def test_failed_daemon_scenarios_own_converge_lifetime_and_molecule_cleanup() -> None:
    for scenario in DAEMON_LIFECYCLE_SCENARIOS:
        root = PLAYBOOKS / scenario
        config = _config(scenario)
        provisioner = config["provisioner"]
        assert isinstance(provisioner, dict)
        playbooks = provisioner["playbooks"]
        assert isinstance(playbooks, dict)
        sequence_config = config["scenario"]
        assert isinstance(sequence_config, dict)
        sequence = sequence_config["test_sequence"]
        assert isinstance(sequence, list)

        assert playbooks["cleanup"] == (
            "${MOLECULE_PROJECT_DIRECTORY}/molecule/shared/mock_daemon_cleanup.yml"
        )
        assert playbooks["destroy"] == (
            "${MOLECULE_PROJECT_DIRECTORY}/molecule/shared/mock_daemon_destroy.yml"
        )
        assert "side_effect" not in playbooks, scenario
        syntax_index = sequence.index("syntax")
        assert sequence[syntax_index - 2 : syntax_index] == ["cleanup", "destroy"], scenario
        assert sequence[-2:] == ["cleanup", "destroy"], scenario
        assert "side_effect" not in sequence, scenario
        assert "idempotence" not in sequence, scenario
        assert "GLUDD_MOCK_PORT" not in yaml.safe_dump(config), scenario

        prepare = (root / "default" / "prepare.yml").read_text()
        converge = (root / "default" / "converge.yml").read_text()
        verify = (root / "default" / "verify.yml").read_text()

        assert "mock_daemon/server.py" not in prepare, scenario
        assert "nohup" not in prepare, scenario
        assert "retries:" not in prepare, scenario
        assert "delay:" not in prepare, scenario
        assert "force_handlers: true" in converge, scenario
        assert "mock_daemon_start.yml" in converge, scenario
        assert "mock_daemon_stop.yml" in converge, scenario
        assert "nohup" not in converge, scenario
        assert "kill $(cat" not in verify, scenario
        for module, arguments in _daemon_calls(yaml.safe_load(converge)):
            assert "daemon_url" in arguments, f"{scenario}: {module} omitted daemon_url"
            assert "psk" in arguments, f"{scenario}: {module} omitted psk"


def test_abtest_mock_route_preserves_fail_closed_verdicts() -> None:
    source = MOCK_SERVER.read_text()

    assert 'path == "/admin/abtest/run"' in source
    assert "_abtest_response" in source
    assert 'payload.get("candidate_root"' in source
    assert '"crashed"' in source
    assert '"promote"' in source


def _daemon_calls(value: object) -> list[tuple[str, dict[str, object]]]:
    calls: list[tuple[str, dict[str, object]]] = []
    if isinstance(value, list):
        for item in value:
            calls.extend(_daemon_calls(item))
    elif isinstance(value, dict):
        for key, item in value.items():
            if key in {
                "general_ludd.agent.gludd_abtest",
                "general_ludd.agent.gludd_git",
                "general_ludd.agent.gludd_make",
                "general_ludd.agent.gludd_reload",
                "general_ludd.agent.gludd_skill",
                "general_ludd.agent.gludd_worktree",
            }:
                assert isinstance(item, dict)
                calls.append((key, item))
            calls.extend(_daemon_calls(item))
    return calls


def test_affected_roles_forward_the_explicit_daemon_contract() -> None:
    role_root = (
        ROOT
        / "collections"
        / "ansible_collections"
        / "general_ludd"
        / "agent"
        / "roles"
    )
    roles = (
        "agent_task",
        "implement_change",
        "ornith_self_improve",
        "refactor_code",
        "self_improve_ab_test",
        "self_improve_promote",
        "self_improve_propose",
    )
    calls: list[tuple[str, dict[str, object]]] = []
    for role in roles:
        for task_file in sorted((role_root / role / "tasks").glob("*.yml")):
            calls.extend(_daemon_calls(yaml.safe_load(task_file.read_text())))

    assert calls
    for module, arguments in calls:
        assert "daemon_url" in arguments, f"{module} did not forward daemon_url"
        assert "psk" in arguments, f"{module} did not forward psk"


def test_language_scenario_owns_one_authenticated_daemon_session() -> None:
    """Keep all language roles on one discovered endpoint and cleanup lease."""
    scenario_root = PLAYBOOKS / "language"
    molecule = yaml.safe_load((scenario_root / "molecule.yml").read_text())
    configured = molecule["provisioner"]["playbooks"]
    controller_env = molecule["provisioner"]["env"]
    sequence = molecule["scenario"]["test_sequence"]
    assert controller_env["NO_PROXY"] == "127.0.0.1,localhost"
    assert controller_env["no_proxy"] == "127.0.0.1,localhost"
    assert configured["cleanup"].endswith("/molecule/shared/mock_daemon_cleanup.yml")
    assert configured["destroy"].endswith("/molecule/shared/mock_daemon_destroy.yml")
    assert sequence[:2] == ["cleanup", "destroy"]
    assert sequence[-2:] == ["cleanup", "destroy"]

    plays = list(yaml.safe_load_all((scenario_root / "default" / "converge.yml").read_text()))
    assert len(plays) == 1
    play = plays[0][0]
    first_task = play["tasks"][0]
    assert first_task["ansible.builtin.include_tasks"].endswith("/mock_daemon_start.yml")
    handler = play["handlers"][0]
    assert handler["ansible.builtin.include_tasks"].endswith("/mock_daemon_stop.yml")
    assert play["force_handlers"] is True

    included_roles = {
        task["ansible.builtin.include_role"]["name"].rsplit(".", 1)[-1]: task
        for task in play["tasks"]
        if "ansible.builtin.include_role" in task
    }
    for role_name in LANGUAGE_DAEMON_ROLES:
        role_vars = included_roles[role_name]["vars"]
        assert role_vars["daemon_url"] == "{{ mock_daemon_url }}"
        assert role_vars["psk"] == "{{ molecule_mock_daemon_psk }}"


def test_multi_model_pipeline_tracks_generic_coder_and_owned_cleanup() -> None:
    """Reject the retired pygame-only prompt contract and missing lifecycle stages."""
    scenario_root = PLAYBOOKS / "multi_model_game_pipeline"
    molecule = yaml.safe_load((scenario_root / "molecule.yml").read_text())
    configured = molecule["provisioner"]["playbooks"]
    sequence = molecule["scenario"]["test_sequence"]
    assert configured["cleanup"] == "default/cleanup.yml"
    assert configured["destroy"].endswith("/molecule/shared/destroy.yml")
    assert sequence[:2] == ["cleanup", "destroy"]
    assert sequence[-2:] == ["cleanup", "destroy"]

    converge = (scenario_root / "default" / "converge.yml").read_text()
    for retired_phrase in ("pygame for graphics", r"pygame\.init\(\)", r"pygame\.quit\(\)"):
        assert retired_phrase not in converge
    for current_phrase in (
        "Honor every explicit constraint",
        "Implement every explicitly named class",
        "Use ONLY the tech stack specified",
    ):
        assert current_phrase in converge

    cleanup = (scenario_root / "default" / "cleanup.yml").read_text()
    assert "/tmp/gludd-multi-model-pipeline-molecule" in cleanup
    assert "state: absent" in cleanup

    runtime_playbooks = "\n".join(
        (scenario_root / "default" / name).read_text()
        for name in ("prepare.yml", "verify.yml")
    )
    assert "python3" not in runtime_playbooks
    assert "{{ ansible_playbook_python }}" in runtime_playbooks
    assert "ignore_errors: true" not in runtime_playbooks
    assert "failed_when: false" in runtime_playbooks


def test_binary_smoke_owns_ephemeral_daemon_through_verify() -> None:
    """Pin the packaged daemon to one bounded container-owned lifecycle."""
    scenario_root = PLAYBOOKS / "binary_smoke_linux"
    molecule = yaml.safe_load((scenario_root / "molecule.yml").read_text())
    sequence = molecule["scenario"]["test_sequence"]
    assert sequence[:2] == ["dependency", "cleanup"]
    assert sequence[-2:] == ["cleanup", "destroy"]

    converge = (scenario_root / "default" / "converge.yml").read_text()
    assert "daemon_port: 8000" not in converge
    assert "nohup" not in converge
    assert "retries:" not in converge
    assert "delay:" not in converge
    assert "sleep " not in converge
    assert "ansible.builtin.shell" not in converge
    assert "candidate.bind(('127.0.0.1', 0))" in converge
    assert "--pid-file" in converge
    assert "async: 910" in converge
    assert "ansible.builtin.wait_for" in converge
    assert "gludd-daemon-endpoint.json" in converge

    verify = (scenario_root / "default" / "verify.yml").read_text()
    assert "force_handlers: true" in verify
    assert "gludd-daemon-endpoint.json" in verify
    assert "stop_daemon.yml" in verify
    assert "http://127.0.0.1:8000" not in verify

    stop = (scenario_root / "default" / "stop_daemon.yml").read_text()
    assert "process_start_ticks" in stop
    assert "binary_daemon_manifest.executable" in stop
    assert "_binary_daemon_current_start_ticks" in stop
    assert "_binary_daemon_executable_matches" in stop
    assert "/proc/{{ _binary_daemon_pid_record.pid | string }}/stat" in stop
    assert "/proc/{{ _binary_daemon_pid_record.pid | string }}/exe" in stop
    assert "Refuse to signal a reused or foreign PID" in stop
    assert "_binary_daemon_owned" in stop
    assert "ansible.builtin.wait_for" in stop
    assert "ansible.builtin.async_status" in stop
    assert "failed_when: false" not in stop
