from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "configure_azure_live_github_environment.py"
MAKEFILE = ROOT / "Makefile"
TARGET_CONTRACT = ROOT / "config" / "make_target_contract.json"
RUNBOOK = ROOT / "docs" / "azure-gha-oidc-live-proof.md"


def _load_script() -> ModuleType:
    assert SCRIPT.is_file(), "the Azure GitHub Environment configurator must exist"
    spec = importlib.util.spec_from_file_location(
        "configure_azure_live_github_environment", SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _arguments(*, validate_only: str) -> list[str]:
    return [
        "--repository",
        "sandboxcom/gludd",
        "--github-environment",
        "azure-containerapp-live",
        "--auth-file",
        "/protected/azure-auth.json",
        "--resource-group",
        "gludd-models-eastus",
        "--containerapp-environment",
        "gludd-gpu-environment",
        "--location",
        "eastus",
        "--workload-profile",
        "gpu-t4",
        "--validate-only",
        validate_only,
    ]


def _credentials() -> SimpleNamespace:
    return SimpleNamespace(
        client_id="11111111-1111-1111-1111-111111111111",
        client_secret="never-render-this-secret",
        subscription_id="22222222-2222-2222-2222-222222222222",
        tenant_id="33333333-3333-3333-3333-333333333333",
    )


def _completed(
    args: list[str],
    *,
    returncode: int = 0,
    stdout: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args, returncode, stdout, "")


def test_validate_only_reads_no_credentials_and_calls_no_network(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script()
    monkeypatch.setattr(
        module,
        "load_azure_accelerator_credentials",
        lambda *args, **kwargs: pytest.fail("validate-only must not read credentials"),
    )
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("validate-only must not call gh"),
    )

    assert module.main(_arguments(validate_only="1")) == 0

    output = capsys.readouterr().out
    assert "AZURE_LIVE_ENV_CONFIG_PLAN" in output
    assert "variable_count=7" in output


def test_live_configuration_never_exposes_the_credential_secret(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script()
    credentials = _credentials()
    monkeypatch.setattr(
        module,
        "load_azure_accelerator_credentials",
        lambda _path: credentials,
    )
    expected = {
        "AZURE_CLIENT_ID": credentials.client_id,
        "AZURE_TENANT_ID": credentials.tenant_id,
        "AZURE_SUBSCRIPTION_ID": credentials.subscription_id,
        "AZURE_RESOURCE_GROUP": "gludd-models-eastus",
        "AZURE_CONTAINERAPP_ENVIRONMENT": "gludd-gpu-environment",
        "AZURE_CONTAINERAPP_LOCATION": "eastus",
        "AZURE_CONTAINERAPP_WORKLOAD_PROFILE": "gpu-t4",
    }
    inventories = iter([[], [{"name": key, "value": value} for key, value in expected.items()]])
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[1:3] == ["variable", "list"]:
            return _completed(args, stdout=json.dumps(next(inventories)))
        return _completed(args)

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    assert module.main(_arguments(validate_only="0")) == 0

    output = capsys.readouterr().out
    assert "AZURE_LIVE_ENV_CONFIG_UPDATED" in output
    assert "variable_count=7" in output
    assert credentials.client_secret not in output
    assert credentials.client_secret not in repr(calls)
    assert len([call for call in calls if call[1:3] == ["variable", "set"]]) == 7


def test_failed_verification_restores_all_changed_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script()
    credentials = _credentials()
    monkeypatch.setattr(
        module,
        "load_azure_accelerator_credentials",
        lambda _path: credentials,
    )
    inventories = iter(
        [
            [{"name": "AZURE_CLIENT_ID", "value": "old-client-id"}],
            [{"name": "AZURE_CLIENT_ID", "value": "wrong-client-id"}],
        ]
    )
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[1:3] == ["variable", "list"]:
            return _completed(args, stdout=json.dumps(next(inventories)))
        return _completed(args)

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    assert module.main(_arguments(validate_only="0")) == 1
    rollback_deletes = [
        call for call in calls if call[1:3] == ["variable", "delete"]
    ]
    assert len(rollback_deletes) == 6
    assert calls[-1][-1] == "old-client-id"


@pytest.mark.parametrize(
    "completed",
    [
        _completed([], returncode=1),
        _completed([], stdout="x" * 1_048_577),
        _completed([], stdout="{"),
        _completed([], stdout="{}"),
        _completed([], stdout="[1]"),
        _completed(
            [],
            stdout=json.dumps(
                [
                    {"name": "DUPLICATE", "value": "first"},
                    {"name": "DUPLICATE", "value": "second"},
                ]
            ),
        ),
    ],
)
def test_inventory_rejects_unusable_github_responses(
    monkeypatch: pytest.MonkeyPatch,
    completed: subprocess.CompletedProcess[str],
) -> None:
    module = _load_script()
    monkeypatch.setattr(module, "_run_gh", lambda _arguments: completed)

    with pytest.raises(module._GithubEnvironmentError):
        module._inventory("sandboxcom/gludd", "azure-containerapp-live")


def test_run_gh_normalizes_process_start_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script()
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("sensitive detail")),
    )

    with pytest.raises(module._GithubEnvironmentError, match="invocation failed"):
        module._run_gh(["variable", "list"])


def test_credential_load_failure_is_content_free(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script()
    monkeypatch.setattr(
        module,
        "load_azure_accelerator_credentials",
        lambda _path: (_ for _ in ()).throw(RuntimeError("sensitive detail")),
    )

    assert module.main(_arguments(validate_only="0")) == 1
    error = capsys.readouterr().err
    assert "class=credential_load_failed" in error
    assert "sensitive detail" not in error


def test_live_configuration_is_idempotent_when_all_values_match(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script()
    credentials = _credentials()
    monkeypatch.setattr(
        module,
        "load_azure_accelerator_credentials",
        lambda _path: credentials,
    )
    monkeypatch.setattr(
        module,
        "_inventory",
        lambda *_args: {
            "AZURE_CLIENT_ID": credentials.client_id,
            "AZURE_TENANT_ID": credentials.tenant_id,
            "AZURE_SUBSCRIPTION_ID": credentials.subscription_id,
            "AZURE_RESOURCE_GROUP": "gludd-models-eastus",
            "AZURE_CONTAINERAPP_ENVIRONMENT": "gludd-gpu-environment",
            "AZURE_CONTAINERAPP_LOCATION": "eastus",
            "AZURE_CONTAINERAPP_WORKLOAD_PROFILE": "gpu-t4",
        },
    )
    monkeypatch.setattr(
        module,
        "_set",
        lambda *args: pytest.fail("unchanged state must not be rewritten"),
    )

    assert module.main(_arguments(validate_only="0")) == 0
    assert "AZURE_LIVE_ENV_CONFIG_UNCHANGED" in capsys.readouterr().out


def test_mutation_failure_reports_failed_rollback_without_secret(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script()
    credentials = _credentials()
    monkeypatch.setattr(
        module,
        "load_azure_accelerator_credentials",
        lambda _path: credentials,
    )
    monkeypatch.setattr(module, "_inventory", lambda *_args: {})
    calls = 0

    def fail_after_one_set(*_args: object) -> None:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise module._GithubEnvironmentError

    monkeypatch.setattr(module, "_set", fail_after_one_set)
    monkeypatch.setattr(
        module,
        "_delete",
        lambda *args: (_ for _ in ()).throw(module._GithubEnvironmentError()),
    )

    assert module.main(_arguments(validate_only="0")) == 1
    error = capsys.readouterr().err
    assert "rollback=failed" in error
    assert credentials.client_secret not in error


@pytest.mark.parametrize(
    ("index", "value"),
    [
        (1, "not-a-repository"),
        (3, "unsafe environment"),
        (7, "unsafe resource group"),
        (11, "East US"),
        (15, "yes"),
    ],
)
def test_invalid_input_is_rejected_before_secret_or_network_access(
    monkeypatch: pytest.MonkeyPatch,
    index: int,
    value: str,
) -> None:
    module = _load_script()
    arguments = _arguments(validate_only="1")
    arguments[index] = value
    monkeypatch.setattr(
        module,
        "load_azure_accelerator_credentials",
        lambda *args, **kwargs: pytest.fail("invalid input must not read credentials"),
    )
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("invalid input must not call gh"),
    )

    assert module.main(arguments) == 2


def test_make_target_passes_only_raw_environment_values() -> None:
    makefile = MAKEFILE.read_text(encoding="utf-8")
    marker = "azure-containerapp-live-environment-config:"
    assert marker in makefile
    recipe = makefile.split(marker, 1)[1].split("\n\n", 1)[0]
    assert "scripts/configure_azure_live_github_environment.py" in recipe
    for name in (
        "REPO",
        "GITHUB_ENVIRONMENT",
        "AUTH_FILE",
        "RESOURCE_GROUP",
        "CONTAINERAPP_ENVIRONMENT",
        "LOCATION",
        "WORKLOAD_PROFILE",
        "VALIDATE_ONLY",
    ):
        assert f'$${{_GLUDD_AZURE_LIVE_CONFIG_{name}_RAW}}' in recipe


def test_make_contract_example_is_network_and_secret_free() -> None:
    contract = json.loads(TARGET_CONTRACT.read_text(encoding="utf-8"))
    target = next(
        item
        for item in contract["targets"]
        if item["name"] == "azure-containerapp-live-environment-config"
    )
    assert "AZURE_LIVE_CONFIG_VALIDATE_ONLY=1" in target["behavior"]
    assert set(target["make_variables"]) == {
        "AZURE_LIVE_CONFIG_REPO",
        "AZURE_LIVE_CONFIG_GITHUB_ENVIRONMENT",
        "AZURE_LIVE_CONFIG_AUTH_FILE",
        "AZURE_LIVE_CONFIG_RESOURCE_GROUP",
        "AZURE_LIVE_CONFIG_CONTAINERAPP_ENVIRONMENT",
        "AZURE_LIVE_CONFIG_LOCATION",
        "AZURE_LIVE_CONFIG_WORKLOAD_PROFILE",
        "AZURE_LIVE_CONFIG_VALIDATE_ONLY",
    }


def test_runbook_uses_owned_environment_configurator_and_excludes_secret() -> None:
    runbook = RUNBOOK.read_text(encoding="utf-8")
    assert "make azure-containerapp-live-environment-config" in runbook
    assert "never transfers `client_secret`" in runbook
    assert "AZURE_LIVE_CONFIG_VALIDATE_ONLY=0" in runbook
